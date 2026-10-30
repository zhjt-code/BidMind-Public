# BidMind AI

投标审查、串标审查与标书编写是本地优先系统。上传招标文件和投标文件后，系统提取要求、比对响应、检查多份投标文件的交叉痕迹，并按确认过的约束生成标书章节。

本仓库是可自行部署的源码。它不包含托管服务、支付与财务模块、模型权重，也不包含客户投标文件。测试数据仅允许使用合成数据，或经过身份信息清理、可公开再分发的招标规则片段；详见 [测试数据政策](docs/test-data-policy.md)。

Copyright (C) 2026 <郑州智蕴信息技术服务有限公司>

## 功能

- 投标审查：从招标文件提取要求，再比对投标响应
- 串标审查：多份投标文件交叉对比
- 标书编写：确认约束与目录后生成章节并导出 Word

  **详情可访问 www.bidmind-ai.com 了解**

## 技术栈

- 后端：Python 3.13、FastAPI、PostgreSQL、pgvector
- 前端：React 18、TypeScript
- 文档：pdfplumber、PyMuPDF
- 向量与推理：bge-m3；推理服务需自行准备（兼容 OpenAI 接口的本地或云端模型）

## 开始使用

非技术人员按步骤配置如存在困难，可将本仓库交由**智能体**来完成配置，配置过程中注意权限管理。

步骤：「先装软件、再按电脑选配置、最后分系统操作」，做完后，用浏览器打开 `http://localhost:3000`，用用户名 `admin` 和你自己设的管理员密码登录。

本地开发与部署步骤目前支持 Windows 和 Linux。Mac操作系统从架构上可完全支持，但未进行测试，如有需要可自行尝试部署安装。

文字版 PDF 可以直接解析。扫描件才需要 OCR。Word 转 PDF 才需要后面的 Gotenberg。对话模型和向量模型都不在本仓库里，要单独准备。

### 1. 先安装这些软件

| 软件 | 用来做什么 | 怎么确认装好了 |
|---|---|---|
| Docker Desktop（Windows）或 Docker Engine（Linux） | 运行数据库和可选的识别服务 | 命令 `docker compose version` 能打出版本号 |
| Python 3.13 | 运行后端 | Windows 的 `py -3.13 --version` 或 Linux 的 `python3.13 --version` 显示 3.13 |
| Node.js 22.13 或更新的 LTS | 运行网页和 TypeScript 契约测试 | `node --version` 显示 v22.13 或更高 |
| Chrome / Edge 111+ 或 Firefox 128+ | 网页端需要 Tailwind CSS 4 支持的现代 CSS 功能 | 建议使用最新稳定版浏览器 |

Windows 安装 Python 时勾选 “Add python.exe to PATH”。安装 Docker Desktop 后，要等托盘里的 Docker 图标显示正在运行，再做后面的步骤。

### 2. 按电脑配置选择 OCR、structure 和 GPU

`docker-compose.yml` 里准备了这些服务：

- `postgres`：数据库。必须启动。不使用显卡。
- `redis`：任务队列。建议启动。不使用显卡。不启动时，任务只留在内存里，后端一停就丢。
- `ocr-1`、`ocr-2`、`ocr-3`：扫描件文字识别。镜像按 NVIDIA GPU 构建，没有 NVIDIA 显卡时不要启动。
- `structure-1`：版面分析，只用 CPU，本地只需要 1 个。不做文档质检时可以先不启动。
- `gotenberg`：把 Word 等 Office 文件转成 PDF。只处理 PDF 时可以不启动。

向量模型用 `backend/.env` 里的 `EMBEDDING_DEVICE`：

- `auto`：有 NVIDIA 显卡且驱动可用时用 GPU，否则用 CPU。这是示例文件里的默认值。
- `cpu`：显存不够，或机器没有 NVIDIA 显卡时，改成这个。

同时处理几份文档由 `DOC_PROCESS_CONCURRENCY` 控制，示例值是 3。普通笔记本改成 2。CPU 核数很多、内存也够时可以改成 4。

`structure-1` 占用的 CPU 核数是环境变量 `STRUCTURE_CPUS`，不设置时按 4 核。可以设成「逻辑核数的八成」，但不要超过机器的逻辑核数。它不使用 GPU。

建议按下面选择要启动的容器：

| 你的电脑 | OCR | structure | 向量 |
|---|---|---|---|
| 没有 NVIDIA 显卡 | 不启动 GPU 版 `ocr-1` / `ocr-2` / `ocr-3`。地址留空，由本机 CPU 识别 | 需要版面分析时启动 `structure-1`，它本身就是 CPU | `EMBEDDING_DEVICE=cpu` |
| 有 NVIDIA 显卡，显存大约 8GB | 只启动 `ocr-1` | 需要版面分析时启动 `structure-1` | `auto` |
| 显存大约 16GB 或更多 | 可以启动 `ocr-1`、`ocr-2`、`ocr-3` | 仍然只启动 1 个 `structure-1` | `auto` |
| `nvidia-smi` 里显存已经用到大约九成 | 停掉多余的 OCR，只留 1 个或 2 个 | 保持 1 个 | 把 `EMBEDDING_DEVICE` 改成 `cpu` |

没有 NVIDIA 显卡时，扫描件不会改走云端。`OCR_SERVICE_URL` 和 `OCR_SERVICE_URLS` 保持空白，后端就在自己的进程里用 CPU 版 PaddleOCR 识别，大约每页 2 到 3 秒。默认依赖里没有这个包，安装完后端依赖后还要再装一次。

这里先只做配置选择，不要立即执行 OCR 安装命令。第 3 节创建虚拟环境、第 4 或第 5 节安装完后端依赖后，再执行对应系统下的 CPU OCR 安装命令。

有 NVIDIA 显卡时，GPU 容器大约每页 0.1 到 0.3 秒。启动容器后还要把地址写进 `backend/.env`，后端才会去用容器。只开 `ocr-1` 时写 `OCR_SERVICE_URLS=http://localhost:9000`。三个都开时写成 `http://localhost:9000,http://localhost:9001,http://localhost:9002`。不写地址时，即使容器已启动，识别仍在本机 CPU 上做。

仓库里的生产 compose 最多准备了 5 个 OCR。那是给显存更大的服务器用的。本机第一次部署用上面的表即可。OCR 容器第一次启动会下载识别模型，可能要几分钟。

### 3. 准备配置文件

在仓库根目录操作。

Windows PowerShell：

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Linux：

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
python3.13 -m venv .venv
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

用记事本或任意文本编辑器打开 `backend/.env`，改这三项：

1. 把刚才打印出来的那一长串填到 `JWT_SECRET_KEY=` 后面，替换原来的占位文字。不改的话，后端拒绝启动。
2. 把 `ADMIN_PASSWORD=` 改成你自己的强密码。不要保留示例值 `replace-with-a-long-random-password`；它只是占位文字，原样保留会成为一个可预测的管理员密码。登录用户名固定是 `admin`。
3. 把 `DATABASE_URL` 里的密码改成 `bidmind`，和 `docker-compose.yml` 里的数据库密码一致。改完应是：

```text
DATABASE_URL=postgresql+asyncpg://bidmind:bidmind@localhost:5432/bidmind_lite
```

按上一节的表格，需要时把 `EMBEDDING_DEVICE=auto` 改成 `cpu`。

对话模型二选一：

- 本机模型：保持 `AI_MODE=local`。Windows 安装 [Ollama](https://ollama.com) 后，`LLM_API_BASE` 用 `http://localhost:11434/v1`，`LLM_MODEL_NAME` 写成你已经拉取的模型名。Linux 可以用 Ollama，也可以用 vLLM，此时把地址改成 `http://localhost:8001/v1`。
- 云端模型：把 `AI_MODE` 改成 `cloud`，填写 `CLOUD_API_KEY`、`CLOUD_API_BASE` 和 `CLOUD_MODEL_NAME`。示例里给出的是 DeepSeek 的地址。

向量模型二选一：

- 把 bge-m3 下载到仓库下的 `models/bge-m3`。
- 或者把 `EMBEDDING_MODEL` 改成 `BAAI/bge-m3`，第一次启动时从网络下载。

### 4. Windows

在仓库根目录打开 PowerShell。Docker Desktop 必须已经在运行。

```powershell
docker compose up -d postgres redis
docker compose ps
```

`bidmind-postgres` 的状态变成 healthy 后再继续。如果 5432 端口已被占用，先停掉本机原来的 PostgreSQL，或同时修改 compose 里的端口和 `DATABASE_URL`。

需要版面分析时：

```powershell
$env:STRUCTURE_CPUS = "4"
docker compose up -d --no-deps structure-1
```

有 NVIDIA 显卡、并且要识别扫描件时，按第 2 节的表启动。只开一个：

```powershell
docker compose up -d --no-deps ocr-1
```

三个都开：

```powershell
docker compose up -d --no-deps ocr-1 ocr-2 ocr-3
```

没有 NVIDIA 显卡时不要执行上面两条。如果需要识别扫描件，稍后按本节的 CPU OCR 步骤安装。

安装后端依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

如果没有 NVIDIA 显卡且需要识别扫描件，现在再安装 CPU OCR：

```powershell
.\.venv\Scripts\python.exe -m pip install paddlepaddle==3.2.0 paddleocr==3.4.0
```

建表并启动后端：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

这个窗口要保持开着。在仓库根目录再开一个 PowerShell：

```powershell
Set-Location frontend
npm ci
npm run dev
```

浏览器打开 `http://localhost:3000`。

如果还要把 Word 转成 PDF，在 `backend/.env` 增加 `OFFICE_CONVERT_URL=http://localhost:3100`，回到仓库根目录执行 `docker compose up -d --no-deps gotenberg`，并重启后端窗口。Compose 已把容器的 3000 端口映射到宿主机 3100，不会与网页开发服务冲突。

### 5. Linux

下面的系统包命令以 Debian/Ubuntu 为例；其他 Linux 发行版需用自己的包管理器安装等价的 `libGL.so.1` 运行库。在仓库根目录打开终端，当前用户要能运行 Docker。

```bash
docker compose up -d postgres redis
docker compose ps
```

`bidmind-postgres` 变成 healthy 后再继续。

需要版面分析时：

```bash
STRUCTURE_CPUS=4 docker compose up -d --no-deps structure-1
```

有 NVIDIA 显卡并且要识别扫描件时：

```bash
docker compose up -d --no-deps ocr-1
```

显存足够时把 `ocr-2 ocr-3` 加到同一条命令里，并按第 2 节写上 `OCR_SERVICE_URLS`。没有 NVIDIA 显卡时不要启动这些容器；如果需要识别扫描件，稍后按本节的 CPU OCR 步骤安装。

安装后端依赖：

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
```

如果没有 NVIDIA 显卡且需要识别扫描件，现在再安装 CPU OCR：

```bash
sudo apt-get install -y libgl1
.venv/bin/python -m pip install paddlepaddle==3.2.0 paddleocr==3.4.0
```

建表并启动后端：

```bash
cd backend
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在仓库根目录另开一个终端：

```bash
cd frontend
npm ci
npm run dev
```

浏览器打开 `http://localhost:3000`。Word 转 PDF 时，在 `backend/.env` 设置 `OFFICE_CONVERT_URL=http://localhost:3100`，回到仓库根目录执行 `docker compose up -d --no-deps gotenberg`，并重启后端。

### 6. 验收启动结果

保持后端和前端窗口继续运行，回到仓库根目录新开一个终端。先确认容器状态：

```text
docker compose ps
```

`bidmind-postgres` 必须是 `healthy`；如果启动了 Redis，`bidmind-redis` 也应是 `healthy`。

Windows PowerShell 检查后端和数据库：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

Linux 检查后端和数据库：

```bash
curl -fsS http://127.0.0.1:8000/health/ready
```

返回结果应包含 `"status":"ready"`。如果返回 `503` 或无法连接，不要继续验收，先按第 8 节检查后端和数据库。

最后打开 `http://localhost:3000`，用用户名 `admin` 和第 3 节设置的 `ADMIN_PASSWORD` 登录。能打开登录页、成功登录，且后端就绪检查通过，才表示基础系统已正常启动。

### 7. 登录后配置 AI

用 `admin` 和你在 `ADMIN_PASSWORD` 里设置的密码登录。推理服务部署完成后，管理员登录，在【管理】-【AI 设置】中按业务需要进行配置。特别注意：AI配置一定要对应好相应能力的大模型，请仔细阅读界面中介绍文字，按AI池要求对应好多模态或轻量模型。尤其注意招标文件评分项提取模型，一定选取可直读PDF的大模型（目前可支持的有qwen-lon、doubao-seed-evolving），配置错误可能导致解析不成功。

### 8. 启动失败时先看这里

- 后端窗口写 JWT 或管理员密码未设置：回到第 3 节，确认这两项已经换成你自己的值，没有保留任何示例值，并且保存了 `backend/.env`。
- 后端窗口写数据库连不上：确认 `docker compose ps` 里 postgres 是 healthy，并且 `DATABASE_URL` 的密码是 `bidmind`。
- `docker compose up` 启动 `ocr-1` 失败并提到 NVIDIA：这台电脑没有可用的 NVIDIA 显卡或驱动。停掉这些 GPU 容器，保持 OCR 地址为空，并确认已经安装 CPU 版 PaddleOCR。
- 扫描件没有识别出文字：后端窗口如果写找不到 `paddleocr`，按第 4 节（Windows）或第 5 节（Linux）的 CPU OCR 命令补装依赖，再重启后端。文字版 PDF 不依赖这一步。
- `npm install` 或 `pip install` 失败：把窗口里的最后 20 行错误原文保留下来再排查。Windows 上如果提示缺少 Microsoft Visual C++ 编译工具，先安装该工具再重新执行安装命令。

## 开发与贡献

提交修改前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。最小校验包括后端测试与静态检查，以及前端 lint、构建和公开契约测试；GitHub Actions 会对 Pull Request 自动运行这些检查。

生产部署前请阅读 [生产部署说明](docs/production-deployment.md)。仓库中的生产 Compose 和 PowerShell 脚本是可审计的参考方案，不是零配置的通用安装器。

## 数据

不要提交客户投标文件、证件、合同、密钥、非公开专家标注或其他客户材料。测试数据必须是合成数据，或经过身份信息清理且确认可公开再分发的招标规则片段；具体要求和现有数据分类见 [docs/test-data-policy.md](docs/test-data-policy.md)。

## 安全

漏洞请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可

本项目以 GNU AGPL-3.0 授权，见 [LICENSE](LICENSE)。

PyMuPDF 由其权利人按 AGPL-3.0 或商业许可双重授权。使用本仓库的 AGPL 构建时，需要同时遵守 PyMuPDF 的 AGPL 条款。对话模型和向量模型不在本许可之内。

若你修改本程序并通过网络提供服务，AGPL 要求向交互用户提供对应源码。
