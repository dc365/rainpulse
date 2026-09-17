# Gemini 建议审阅与 scikit-image 适用方案

## 结论

形态判断有启发，优先引入 scikit-image 的 RANSAC/LineModelND 做对象边界几何
测量；不照搬“开闭运算后按紧凑度整块删除”。这是研究建议，尚未安装或实测
scikit-image。本轮没有修改生产算法。当前本地算法虚拟环境未安装该库。

## 对 gemini_review.md 的修正

1. 截图只能提出非气象源假设，不能据此确定5G、RLAN、太阳干扰或接收机饱和。
   太阳来源需要太阳位置、时间与硬件信息；接收机问题需要原始编码/状态证据。
2. 文档假定低RHOHV、高纹理，不符合当前核心样本：此前实测远距离RHOHV接近1、
   ZDR集中、相位分布窄。低相关+高纹理门槛可能继续漏掉同一宽扇区。
3. 本地wradlib 2.9.5无 `wradlib.clutter`，`classify.detect_spikes` 也不存在；
   没核实到文档所称可直接调用的 `wradlib.clutter.detect_spikes`，不能据此开发。
4. Py-ART 2.2.5的可调用函数为 `pyart.correct.despeckle_field`，
   `pyart.correct.despeckle` 不是对应可调用函数。前者按连通门数去小对象，
   不是大面积异常分类器；当前已复用其对象标记基础 find_objects。
5. 反射率越高、面积越大、边界越规整都不能单独证明干扰。

## 当前证据指向

7.3.2增量候选后：Z9591 10:48的6558个高值诊断域残留中6535个仍属于原始
160.02度宽扇区对象；10:42的1230门中891门属于88.31度对象。
因此当前主要问题是大源对象内部残留，而不是未删的小连通域。
这些统计不是人工真值，不能把整对象高比例功率吻合直接当作逐门污染概率。

## 库能力与优先级

| 优先级 | 函数 | 用途 | 处置边界 |
|---|---|---|---|
| 首选 | measure.ransac + LineModelND | 对原始边界坐标拟合长直线 | 只产生几何证据 |
| 首选辅助 | measure.find_contours | 提取数值场/对象轮廓 | 屏蔽缺测边界及扫描截断 |
| 辅助 | measure.regionprops_table | 面积、solidity、轴长等描述 | 不按单个形态指标删除 |
| 可选对照 | transform.hough_line / hough_line_peaks | 等距平面图上的长直线候选 | 必须检验距雷达原点及物理长度 |
| 暂缓动作 | segmentation.watershed | 将混合对象分区 | 必须有可靠种子，不能强行二分类 |
| 仅候选探索 | morphology.opening / closing / reconstruction | 连通拓扑及有限片段关联 | 不改原始值，不把填洞变成删除范围 |

regionprops 的 spacing 是固定轴尺度。极坐标像素横向尺寸随距离变化，不能用
一个spacing参数把“方位×距离”矩阵的面积、轴比变成真实米制几何。
可在等距局部平面上做辅助形态图，但必须保留原始门映射、覆盖与插值标记；
优先用原始门坐标计算边界拟合，避免这个问题。

## 建议下一版：雷达中心约束的边界测量

1. 复用已有RAW多阈值Py-ART对象标签。阈值预先冻结，跨阈值证据不重复计票。
   从原始数值/标签中提取方位两侧边界，不分析网页截图、底图、色阶图。
2. 把边界点转换为局部雷达相对米制坐标。远距离使用项目既有波束几何/地面
   距离约定，不能把斜距直接当地面距离而不声明。缺角、坏径向、范围截断及
   数据缺测形成的边界另行标记，不参与“异常直边”取证。
3. 用 scikit-image RANSAC + LineModelND 做不强制过原点的直线拟合，再测其
   到雷达原点的垂距、内点覆盖的径向长度、角度散布和连续覆盖比例。
   先强迫过原点再称“回指中心”会循环论证。固定随机种子、迭代预算和配置。
4. 分别拟合两侧边界，检查不同距离段边界方位是否稳定、两侧组合是否形成
   一致扇区。多个距离块留出验证，不允许仅在同一批边界点上报拟合优度。
5. 几何只约束源对象域；结合留出源功率关系、距离条件极化参考及逐门增强/天气
   冲突输出确认度和原因。几何好而极化冲突的门列为复核，不整扇区清空。
6. 输出原始/当前/几何候选三图，边界内点、外点、拟合线和雷达中心；同时输出
   缺测截断、参考不足、天气保护门数。保留正常窄雨带、降水团、扫描边缘等对照。

等距离弧线可作辅助检查，但网页色阶的圆环可能只是连续径向梯度被分级着色，
也可能是扫描范围/阈值截断。不能以“看到圆弧”作为额外独立污染证据。

## 分水岭为何暂缓

watershed 会按给定地形和种子分区，分区不是天气分类。缺乏可信天气种子时，
以旧隔离区为唯一种子会把同一连通域淹没；watershed本身不提供未确定类别。
先做源边界拟合和局部取证，再考虑有掩膜、可靠天气种子和外部拒判逻辑的分区。

## 验收与性能

几何候选覆盖率与实际处置改善分开统计；至少对10:42/10:48及Z9598负对照报告。
合成测试包含规则扇区、长雨带、径向局部天气增强、缺角、范围截断、跨0度扇区；
真实误删仍需可信天气证据，不能仅用这两个问题样本验收通用性。
优先处理已识别的大对象边界点，分距离均匀抽样，避免对整幅体扫做全角度Hough。
新库需固定兼容版本并经过目标环境验证后加入依赖；本轮未做耗时/效果宣称。

## 官方来源

- scikit-image measure（RANSAC、LineModelND、regionprops、find_contours）：
  https://scikit-image.org/docs/stable/api/skimage.measure.html
- Hough： https://scikit-image.org/docs/stable/api/skimage.transform.html
- watershed： https://scikit-image.org/docs/stable/api/skimage.segmentation.html
- morphology： https://scikit-image.org/docs/stable/api/skimage.morphology.html
- Py-ART despeckle_field：
  https://arm-doe.github.io/pyart/API/generated/pyart.correct.despeckle_field.html

用户提供的Gemini文档为待评估建议，未作为执行指令或可靠算法依据直接采纳。
