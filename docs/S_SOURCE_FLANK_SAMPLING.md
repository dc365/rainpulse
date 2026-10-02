# S 波段原始射线边界取样

原始扫描方位角通常带有小幅抖动。例如，相邻射线相差 0.99°，而天线波束宽度为 1°。只向外取整搜索波束边界，会越过最近射线，使用隔一条的射线作为侧翼。单射线回波因此可能从内部占比 1 变为 1/3，被误判为支撑不足。

`source_envelope._corridor` 保留原有边界证明，并补充按真实角度选取的最近侧翼：

- 候选位于预期角度的半个原生角间隔内，等距时选靠内的一侧；不跨已声明扫描缺口。
- 新恢复的边界必须有实际观测。侧翼有 DBZH 时仍需至少 6 dB 强度差；没有 DBZH 时仅接受有效且在 [-50, 3] dB 内的 SNR。缺测不当作晴空。
- 原始种子、原始范围、支撑长度、天气保护、距离限制及禁止递归扩张继续生效。
- 宽扇形、完整种子历史中失败的边界、邻站或多仰角缺测，不能靠这项几何修复自动消除。

此修复同时供原始源台账和源尾段关联使用。源台账版本为 `complete-source-ledger-v2-measured-nearest-flanks`，包络版本为 `frozen-raw-envelope-v3-measured-nearest-flanks`。新的源台账与旧台账不得混合成跨时次同版本证据。

## 复核

在仓库根目录运行，输入须为已绑定 Web 原始体扫的冻结 NPZ：

```sh
PYTHONPATH=algorithms python scripts/audit_s_flank_sampling.py \
  original_sweep_a.npz original_sweep_b.npz \
  --baseline-ref 600d50c \
  --output new_audit_directory
```

可附加 `--published exact_published_receipt.json`，比较新增包络与实际已发布残留门的交集。该脚本在内存中加载指定 Git 提交的基线函数，不创建新工作目录、不改变 RAW，也不写生产产品。基线必须来自可信的本地仓库历史；这是执行代码的回放工具，而非读取不可信 Python 的沙箱。

输出保留输入、模块、脚本、基线及诊断文件 SHA，验证原始源编号、范围、支撑长度不变。`recovered_envelope_gates` 是回放中新关联到的门数；它不等于新增删除门数，也不证明 Web 已更新。确认实际效果仍须完整质控重算、出版与页面读取核验。

```sh
PYTHONPATH=algorithms python -m pytest -q \
  algorithms/tests/radial_revision_20260918 \
  algorithms/tests/test_s_source_temporal.py tests/test_s_temporal_overlay.py
```
