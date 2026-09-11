# 自动训练调度（2026-09-10 用户授权）

本安排替代逐夜人工许可，使用 Asia/Taipei 时区。周一至周五 20:00 启动；周一至周五 08:00 停止并保存 checkpoint，随后恢复受管 Qwen 服务。周五 20:00 至周一 08:00 连续训练；周末零点触发仅补启动，已有训练时跳过。08:00 为停止请求时间，保存与服务恢复需要额外短暂时间。

服务器部署脚本为 `scripts/install_training_schedule.py`，在 GPU 服务器以训练账户执行。依赖现有 formal env、训练服务、守卫、停机服务与 Qwen 服务。用户 linger 已启用，SSH 断开后仍执行。

- `rainpulse-training-weekly.timer`：工作日 20:00、周末 00:00；开机两分钟后检查，错过开始时补触发，但工作日白天直接退出。
- `rainpulse-training-weekly-stop.timer`：周一至周五 08:00；不追补旧停止事件，避免夜间启用后误停。
- 启动器带文件锁；active/activating/deactivating 时跳过。每次许可绑定最新 step、SHA、冻结配置、父模型与源码；原守卫仍检查文件哈希和 metrics 尾步一致性，GPU 预检保持不变。
- 到达 500,000 步后自动启动器退出。失败不循环重试，需检查 systemd 日志；独立留出仍关闭。
- 样本仅 NAS；checkpoint 和指标仍保存在服务器训练目录。

本次从 159,027 步自动启动 window-0011。此前完整窗口结尾为 159,027，SHA 为 `a108605dd5c1a951338520a6862e3142ba3558bec2c660d4746491fb9bd8b2a3`。

查看：`systemctl --user list-timers --all`、`journalctl --user -u rainpulse-training-weekly.service -u rainpulse-nowcastnet-generative.service`。

暂停后续自动启动：`systemctl --user disable --now rainpulse-training-weekly.timer`；保留 stop timer 可确保当前夜间训练按时停止。
