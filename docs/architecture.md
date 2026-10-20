# BidMind AI Lite 技术架构参考

> 本文档面向部署者和贡献者，说明当前公开版本的主要组件、业务流程和运行契约。

## 一、核心技术栈

| 层级 | 技术选型 | 说明 |
|-----|---------|------|
| 前端 | React 18 + TypeScript | 交互界面 |
| 后端 | FastAPI | 异步API服务 |
| 数据库 | PostgreSQL + PGVector | 向量与业务数据统一存储 |
| 文档解析 | pdfplumber | PDF布局与表格提取 |
| 语义分块 | LangChain RecursiveCharacterSplitter | 文档分块 |
| 向量化 | sentence-transformers (BAAI/bge-m3) | 文本向量化 |
| 推理引擎 | vLLM + Qwen3-vl:8B | 本地部署LLM |
| 类型校验 | Pydantic | AI输出结构化校验 |
| PDF生成 | ReportLab | Python原生PDF报告生成 |
| 流程编排 | LangChain | 多步骤流程协调 |

## 二、数据流架构

```
离线处理（数据准备）:
PDF文档 → pdfplumber解析 → LangChain分块 → bge-m3向量化 → PostgreSQL存储

在线审查（交互生成）:
用户请求 → FastAPI → LangChain编排 → 语义检索/LLM推理 → Pydantic校验 → ReportLab → PDF报告

标书编写（内容生成）:
招标文件 → 约束提取 → 目录生成 → 素材匹配 → 章节生成(三分法路由) → 一致性检查 → Word渲染
```

## 三、部署与数据边界

- **数据责任**：系统会处理用户上传的原始文档，部署者应自行落实访问控制、备份、保留期限和隐私要求
- **推理运算**：支持兼容 OpenAI API 的本地或云端推理服务
- **统一存储**：向量与业务数据存于同一PostgreSQL实例

## 四、三大核心功能流程

### 功能一: 投标审查

利用AI对标招标文件审核投标文件的响应情况。

| 步骤 | 内容 | 人工确认 |
|-----|------|---------|
| 1. 招标要求提取 | 从招标文件提取结构化要求 | **是（必须）** |
| 2. 投标响应提取 | 从投标文件提取对应响应 | 否（AI自动） |
| 3. 对比审查 | 逐项对比，判断符合程度 | 否（AI自动） |

### 功能二: 串标审查

通过多份投标文件的交叉对比识别潜在串通行为。

| 步骤 | 内容 | 人工确认 |
|-----|------|---------|
| 1. 文档信息提取 | 从每份投标文件提取关键信息 | 否（AI自动） |
| 2. 文件交叉对比 | 两两对比，多维度分析 | 否（AI自动） |
| 3. 综合研判 | 识别串通网络，评估风险 | 否（AI自动） |

### 功能三: 标书编写

AI辅助生成投标文件。

| 步骤 | 内容 | 人工确认 |
|-----|------|---------|
| 1. 约束提取 | 从招标文件提取三层约束（硬约束/结构/引导） | **是（确认约束）** |
| 2. 目录生成 | AI生成标书目录树，分配章节类型与字数 | **是（调整目录）** |
| 3. 素材处理 | 上传素材文件，AI解析打标分类 | 否（AI自动） |
| 4. 三元匹配 | 约束-章节-素材三元绑定 | **是（确认绑定）** |
| 5. 内容生成 | 三分法路由：固定模板/资料填充/自由论述 | 否（AI自动） |
| 6. 检查与渲染 | 一致性检查 + 去AI化 + Word渲染 | 否（AI自动） |

#### 素材图片处理的节点内部 LLM 子任务

- `writing_material_process_job` 归属 `process` 资源，负责素材解析、PDF/DOCX/XLSX文本提取、OCR-first 本地识别、图片去重、内容组装、向量化索引和后续标签决议。
- 图片识别采用 OCR-first：高/中优先级图片先尝试 OCR，置信度不足、OCR 拥堵降级、OCR 预算耗尽或低优先级图片才进入 VL 模型通道。
- 实际模型调用通过 LLM worker 子任务承接：`writing_image_vl_job` 处理 VL fallback，`writing_image_classify_job` 处理图片类型、业务子类和结构化字段提取。
- process worker 通过 Redis 批次结果等待子任务回填；超时或 LLM worker 不可用时按降级语义继续，VL 结果为空或图片分类标记为 `unknown`，避免素材批处理永久阻塞。

### 设计原则

- **投标审查**：招标要求必须人工确认
- **串标审查**：全程AI自动执行，无人工审核节点
- **标书编写**：约束/目录/绑定三个节点需人工确认，内容生成与渲染自动执行

#### 招标要求溯源（RequirementReview 页）

- 每条招标要求展示后端返回的 `source_pages`（多页合并条目的全部来源），单页时直接展示，多页时以 chip 列表呈现，超过内联上限提供“全部 N 页”弹窗
- 单页 chip 点击直接打开招标 PDF 第 N 页（复用 `frontend/src/pages/result/PdfPreviewDialog.tsx`）；缺少 `documentId` 时回退到项目招标文档 ID
- 手工补录的要求若缺失来源页，展示“暂无来源页码”，禁止伪造定位
- 后端在 `TenderExtractor._extract_from_chunk` 后将每条 `source_pages` 折回真实 `Chunk.page_num`；本地去重 (`_local_pre_dedupe` / `_simple_dedupe`) 在丢弃同义条目时通过 `_merge_provenance` 把被丢弃条目的来源页码、关键词、子要求、合并计数并入保留条目，确保多分块来源不被吞掉
- **约束确认可人工修订**：支持在约束确认页对 AI 提取结果执行编辑、人工补录和删除；手工补录条目复用招标文件来源并默认待确认
- **一致性提示而非自动回退**：项目进入目录/绑定/写作等后续阶段后，约束页仍允许继续修订，但系统仅提示"下游结果可能已过期"，不自动清理目录、绑定、草稿和预览

## 五、显式重试与断点恢复契约

> 本章节是三大业务的显式重试总 Plan。后续实现重试按钮、状态接口、任务恢复、测试用例时必须以本契约为准，禁止把"点击重试"实现为无差别全量重跑。

### 5.1 设计目标

- **用户可感知**：任何长任务节点失败后，前端必须展示失败节点、失败原因、已保留成果和可执行的重试动作。
- **成果可保留**：重试只能从失败节点或其安全上游恢复，不得清理失败节点之前已经确认或已经完成的业务产物。
- **范围可解释**：每次重试前必须明确 `preserved_outputs`（保留产物）和 `reset_scope`（允许清理范围），供状态接口和前端展示。
- **任务可审计**：显式重试应记录新的 attempt、操作者、触发时间、失败节点、任务名和最后错误；Redis heartbeat 只负责运行态，不作为唯一审计来源。
- **自动恢复降级为辅助**：现有自动恢复可保留，但用户界面必须提供显式重试入口；自动恢复达到上限后应进入 `retryable` 失败态，而不是只显示黑盒失败。

### 5.2 统一状态字段

三大业务的状态接口应逐步补齐以下字段；已有 `overall_status`、`steps`、`is_recovering`、`recovery_count` 保持兼容。

| 字段 | 类型 | 含义 |
|---|---|---|
| `retryable` | bool | 当前失败态是否允许用户显式重试 |
| `failed_node` | string \| null | 失败节点标识，如 `writing_material_process` |
| `failed_stage` | string \| null | 节点内部阶段，如 `image_classify`、`reviewing` |
| `retry_action_label` | string | 前端按钮文案，如"从素材处理继续重试" |
| `preserved_outputs` | string[] | 点击重试会保留的业务成果 |
| `reset_scope` | string[] | 点击重试前允许清理或重置的失败/下游产物 |
| `last_attempt` | int | 当前节点最近一次运行 attempt |
| `retry_running` | bool | 用户触发的重试是否正在运行 |

推荐新增持久化 `TaskRun`（或等价表）作为显式重试主记录，字段至少包含：`project_id`、`flow`、`node_key`、`task_name`、`status`、`attempt`、`current_arq_job_id`、`last_stage`、`last_error`、`retry_requested_by`、`retry_requested_at`、`preserved_summary_json`、`reset_scope_json`。现有 `TaskObservation` 继续记录事件流，`VectorJobState` 继续作为文档向量化专用状态机。

### 5.3 投标审查重试节点

| 节点 | 失败后保留产物 | 重试前允许清理范围 | 任务入口 |
|---|---|---|---|
| `document_process` 招标/投标文件解析向量化 | 已上传文件、已完成解析和向量化的文档、项目基础信息 | 失败文档的处理中状态、失败的 `VectorJobState` 运行态 | `process_document_job` |
| `requirement_extract` 招标要求提取 | 已上传和已解析招标文件、已有人工补录要求 | 未确认且由本次失败提取产生的不完整要求、提取任务心跳 | `extract_requirements_job` |
| `bid_response_extract` 投标响应提取 | 已确认招标要求、已成功提取的投标响应 | 失败或不完整的投标响应中间结果 | `review_project_job` 或后续独立 `review_bid_extract_job` |
| `review_compare` 对比审查 | 已确认招标要求、已成功投标响应、已完成的 `ReviewResult` | 失败或缺失的审查结果、报告草稿 | `review_project_job` |
| `review_report` 报告生成 | 全量审查结果、人工确认过的要求 | 失败报告文件、报告生成缓存 | 报告生成入口，当前可保持同步 |

投标审查重试必须继续使用已有去重和跳过语义，例如投标响应提取的 `skip_existing=True` 与 `ReviewResult(project_id, requirement_id)` 唯一约束，避免重试造成结果重复。

实现状态（2026-04-28）：投标审查已按本节五节点暴露项目级 `retry-state` / `retry`。`document_process` 复用 `process_document_job` 和 `VectorJobState`；`requirement_extract` 复用 `extract_requirements_job`；`bid_response_extract`、`review_compare` 复用 `review_project_job` 并依赖 `skip_existing=True` 与结果唯一约束保留前置成果；`review_report` 当前仍为同步报告生成入口，显式重试仅恢复报告节点状态，不引入额外 LLM Prompt。显式重试 attempt 复用 `TaskRun(flow='bid_review')` 持久化记录，区别于链内 LLM 自动重试。

### 5.4 串标审查重试节点

| 节点 | 失败后保留产物 | 重试前允许清理范围 | 任务入口 |
|---|---|---|---|
| `document_process` 多文件解析向量化 | 已上传文件、已完成解析和向量化的文档 | 失败文档的处理中状态、失败的 `VectorJobState` 运行态 | `process_document_job` |
| `collusion_extract` 文档信息提取 | 已成功的 `CollusionExtraction(extraction_status='success')`、文件解析结果 | `extraction_status!='success'` 的提取记录 | `collusion_analyze_job` |
| `collusion_global` 全局特征分析 | 成功提取的文档结构化信息 | 失败的全局特征缓存或摘要 | `collusion_analyze_job` |
| `collusion_compare` 重点文件对比 | 已完成的 `CollusionResult(project_id, doc_a_id, doc_b_id)` pair | 失败或不完整的 pair 结果 | `collusion_analyze_job` |
| `collusion_cluster` 嫌疑群组识别 | 已完成 extraction 和 pair 结果 | 旧的群组聚类结果 | `collusion_analyze_job` |
| `collusion_synthesis` 群组/全局综合研判 | 已完成 extraction、pair 和聚类结果 | 失败的综合研判字段、报告草稿 | `collusion_analyze_job` |
| `collusion_report` 报告生成 | 全部分析结果和证据链 | 失败报告文件、报告生成缓存 | 报告生成入口，当前可保持同步 |

串标审查天然具备断点锚点：`CollusionExtraction(project_id, document_id)` 和 `CollusionResult(project_id, doc_a_id, doc_b_id)` 的唯一性决定了重试应优先补齐缺失或失败产物，而不是删除全部结果。

实现补充（2026-04-28）：`collusion_global` 当前是由 `GlobalFeatureAnalyzer` 计算的纯算法阶段，没有独立业务表或缓存文件；它仍作为独立 `failed_node` 暴露，但失败阶段和 attempt 审计短期以 `TaskRun(flow='collusion_check').last_stage` 与任务 heartbeat 为真源。后续若引入全局特征缓存，应将其纳入 `reset_scope`，不得改变成功 extraction/pair 的保留语义。

### 5.5 标书编写重试节点

| 节点 | 失败后保留产物 | 重试前允许清理范围 | 任务入口 |
|---|---|---|---|
| `writing_tender_process` 招标文件解析向量化 | 招标文件、已完成解析和向量化结果 | 失败的文档处理状态 | `process_document_job` |
| `writing_extract` 约束提取 | 招标文件解析结果、已人工补录且确认的约束 | 未确认的失败提取结果、提取任务心跳 | `writing_extract_job` |
| `writing_outline` 目录生成 | 已确认约束、渲染配置、行业画像 | 失败的目录草稿；已人工调整目录不得被自动清理 | `writing_outline_job` |
| `writing_material_process` 素材解析、图片识别、索引、打标 | 已上传素材、已完成文本拆分、已完成图片识别、已完成素材标签、已确认目录 | `processing_status='failed'` 的素材或子素材、失败图片识别结果、失败标签决议 | `writing_material_process_job` |
| `writing_match` 三元匹配 | 已确认约束、目录、素材处理成果 | 未确认的绑定建议、失败匹配缓存 | `writing_match_job` |
| `writing_compose` 全文生成 | 已确认绑定、已成功章节草稿、用户已编辑章节 | 失败或缺失章节草稿、未确认的生成中间态 | `writing_compose_job` |
| `writing_compose_single` 单章生成 | 当前章节的上一版本草稿、其他章节草稿、用户已编辑章节 | 目标章节本次失败 attempt 的中间态 | `writing_compose_single_job` |
| `writing_render` Word 渲染 | 全部章节草稿、一致性检查结果、渲染配置 | 失败的 `WritingRenderArtifact` 文件路径或缓存 | 渲染入口，当前可保持同步 |

标书编写中 `OUTLINING`、`MATCHING`、`WRITING` 等阶段的孤儿任务不应直接把项目永久置为不可恢复失败。状态服务应优先进入 `retryable=true`，前端展示"从当前节点重试"，并保留前置人工确认节点的成果。

### 5.6 API 与前端交互约束

- 每个业务至少提供项目级 `retry-state` 查询和 `retry` 触发接口；素材、单章等细粒度对象可提供对象级重试接口。
- 重试接口必须幂等：同一节点已有 `queued/running/retry_running` 时，不得重复投递后台任务。
- 重试触发必须复用 `dispatch_background_job(...)`，不得绕过 ARQ/fallback 统一分派契约。
- 前端必须区分"刷新状态"与"重试处理"：刷新只重新拉取状态，重试才调用 `POST .../retry` 并生成新 attempt。
- 失败态 UI 必须包含：失败节点、失败原因、保留产物、将被清理的范围、主按钮"重试"、次按钮"刷新状态"或"查看已有结果"。
- 自动恢复中的文案应说明"系统正在尝试恢复"，但自动恢复达到上限后必须转为显式可重试状态，避免用户只能刷新页面。

### 5.7 维护与验收约束

显式重试已进入维护阶段。后续新增或调整任一长任务节点时，必须同步维护以下约束：

1. **节点契约先行**：先在本章登记 `failed_node`、`preserved_outputs`、`reset_scope` 和任务入口，再改代码。
2. **状态真源一致**：状态接口、`retry-state` 接口和 `TaskRun` 的 `attempt/retry_running/last_error` 必须一致，不得分别推导出相互矛盾的失败节点。
3. **触发幂等**：同一节点已有 `queued/running/retry_running` 时，`POST .../retry` 必须返回现有运行态，不得重复投递后台任务。
4. **清理范围可测**：每个 `reset_scope` 都必须有防御测试证明不会误删前置成果或已成功产物。
5. **前端入口完整**：上传页、进度页、结果页或对应业务阶段页中至少一个用户自然停留页面必须展示完整 `RetryActionPanel`。
6. **链内重试隔离**：LLM/解析器内部自动重试不得计入用户显式重试 attempt，也不得替代 `retry-state` 展示。

## 六、LLM Center 管理链路

> LLM Center 是管理员专属域，普通用户不可见、不可访问。所有接口均依赖 `require_admin` 鉴权。

### 单节点操作（Phase 2）

```
编辑 Prompt -> 保存草稿(draft) -> 校验(validated) -> 发布(published) -> [可回滚(archived)]
```

### 批量校验与发布（Phase 6）

支持按**当前流程**或**全部待处理节点**两种范围进行批量操作：

| 操作 | 接口 | 行为 |
|------|------|------|
| 一键校验 | `POST /batch/validate` | 收集范围内所有 draft/validated 节点，逐节点校验，返回汇总与链路 |
| 校验并发布 | `POST /batch/validate-publish` | 先校验，全部通过则自动发布；存在失败则不发布任何节点 |
| 发布已通过节点 | `POST /batch/publish-passed` | 管理员在失败场景下显式确认，仅发布指定的已通过节点 |

### 批量操作规则

- **失败不自动整体发布**：存在校验失败项时，系统不会自动发布通过项，需管理员显式确认
- **全局范围需二次确认**：`scope=all_pending` 影响所有流程，前端弹出强确认弹窗
- **失败链路可定位**：每个失败项返回 `流程 > 阶段 > 节点` 的完整链路，前端支持点击直达
- **统一审计**：所有批量操作（batch_validate / batch_validate_publish / batch_publish_passed）均写入审计日志

## 七、任务契约统一收口（后台任务分派规范）

> 本章节记录当前后台任务编排规范，用于指导新业务节点接入后台任务框架。

### 7.1 执行口径

- **常态口径**：长耗时业务必须由独立 ARQ worker 承接；API 层只做触发、查询、取消。
- **降级口径**：允许在 worker/Redis 不可用时回退到同进程 `asyncio_fallback` 执行，但回退路径必须复用 **同一 `run_*_task` 协程**，不得业务层另起旁路实现；fallback 受 `BACKGROUND_FALLBACK_MAX_JOBS` 许可约束，避免 worker 下线时把重任务全部压回 API 进程。
- **一致契约**：无论 ARQ 或 `asyncio_fallback` 执行，都必须表现出同一外部语义——`task_name` 一致、`queued/running/completed/failed` 状态一致、心跳 schema 一致、取消语义一致。
- **资源口径**：ARQ job 入口按 `process` / `llm` / `maintenance` 等资源类型申请 Redis 许可；许可只限制重资源并发，不改变三大业务流程内部的严格节点顺序。

### 7.2 统一分派 helper

新代码应使用 `app/core/tasks/__init__.py` 中的 `dispatch_background_job(...)` 完成"ARQ 优先 + 受控 fallback + 写 queued 心跳"三段动作，签名如下：

```python
queue_mode, arq_job_id = await dispatch_background_job(
    task_name=task_name,
    arq_function="writing_material_process_job",
    fallback_factory=lambda: run_writing_material_process_task(project_id),
    task_type="writing_material_process",
    project_id=project_id,
    arq_args=(project_id,),
)
```

硬性约束：
- `fallback_factory` 必须返回同一 `run_*_task` 协程；
- 返回后调用方只负责日志和 API 响应，不得再自行写心跳；
- 任一已注册 ARQ function 都需在 `backend/app/worker.py` 的 `WORKER_FUNCTION_NAMES` + `WorkerSettings.functions` 成对出现，测试 `tests/test_worker_registration.py` 会强校验；
- 若启用 `WORKER_RESOURCE_QUEUES_ENABLED=true`，需分别启动 `ProcessWorkerSettings`、`LlmWorkerSettings`、`MaintenanceWorkerSettings` 等资源型 worker；默认 `WorkerSettings` 仍保持单队列兼容。

### 7.2.1 Worker 资源调度

| 资源类型 | 典型 job | 默认上限 | 说明 |
|---|---|---:|---|
| `process` | `process_document_job`, `writing_material_process_job`, `writing_clause_vectorize_job` | `WORKER_PROCESS_MAX_JOBS=5` | PDF 解析、OCR/VL、向量化等重 IO/CPU/GPU 流程 |
| `llm` | `review_project_job`, `collusion_analyze_job`, `writing_compose_job` 等 | `WORKER_LLM_MAX_JOBS=5` | 项目级 LLM 入口上限；真实请求并发继续由 Dispatcher AIMD 控制 |
| `maintenance` | `flush_metrics_job`, `regression_runner_job` | `WORKER_MAINTENANCE_MAX_JOBS=1` | 低优先级维护任务 |
| `fallback` | FastAPI 同进程兜底任务 | `BACKGROUND_FALLBACK_MAX_JOBS=1` | 保留稳定兜底，但避免 API 被重任务拖死 |

资源许可只在 job 入口层生效。单个项目内部仍由现有 `run_*_task` 的顺序 `await` 和状态机维护，因此不会出现后续节点越过前置节点执行的问题。

### 7.2.2 Worker-first 与自动 fallback

- **worker-first**: 所有长任务入口必须先尝试 ARQ，并记录结构化入队结果：`queued / worker_missing / function_worker_missing / redis_unavailable / enqueue_error`。
- **等待窗口**: worker 短暂不可用时，任务进入 `waiting_worker`，等待 `WORKER_WAIT_BEFORE_FALLBACK_SEC`；等待期间不得伪装成 running。
- **自动 fallback**: 超过等待窗口或明确 enqueue 异常后，系统自动进入 `asyncio_fallback`，并显式写入 `fallback_waiting / fallback_running`，无需人工开启。
- **状态展示**: 前端/状态接口应区分 `waiting_worker`、`fallback_waiting`、`fallback_running`、`running`，避免“假处理中”。

### 7.2.3 Worker 心跳与退出

- Worker alive 心跳除 `functions` 外，还应包含 `state=idle/running/draining`、`current_function`、`current_resource`、`event_loop_lag_ms`。
- Ctrl+C 或进程关闭时，worker 先进入 `draining`，停止领取新任务并清理 heartbeat；若当前任务被中断，业务状态应进入可恢复状态。
- 资源许可必须记录 owner，探针可看到占用者，避免只知道 inflight=1 但无法定位残留任务。

### 7.2.4 最小 Orchestrator 约束

写标流程先启用轻量前置依赖检查：素材批处理必须等目录节点已存在且项目状态进入 `OUTLINED/MATCHING/MATCHED/COMPOSING/COMPLETED` 后才释放。后续投标审查和串标流程按同一模式迁移到 step-level orchestrator。

### 7.3 ARQ job 注册一览

| job 函数名 | 入口任务 | 触发方 | 典型心跳阶段 |
|---|---|---|---|
| `process_document_job` | `run_process_task` | files 上传 / 写标 / 恢复 | parsing / vectorizing / completed |
| `extract_requirements_job` | `run_extract_task` | 投标要求确认前 | extracting / completed |
| `review_project_job` | `run_review_task` | 投标审查入口 | extracting / matching / reviewing |
| `collusion_analyze_job` | `run_collusion_task` | 串标分析入口 | s1 / s2 / s3 / s3.5 / s4 / completed |
| `writing_extract_job` | `run_writing_extract_task` | 约束提取触发 | extracting / render_profile / industry_profile / structure_analysis |
| `writing_outline_job` | `run_writing_outline_task` | 目录生成触发 | outlining / completed |
| `writing_material_process_job` | `run_writing_material_process_task` | 素材上传后自动 | parsing / vl_recognition / indexing / image_classify / tagging / completed |
| `writing_match_job` | `run_writing_match_task` | 素材关联确认前 | vectorizing / retrieving / materials / saving |
| `writing_compose_job` | `run_writing_compose_task` | 全文生成触发 | composing / completed |
| `writing_compose_single_job` | `run_writing_compose_single_task` | 单章节重新生成 | starting / composing / completed |
| `writing_clause_vectorize_job` | `run_writing_clause_vectorize_task` | 条款向量化附加 | vectorizing / completed |

### 7.4 状态单一事实源

- **任务活跃性**：`is_task_active(task_name)` + `is_task_in_enqueue_grace(task_name)`，基于 heartbeat(Redis)+本地 asyncio.Task 表；
- **阶段与进度**：`get_task_heartbeat(task_name)`，字段 `stage`, `progress="xx%|detail"`, `extra`；
- **素材批处理 phase**：从 heartbeat.extra.phase 与 heartbeat.stage 推导，前端别名映射由服务层完成，不再依赖任何进程内类变量；
- **串标进度停滞检测**：`read_collusion_stall_snapshot` / `write_collusion_stall_snapshot` 改为 Redis 优先、本地降级，避免多实例语义分叉。

### 7.5 交互型重请求策略

| 接口 | 决策 | 理由 |
|---|---|---|
| `POST /writing/.../materials/process-batch` | **异步**（触发 + 前端轮询 `process-status`） | 原流程在 API 内同步跑解析+VL+索引+打标，典型分钟级 |
| `POST /writing/.../compose/{node_id}` | **异步**（触发 + 前端轮询 `compose/{node_id}/status`） | 单章节生成涉及 LLM + gate + save，最长可达数十秒 |
| `POST /writing/.../render` | 保持同步 | python-docx 渲染通常几秒，且有 db 产物缓存 + `_render_locks`；若后续指标证明需要异步化，再按统一契约改造 |
| `GET /writing/.../download/docx` | 保持同步 | 仅读已渲染产物，典型 < 1s |
| 投标 / 串标报告导出 | 保持同步 | ReportLab PDF 生成当前耗时可接受；仍需补耗时埋点以保留异步化决策依据 |

新增"异步触发 + 查询"类接口必须遵循上表模式并在路由旁边维护对应 `status` 端点。
