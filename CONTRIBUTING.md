# Contributing to BidMind AI

感谢你改进 BidMind AI。请先通过 Issue 描述较大的功能或架构调整；小型修复可以直接提交 Pull Request。

## 开发环境

- Python 3.13
- Node.js 22.13 或更新的 LTS
- Docker Compose
- PostgreSQL 16 + pgvector；涉及后台任务时还需要 Redis

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd frontend && npm ci
```

Windows 可将 `.venv/bin/python` 替换为 `.venv\Scripts\python.exe`。

## 提交前检查

```bash
.venv/bin/python scripts/check_public_data.py
.venv/bin/python -m ruff check backend/app backend/tests ocr scripts
cd backend && ../.venv/bin/python -m pytest -q
cd ../frontend && npm run lint && npm run build && npm run test:public
```

如果改动需要 GPU、模型权重或外部 LLM，请同时提供不依赖这些资源的单元测试或清楚说明人工验证步骤。

## 数据与安全

- 不得提交客户投标文件、证件、合同、真实密钥、服务器地址或非公开专家标注。
- 测试数据必须满足 [测试数据政策](docs/test-data-policy.md)。
- 安全问题不要提交公开 Issue，请按 [SECURITY.md](SECURITY.md) 私下报告。

## Pull Request

- 一次 PR 聚焦一个问题，并说明动机、主要改动和验证结果。
- 行为变化应补测试；数据库变化必须通过 Alembic 迁移实现。
- 不要提交生成物、模型权重、运行日志、上传文件或本地环境配置。
- 保持公开版不依赖私有文档、内部服务器或未随仓库提供的脚本。

