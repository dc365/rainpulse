# V6.1 验证记录（2026-09-14）

## 范围

基线 upstream `dbcfa73853cf809ca3076e2ce33a86a6814d0a1f`，完整根源码树
`25cb9815495ce308dba7b86e4972c58650ca6bf1`。本地重建的干净基线与该根树逐字节一致。
所有旧QC YAML冻结；原V6 renderer1.2.0保持不变。

本轮不连接105，不发布、不执行现场重算；没有本案真实PNG、原始体扫或人工标签。
测试中的对象存储为受控测试实现，算法核心实际调用安装的Py-ART2.2.5/wradlib2.9.5。
原生bRopo/RDD未构建运行，不能算已验证的效果基线。

## 最终实际执行

|检查|结果|
|---|---|
|Python算法、配置、契约全量|881通过，0失败、0跳过；18条既有pySTEPS警告|
|新增6.1专项|44项，已包含在上述全量中|
|6.1 QC→Hybrid→Grid→Mosaic→QPE补充参数化用例|1项，已包含在上述全量中|
|新专项及既有开源交付测试合跑|57通过，均包含在上述全量中|
|Go仓库脚本 test/vet/build|均通过|
|Web vitest|79项，19文件，通过|
|TypeScript、Vite生产构建|通过|
|ESLint|0错误、3条既有警告|
|Ruff|通过|
|forensic_export/network_compare CLI --help|实际执行通过|

实际命令：

```bash
python -m pytest algorithms/tests configs/tests contracts/tests -q -ra --junitxml=final-all-python.xml
bash scripts/go_control.sh test ./...
bash scripts/go_control.sh vet ./...
bash scripts/go_control.sh build ./...
# 在 apps/web 下，使用已安装的锁定依赖执行等价package脚本
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
node node_modules/eslint/bin/eslint.js .
ruff check algorithms scripts/qc_v61_mechanism_demo.py
```

全量Python最终运行约215秒，整个测试集不是单体扫性能。环境版本和最终日志随ZIP的
validation/交付；没有用上游CI状态替代本地执行结果。

## 本次回归覆盖

- 使用真实可解析雷达配置测试v1/v5/v6/v6.1引擎能力加载，未配置/无效配置显式降级。
- 250/500/1000m门距下，细线与宽父对象相连时恢复局部候选，不吞掉宽交汇区。
- 原窄对象和形态模型保留；保护天气、真实无回波、缺门及0°/360°缺方位边不能越过。
- 50/100/250/440km相邻射线的中心距与足迹间距区分，父ray/gate仍来自原始锚点。
- geometry-only（两项局部开关关闭）时，在相同prepared条件下与V6动作、QI、flags、数值精确一致。
- 真实库的Worker函数与本地回放输出工件摘要一致；此测试输入是合成体扫。
- 显式shared_baseline/shared_candidate/each_profile模式；保存产物绑定prepared上下文。
- 辅助资源内容校验、环境作用域恢复、禁止捕获凭据；文件读取导致atime变化不误判内容变更。
- 原始640×640 RGBA PNG、layer、sweep、asset、sampling绑定；错误身份/摘要/尺寸被拒绝。
- 原生datetime64[ns]射线时间不会浮点化丢精度，时间语义摘要支持日期/时间数组。
- 同scan跨analysis只计一个物理观测；旧产物无prepared身份返回未证明，不造假“一致”。
- 确认准确率只统计可信二类标签，隔离不冒充确认，未知/混合不算成正确标签；无标签保持null/INSUFFICIENT。

开发期间先定位修复了目录哈希误比较atime、旧参数测试未忽略新None字段，以及真实时间编码
在取证路径中的处理缺口。冻结旧参数摘要未被改写以使测试变绿。

## 小型机制演示（不是天气召回）

`python scripts/qc_v61_mechanism_demo.py --output /new/synthetic.json`
使用仓库的开发测试fixture，需要dev依赖；输出中明确data_kind=synthetic。

12×1840、250m合成切面，窄线连接宽父对象：指定原细线域1338个实测门，原窄线候选0，
6.1局部分支候选1338；宽交汇区两者都不增加候选。这是候选拓扑检查，不是确认污染准确率。
500m和1000m测试得到同类行为。

250km处相差1°的邻接采样，中心距约4363m、足迹间距0；新模式可以进入有界复核。
缺射线、缺门、天气屏障仍使该路径不可用。进入复核不等于产生确认或隔离动作。

窄模块在该小切面上单次执行约1.7ms→4.8ms，仅是本机合成计时，不外推为105均值/P95。
资源准备、上传和序列化不包含在此单模块计时中。目标硬件和真实体扫性能待验证。

## 不作出的结论

不声称08:15/08:40/08:45真实残留已经消除；不声称确认召回、天气误删/误隔离或雨量站效果提升。
没有实屏/浏览器和生产资源负荷测试。现场必须冻结同一原始体扫、实际上下文、配置和同一渲染器，
用只读取证报告定位剩余门，再做开发回归及独立天气过程验收。
