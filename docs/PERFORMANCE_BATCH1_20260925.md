# S/X CPU性能第一批：流式扫层、数组共享、分组统计、几何复用

基线：`9c6ea02dcef750c9499fac5763b5cc84793e1e61`。这是源码候选与合成一致性验证，
不是105部署报告，不是X波段真实资料的吞吐或气象质量验收。GPU不在本批范围内。

## 1. 设计与实际落地

沿用Go operations、原multiband Executor、VerifiedArtifactReader和原发布器。
不增加服务、队列或另一套天气算法。新增流式入口由独立CPU执行JSON显式启用。

路径：冻结清单 → 按站／切面校验和读取 → 原S转换／X质控 → 更新所有输出tile
的共同高度赢家 → 释放当前切面 → 垂直最大值 → 独立候选产物。

没有把原来的tile外循环直接套上重复解码；每个源切面在本任务只做一次QC。
旧packed schema3顺序暂存每个物理包，并验证全部逻辑对象汇总哈希，包括未选中
内容，才允许读取。schema1/2按需验证所选对象。Zarr不再更新整个MemoryStore；
按切面读所需字段。任务内小型元数据缓存最多4MiB／4096项，结束即销毁。
物理对象读取仍返回完整bytes，超过执行上限就拒绝，不声称实现了网络Range I/O。

共有高度层赢家状态与站数无关：36 bytes × 网格格数 × 高度层数。小状态可按
显式上限驻留；大状态用私有本地mmap tile，单次只打开一个tile。mmap页缓存、
输出数组和编码缓冲都占真实资源；不能把它称为硬RSS限制。容器限额仍需现场实测。

数组共享只针对不修改的输入及坐标，使用只读view；传播资格等原地修改目标仍复制。
调用方必须遵守原始资产不可变合同；readonly view不防止调用方恶意修改原数组。
原Volume.nbytes仍按字段求和，可能重复计算别名，仅作保守数组预算，不等于RSS。

同一站／tile的地面方位与测地距离只计算一次；每个cut实际射线、距离库、仰角、
波束和年龄仍独立计算。不同产品时间不复用年龄／资格／最终赢家。几何缓存有
明确字节上限，容量不足时重新计算，不能为了命中而使用不相同的几何。

S CF的两个连通域统计循环改成NumPy bincount表，并映射回原标签。不改label
生成、接缝、对象域、阈值、保护、膨胀或时间政策。本包只改统计实现；现有那些
气象策略的优劣不属于本次性能发布的变化。

可选Numba仅编译同高度赢家更新的小循环；分数仍用原NumPy计算。单线程、nogil、
cache=True、fastmath=False；保留1e-12、年龄、分辨率和稳定输入顺序的所有规则。
没安装库／编译失败就明确报错，不悄悄改后端。冷启动和新签名编译仍可能发生，
不能拿纯热核时间代表整个任务。默认NumPy，部署现场确定收益后再显式选Numba。

## 2. 明确的边界

- 旧eager接口/native-v1仍为32层／800万门；新执行路线上限64层、每切面仍不超过
  800万门，体扫与任务总门数、字段字节、物理对象、工作区和产物分别限额。
- 不提高网络16站上限、不修改Go网络schema／一分钟或六分钟合同。24X+5S全网
  发布仍需后续单独验收。不是只把所有常量一起放大。
- 不改变X上游订正声明、频率、标定、MSL、相控阵逐beam能力。未经核验站点仍
  不可做空间融合。本批不替代现有X接入计划的设备合同工作。
- 原始FMT读取／解压／标准化阶段没有迁移到新的流式解码器。本批边界从已发布
  标准化／原有QC／canonical资产进入multiband开始。
- 单切面内所有射线及全部距离仍一起处理；不按距离切断相位，不在缺口之后
  重启PIA或悄悄重置损耗。相位／中值滤波函数本身未重写。
- 原子发布器仍接收bytes；原生NPZ通过磁盘增量编码减少解码数组驻留，最终读回
  一份有上限的编码bytes。未声称无界输出或流式上传。
- 原X输出全部candidate-only，QPE关闭。S原来的CR资格／硬拒绝保持。没有自动
  切换、部署、清理历史数据、改near_enabled或新建晴空资产。

## 3. 配置与运行

无 `RAINPULSE_MULTIBAND_EXECUTION_CONFIG`：保留eager路线，但采用数值等价的
数组共享、对象统计与几何复用。存在显式执行文件且streaming=true：走新路线。

```bash
python scripts/configure_performance_batch1.py \
  --output /新位置/cpu-stream.json --backend numpy \
  --scratch-parent /已存在且已分配预算的本地工作目录
python scripts/preflight_performance_batch1.py \
  --execution /新位置/cpu-stream.json --network /实际网络.json --require-zarr
```

生成器只写新文件，拒绝覆盖；默认预算是执行防护上限，不是对机器空闲容量的
推荐。用 `--limits /审核后的完整执行JSON` 提供适合实际容器的预算。背景磁盘、
输入源目录和临时工作目录必须分开。切换要按既有release/drain流程完成。

启动时读取且冻结执行文件，每个任务重新核对。Go目前没有必填execution摘要
字段：本批不虚称规划器已经冻结这个新字段。若任务显式携带execution_sha256会
验证；否则必须通过协调镜像／只读挂载／排空任务保证同池同执行版本。产物记录
执行摘要、后端、Python与依赖版本。不同执行模式不要并发接替旧冻结任务。

Numba不是基础依赖。先在目标Python/NumPy环境单独安装并锁定、预热、跑对照后
生成 `--backend numba` 的新文件。这里的测试环境为Python3.13.5、NumPy2.3.5、
SciPy1.17.0、Numba0.65.1；不声称这些是现场锁定版本。

## 4. 新原生产物合同

流式x_qc仍返回4个组合逻辑对象及2个native对象；native元数据使用新的
`rainpulse.multiband.native-stream-v1`，不是把40层塞进旧native-v1。新NPZ逐切面
写入，固定ZIP元数据、numeric-only、逐头检查、最多4096数组。旧eager reader
明确不支持新合同。产品PNG/二维数组字段含义不变，新增执行回执。

新合同schema和具体限额见 `contracts/internal/multiband/cpu-stream-v1.md`。
旧网络配置、现有原始资产和已有产品不迁移、不删除。

## 5. 验证与现场验收

```bash
bash scripts/test_performance_batch1.sh
# 单独进程跑三个后端；包含合成数据生成、X QC、融合，不含真实I/O和发布
python scripts/benchmark_performance_batch1.py --mode reference --output /tmp/ref.json
python scripts/benchmark_performance_batch1.py --mode stream-numpy --output /tmp/cpu.json
python scripts/benchmark_performance_batch1.py --mode stream-numba --output /tmp/jit.json
python scripts/benchmark_performance_batch1.py --mode stream-numpy \
  --cuts 40 --rays 360 --gates 1627 --output /tmp/large.json
```

参考测试用 `git show` 读取固定基线，或指定 `RAINPULSE_PERF_REFERENCE_ROOT` 指向
交付包reference-source.zip解压后的目录。不能把参考路径指向修改后的工作树。

必须另外跑原有multiband测试、既有S质控回归、实际Zarr2读写、真实MinIO/NATS/
operations完成事件。新原生格式需确认Preview/导出方正确标记；不允许把它注册成
已验收S QC。以原本可处理的真实小X、40层大X、S+X多站和历史／实时混合压测。

按字段比较RAW、QC、PIA、KDP、flags、资格、no-echo、winner/source、年龄、高度、
分辨率，源顺序和tile大小不能引入变化。记录冷/热缓存、首次JIT、P95/P99、
RSS/cgroup峰值、磁盘实际I/O、MinIO字节、队列延迟和取消/失败清理，不只看平均核耗时。

运行回退：先停接单并排空，改回原镜像/设置；源码回滚与运行回滚不同。不同native
合同产物保留原身份，不能通过覆盖同名旧资产“回退”。
