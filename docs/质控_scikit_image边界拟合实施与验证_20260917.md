# scikit-image 边界拟合实施与验证

已引入 scikit-image==0.26.0，更新 algorithms/pyproject.toml 与 uv.lock。
本地安装通过；105现有worker内仅安装到 /tmp/qc-skimage 隔离目录，未覆盖生产
Python依赖或变更生产QC动作。代码尚未提交。

## 实现

`qc_engine/source_boundary.py` 使用 scikit-image RANSAC + LineModelND，
对Py-ART原始对象每个距离上的两侧方位外包络分别拟合。内部孔洞不参与。
边界两侧必须都有真实DBZH观测；坏径向、缺测邻边、扫描端点不构成物理边界。
用wradlib.bin_distance的4/3地球模型转为雷达局部米制坐标。
缓存不含站高时，验证脚本显式加载站点配置并记录SHA256；不使用默认高度。
当前站高来自1985基准配置，属于明确的几何近似，不恢复邻站高程信任。

按50km距离块奇偶留出，分别按距离稳定排序并最多取2000点；RANSAC固定seed=42、
max_trials=200，残差阈值1500m。训练及留出均需20点、100km跨度。
训练内点比例≥0.8，留出内点比例≥0.8且P90残差≤1500m，拟合线到原点≤3000m。
这些是冻结的候选工程阈值，尚非通用气象验收参数。没有强制拟合线经过原点。
双边都通过才记录paired_radial_geometry；不直接修改逐门资格或反射率。

第一轮把内部孔洞边界混入，导致不一致。根据源代码及实测点分布修正为外包络，
保留原数值阈值；新增内部孔洞扰动不改变外边界结果的回归测试。

## 105 最终实测

使用7.3.2增量候选剩余门审计，四缓存样本均执行完成。

- Z9591 10:42 主对象24：两侧均通过。边界点247/461；离中心约0/572m；
  留出P90残差约0/320m，留出内点比例均100%。0是浮点近似，不能理解为物理零误差。
- Z9591 10:48 主对象1：一侧通过，521边界点，离中心269m，留出P90约1252m。
  另一侧241点但训练/留出距离跨度不满足要求，记录insufficient_support；
  对象整体仍unconfirmed_geometry，未降低门槛或用缺测边缘凑双侧。
- Z9598两个对照在45dBZ对象层没有对象，故没有边界评估对象。这不是证明真实
  降水几何误报率为零，需要较低阈值及正常窄雨带另做对照。

几何通过只支持“符合径向源形态”，不等同于确认污染或降水可整块删除。
本轮新增隔离为零，未重发布QC数据或网页图，生产仍7.3.1。

## 验证

新边界5项测试通过：过原点/偏移直线、留出扰动不改训练拟合、稀疏/缺测拒判、
真实邻边与缺测邻边区分、内部孔洞不改外包络。此前缺模块红灯实际运行。
相关对象/宽扇区测试也通过；最终新增测试单独通过。Ruff及uv lock --check通过。
原有numpy扩展ABI警告仍存在，未出现新增失败。

```sh
algorithms/.venv/bin/python -m pytest algorithms/tests/test_source_boundary.py algorithms/tests/test_object_morphology.py algorithms/tests/test_broad_source.py -q
```

105入口：
```sh
PYTHONPATH=/tmp/qc-skimage:/tmp/qc732/algorithms:$PYTHONPATH python /tmp/audit_qc_pyart_objects.py --case 1 --boundaries
```

最终JSON：runtime/reports/qc732-distance-polar-20260917/boundaries-{1,2,3,4}.json。

后续将几何证据与局部源功率、距离极化和天气保护连接，先输出逐门原因对照。
10:48缺另一侧真实边界支持，不能直接整对象删除；也不能把通过几何检验作为
一个与源模型完全独立、可重复计票的概率。
