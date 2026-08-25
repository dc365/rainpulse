# RP-015 应用产品与 API 验收记录

日期：2026-08-26
代码基线：`ab7748f`
测试服务器：`private-test-host`
部署目录：`<remote-project-dir>`

## 1. 验收结论

RP-015 的产品构建、发布、目录查询、受控文件交付、点查询、区域统计、SSE 状态和
幂等重放纵向链路已完成，并通过 测试服务器端到端验收。输入严格限定为一个已提交
的 `ForecastOutput contract_version=1.1`，内部预报 Zarr 没有直接暴露给前端，也
没有作为应用 NetCDF 的替代品。

本次仍使用 RP-014 的静态合成预报，因此结果只证明产品、交付链路和交互工作台，
不证明真实降水预报技巧。OpenLayers 短临 GIS、五分钟播放时间轴、单点曲线、区域统计、
资产交付和产品溯源均已实施并部署验收。RP-015 的合成纵向验收整体完成，真实
预报技巧验收继续作为业务上线门槛。

## 2. 冻结的产品边界

- 配置：`rp015-application-products-v1`。
- 产品 bundle：`rainpulse.application-product-bundle` 1.0。
- 输入：`ForecastOutput` 1.1，来源 URI 与 SHA-256 必须和已提交 model run 一致。
- 网格：EPSG:4326，`118–123°E`、`25–27°N`，`0.01°`，`501 × 201`，点中心注册。
- 像素边界：`117.995, 24.995, 123.005, 27.005`。
- 瞬时降水率：T+5 至 T+120，共 24 个五分钟时效，单位 `mm h-1`。
- 累计降水：T+60 和 T+120，单位 `mm`。
- 每个二维场生成透明 RGBA PNG、WGS84 COG 和 NetCDF3 classic。
- `rain_rate` 额外生成固定记录点查询索引，不把大网格 JSON 化。
- 有效无雨保留数值 `0.0`；缺测在 COG/NetCDF 边界使用 nodata/`-9999.0`，不能
  转为有效零。PNG 中两者都可透明，但 manifest 必须记录有效、缺测和无雨单元数。
- bundle 使用临时对象、最终复制和最后写 `_SUCCESS.json` 的原子提交协议。

## 3. 实现内容

### 3.1 Python 产品 Worker

- 新增 `product-builder` profile、独立 subject/consumer 和 Compose 健康检查。
- 构建 24 个降水率场、60/120 分钟累计场和点查询索引。
- COG 固定 EPSG:4326、北向上、DEFLATE、COG layout 和像素边界。
- NetCDF 固定二维 `lat × lon`、递增经纬坐标、CF/旧模式样例兼容属性和
  `_FillValue=-9999.0`。
- 每个对象记录媒体类型、SHA-256、大小、时效、有效时刻、单位和格点状态摘要。
- 构建完成后从内存重新打开 COG、NetCDF、PNG 和点索引做强校验，再执行原子发布。

### 3.2 Go 控制面与事件

- 新增 `product.build.requested.v1`，只有 `BASELINE_READY` 可进入
  `PRODUCT_BUILDING`。
- 完成事件再次核对 run/job/model-run、源预报 URI/SHA、网格、版本、3 个产品、
  79 个资产、时效和格点状态计数，成功后才进入 `PUBLISHED`。
- 数据库迁移 `0014_application_products.sql` 增加构建记录、产品有效时刻、源预报
  身份和资产有效时刻。
- 每个产品产生一条确定性的 `product.published` 事件，JetStream 流显式覆盖
  `rainpulse.products.published`。
- replay 对所有请求事件显式映射主题，未知事件拒绝发布，避免回退到模拟 Worker。

### 3.3 REST 与查询

- 产品列表、详情和资产列表。
- 只允许读取已登记的产品资产；响应前校验对象大小、SHA-256 和格式签名。
- PNG 使用 immutable cache、ETag 和 `nosniff`；COG/NetCDF 以附件交付。
- 点查询按最近网格点返回 24 个时效的降水率、置信度、有效性和网格坐标。
- 区域统计按指定 bbox 和 lead 返回有效/缺测数、覆盖率、均值和最大值。
- SSE 继续输出 `run.updated`，发布后快照为 `PUBLISHED`。

### 3.4 React 短临工作台

- 默认进入短临预报工作区，保留分析诊断和雷达运行两个既有业务入口。
- 使用 OpenLayers 作为单一 GIS 运行时，View 固定为 EPSG:4326；可平移、缩放、复位，
  并显示经纬网、比例尺、城市标注、坐标读数与当前选点。
- 福州产品点中心范围为 `118–123°E`、`25–27°N`，透明 PNG 严格按像元边界
  `117.995,24.995,123.005,27.005` 进行 ImageStatic 地理配准，与网格不产生半格偏移。
- 测试部署默认使用可配置 XYZ OpenStreetMap 底图，保留 GSHHG 2.3.7 本地海岸线作为
  不依赖底图的参考层；正式部署应通过 Vite 构建变量指向经批准的内网瓦片服务。
- 支持 24 个五分钟时效、前后帧、900 ms 自动播放、滑轨选时和方向/Home/End 键选时；
  T+60/T+120 累计量保持固定累计窗口，不进行逐帧播放。
- 地图点击会以实际 EPSG:4326 坐标查询单点预报；单点面板展示 24 时效雨强曲线、当前置信度及
  T+30/T+60/T+120 关键值。
- 区域面板支持福州城区、闽江口、闽东沿海预设和自定义 bbox，展示均值、最大值、
  覆盖率及有效/缺测格点数。
- 当前时效直接提供 NetCDF、COG、PNG 受控下载；产品溯源展示 run/product/grid、
  配置版本、源预报 URI/SHA 和当前资产 SHA。
- 页面明确显示“发布状态不等同于预报技巧通过”，避免把工程验收误读为业务技巧
  验收。

## 4. 本地验证

- RP-015 产品测试 4 项通过。
- 合约测试 34 项通过，OpenAPI 生成的 Go/TypeScript 类型一致。
- Go 全包测试通过，新增 JetStream 主题和 replay 全事件路由回归测试。
- Web 2 个测试文件共 3 项测试通过，覆盖产品加载、五分钟时效、累计产品及点/区域
  查询；ESLint 和 Vite 生产构建通过。
- `make lint`、`git diff --check`、Linux/amd64 API、Web、orchestrator 和 Worker
  制品构建通过。
- JetStream 与 replay 两个修复均执行了 red/green：修复前测试分别观察到缺少产品
  发布主题，以及 QPE/diagnostics/product replay 错投模拟主题；修复后全量 Go 测试
  通过。

## 5. 测试服务器验收

### 5.1 部署状态

- 原地部署并保留 `deploy/.env`、`runtime/`、PostgreSQL、NATS 和 MinIO 卷。
- `0014_application_products.sql` 已应用。
- 16 个长期 Compose 服务全部 healthy，新增 `product-builder-worker`。
- 部署前数据库无历史 `products`/`product_assets`，迁移不存在兼容阻塞。

### 5.2 输入与构建身份

- forecast run：`0ce8e90c-3160-5e5d-874d-1eda09bf1084`
- model run：`79eff584-e840-5f38-a5fd-d69a0caaf6fc`
- RP-015 job：`c495e279-5ba0-53fa-a3c9-e2ed51e9aa5f`
- 最终状态：`PUBLISHED / SUCCEEDED / SUCCEEDED`
- 输入 ForecastOutput SHA-256：
  `6344c411c9e3f3b2feb7da959f38563577383487e2efb65092609eaf37a0da9f`
- bundle SHA-256：
  `9ec6d6030e67a56e7aa0322bac2c90db2098dec0e122dc2b0d95d8df300feed0`
- Worker 运行时间：1152 ms。
- bundle：80 个对象、79 个登记资产、22,887,279 bytes。

```text
s3://rainpulse/products/0ce8e90c-3160-5e5d-874d-1eda09bf1084/
pysteps-lk/pysteps-lk-1.0.0/distribution/
rp015-application-products-v1/application-products
```

### 5.3 产品与资产

| 产品 | product_id | 有效时刻 | 资产数 |
|---|---|---:|---:|
| rain_rate | `c6896a27-dbf4-55b1-b153-fb3d05da3fc7` | 24 | 73 |
| accumulation_60 | `6f9ecf8f-4aa8-5c47-9728-9a6c61400d6e` | 1 | 3 |
| accumulation_120 | `60832291-d78e-59b6-af7a-f84edab50b5b` | 1 | 3 |

| 资产类型 | 数量 | 总大小 bytes |
|---|---:|---:|
| PNG | 26 | 43,654 |
| COG | 26 | 96,208 |
| NetCDF | 26 | 10,612,408 |
| 点查询索引 | 1 | 12,084,184 |

首时效场记录 100,701 个格点，其中有效 95,676、缺测 5,025、有效无雨 10,050，
覆盖率 `0.9500998003992016`。测试 PNG 为 `501 × 201`，通过 API 下载后 SHA-256
与登记值一致。

### 5.4 API 与事件证据

- 产品目录返回 3 个产品及完整源预报身份。
- T+5 PNG 返回 `200 image/png`、正确长度、immutable cache、ETag 和 `nosniff`。
- 点 `(120, 26)` 返回 24 个时效；T+5 与 T+120 均为有效 `2 mm/h`，置信度随时效
  从约 `0.7874` 降至 `0.3031`。
- bbox `119,25.5,120,26`、T+60 返回 5,151 个有效格点、0 个缺测格点、均值和最大值
  都为 `2 mm/h`。
- SSE 首个快照为 `run.updated`，状态 `PUBLISHED`。
- 3 条 `product.published` outbox 均为 `published`，部署结束时未发布 outbox 为 0。

### 5.5 幂等重放与运行修复

首次验收暴露两个集成缺口：JetStream 流只包含 `rainpulse.jobs.>`，以及 replay 未映射
新的 product 事件。修复并重新部署后，Worker 明确记录
`job.idempotent_replay`。数据库仍为 3 个产品、79 个资产、1 个 job attempt、3 条
产品发布事件，没有重复产品、资产或构建记录。

### 5.6 Web 部署与浏览器证据

- 前端功能基线 `ab7748f` 编译为 Linux/amd64 Web 二进制和 Vite 静态产物，只重建
  `web` 服务；`deploy/.env`、`runtime/` 和三个持久化数据卷均保留。
- 部署后 16 个长期 Compose 服务全部 `healthy`，Web 入口为
  `http://private-test-host:4173`。
- 服务器实际产品目录加载 run
  `0ce8e90c-3160-5e5d-874d-1eda09bf1084`，状态 `PUBLISHED`，24 个降水率时效、
  两个累计产品和三类分发资产均可访问。
- 实际浏览器载入 2 个 OpenLayers canvas，初始 z8 XYZ 瓦片、本地海岸线与当前产品 PNG
  均返回 200；点击放大后实际请求 z9 瓦片，比例尺从 100 km 更新为 50 km。
- 地图中心点击后查询坐标更新为 `120.50°E,26.00°N`；时间轴播放后地图、有效时刻、
  点置信度、区域统计和交付资产同步到 T+30。
- 切换 2 小时累计后展示 T+120 及对应 NetCDF、COG、PNG 资产，播放控件正确禁用。
- 闽江口预设 bbox 返回
  2,806 个有效格点、0 个缺测格点。
- 1440、768、375、320 px 实际浏览器检查通过；375 和 320 px 页面无全局横向
  溢出，浏览器控制台为 0 错误、0 警告。
- 截图证据位于 `output/playwright/rp015-gis-server-1440.png` 和
  `output/playwright/rp015-gis-server-375-top.png`，不作为运行时发布资产。

## 6. 后续业务门槛与下一步

- RP-016 加入检验、故障注入和原始雷达到 React 的完整业务验收。
- 真实预报产品仍需至少三个连续、业务可用、具有可跟踪降水回波的上游时次；当前
  静态合成场不能用于评价产品业务效果。
- 业务上线前仍需用代表性普通降雨、强对流和台风个例验证时空演变、阈值色标、点面
  查询、缺测显示和分发格式，并形成预报技巧指标。
- 当前控制面运行标签仍为历史保留值 `rp008-v1.1-0748898-20260824`；代码提交和本
  验收记录是当前实现的权威基线，普通测试部署不修改远端 `.env`。
