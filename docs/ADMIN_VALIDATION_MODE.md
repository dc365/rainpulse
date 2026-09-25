# RainPulse 管理后台验证免凭据模式

运行阶段验证时，可在服务环境配置中显式设置：

```ini
RAINPULSE_ADMIN_AUTH_MODE=validation
```

此模式只免除 `/api/v1/admin/ops` 管理接口的管理员 Token 校验；未设置或设置为其他值时仍使用凭据校验。`GET /api/v1/admin/ops/auth-mode` 返回当前模式，前端据此决定显示凭据登录页或直接进入后台，并在页面持续标注验证模式。

`/internal/ops/v1` Worker 接口始终要求独立的 `RAINPULSE_OPS_WORKER_TOKEN`。验证模式会在服务启动日志中产生警告，应仅用于受信任网络中的验证实例。

验证命令：

```bash
cd services/control && go test ./internal/operations ./internal/apiapp
cd apps/web && npm test -- --run src/admin/AdminApp.test.tsx && npm run build
```
