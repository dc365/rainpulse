# 邻站支持阻断与1985高程基准（2026-09-15）

## 已确认

105 的 configs/radars/fujian-20260828 下四站 altitude_datum 均为 null。用户本轮确认四站站高/天线海拔采用1985国家高程基准。四站天线海拔分别为 Z9591=641 m、Z9593=546 m、Z9598=1740 m、Z9599=1047 m。

build_trusted_cross_radar_support 在当前站高程未验证为 EGM2008 或 terrain 缺失时立即返回，因此这些扫描的可信邻站比较首先被高程基准门控阻断。不能推断解除该条件后所有门都可比较；仍需覆盖、波束高度重合、地形遮挡和供体QC验收。

## 本轮代码修改

- qc_geometry.py：加入 availability_audit；区分缺少波束配置、未验证/不兼容的当前高程、缺DEM。对可执行比较的每份参考记录水平配准、有效测量、地形、供体QC、波束高度重合阶段的通过次数，以及最终唯一可比较门数。阶段次数按参考仰角累计，不能当成唯一门数。
- qc_engine/context.py：把每仰角诊断写入 support_statistics.availability_by_sweep；将初始化 reference_prepared_not_yet_comparable 更新为实际 comparable/no_comparable_gates，并记录 comparable_sweeps。原有观测/动作数组不变。
- configs/radars/fujian-1985-20260915：四份新版本配置声明 EPSG:5737；保留旧配置及原始海拔数值，未切换生产路径。此配置不是高程转换后的数据。

## 仍缺的转换条件

本地 pyproj 对 EPSG:5737→EPSG:3855 执行 TransformerGroup(..., allow_ballpark=False)，可用转换=0、不可用已登记操作=0。这只描述当前安装环境，不代表不存在区域转换资料。

PROJ 官方明确说明，垂直基准的 ballpark 转换可能只处理单位，不能作为实际基准校正：
https://proj.org/en/stable/glossary.html
实际垂直格网转换机制：
https://proj.org/en/stable/operations/transformations/vgridshift.html

恢复可信比较仍需可追溯的1985→EGM2008转换格网，或各站由测绘依据确定的天线EGM2008高程/修正量。不能假定修正量为0，也不能直接将1985改标为EGM2008。按站接入转换时应保留源高程、转换符号、来源、精度和版本，并与DEM对齐。

## 验证与边界

- 新的缺配置/缺DEM诊断测试先失败（旧对象无availability_audit），修改后通过。
- 几何、上下文、碎片测试28项通过；新增1985配置回归后，几何9项通过，共29项不同测试通过。既有NumPy二进制尺寸警告仍存在。
- 四站新配置经真实加载器及可信比较函数执行，均准确返回 current_vertical_datum_incompatible，未产生伪造的可用门。证据：runtime/reports/radial-fragments-20260915/height1985-audit.json。
- 同类检查：高程门控还用于垂直一致性、Hybrid和相对偏差模块，均保留，不绕过。
- 尚未部署本轮诊断代码，未发布新质控产品，未声称径向残留进一步改善。代码未提交。
