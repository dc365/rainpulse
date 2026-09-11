# 按需累计时间轴（2026-09-09）

## 当前实现

- 仅保留原来的 5 分钟时间轴：单击一个刻度查看该时效雨强，按住并拖动跨刻度
  选择累计区间（支持反向拖选），松开计算。保留 0–1、1–2、0–2 时快捷按钮；
  删除模式切换按钮和独立双滑块。累计后再次单击即可查看单时效。
- 四图分别请求，已完成的算法先显示。快速换区间取消旧请求，不展示旧区间结果。
- 浏览器只调用 Go；Go 解析当前数据身份，Python 在既有 product-builder worker
  中读取数值场累计，返回透明 PNG 和同一数组的精确点值，单位 mm。
- QPE 用后续真实分析场；LK 用逐帧雨强；STEPS 先逐成员累计再取 P50；
  NowcastNet 用已保存的集合均值。缺测不补零，不累计图片或瞬时 P50。
- 结果只在内存保留 10 分钟，最多 32 项 / 64 MiB；原始点索引缓存另限 64 MiB。
  源 URI/SHA256/时效/网格进入缓存键，重算后不会混用旧结果；不生成永久版本、
  不改数据库产品、不调用 GPU。旧的固定累计产品仍兼容，但此视图不依赖它们。
- 同时修正数值取值兼容性：LK 的 `/content` 图片 URL、NowcastNet 的
  `nowcastnet` 点索引键均可使用既有精确取值接口。
- 兼容旧 QPE 点索引用首两个 float32 坐标相减记录步长的方式；仅接受该已知
  编码误差，仍拒绝不匹配的网格，不修改历史数据或放宽一般网格校验。

接口约定见 `contracts/data/workspace-interval.md`。

## 105 验证

原位更新 api、web、product-builder-worker，增加历史集合源只读权限；没有重算
模型、清理历史数据、重启 GPU 服务或增加独立服务。

2026-08-28 16:30 北京时间，四算法分别验证 0–60、60–120、0–120、15–45 分钟：
16 组均返回累计图片与点值。首次请求约 0.8–3.1 秒；命中缓存约 0.8–1.1 秒
（含 Go 当前来源解析）。QPE/LK/NowcastNet 通过两小时可加性核对。

STEPS 后段的有效累计覆盖较少，是输入成员逐时有效范围的交集；无完整支持的
格点保持缺测。不能为扩大色块而补零，也不能相加两个小时的 P50 得到两小时 P50。

## 可复现验证

```sh
algorithms/.venv/bin/python -m pytest algorithms/tests/test_interval.py algorithms/tests/test_accumulation_windows.py algorithms/tests/test_worker_runtime.py -q
bash scripts/go_control.sh test ./internal/workspace ./internal/webgateway
pnpm --filter @rainpulse/web exec vitest run src/workspace/IntervalTimeline.test.tsx src/workspace/accumulation.test.tsx src/workspace/model.test.ts src/workspace/MainWorkspace.test.tsx
pnpm --filter @rainpulse/web build
python3 scripts/check_interval_api.py --base-url http://DEPLOY_HOST:4173 --cycle-id CATALOG_CYCLE_ID
```

本地 Python 32 项、前端 20 项及 Go 两个包通过。接口检查脚本还将自定义区间
与原始逐帧点值积分独立核对。浏览器验收包含桌面四图、375px 窄屏、拖选与快捷按钮。

本轮未重新生成离线程序 ZIP；内网打包应重新打入更新后的上述三个镜像和程序。
