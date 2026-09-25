# X 站点与原生质控浏览交付

主工作台“质控排查 → X 波段质控对比”打开 `/?preset=qc&band=X`。
站点/体扫目录独立于预报周期，按 UTC+8 日期查询登记资料；首次进入选最近有资料日期和成功体扫。
仅列已登记且当前配置明确标为 X 的站点，不通过站号前缀猜测波段。未完成解码/QC 的体扫仍可见。
已完成候选结果直接关联已有 ops_tasks，不重算、不修改原始扫描状态或业务资格。

## 接口与身份

OpenAPI 包含 radar-stations、radar-scans、radar-products 详情与资产四个只读入口。
目录只提供展示字段，不暴露 Worker 身份、环境配置、任意对象存储路径或管理写权限。
结果 ID 固定为 task UUID + attempt UUID；结果详情校验站号、scan_id、候选资格、扫层数量/身份与路径。
资产只允许已验证 manifest 的 raw/qc/flags PNG；复用原资产路径、发布标记、SHA256、类型和大小检查。
退役或失效结果不能冒充已就绪。新失败任务不会覆盖旧成功结果，最近最多 20 个成功版本可选。

## 页面

双栏共同显示同一原生扫层，默认最低仰角，保留原始编号与重复仰角。
两张图均读取完成才切换，任一图失败不显示错配图对；切站、切扫取消旧请求。
支持联动缩放/拖动、键盘平移、质控标记、版本选择、真实体扫时间轴和可用图件播放。
缺扫不插值，前后图使用产物原有同一色标；PNG 中已烧入未决覆盖色，不能假称支持单独关闭该覆盖层。
日期/站/扫/层/结果保存在 URL；链接到较早分页结果时自动继续读取同一天有界目录。
手机宽度使用可展开站点清单。后台重算入口预填所选站和体扫时间范围，但浏览不会自动触发计算。

## 验证

- `cd apps/web && pnpm test && pnpm lint && pnpm build`
- `cd services/control && go test ./internal/operations ./internal/apiapp ./internal/workspace ./internal/controlplane`
- `bash scripts/check_generated_contracts.sh && bash tests/rp002_contracts_test.sh`
- PostgreSQL：独立临时数据库，`RAINPULSE_OPS_ALLOW_INTEGRATION=1 RAINPULSE_OPS_TEST_DATABASE_URL=... go test -tags=integration ./internal/operations -run TestRadarWorkspaceCatalogIntegration -count=1`

数据库测试覆盖 X/S 分离、未有体扫的站、NORMALIZED 与候选 READY 共存、后失败不遮蔽成功、资产不可用、日期边界、分页和游标跨站拒绝。
React 测试覆盖独立目录、历史日期、非连续扫层号、无 QC 体扫不残留旧图、图片失败无半对照、固定版本链接。

## 当前资料边界

105 已登记的 X 站是 ZF101、ZF505，当前已验证的成功图件分别为 40/9 层。
磁盘目录历史清单记录了 24 个 X 站；其余站点尚未通过当前 ingest 配置与身份登记，因此页面明确标注“已登记 X 站点”，不宣称全站全日已接入。
后续按原 S/X 历史接入流程核对头信息/别名、登记和分批解码/QC，新登记 X 站会自动进入此目录。
空间坐标与标定未核验时仅展示站心 PPI，不提供伪地理覆盖、不启用业务融合/QPE。
本期不从图片估算逐门值，不实现数值点查。

发布按本地整合、测试、main 合并、105 构建产物更新执行。Go/Web 可回退，不删除扫描与候选结果。
