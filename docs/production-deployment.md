# 生产部署说明

`docker-compose.prod.yml` 是维护者当前服务器拓扑的参考实现，不是面向所有云厂商的零配置安装器。它假设已有外部 PostgreSQL、NVIDIA 容器运行时、TLS 证书和已构建的前端静态文件。普通本地安装请使用根目录 README 的开发部署流程。

## 必要准备

1. 复制根目录 `.env.example` 为 `.env`，逐项替换占位值。不要提交 `.env`。
2. 准备支持 pgvector 的 PostgreSQL，并把 `DATABASE_URL` 指向该实例；生产 Compose 不创建数据库容器。
3. 生成独立的 `JWT_SECRET_KEY`、`ADMIN_PASSWORD` 和 `AI_SETTINGS_KEY`。
4. 把 `CORS_ORIGINS` 设置为实际的 HTTPS 前端源，例如 `https://bid.example.com`。不要使用 `*`。
5. 将 bge-m3 模型放到 `models/bge-m3`，并确认服务器已安装匹配的 NVIDIA 驱动和 Container Toolkit。
6. 按自己的域名和证书路径修改 `nginx/nginx.conf`。示例中的域名只是维护者参考值。
7. 在 `frontend` 执行 `npm ci && npm run build`，确认 `frontend/dist` 已生成。

首次发布前先迁移数据库：

```bash
docker compose -f docker-compose.prod.yml --env-file .env run --rm backend alembic upgrade head
docker compose -f docker-compose.prod.yml --env-file .env up -d
```

随后检查所有容器健康状态、HTTPS、登录、文件上传和异步任务。升级前应备份数据库和 `storage/`，并在兼容环境中先运行迁移与回归测试。

## 可选的阿里云脚本

根目录 `deploy.ps1` 是维护者使用的阿里云 ECS/SSH 发布辅助脚本。它包含特定目录、容器拓扑和同步策略，只应在读懂脚本并完成上述配置后使用。首次连接采用 OpenSSH 的 `accept-new` 主机密钥策略；高安全环境应提前把服务器指纹加入 `known_hosts`。

该脚本不是本项目的必需组件，也不应成为其他环境的部署前提。
