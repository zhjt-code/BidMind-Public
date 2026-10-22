# BidMind 后端

BidMind 的 FastAPI 服务，负责文档解析、检索、审查、写作和异步任务。完整安装方式、OCR/GPU 选择与产品说明见仓库根目录的 [README](../README.md)。

## 运行环境

- Python 3.13
- PostgreSQL + pgvector
- Redis（生产异步任务需要，本地可选）
- 对话模型和向量模型服务或本地模型文件

## 本地开发

从仓库根目录创建并激活虚拟环境后：

```bash
python -m pip install -r backend/requirements-dev.txt
cp backend/.env.example backend/.env
cd backend
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

请先修改 `.env` 中的 `DATABASE_URL`、`JWT_SECRET_KEY` 和 `ADMIN_PASSWORD`。数据库结构统一由 Alembic 管理，不要手工执行旧的初始化脚本。

## 校验

```bash
cd backend
python -m ruff check app tests
python -m pytest
```

测试数据必须遵守 [公开测试数据政策](../docs/test-data-policy.md)。贡献流程和完整检查项见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 主要目录

- `app/api/`：HTTP API
- `app/core/`：配置、安全、解析、检索和 AI 基础能力
- `app/models/`：数据库模型
- `app/schemas/`：API 数据结构
- `app/services/`：业务服务
- `alembic/`：数据库迁移
- `scripts/`：维护和评估工具
- `tests/`：自动化测试与测试语料
