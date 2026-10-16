# Windows CMD entry points

[Script guide](../README.md) · [Setup](../../docs/implemented/安装配置与启动.md) · [Backend lifecycle](../../docs/implemented/本机后台启动与停止.md)

Double-click these files or run them from the repository root in PowerShell:

```powershell
.\scripts\cmd\build_frontend.cmd
.\scripts\cmd\start_backend.cmd
.\scripts\cmd\start_backend.cmd -Action Status
.\scripts\cmd\stop_backend.cmd
```

The entry points resolve the repository from their own location, so they also work when called from another directory. Arguments are forwarded to `scripts/frontend.ps1` or `scripts/backend.ps1`; quote configuration paths containing spaces. For example:

```powershell
.\scripts\cmd\start_backend.cmd -Port 2238 -Config "config/system.toml"
.\scripts\cmd\stop_backend.cmd -Port 2238
.\scripts\cmd\build_frontend.cmd -Install
```

Success exits with code 0. Failure keeps a readable message and pauses for double-click usage. Prepare the project `.venv`, private config and frontend dependencies first. Future Linux entry points are reserved under [scripts/sh](../sh/README.md).
