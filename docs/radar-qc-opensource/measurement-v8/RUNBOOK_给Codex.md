# Codex 接入与验证步骤

## 0. 使用方式

本包是V7仓库上的离线扩展，不是可直接替换14个Worker的镜像。优先用`changes.patch`。
模型权重、真实标签和105资产未随包附带；本包不会自动下载私有资料。勿拿旧合图训练判定器。

在干净分支上应用并运行：

```bash
python /解压目录/apply_patch.py --repo /path/to/rainpulse --check
python /解压目录/apply_patch.py --repo /path/to/rainpulse
cd /path/to/rainpulse
# 使用原V7已冻结Python环境；仅增加训练依赖，不盲目升级雷达库。
python -m pip install -r configs/qc-experiments/measurement-v8-requirements.txt
python tools/radar_native/build.py --output /absolute/experiment-tools/emitter-core
# 首次输出中的 binary_sha256 保存供后续全部命令使用。
python scripts/qc_measurement_v8.py hash /absolute/experiment-tools/emitter-core
python -m pytest algorithms/tests/test_measurement_v8.py algorithms/tests/test_qc_paper_algorithms.py -q
```

构建需要Linux/GCC、数学库及原项目Python依赖。原生源码已附，无需联网获取bRopo。
原Py-ART2.2.5/wradlib2.9.5依赖不变；训练固定sklearn1.8.0，推断不用pickle。

## 1. 先跑完整合成闭环，检查实现而非气象效果

```bash
python scripts/demo_qc_measurement_v8.py \
  --native-binary /absolute/experiment-tools/emitter-core \
  --native-sha256 <上一步的真实SHA256> \
  --output /新的目录/v8-synthetic-demo
```

输出包含6个合成过程（train/calibrate/validate各2），原生特征、实际训练的**合成权重**、
JSON推断、提议PNG、动作评价。其基线是合成ALL_KEEP数据，不是运行旧V7得到的真实质控基线。
`DEMO_ONLY.json`标记synthetic，不能在真实资料中使用此权重。测试通过不能声称天气效果达标。

## 2. 从105只读冻结资料

按真实请求与对象清单导出，建议每个案例一个目录：

```text
frozen/case-A/
  normalized.zarr/    完整标准化体扫
  qc.zarr/            该图层绑定的V7 QC资产（完整所有chunk）
  v7.yaml            实际参数文件
  flags.yaml         实际qc-flags-v2文件
  prepared-context.npz  可选，只能是该请求实际准备的数据
```

不要导出账号密码或整个.env。单独保存原job请求、资源清单、投影层绑定，便于验证上下文来源。
原始PNG坐标先经现有forensic_export映射到原始ray/gate，不能将网页像素当门号。

```bash
python scripts/qc_measurement_v8.py freeze \
  --root /absolute/frozen/case-A \
  --normalized normalized.zarr --qc qc.zarr --profile v7.yaml --flags flags.yaml \
  --case-id case-A --process <真实独立天气过程ID> --partition inspect \
  --data-kind real --sweeps sweep_000 --name case.json
```

存在可核实的准备后上下文时再增加 `--context prepared-context.npz`。不得因缺少文件就伪造。
该命令从QC中读取已有pipeline、asset/scan/radar、context_fingerprint并核对原始矩；缺身份即失败。
目录SHA使用`relative-path-file-sha256-v1`，**不是API的artifact_sha256**。

旧V7残留的条件动作上界，可先调用：

```bash
PYTHONPATH=algorithms python tools/qc_v8/action_ceiling.py --help
```

该脚本复用前轮已核定逻辑，需要真实资产与配置身份；只输出有条件工程上界，不提供安全删除名单。

## 3. 提取原生基线与连续特征

```bash
python scripts/qc_measurement_v8.py extract \
  --case /absolute/frozen/case-A/case.json \
  --config configs/qc-experiments/measurement-v8.yaml \
  --native-binary /absolute/experiment-tools/emitter-core --native-sha256 <SHA256> \
  --output /absolute/新目录/features-A
```

得到`*-features.npz`、`*-native.npz`、标签空模板、`extraction.json`。确认原生calls、模式、
可用门比例、失败与预算状态，不要仅检查有输出文件。无支持的分数是NaN；不当成0分样本。
全部特征按原始ray/gate索引提供。手工标签可选有限点/门段，不需要标全图；模板不自动填真值。

## 4. 数据划分与训练

训练、校准、验证分别使用不同`process_id`及独立`scan_id`。同原始体扫不同分析时次不得重复。
当前多次调参的四站案例是开发资料；效果验收应增加其它未参与设计的天气过程。

标签CSV严格为：

```csv
ray,gate,label
```

按真实取证填入`weather/interference/mixed/unknown`。上述空模板故意不给虚构门号或标签。
建立dataset.json（所有路径相对dataset.json所在目录，SHA由hash命令计算）：

```json
{
  "schema_version": "rainpulse.measurement-dataset.v1",
  "packs": [
    {
      "features": {"path": "features-A/sweep_000-features.npz", "sha256": "真实文件SHA"},
      "labels": {"path": "labels-A.csv", "sha256": "真实标签文件SHA"}
    }
  ]
}
```

该例只展示一个条目；实际须补足train/calibrate/validate每区至少两个独立过程，并审核代表性。
每个特征包的分区来自冻结case，不通过重命名把相同体扫拆到其它分区。修改case分区后重新提取。

```bash
python scripts/qc_measurement_v8.py train --dataset /absolute/training/dataset.json \
  --config configs/qc-experiments/measurement-v8.yaml --output /新目录/trained-real
```

产物`model.json/lineage.json/validation.json`。训练只用train拟合特征中位数，calibrate拟合校准器，
validate输出分类指标。统计按已提供标签的抽样分布解释；不能自动推广到全网发生概率。

## 5. 冻结推断、对照与动作评价

```bash
python scripts/qc_measurement_v8.py infer \
  --case /absolute/frozen/case-A/case.json --config configs/qc-experiments/measurement-v8.yaml \
  --model /absolute/trained-real/model.json --model-sha256 <模型真实SHA256> \
  --native-binary /absolute/experiment-tools/emitter-core --native-sha256 <同一二进制SHA256> \
  --output /新目录/inference-A
```

默认audit：`EXPERIMENT_QC_ACTION`等与V7一致；新提议在`V8_PROPOSED_*`。
查看三组PNG时特别区分`proposed_not_applied`，它不表示已经更改生产结果。
不同特征配方/二进制不能混用模型；越出训练几何域的门不产生新动作。

建立assessment.json：

```json
{
  "schema_version": "rainpulse.measurement-assessment.v1",
  "labels_source": "human_reviewed",
  "required_radars": ["z9591", "z9593", "z9598", "z9599"],
  "cases": [
    {
      "report": {"path": "inference-A/experiment.json", "sha256": "真实报告SHA"},
      "labels": {"sweep_000": {"path": "labels-A.csv", "sha256": "真实标签SHA"}}
    }
  ]
}
```

四站只是这批验收必须覆盖的对象，不是删除名单，也不是分类器特征。

```bash
python scripts/qc_measurement_v8.py assess --manifest /absolute/assessment.json \
  --output /新的目录/assessed
```

按原始标签域分别输出确认precision/recall、隔离、天气误隔离/误拒、覆盖损失和逐站/体扫最差表。
无标签时只看提议数量；标签不齐或必测站缺失不算通过。工具不会代替人员选择可容忍退化阈值。

## 6. 可选实验应用与生产接入

只有独立真实标签验证后，人工创建 `rainpulse.measurement-review.v1` 回执，内容绑定：
model_sha256、policy_sha256（来自inference报告）、feature_identity（来自model）、
data_kind=real、decision=approved_for_offline_experiment、reviewer、reviewed_at_utc、
validation_report={path,sha256}、validation_report_sha256、validation_dataset_sha256。
政策验证报告须来自assess，至少2独立过程且必测站齐全。此回执是本地人工声明，不是密码学签名。

只改配置`policy.mode: experimental`，其余阈值与已评估policy必须一致，再用`infer --review 回执路径`。
输出仍仅为新目录内的独立实验副本。旧QC资产、原始反射率、有效无雨不回写；旧拒绝/隔离不恢复。
**目前没有已签核真实模型，包内不提供可直接照抄的批准回执。**

生产接入需Codex后续独立完成：模型SHA和特征配方进入任务身份、冻结模型加载，实验动作转换为
原生QCSweep并补齐版本化validator，QC→Hybrid→Grid→Mosaic→QPE→diagnostics全链回归，保持renderer1.2.0，
校验NATS完成事件仍有界，并准备原V7镜像回退。不要将本地NPZ伪装成生产QCRadarVolume。

## 停止条件

身份/摘要不符、原生失败被掩盖、过程/scan跨分区、没有真实校准权重、天气损失超过事先签核限值、
任一站明显退化或目标硬件P95超预算，应停止或保持audit。不要为追求图面干净放宽这些条件。
