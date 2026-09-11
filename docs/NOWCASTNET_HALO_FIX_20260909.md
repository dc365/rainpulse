# 原切片接缝修复（影子产品）

根因：atlas v1 输入重叠，但 trusted 输出相邻，归一化融合退化为硬拼接。前次重算仍用该路径，不能靠浏览器清缓存解决。

保留原输入、公开权重、随机种子、批处理和时间插值，仅将 trusted 输出在输入边界内扩展16格，逐成员加权融合。发布仍受原有效掩膜约束；不对PNG滤波、不补造缺测雨量。worker diagnostics 记录 `trusted-halo16-raised-edge-v1`。原 atlas 文件保持可追溯。

## 实测

105 对16:00、16:30、17:00三个起报做同一切片预测对照；16:30另复验线上batch=4。+110原接缝平均跳变1.633→0.507 mm/h（约下降69%），共同实况域MAE 2.381→2.366；≥10 mm/h格数1040→963。+115 MAE 2.108→2.107，强雨格数416→350。不能把连续性改善等同于精度提升，混合导致的强雨面积下降仍需注意。17:00长时效无实况，不作精度结论。

`scripts/compare_nowcastnet_overlap.py --halo-only --batch-size 4 --issue-time 2026-08-28T08:30:00Z --output <新实验目录>` 可复现。报告和数值对照在105的 runtime/experiments/nowcastnet-halo-20260909-*，不提交数据。

回归测试先因缺少trusted_halo参数失败，实现后14项atlas/worker/temporal测试通过。测试包含旧硬接缝阳性对照、新横纵梯度约束以及80 mm/h一致峰值不变。未修改其他atlas实验路径、旧动态拼图路径和时间插帧策略。

已重启105的NowcastNet worker，16:30重算任务 `e44fe342-011c-5b6d-b1a5-92f5c55309f7` 成功，publication_reused=false；当前cycle已切换该结果，通过4173入口读取并检查+110实际PNG。不是仅修改代码后继续展示旧图。

截图中的数据源暂不可用提示本轮未复现；ensemble接口与cycle接口检查正常，不以关闭告警掩盖问题。当前只保证16:30已更新，其他旧起报可在后台选择NowcastNet重算。代码尚未提交。
