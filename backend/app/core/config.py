"""应用配置"""

import os
from typing import Literal, Optional

from loguru import logger
from pydantic import model_validator
from pydantic_settings import BaseSettings

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


class Settings(BaseSettings):
    """应用配置类"""

    # 数据库配置
    # 必须在 .env 文件中配置，示例: DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname
    DATABASE_URL: str = ""
    
    # 文件存储
    UPLOAD_DIR: str = "./storage/uploads"
    CHUNK_DIR: str = "./storage/chunks"
    
    # ==================== AI模式配置 ====================
    # AI处理模式: local=本地模型, cloud=云端模型
    AI_MODE: Literal["local", "cloud"] = "local"
    
    # ==================== 本地LLM配置 ====================
    # 支持 vLLM (Linux) 或 Ollama (Windows/Mac/Linux)
    # Ollama默认端口11434，vLLM建议使用8001
    LLM_API_BASE: str = "http://localhost:11434/v1"  # Ollama兼容OpenAI API
    LLM_MODEL_NAME: str = "qwen3-vl:4b"  # Ollama模型名称格式
    # 本地模型路径（用于vLLM启动，Ollama不需要）
    LLM_MODEL_PATH: str = os.path.join(PROJECT_ROOT, "models", "qwen2.5-7b")
    
    # 兼容旧配置名（别名）
    @property
    def VLLM_API_BASE(self) -> str:
        return self.LLM_API_BASE
    
    @property
    def VLLM_MODEL_NAME(self) -> str:
        return self.LLM_MODEL_NAME
    
    # ==================== 云端LLM配置 ====================
    # 云端API基础URL（兼容OpenAI API格式）
    # 常见服务:
    # - OpenAI: https://api.openai.com/v1
    # - DeepSeek: https://api.deepseek.com/v1
    # - 智谱AI: https://open.bigmodel.cn/api/paas/v4
    # - 通义千问: https://dashscope.aliyuncs.com/compatible-mode/v1
    CLOUD_API_BASE: str = "https://api.deepseek.com"
    CLOUD_API_KEY: str = ""  # 云端API密钥，必须在.env中设置
    CLOUD_MODEL_NAME: str = "deepseek-chat"  # 云端模型名称
    CLOUD_TIMEOUT: float = 300.0  # 云端请求超时（秒），大文本合并去重需要更长时间
    
    # ==================== 向量模型配置 ====================
    # 优先使用本地模型路径，如果不存在则回退到HuggingFace下载
    EMBEDDING_MODEL: str = os.path.join(PROJECT_ROOT, "models", "bge-m3")
    EMBEDDING_MODEL_FALLBACK: str = "BAAI/bge-m3"
    # 向量化设备: cpu / cuda / auto
    # auto = 自动检测，有GPU用GPU，否则用CPU（推荐）
    EMBEDDING_DEVICE: str = "auto"
    # 推理后端: auto / onnx / pytorch
    # auto = CPU 时尝试 ONNX, GPU 时使用 PyTorch
    EMBEDDING_BACKEND: Literal["auto", "onnx", "pytorch"] = "auto"

    # 应用配置
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["text", "json"] = "text"
    DEBUG_SQL: bool = False  # True 时打印所有 SQL 语句，生产/日常开发建议关闭
    # 错误日志治理开关（两者独立，可分别回滚）：
    # AGGREGATION: 同指纹回声折叠为一行并累加 occurrence_count；关闭后退回旧的 WARNING 窗口去重丢弃
    # TRAFFIC_ISOLATION: 查询侧默认只返回 production 流量；关闭后默认跨全部流量类别
    ERROR_LOG_AGGREGATION_ENABLED: bool = True
    ERROR_LOG_TRAFFIC_ISOLATION_ENABLED: bool = True

    # ==================== 数据库连接池 ====================
    DB_POOL_SIZE: int = 15
    DB_MAX_OVERFLOW: int = 15
    # 阿里云 RDS + 内网 SLB/NAT 可能在长 idle 后单方面关闭半开 TCP；
    # 1800s (30 分钟) 窗口过大，配合客户端 TCP keepalive 收窄到 5 分钟更稳。
    DB_POOL_RECYCLE: int = 300
    DB_POOL_TIMEOUT: int = 60
    # 进程级"DB 会话许可"上限, 控制并发循环中同时打开的短 session 数。
    # 选值要求: ADMISSION_LIMIT + 峰值外层 session (≈ worker max_jobs) < 池容量。
    # 默认 12 (< DB_POOL_SIZE=15), 留出余量给外层编排 session 与瞬时事务。
    # 仅作用于 db_session_scope(), 不影响 API get_db / 外层编排 session。
    DB_SESSION_ADMISSION_LIMIT: int = 12

    # ==================== Redis / ARQ 配置 ====================
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # 在线用户统计: 前端定时心跳写入 Redis ZSET, 后端按窗口统计唯一用户数
    USER_PRESENCE_ONLINE_WINDOW_SECONDS: int = 120
    USER_PRESENCE_HEARTBEAT_INTERVAL_SECONDS: int = 30

    # ARQ Worker alive 心跳:
    # - TTL: Redis 键过期时间, 过期即视为 worker 已下线
    # - INTERVAL: worker 主动刷新心跳的周期
    # - FRESHNESS_WINDOW: API 侧判定 worker 是否"新鲜"的时间窗口
    # 要求: INTERVAL < FRESHNESS_WINDOW <= TTL, 避免正常保活被误判下线
    # 心跳与业务共环, 窗口必须盖住进程内合法堵塞
    # (encode_gate CPU/batch stall=120s, OCR_HTTP_TIMEOUT=120s), 取 180 与任务心跳超时对齐
    ARQ_WORKER_HEARTBEAT_TTL: int = 180
    ARQ_WORKER_HEARTBEAT_INTERVAL: int = 15
    ARQ_WORKER_FRESHNESS_WINDOW: int = 180

    # 任务级心跳超时: vector_job_states.last_heartbeat_at 超过该窗口未推进, 视为卡死/丢失
    # 值需大于单次推进最慢的业务节拍(例如大文件 VL 批次), 避免正常处理被误判
    ARQ_JOB_HEARTBEAT_TIMEOUT: int = 180

    # ==================== 主动失活巡检 Reaper ====================
    # 主动驱动既有惰性自愈链(失活转FAILED/自动恢复/停滞重启), 使任务死亡后无需有人轮询
    # 也能自愈。复用现有判定阈值, 仅多一个触发源, 不重写判定逻辑。
    LIVENESS_SWEEP_ENABLED: bool = True
    LIVENESS_SWEEP_BATCH: int = 50  # 单轮扫描上限, 防止巡检自身成为重负载
    LIVENESS_SWEEP_LOCK_TTL: int = 55  # Redis 单飞锁 TTL(秒), 略小于 1 分钟 cron 间隔

    # ==================== 周度 LLM 调度建议 ====================
    # 周一 00:00 北京时间（周日 24:00）由 maintenance cron 触发算法分析；环境 TZ=Asia/Shanghai
    WEEKLY_LLM_ADVICE_ENABLED: bool = True

    # 项目取消墓碑 TTL (秒): 删除项目时写入 Redis, worker 任一阶段读到即立即退出
    # 需覆盖"最长可能还在运行的任务自然终止时间", 超期自动清理
    ARQ_PROJECT_CANCEL_TTL: int = 86400

    # ARQ job 取消黑名单 TTL (秒): 覆盖 queued -> claimed 的最大窗口
    # 超过这个窗口任务要么已经开始(由项目墓碑继续拦截), 要么已被 worker 丢弃
    ARQ_JOB_CANCEL_TTL: int = 3600

    # ==================== Worker 资源调度配置 ====================
    # 目标: 允许多个项目同时处于 running, 但按资源类型控制实际重活并发。
    WORKER_TARGET_RUNNING_TASKS: int = 5
    WORKER_RESOURCE_UTILIZATION_TARGET: float = 0.8
    WORKER_RESOURCE_PERMIT_ENABLED: bool = True
    WORKER_RESOURCE_PERMIT_TTL: int = 3600
    # 无主 inflight 观察宽限: 只覆盖 INCR 与 owner 登记之间的并发窗口，
    # 不得复用 TTL(3600s)，否则幽灵计数会把空闲集群卡死一个小时。
    WORKER_RESOURCE_PERMIT_ORPHAN_GRACE: int = 60
    WORKER_RESOURCE_PERMIT_WAIT_TIMEOUT: float = 30.0
    WORKER_RESOURCE_PERMIT_POLL_INTERVAL: float = 1.0
    WORKER_RESOURCE_QUEUES_ENABLED: bool = False
    WORKER_WAIT_BEFORE_FALLBACK_SEC: float = 30.0
    WORKER_WAIT_POLL_INTERVAL: float = 2.0
    WORKER_EVENT_LOOP_LAG_WARN_MS: int = 5000
    WORKER_DRAIN_TIMEOUT_SEC: float = 15.0

    WORKER_DEFAULT_QUEUE_NAME: str = "arq:queue"
    WORKER_PROCESS_QUEUE_NAME: str = "arq:queue:process"
    WORKER_LLM_QUEUE_NAME: str = "arq:queue:llm"
    WORKER_REPORT_QUEUE_NAME: str = "arq:queue:report"
    WORKER_MAINTENANCE_QUEUE_NAME: str = "arq:queue:maintenance"

    WORKER_DEFAULT_MAX_JOBS: int = 5
    WORKER_PROCESS_MAX_JOBS: int = 5
    WORKER_LLM_MAX_JOBS: int = 5
    WORKER_REPORT_MAX_JOBS: int = 3
    WORKER_MAINTENANCE_MAX_JOBS: int = 1

    # ==================== 项目级公平调度 ====================
    # 上面的 *_MAX_JOBS 计量单位是 job, 无法阻止单个项目独占全部槽位;
    # 这一组把计量单位换成 project, 让 WORKER_TARGET_RUNNING_TASKS 真正生效。
    # 关掉 WORKER_PROJECT_FAIRNESS_ENABLED 即退回旧 FIFO 行为。
    WORKER_PROJECT_FAIRNESS_ENABLED: bool = True
    # 份额持有者心跳 TTL: 超过该窗口未续期即判定持有进程已死, 份额可被回收
    WORKER_FAIRNESS_HOLDER_TTL: int = 900
    WORKER_FAIRNESS_HOLDER_HEARTBEAT_INTERVAL: float = 60.0
    # 排队者需主动重新轮询才保留资格, 超时即视为该 job 已消失
    WORKER_FAIRNESS_WAITER_TTL: int = 1800
    # 借用空闲份额前的让路窗口: 留出槽位空闲期, 让其它项目的排队 job 有机会登记需求
    WORKER_FAIRNESS_BORROW_PROBATION: float = 1.5
    WORKER_FAIRNESS_DEFER_DELAY: float = 2.0
    WORKER_FAIRNESS_DEFER_DELAY_CAP: float = 30.0
    # 让位次数受 arq max_tries(默认5) 硬约束, 超出会让 job 被判定失败, 不得调高
    WORKER_FAIRNESS_MAX_DEFERS: int = 3
    # Dispatcher 请求级项目轮转; 关掉即退回单一 FIFO Semaphore
    AI_PROJECT_FAIR_QUEUE_ENABLED: bool = True
    AI_PROJECT_FAIR_QUEUE_QUANTUM: float = 1.0

    # ARQ job_timeout(秒): 定位为"防呆上限"而非"卡死判定"。
    # 大文档(313 页)实测 GPU~25min / CPU 兜底最坏~49min, 旧硬编码 1800s 会误杀健康任务
    #。真正的卡死由业务进展看门狗 + reconciler 心跳 + in-progress 守卫负责。
    # 长 LLM job 族抬到 7200s 防呆；in-progress TTL 副作用由 liveness 证据封顶中和。
    WORKER_PROCESS_JOB_TIMEOUT: int = 3600
    # 招标要求提取与写标/审查/串标长任务统一长防呆上限。
    WORKER_EXTRACT_JOB_TIMEOUT: int = 7200
    WORKER_LONG_LLM_JOB_TIMEOUT: int = 7200
    WORKER_LLM_JOB_TIMEOUT: int = 1800
    # 无进展看门狗: 心跳业务指纹连续无变化超过该秒数则主动终止。
    # 锚定 2 × LLM_CALL_DEADLINE_SECONDS(900)，至少覆盖两个完整逻辑调用周期。
    WORKER_TASK_NO_PROGRESS_TIMEOUT: int = 1800
    WORKER_TASK_NO_PROGRESS_GUARD_ENABLED: bool = True

    BACKGROUND_FALLBACK_ENABLED: bool = True
    BACKGROUND_FALLBACK_MAX_JOBS: int = 1
    BACKGROUND_FALLBACK_WAIT_TIMEOUT: float = 1.0

    # ==================== AI密钥加密 ====================
    AI_SETTINGS_KEY: str = ""

    # ==================== 可观测性 ====================
    METRICS_ENABLED: bool = False

    # ==================== B1: 失败合同与 Gate 基础设施 ====================
    FAILURE_STATE_ENABLED: bool = True
    GATE_SEVERITY_OVERRIDES: str = "{}"

    # ==================== B2: 约束解码灰度 ====================
    # per-flow 灰度比例 (0.0=关闭, 1.0=全量); hash(project_id) % 100 < ratio*100 即开启
    CONSTRAINED_DECODING_RATIO_COLLUSION: float = 1.0
    CONSTRAINED_DECODING_RATIO_EXTRACTOR: float = 1.0
    CONSTRAINED_DECODING_RATIO_REVIEW: float = 1.0

    # ==================== B3: Gate Pipeline 灰度 ====================
    # (2026-04-24): collusion 流已彻底下线 legacy 双路，
    # should_use_gate_pipeline("collusion", ...) 恒返 True，配置项移除。
    GATE_PIPELINE_RATIO_REVIEW: float = 1.0

    # ==================== B4: 算法主判灰度 ====================
    INVESTIGATE_ALGORITHM_FIRST_ENABLED: bool = True
    INVESTIGATE_ALGORITHM_FIRST_RATIO: float = 1.0

    # ==================== B5: 写标 Gate + Critic 层灰度 ====================
    WRITER_GATE_PIPELINE_RATIO: float = 1.0
    WRITER_DATA_FILL_TEMPLATE_RATIO: float = 1.0
    COVE_ENABLED: bool = True
    COVE_RATIO: float = 1.0
    JUDGE_ENABLED: bool = True
    JUDGE_SAMPLING_RATIO: float = 0.1

    # ==================== S.3: LLM 非幂等收敛 ====================
    # S.3.1: seed 透传总闸（默认 on；不支持 seed 的 provider 会静默忽略，不报错）
    LLM_SEED_ENABLED: bool = True
    # S.3.5: gate_trace 审计字段开关（prompt_hash / model_id / seed 写入 verdict.trace._audit）
    GATE_AUDIT_TRACE_ENABLED: bool = True

    # ==================== S.5: 算法裁决最后一公里 ====================
    # S.5.1: AlgorithmicRiskBandGate 升 enforce 的置信度阈值；≥ 该值的分歧才 patch
    ALGORITHMIC_RISK_BAND_ENFORCE_CONFIDENCE: float = 0.8

    # ==================== 运行环境 ====================
    # development / production；生产环境下关闭公开 API 文档并强化安全校验
    ENVIRONMENT: str = "development"

    # ==================== 限流配置 ====================
    # 认证与上传入口的 IP 维度限流(slowapi 语法), 留空字符串表示不限流
    RATE_LIMIT_REGISTER: str = "5/minute"
    RATE_LIMIT_LOGIN: str = "10/minute"
    RATE_LIMIT_UPLOAD: str = "30/minute"

    # ==================== CORS配置 ====================
    CORS_ORIGINS: str = "http://localhost:3000"

    # ==================== JWT认证配置 ====================
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    # 开发默认 480 分钟（8 小时），对齐生产配置，覆盖一个工作日连续开发；
    # access token 到期由前端无感刷新兜底（见 httpClient 401 拦截器），
    # 且远短于 refresh token 有效期，安全窗口可控。
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # ==================== 初始管理员配置 ====================
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "admin@example.invalid"
    ADMIN_PASSWORD: str = ""
    
    # 审查配置
    REVIEW_MAX_RETRIES: int = 5  # 增加重试次数，提高成功率
    REVIEW_BATCH_SIZE: int = 20  # 每批次提交数量，减少数据库事务开销
    REVIEW_CONCURRENCY: int = 20  # 并发数（本地vLLM可设20-30，云端API需降低防限流）
    REVIEW_STALL_TIMEOUT: int = 180  # 审查进度停滞阈值（秒），缩短以更快检测卡死
    # 协程数绝对上限, 防止通道数膨胀使单 job 扇出协程数失控(调度与内存开销)。
    # 本值原为 8, 承担"限制协程数以间接保护连接池"的职责, 但它低于
    # Dispatcher AIMD 容量(通道数 × CLOUD_MAX), 使 AIMD 在 8 以上的调节全部失效——
    # 业务层只放 8 个请求进来, 其余槽位永远空闲。连接池保护已由会话层的
    # DB_SESSION_ADMISSION_LIMIT 直接承担(db_session_scope 对 session 获取加信号量,
    # 连接数上界与协程并发数无关), 此处不再重复把关, 故放宽到不低于 AIMD 容量。
    # 调整时必须同步满足 test_concurrency_gate_alignment 中的分层契约。
    REVIEW_CONCURRENCY_DB_SAFE_CAP: int = 40
    # 审查输出契约瘦身。开启后模型只输出必须由其判断的字段,
    # requirement_id/category/摘要/页码等由服务端装配, 不再进入模型失败面。
    # 置 false 回退旧的整体回显契约（回滚开关, 观测口径修正不受影响）。
    REVIEW_OUTPUT_CONTRACT_SLIM_ENABLED: bool = True
    # 阶段8a: 未响应确定性判定模式。off=不计算 / shadow=计算+记录对比但不改结论 /
    # enforce=命中且未弃权时接管结论并跳过 LLM。
    # 默认 shadow: enforce 放行须先完成 8b 的双盲人工标注验收, 回滚即改回 shadow/off。
    REVIEW_DETERMINISTIC_NO_RESPONSE_MODE: str = "shadow"
    # 要求文本短于此长度视为输入不完备, 确定性判定一律弃权。
    REVIEW_DETERMINISTIC_NO_RESPONSE_MIN_TEXT_LEN: int = 12

    # ==================== 投标响应提取输入预算配置 ====================
    # bid_extractor 发送给云端模型的检索上下文字符预算（不含 prompt 模板本身）
    BID_EXTRACT_CONTEXT_CHAR_BUDGET: int = 16000
    # 单个检索片段的最大字符数，避免少量超长 chunk 顶爆输入
    BID_EXTRACT_CHUNK_CHAR_LIMIT: int = 1800
    # 命中“输入过长类400”后的上下文预算缩减比例
    BID_EXTRACT_RETRY_REDUCTION_RATIO: float = 0.7

    # ==================== 投标审查输入预算配置 ====================
    # 命中“输入过长类400”后, 审查 Prompt 可裁证据块的字符预算与收缩策略。
    # 骨架(招标要求/投标响应摘要/输出 schema)永不进入预算裁剪;
    # 知识库参考(辅助)优先牺牲, 投标原文佐证次之, 均采用首尾保留式截断。
    REVIEW_KNOWLEDGE_CHAR_BUDGET: int = 4000
    REVIEW_EVIDENCE_CHAR_BUDGET: int = 12000
    # 审查 Prompt 全部证据区(图片/独立补搜/违禁/账本/原文/跨类别/知识库)
    # 的 round 0 合计上限。分段预算不再各自累加, 统一由证据包预算器分配。
    REVIEW_EVIDENCE_PACK_CHARS: int = 12000
    # 进入 Prompt 的证据条目条数上限; 广召回候选留库, 只送最小充分集。
    REVIEW_EVIDENCE_ITEM_MAX: int = 12
    # 发送前硬上限预检: 估算 token 与请求体字节。0 表示关闭该项校验(回滚开关)。
    REVIEW_PROMPT_HARD_CAP_TOKENS: int = 24000
    # 实测 Qwen 请求体字节上限 6291456。
    REVIEW_PROMPT_HARD_CAP_BYTES: int = 6291456
    # 项目证据账本注入审查 Prompt 的字符帽（round 0 即生效）
    REVIEW_LEDGER_CHAR_BUDGET: int = 6000
    # 每个分面 kind 写入 Prompt 的代表原子数，其余页码归并到该 kind 行
    REVIEW_LEDGER_ATOMS_PER_KIND: int = 3
    REVIEW_RETRY_REDUCTION_RATIO: float = 0.5
    # 输入超长收缩重试轮数(不含首次原样发送)
    REVIEW_INPUT_SHRINK_RETRIES: int = 3

    # ==================== R1: 审查阶段原文佐证注入 ====================
    # 注入投标原文佐证的 chunk 数量上限（按 role primary>deep>toc>image,
    # 同 role 按 similarity 降序）。设为 0 即关闭原文注入，充当回滚开关。
    REVIEW_RAW_EVIDENCE_CHUNKS: int = 3
    # 单段原文摘录的字符截断上限。
    REVIEW_RAW_EVIDENCE_CHARS: int = 500

    # ==================== 检索饥饿闸门配置（闸门 / 粒度错配修复）====================
    # 判定饥饿的最小"未覆盖证据页"数（原硬编码2，上调以削弱低强度噪音）
    RETRIEVAL_STARVATION_MIN_UNCOVERED_PAGES: int = 3
    # 候选证据页明细探测上限（仅影响 relevance_note 展示的页码数量，不影响占比判定）
    RETRIEVAL_STARVATION_CANDIDATE_LIMIT: int = 200
    # 候选页占全文档比例上限：超过即视为"无区分度短语"（如遍布全文的盖章/签字），
    # 判定该信号不可靠并抑制饥饿告警。取值 (0,1]，>=1 表示关闭占比护栏。
    RETRIEVAL_STARVATION_MAX_CANDIDATE_RATIO: float = 0.35
    # 饥饿页有界注入证据包上限, 禁止全文灌模型
    RETRIEVAL_STARVATION_EXPAND_MAX_PAGES: int = 8
    RETRIEVAL_STARVATION_EXPAND_CHUNKS_PER_PAGE: int = 3
    # 词项通道单文档召回数(与向量 top_k 同量级)
    RETRIEVAL_LEXICAL_TOP_K: int = 8

    # ==================== VL识别质量门禁 ====================
    VL_PROBE_COUNT: int = 2                      # 前置探测图片数量
    VL_GATE_MIN_SUCCESS_RATE: float = 0.15       # 扫描密集文档最低VL成功率（低于则中止）
    VL_GATE_SCAN_CRITICAL_RATIO: float = 0.5     # 扫描页占比阈值（超过视为扫描密集文档）

    # ==================== 招标提取二次合并稳态配置 ====================
    # 当要求数量达到阈值时启用分批二次合并
    TENDER_SECONDARY_BATCH_TRIGGER: int = 60
    # 单批次目标规模（过大易导致JSON输出不稳定）
    TENDER_SECONDARY_BATCH_SIZE: int = 24
    # 二次合并单轮最大输出token上限（不建议直接拉到20000）
    TENDER_SECONDARY_MAX_TOKENS: int = 14000
    # 分批汇总后的最终收敛轮token上限
    TENDER_SECONDARY_FINAL_PASS_MAX_TOKENS: int = 8000
    # 二次合并JSON结构化失败后的补救重试次数
    TENDER_SECONDARY_JSON_RETRY: int = 1
    # final-pass 单轮输入上限，超过则改为分批收敛
    TENDER_FINAL_PASS_SINGLE_MAX_INPUT: int = 50
    # final-pass 分批收敛批大小
    TENDER_FINAL_PASS_BATCH_SIZE: int = 20

    # ==================== 招标提取合并并发与分桶配置 ====================
    # 二次合并批次间并发度（云端建议 3-4, 本地建议 2）
    TENDER_SECONDARY_MERGE_CONCURRENCY: int = 3
    # final-pass 批次间并发度
    TENDER_FINAL_PASS_CONCURRENCY: int = 2
    # 分类合并批次间并发度
    TENDER_CATEGORY_MERGE_CONCURRENCY: int = 3
    # token 预算分桶: 每批次的目标 token 预算（中文约 2 char/token）
    TENDER_MERGE_TOKEN_BUDGET: int = 3000

    # ==================== 招标提取分类合并稳态配置 ====================
    # 当单类别要求数量达到阈值时启用类别内分批合并
    TENDER_CATEGORY_BATCH_TRIGGER: int = 40
    # TECH 类强制分批阈值（防止超大技术类单轮输出爆炸）
    TENDER_CATEGORY_TECH_FORCE_BATCH_THRESHOLD: int = 50
    # 单类别分批合并批大小
    TENDER_CATEGORY_BATCH_SIZE: int = 20
    # 分类合并单轮最大输出token上限
    TENDER_CATEGORY_MAX_TOKENS: int = 12000
    # 分类合并最终收敛轮token上限
    TENDER_CATEGORY_FINAL_PASS_MAX_TOKENS: int = 6000
    # 分类合并JSON结构化失败补救重试次数
    TENDER_CATEGORY_JSON_RETRY: int = 1
    # 分类最终收敛轮单轮输入上限，超过后改走分批收敛
    TENDER_CATEGORY_FINAL_PASS_SINGLE_MAX_INPUT: int = 40
    # 分类最终收敛轮分批大小
    TENDER_CATEGORY_FINAL_PASS_BATCH_SIZE: int = 16

    # ==================== 反思链路精度保护配置 ====================
    # 反思链路JSON失败补救次数
    TENDER_REFLECTION_JSON_RETRY: int = 1
    # 反思链路输出token上限
    TENDER_REFLECTION_MAX_TOKENS: int = 3000
    # 反思链路质量分阈值（低于该值才触发LLM问题清单）
    TENDER_REFLECTION_QUALITY_THRESHOLD: int = 85

    # ==================== 提取质量门控配置 ====================
    # 分块失败率超过此阈值时，启动审查前要求用户确认缺口风险
    EXTRACTION_FAILURE_RATE_WARN: float = 0.05
    # 连续失败页簇长度超过此值视为高风险
    EXTRACTION_CONSECUTIVE_FAIL_THRESHOLD: int = 3

    # ==================== 招标要求提取效率优化配置 ====================
    # chunk 级 LLM 抽取结果缓存。key 绑定 chunk 指纹 + prompt 签名 + 模型 + schema 版本。
    TENDER_CHUNK_EXTRACT_CACHE_ENABLED: bool = True
    TENDER_CHUNK_EXTRACT_CACHE_SCHEMA_VERSION: str = "v1"
    # LLM 合并前仅压缩低风险完全重复候选；否决项/评分项/重点项永不删除。
    TENDER_PROTECTED_PREMERGE_ENABLED: bool = True
    # LLM 合并前候选图门控: 孤立条目绕过 LLM, 仅候选重复分量进入合并。
    TENDER_CANDIDATE_GATE_ENABLED: bool = True
    TENDER_CANDIDATE_SIM_THRESHOLD: float = 0.75
    TENDER_CANDIDATE_KEYWORD_JACCARD: float = 0.5
    # LLM 合并前低风险近重复自动压缩。
    TENDER_NEAR_DUP_PREMERGE_ENABLED: bool = True
    TENDER_NEAR_DUP_NGRAM_THRESHOLD: float = 0.9
    # 分块提取过程中提前启动候选门控所需的向量化。
    TENDER_INCREMENTAL_EMBED_ENABLED: bool = True
    # 文档处理完成后自动预热招标要求提取，普通用户进入确认页时可直接读结果或看进度。
    TENDER_EXTRACT_PREWARM_ENABLED: bool = True
    # 普通提取落库后，对废标/资格/★ 等原子条件做作用域召回与信号覆盖。
    TENDER_MANDATORY_COVERAGE_ENABLED: bool = True
    TENDER_MANDATORY_COVERAGE_MAX_CHARS: int = 12000

    # ==================== PDF解析文本修复配置 ====================
    # 修复 E1/E2 等公式上下标因基线偏移被 pdfplumber 甩出主行的问题。
    PARSER_SCRIPT_LIFT_ENABLED: bool = True

    # ==================== 精度闸门与回放基线观测配置 ====================
    # 是否输出精度元信息与基线快照（仅观测，不影响业务结果）
    TENDER_PRECISION_GUARD_ENABLED: bool = True
    # 回放基线项目ID列表（逗号分隔）
    TENDER_PRECISION_BASELINE_PROJECT_IDS: str = "122"
    # 精度回放快照输出目录（相对工作目录）
    TENDER_PRECISION_BASELINE_DIR: str = "backend/storage/precision_baseline"

    # ==================== 招标要求提取精度优化开关（P0-P2）====================
    # P0: 提取时向 LLM 注入 chunk 章节上下文 + 合同区段豁免
    TENDER_EXTRACT_SECTION_CONTEXT_ENABLED: bool = True
    # P1: 评审意义三级分类（actionable/informational/procedural）+ 确认页分级折叠
    TENDER_REVIEW_RELEVANCE_ENABLED: bool = True
    # P2: 评分区段跨页表拼接，生成整表 scoring_table chunk
    TENDER_TABLE_STITCH_ENABLED: bool = True
    # P2: 评分项结构化提取（评分项+分值+评分规则+重点评审）
    TENDER_SCORING_EXTRACT_ENABLED: bool = True
    # 评分项校验失败时, 是否允许 LLM 生成增量补丁并由确定性闭环验收。
    SCORING_LLM_REPAIR_ENABLED: bool = True
    SCORING_REPAIR_CONTEXT_CHAR_BUDGET: int = 6000
    SCORING_REPAIR_MAX_TOKENS: int = 2000
    # P3: 评审意义独立判定 pass（合并后整表批量判定可评审性；关闭则回退逐块标注+规则）
    TENDER_RELEVANCE_PASS_ENABLED: bool = True
    # 评审意义判定 pass 的批大小（每次 LLM 调用判定的要求条数）
    TENDER_RELEVANCE_BATCH_SIZE: int = 20
    # 评审意义判定批次间并发度（云端建议 4；墙钟由最慢单批决定）
    TENDER_RELEVANCE_CONCURRENCY: int = 4
    # P4: 可评审性消费闸门——确认/响应提取/匹配/审查/进度/重试统一只消费
    # "confirmed=1 且 review_relevance=actionable" 的要求；评分项落库恒为 actionable，
    # 天然通过闸门不受影响。关闭则回退仅按 confirmed=1 消费的旧行为。
    REVIEW_ACTIONABLE_GATE_ENABLED: bool = True
    # P4: 否决项行为性折叠——LLM 证据载体判非投标文件载体且规则地板双确认时，
    # 允许把纯行为性/程序性否决条款折叠为风险告知（procedural）；关闭则否决项一律 actionable。
    TENDER_BEHAVIORAL_MANDATORY_FOLD_ENABLED: bool = True

    # 模板编码边界检测开关——从页面标题行识别 JS-FWTY/FWJS05 等编码锚点，
    # 在文档含>=2个不同模板编码时自动切分装配范围，防止跨模板拼接。关闭则禁用边界检测。
    SCORING_TEMPLATE_BOUNDARY_ENABLED: bool = True
    # 评分逻辑表装配——按"表头锚定+表尾封口"重建跨页评分表(列数抖动对齐/
    # 失表页桥接/接缝 forward-fill), 未认领表仍走既有拼接路径。关闭则回退纯拼接行为。
    SCORING_LOGICAL_ASSEMBLY_ENABLED: bool = True
    # 评分文字载体识别——卷定义声明句 prelude 注入、价格公式段落识别。
    # 关闭则回退仅消费表格载体的旧行为。
    SCORING_TEXT_ELEMENTS_ENABLED: bool = True
    # 多采购包评分维度。开启后 scoring_table chunk 页头携带 lot 标签,
    # 后续去重/核计按 lot 分组; 无多包锚点文档行为不变。
    SCORING_LOT_DIMENSION_ENABLED: bool = True
    # SchemeReader 阶段0: 装配前捕获评分结构边界信号快照并落 documents.scoring_signals。
    SCORING_STRUCTURE_SIGNALS_ENABLED: bool = True
    # 治理阶段5 配置收口: 以下两项此前只经 getattr 兜底默认取值、未在 Settings 声明，
    # 属"删声明即静默变行为"的暗雷（同族）。默认值与原 getattr 兜底一致。
    # 评分组锚点切分(scoring_chunk_builder): 按组锚点切装配 span。
    SCORING_GROUP_ANCHOR_ENABLED: bool = True
    # 孤表续接恢复(scoring_chunk_sources): 装配后把失表页/孤表接回逻辑表。
    SCORING_STUB_RECOVERY_ENABLED: bool = True
    # SchemeReader 判读模式: off=不调用 / shadow=判读+分歧日志不改值 / enforce=按判读贴标。
    # 阶段5(2026-07-24): 2249/2250 shadow 实测通过后切 enforce; 回滚即改回 shadow/off。
    SCORING_SCHEME_READER_MODE: str = "shadow"
    # enforce 贴标的最低有效置信度(锚点核验折算后), 低于阈值降级为观察。
    SCORING_SCHEME_READER_MIN_CONFIDENCE: float = 0.6
    # "主行满分+档次折扣行"版式的档次行归并(消除重复计分), 关闭即恢复原行为。
    SCORING_GRADE_BAND_CONSOLIDATION_ENABLED: bool = True

    # ==================== 文档处理并发配置 ====================
    DOC_PROCESS_CONCURRENCY: int = 8  # 文档处理全局并发上限
    PARSE_THREAD_WORKERS: int = 3      # PDF解析进程池大小（变量名沿用）
    # PDF解析阶段硬超时（秒）。解析在独立子进程同步执行且无法回传页级进度，
    # 唯一的兜底是此超时：超时后终止失控子进程并判定任务失败，避免大文件/病态PDF
    # 永久停在“处理中”不报错（见 心跳保活使看门狗永不恢复的副作用）。
    PARSE_STAGE_TIMEOUT_SEC: int = 1800

    # Office → PDF（Gotenberg sidecar）。空 URL 时 Word 处理阶段 fail-visible，PDF 直通不受影响。
    OFFICE_CONVERT_URL: str = ""
    OFFICE_CONVERT_TIMEOUT_SEC: int = 300

    # ==================== VL图片识别自适应并发配置 ====================
    VL_CONCURRENCY_CLOUD_INITIAL: int = 4
    VL_CONCURRENCY_CLOUD_MIN: int = 1
    VL_CONCURRENCY_CLOUD_MAX: int = 8
    VL_CONCURRENCY_LOCAL_INITIAL: int = 2
    VL_CONCURRENCY_LOCAL_MIN: int = 1
    VL_CONCURRENCY_LOCAL_MAX: int = 4
    VL_CONCURRENCY_WINDOW_SIZE: int = 15
    VL_CONCURRENCY_INCREASE_COOLDOWN: int = 5         # 并发增加冷却时间(秒)
    VL_CONCURRENCY_LATENCY_CEILING_CLOUD: int = 120000  # 云端延迟上限(ms)
    VL_CONCURRENCY_LATENCY_CEILING_LOCAL: int = 60000  # 本地延迟上限(ms)

    # ==================== Review/Extraction 调度器自适应并发配置 ====================
    # 每通道初始并发数（总初始 = channel_count * INITIAL）
    REVIEW_CONCURRENCY_CLOUD_INITIAL: int = 4
    # 每通道最小并发数（AIMD 下限保护）
    REVIEW_CONCURRENCY_CLOUD_MIN: int = 1
    # 每通道最大并发数（AIMD 上限 + 业务层 Semaphore 上限）
    REVIEW_CONCURRENCY_CLOUD_MAX: int = 8
    # AIMD 滑动窗口大小（积满后触发调整判断）
    REVIEW_CONCURRENCY_WINDOW_SIZE: int = 15
    # 并发增加冷却时间（秒），防止过快爬升
    REVIEW_CONCURRENCY_INCREASE_COOLDOWN: int = 5
    # 延迟上限（ms），超过此值不再增加并发
    REVIEW_CONCURRENCY_LATENCY_CEILING: int = 120000
    # 尾延迟降档开关。LLM 成功请求的自然重尾由输出长度差异造成，不是拥塞证据；
    # 开启后单条长输出即触发 -2 并清空窗口，会把有效并发棘轮式压到下限且无法回升。
    # 仅当确认尾部延迟确由服务端过载引起（且 429/timeout 信号缺失）时才打开。
    REVIEW_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED: bool = False

    # ==================== Vision 调度器自适应并发配置 ====================
    # Writer 素材 VL 与图片审查共用视觉调度器，但并发参数独立于 review 调度器
    VISION_CONCURRENCY_CLOUD_INITIAL: int = 4
    VISION_CONCURRENCY_CLOUD_MIN: int = 1
    VISION_CONCURRENCY_CLOUD_MAX: int = 8
    VISION_CONCURRENCY_WINDOW_SIZE: int = 15
    VISION_CONCURRENCY_INCREASE_COOLDOWN: int = 5
    VISION_CONCURRENCY_LATENCY_CEILING: int = 120000
    # 语义同 REVIEW_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED
    VISION_CONCURRENCY_TAIL_LATENCY_DECREASE_ENABLED: bool = False

    # ==================== LLM 429 分类冷却配置 ====================
    LLM_RATE_LIMIT_COOLDOWN_DEFAULT: int = 30
    LLM_RATE_LIMIT_COOLDOWN_MAX: int = 300
    LLM_QUOTA_EXHAUSTED_COOLDOWN: int = 1800

    # ==================== P0-A LLM 可靠性观测 ====================
    # 逐物理 attempt 事实记录总闸(llm_attempt_events); 关闭后记录器短路
    LLM_ATTEMPT_EVENTS_ENABLED: bool = True

    # ==================== P0-B LLM 尝试预算与 deadline ====================
    # 关闭后退回现状: 最大尝试数=通道数、无端到端时限
    LLM_ATTEMPT_BUDGET_ENABLED: bool = True
    # 单次逻辑调用跨通道最大物理尝试数(与通道数取小)
    LLM_MAX_ATTEMPTS_DEFAULT: int = 6
    # 超时类(APITimeoutError / asyncio.TimeoutError)物理尝试绝对上限(含首发);
    # 独立于 LLM_ATTEMPT_BUDGET_ENABLED, 始终生效; 调大可近似回退旧盲重试行为
    LLM_TIMEOUT_MAX_ATTEMPTS: int = 2
    # 端到端 deadline(秒), 含排队/熔断等待/全部重试; 云端单请求 timeout 300s x 3 裕量
    LLM_CALL_DEADLINE_SECONDS: float = 900.0
    # 超时弃组从单次 dispatch 提升为同 purpose 短时粘滞
    LLM_TIMEOUT_STICKY_ENABLED: bool = True
    LLM_TIMEOUT_STICKY_SECONDS: float = 180.0

    # ==================== P0-C 模型能力路由 ====================
    # 关闭后不按能力档案过滤通道、不做 thinking/seed 请求整形
    LLM_CAPABILITY_ROUTING_ENABLED: bool = True

    # ==================== 向量化性能配置 ====================
    VECTOR_DYNAMIC_BATCHING: bool = True      # 启用按token预算动态组批
    VECTOR_BUCKETING_ENABLED: bool = True     # 启用按文本长度分桶减少padding浪费
    VECTOR_TOKEN_BUDGET: int = 12000          # 单批次近似token预算上限
    VECTOR_DB_INSERT_MODE: str = "orm"        # orm/core（core为SQLAlchemy Core批量写入）
    VECTOR_EMBEDDING_VERSION: str = "bge-m3-v1"
    VECTOR_MAX_ITEM_CHARS: int = 1800
    VECTOR_MAX_ITEM_TOKENS: int = 1000
    VECTOR_PARAGRAPH_TARGET_CHARS: int = 700
    VECTOR_PARAGRAPH_OVERLAP_CHARS: int = 120
    VECTOR_CONTEXT_NEIGHBOR_RADIUS: int = 1
    VECTOR_CONTEXT_MAX_EXPANSIONS: int = 6
    VECTOR_CPU_FALLBACK_BASE_SEC: float = 45.0
    VECTOR_CPU_FALLBACK_PER_1K_CHARS_SEC: float = 18.0
    VECTOR_CPU_FALLBACK_MAX_SEC: float = 360.0

    # ==================== CPU 路径自适应配置 ====================
    # 与 GPU 自适应 (encode_gate 中的 EmbeddingBatchSizeController) 完全解耦。
    # 启动时通过 cpu_runtime + cpu_budget 探测真实可用核数, 用 Semaphore 控制并发路数。
    # None 表示自动 (按 cpu_budget 默认公式), 显式覆盖时填入正整数。
    VECTOR_CPU_DETECT_ENABLED: bool = True
    VECTOR_CPU_INTRA_OP_THREADS: Optional[int] = 4
    VECTOR_CPU_INTER_OP_THREADS: int = 1
    VECTOR_CPU_MAX_CONCURRENCY: Optional[int] = 2
    # CPU 路径下 SentenceTransformer.max_seq_length 的硬上限, 减少 padding 浪费;
    # bge-m3 默认 8192 远超业务文本中位数, 收紧到 512 不会截断 (因为 indexer 的
    # VECTOR_MAX_ITEM_CHARS=1800 已约束输入长度)
    VECTOR_CPU_MAX_SEQ_LENGTH: int = 512
    VECTOR_CPU_CONCURRENCY_INITIAL: int = 2
    VECTOR_CPU_CONCURRENCY_MIN: int = 1
    VECTOR_CPU_CONCURRENCY_MAX: Optional[int] = 2
    VECTOR_CPU_CONCURRENCY_WINDOW_SIZE: int = 4
    VECTOR_CPU_CONCURRENCY_INCREASE_COOLDOWN: int = 2
    VECTOR_CPU_CONCURRENCY_LATENCY_CEILING_MS: int = 30000
    VECTOR_CPU_CONCURRENCY_QUEUE_WAIT_CEILING_MS: int = 5000
    # OS load (Linux) 或 cpu_percent (Windows/macOS) 归一化压力阈值;
    # >= 该值视为系统过载, 触发降档让出核给 OCR/解析等其他 CPU 任务
    VECTOR_CPU_LOAD_PRESSURE_FACTOR: float = 1.5
    VECTOR_USE_CHUNK_PLANNER: bool = True
    VECTOR_SHADOW_COMPARE_ENABLED: bool = False

    # ==================== 分块质量护栏配置 ====================
    # P0: 主链路统一质量门控 (chunk_quality.filter_chunks)
    # rejected 块直接丢弃; degraded 块 (compat_ratio 超阈) 仍编码与入库, 仅标记并告警
    CHUNK_QUALITY_GATE_ENABLED: bool = True
    # 有效字符 (中文/数字/字母) 下限, 低于此值的块视为无意义 (纯页码/纯符号已单独识别)
    CHUNK_MIN_VALID_CHARS: int = 10
    # CJK 兼容字符占比阈值, 与 writer.clause_extractor._COMPAT_RATIO_THRESHOLD 保持一致
    CHUNK_COMPAT_RATIO_THRESHOLD: float = 0.02
    # P1: 页级降噪 (text_cleaner.denoise_pages) -- 重复页眉页脚剔除 + 软换行合并
    CHUNK_DENOISE_ENABLED: bool = True
    # 跨页重复行被识别为页眉/页脚所需的最少出现页数
    CHUNK_HEADER_FOOTER_MIN_PAGES: int = 3
    # Page Chrome: 解析源头基于跨页重复信号过滤页眉页脚文本/图片, 并裁剪表格/OCR输入
    PAGE_CHROME_FILTER_ENABLED: bool = True
    PAGE_CHROME_MIN_REPEAT_PAGES: int = 3
    PAGE_CHROME_REPEAT_RATIO: float = 0.6
    PAGE_CHROME_CANDIDATE_BAND_RATIO: float = 0.25
    # 文档门禁(解析前要素校验): 识别非正规招标/投标文档并给出非阻断提示
    DOC_GATE_ENABLED: bool = True
    # 文档质检(Document QA): 投标审查 BID 文档内生质量检查(独立结果域)
    DOCUMENT_QA_ENABLED: bool = True
    # v3.6: 正文与括号只按紧邻续接, 最多两页; 摘录取取值行或未配括号附近
    DOCUMENT_QA_RULESET_VERSION: str = "v3.6"
    # 复核层: 提取池空或调用失败时原样保留候选, 不回落审查池
    DOCUMENT_QA_VERIFY_ENABLED: bool = True
    DOCUMENT_QA_VERIFY_CONFIDENCE: float = 0.8
    # 复核整批投给 LLM worker 后的等待上限; 超时则候选原样保留并记 degraded
    DOCUMENT_QA_VERIFY_WAIT_TIMEOUT: float = 600.0
    # 同时运行的单文档质检数(跨进程许可); 超出者退回队列, 不占处理槽位
    DOCUMENT_QA_MAX_CONCURRENT: int = 1
    DOCUMENT_QA_STRUCTURE_ENABLED: bool = True
    DOCUMENT_QA_STRUCTURE_URLS: str = ""
    DOCUMENT_QA_STRUCTURE_TIMEOUT: float = 180.0
    DOCUMENT_QA_STRUCTURE_FAIL_THRESHOLD: int = 2
    DOCUMENT_QA_STRUCTURE_COOLDOWN_SEC: float = 60.0
    # 超大文档跳过 span 级取证(仅文本级检查并记 degraded), 防止 fitz dict 拖垮解析池
    DOCUMENT_QA_MAX_SPAN_PAGES: int = 800
    # 水印预过滤(根治层): 解析源头按"信息恒定+多页重复+弱显示属性"剔除水印字符/图片
    WATERMARK_FILTER_ENABLED: bool = True
    WATERMARK_MIN_REPEAT_PAGES: int = 3
    WATERMARK_REPEAT_RATIO: float = 0.6
    # P2: 向量化前在 chunk 文本前注入 section_path 上下文块头, 不污染存储 content
    VECTOR_CONTEXT_HEADER_ENABLED: bool = True
    # 默认仍以 asyncio 为主，确认独立 worker 已部署后再显式开启 ARQ。
    VECTOR_PREFER_ARQ: bool = False
    # GPU 设备级冷却/熔断（encode_gate.GpuDeviceCooldownController）
    # bs 降到下限仍 stall/OOM 或 orphan 达阈值时触发；冷却期内后续批次全部走 CPU
    VECTOR_GPU_COOLDOWN_SEC: float = 60.0
    VECTOR_GPU_COOLDOWN_MAX_SEC: float = 300.0
    VECTOR_GPU_COOLDOWN_ORPHAN_COUNT: int = 2
    # 任务级降级粘滞：任务内一旦发生 CPU fallback，是否让同任务剩余批次全部走 CPU
    VECTOR_TASK_STICK_TO_CPU_AFTER_FALLBACK: bool = True

    # ARQ worker 优雅关闭窗口 
    # WORKER_JOB_COMPLETION_WAIT_SEC: 收到 SIGINT/SIGTERM 后, ARQ 等 asyncio.Task
    #   完成的窗口; 超时后 ARQ 会 task.cancel(). K8s 部署需保证
    #   terminationGracePeriodSeconds >= 此值 + ENCODE_SHUTDOWN_GRACE_SEC + 5s
    # ENCODE_SHUTDOWN_GRACE_SEC: shutdown 钩子内额外等 encode in-flight 完成的窗口;
    #   超时后强制 cancel 排队 future + shutdown executors. 已运行的 thread 仍由
    #   Python atexit 自然等待 (Python ThreadPoolExecutor 标准限制)
    WORKER_JOB_COMPLETION_WAIT_SEC: int = 30
    ENCODE_SHUTDOWN_GRACE_SEC: float = 15.0

    # GPU AIMD 耗时感知软降级（encode_gate.EmbeddingBatchSizeController）
    # 在原有 stall/OOM 触发降档之外，增加一道基于 elapsed_ms 与历史 p95 对比的
    # 早期预警；解决"非超时型慢化"对 AIMD 的盲区（典型场景：长短文本混批使
    # 单批耗时数倍于历史均值，但仍在 stall 阈值内，控制器会继续升档放大抖动）
    # 默认关闭，灰度开启；阈值保守，最坏情况一键关闭即可回退到现状
    VECTOR_GPU_AIMD_SOFT_DEGRADE_ENABLED: bool = False
    VECTOR_GPU_AIMD_SOFT_DEGRADE_MIN_SAMPLES: int = 20
    VECTOR_GPU_AIMD_SOFT_DEGRADE_PAUSE_RATIO: float = 2.0
    VECTOR_GPU_AIMD_SOFT_DEGRADE_TRIGGER_RATIO: float = 3.0
    VECTOR_GPU_AIMD_SOFT_DEGRADE_COOLDOWN_SEC: float = 30.0
    VECTOR_GPU_AIMD_SOFT_DEGRADE_STEP: int = 2
    VECTOR_GPU_AIMD_ELAPSED_WINDOW_SIZE: int = 60

    # GPU AIMD batch_size 上限与升档步长（针对显存余量灵活调优）
    # 默认值与原硬编码保持一致；显存大的卡（如 A10 24GB）可通过 env 覆盖：
    #   VECTOR_GPU_AIMD_MAX_BS=64        # 上限提升 → 吞吐峰值更高
    #   VECTOR_GPU_AIMD_INCREASE_STEP=4  # 升档步长加大 → 更快达到峰值
    # 使用约束：MIN(=4) ≤ INCREASE_STEP ≤ MAX_BS；越界值在控制器初始化时
    # 会被 clamp 并写日志，不会让进程崩溃
    VECTOR_GPU_AIMD_MAX_BS: int = 32
    VECTOR_GPU_AIMD_INCREASE_STEP: int = 2
    # GPU AIMD 升档水位门控: 只约束 "是否允许继续升 batch_size",
    # 不触发 CPU fallback / device cooldown。阈值按显存占用比例配置, 适配不同显存规格。
    VECTOR_GPU_AIMD_WATERMARK_ENABLED: bool = True
    VECTOR_GPU_AIMD_MAX_USED_RATIO: float = 0.90
    VECTOR_GPU_AIMD_WATERMARK_ON_SNAPSHOT_MISSING: str = "allow"

    # ==================== GPU Admission Control ====================
    # 不降低业务并发/批量预算；只在进入 CUDA 临界区前按显存余量和跨进程 inflight 决策。
    GPU_ADMISSION_ENABLED: bool = True
    GPU_ADMISSION_NVML_REQUIRED: bool = False
    GPU_ADMISSION_MIN_FREE_MB: int = 512
    GPU_ADMISSION_ESTIMATE_BASE_MB: int = 256
    GPU_ADMISSION_ESTIMATE_PER_1K_TOKENS_MB: int = 128
    GPU_ADMISSION_PERMIT_LIMIT: int = 1
    # GPU 许可独立 TTL: 临界区是秒级单批 encode, 持有期间心跳续期;
    # 进程被杀留下的计数按此过期, 不沿用 WORKER_RESOURCE_PERMIT_TTL 的小时级.
    GPU_ADMISSION_PERMIT_TTL_SEC: int = 300
    # 等的是别的进程的一个 CUDA 临界区 (单批 encode, 秒级), 不是等显存;
    # 显存不足仍走 `insufficient_vram` 立即回退。
    # 默认 10s: API 同步路径 (知识库/审查检索 safe_encode 单批查询) 不能被卡死; 进程间争用给足余量。
    GPU_ADMISSION_WAIT_TIMEOUT_SEC: float = 10.0
    # 进入前设备利用率不超过该值, 且空闲显存够本次进入, 说明许可计数
    # 没有对应的设备占用. 此时不等待窗口, 直接回收计数再进 GPU.
    # 真实 encode 的利用率远高于该值, 那种争用仍排队等待.
    GPU_ADMISSION_IDLE_RECLAIM_ENABLED: bool = True
    GPU_ADMISSION_IDLE_UTIL_MAX: int = 10
    GPU_ADMISSION_PROBE_MIN_FREE_MB: int = 2048
    GPU_ADMISSION_REPEATED_FAIL_THRESHOLD: int = 3

    # ==================== 标书编写配置 ====================
    WRITING_CHAPTER_CONCURRENCY: int = 1
    WRITING_MAX_MATERIAL_SIZE_MB: int = 200
    WRITING_SUPPORTED_MATERIAL_TYPES: str = ".pdf,.docx,.doc,.xlsx,.xls,.jpg,.jpeg,.png,.bmp"
    WRITING_DEFAULT_FONT_BODY: str = "仿宋"
    WRITING_DEFAULT_FONT_HEADING: str = "黑体"
    WRITING_DEFAULT_LINE_SPACING: float = 1.5
    WRITING_MAX_SECTION_TOKENS: int = 4000
    WRITING_CONTEXT_BUDGET_TOKENS: int = 12000

    # W1: free_writing 章节长度闭环（生成后字数校验 + token 上限联动）
    # max_tokens 计算公式 ≈ target_word_count * tokens_per_char + safety_buffer，
    # 中文 1.3~1.6 token/char，留 800 token 余量供 HTML 标签。
    WRITING_SECTION_TOKENS_PER_CHAR: float = 1.5
    WRITING_MAX_SECTION_TOKENS_CAP: int = 8192
    # 生成后纯文本字数 / target_word_count 比例容忍区间；
    # MAX_RATIO 是动态上限 cap，短中篇 free_writing 可放宽，长章节逐档收敛。
    # 超出区间触发一次定向重生成。设 MIN_RATIO=0 即关闭闭环（回滚开关）。
    WRITING_WORD_COUNT_MIN_RATIO: float = 0.6
    WRITING_WORD_COUNT_MAX_RATIO: float = 2.0

    # W2: free_writing 章节生成后安全改写开关（我们→我方 + 段首套话删除）。
    # 关闭时仅保留套话 metrics 计数与 Gate warn，不改写正文。
    WRITING_DE_AI_REWRITE_ENABLED: bool = True
    # 全文生成 Judge 延后到阶段3结束后有界 drain；关闭则逐章内联 await（回滚）。
    WRITING_JUDGE_DEFER_ENABLED: bool = True
    # SectionPlan 预取：阶段1/2期间并行生成计划，阶段3按指纹复用。关闭即回退组内串行规划。
    WRITING_PLAN_PREFETCH_ENABLED: bool = True
    WRITING_PLAN_PREFETCH_CONCURRENCY: int = 8
    # 写标提取预取：统一提取期间并行收集评分权威与目录规格。关闭即回退全串行。
    WRITING_EXTRACT_PREFETCH_ENABLED: bool = True
    # P1: SectionPlan 章节论证计划。默认开启, 灰度开启后仅影响 free_writing。
    WRITING_SECTION_PLAN_ENABLED: bool = True
    WRITING_SECTION_PLAN_RATIO: float = 1.0
    # 不得短于单次云端 HTTP。compose_free 再以 max(本项, CLOUD_TIMEOUT) 兜底。
    WRITING_SECTION_PLAN_TIMEOUT_SEC: int = 300
    WRITING_SECTION_PLAN_LEASE_SEC: int = 180

    # 章节画像分批产出与硬完整契约（灰度可回退）。
    # BATCH_SIZE: 每批叶子数；<=0 表示整卷一批（兼容旧行为）。
    # ENFORCE_COVERAGE: True 时批内有缺即补齐（不再使用 coverage>=0.5 死区）。
    # MAX_SUPPLEMENT_ROUNDS: 单批最多补齐轮数，防止 LLM 循环。
    WRITING_CHAPTER_PROFILE_BATCH_SIZE: int = 12
    WRITING_CHAPTER_PROFILE_ENFORCE_COVERAGE: bool = True
    WRITING_CHAPTER_PROFILE_MAX_SUPPLEMENT_ROUNDS: int = 2
    # 章节裁决候选封顶与分批，避免一次回显上百条撞预算
    WRITING_CHAPTER_JUDGE_BATCH_SIZE: int = 20
    WRITING_CHAPTER_JUDGE_MAX_CANDIDATES: int = 40

    # P4: 事实型图示(流程图/甘特图)。默认开启, 仅作用于 free_writing + SectionPlan 成功路径。
    WRITING_SECTION_DIAGRAM_ENABLED: bool = True
    WRITING_SECTION_DIAGRAM_RATIO: float = 1.0
    WRITING_SECTION_DIAGRAM_MAX_PER_SECTION: int = 2
    WRITING_SECTION_DIAGRAM_DOC_MAX_TOTAL: int = 24
    WRITING_SECTION_DIAGRAM_TIMEOUT_SEC: int = 30

    # 约束去重
    WRITING_DEDUP_BATCH_SIZE: int = 20
    WRITING_DEDUP_MAX_TOKENS: int = 3000
    WRITING_DEDUP_JSON_RETRY: int = 1
    WRITING_DEDUP_BATCH_CONCURRENCY: int = 3

    # 约束写作可操作性过滤（提取后、去重前的规则兜底，剔除纯行为性废标条款）
    WRITING_WRITABILITY_FILTER_ENABLED: bool = True

    # 写标 matcher 判定为 irrelevant 的约束折叠为 reference, 而非静默丢弃
    WRITING_IRRELEVANT_FOLD_ENABLED: bool = True

    # 写作引导类约束未匹配到章节时, 作为全局 highlight 指令兜底注入
    WRITING_GUIDE_FALLBACK_ENABLED: bool = True

    # 写标提取后约束消费分级: direct/reference, LLM失败时保守全部direct
    WRITING_RELEVANCE_PASS_ENABLED: bool = True

    # 写标废标条款双池: 格式/签章/密封类红线仅走一致性检查, 不进章节匹配
    WRITING_DISQ_CHECK_ONLY_ENABLED: bool = True

    # 写标评分项权威提取（用审查侧评分表结构化解析替换 LLM 自由提取的 scoring_criterion）
    WRITING_SCORING_AUTHORITY_ENABLED: bool = True

    # 写标散落评分条目关联收口（将其它标签下与权威评分项等价的条目双向关联，不搬家）
    WRITING_SCORING_LINK_ENABLED: bool = True

    # 写标评分项响应专项检查（末端一致性：未响应 high / 占位 medium / 非正文响应 info 三级）
    WRITING_SCORING_RESPONSE_CHECK_ENABLED: bool = True

    # ==================== OCR 配置 ====================
    OCR_ENABLED: bool = True              # OCR 总开关
    OCR_SERVICE_URL: str = ""             # OCR sidecar 服务地址 (空=本地进程内 CPU OCR)
    OCR_HTTP_TIMEOUT: float = 120.0       # OCR HTTP 调用超时（秒）
    OCR_HTTP_MAX_CONCURRENCY: int = 1     # OCR sidecar 客户端并发上限（单实例Paddle默认串行）
    OCR_QUEUE_WARN_MS: int = 5000         # OCR 客户端排队超过该阈值时打印告警
    OCR_CONCURRENCY_INITIAL: int = 1      # OCR AIMD 初始并发
    OCR_CONCURRENCY_MIN: int = 1          # OCR AIMD 最小并发
    OCR_CONCURRENCY_MAX: int = 3          # OCR AIMD 最大并发（单卡先保守）
    OCR_CONCURRENCY_WINDOW_SIZE: int = 12  # OCR AIMD 滑动窗口大小
    OCR_CONCURRENCY_INCREASE_COOLDOWN: int = 10  # OCR 并发增加冷却时间（秒）
    OCR_CONCURRENCY_LATENCY_CEILING_MS: int = 15000  # OCR 执行耗时上限
    OCR_CONCURRENCY_QUEUE_WAIT_CEILING_MS: int = 8000  # OCR 排队等待上限
    OCR_CONFIDENCE_THRESHOLD: float = 0.75  # 置信度阈值，低于此值回退 VL
    OCR_MIN_TEXT_LENGTH: int = 20         # 最小有效文字长度，低于此值回退 VL
    OCR_LANG: str = "ch"                  # 识别语言 (ch=中英混合)
    OCR_VERSION: str = "PP-OCRv4"         # OCR 模型版本 (PP-OCRv4/PP-OCRv5/PP-OCRv6)；sidecar 模式下由各 compose 的 OCR_VERSION/模型名覆盖
    OCR_ROUTE_DEGRADE_QUEUE_WAIT_MS: int = 8000  # OCR 排队 p95 超过此值时中优先级图片降级到 VL
    OCR_BUDGET_PER_BATCH: int = 50        # 每批次最多允许多少张图片尝试 OCR（0=不限制，高优先级不受限）
    OCR_STEAL_BACK_WAIT_SEC: float = 3.0  # 竞争路由中等待 OCR 拥堵恢复的超时秒数
    OCR_CONGESTION_STALE_SEC: int = 30    # OCR 拥堵判定陈旧度护栏：历史 queue p95 超过此秒数未更新即视为失效，防止单次尖峰永久钉死降级路径
    OCR_CONTENDED_MAX_SHARE_RATIO: float = 0.6  # 多流程争用时单流程软份额上限（支持空闲借用）
    IMAGE_LLM_WAIT_TIMEOUT: float = 600.0  # 通用文档图片 LLM 子任务等待超时
    IMAGE_LLM_POLL_INTERVAL: float = 0.5   # 通用文档图片 LLM 子任务结果轮询间隔
    IMAGE_LLM_RESULT_TTL: int = 3600       # 通用文档图片 LLM 子任务结果 Redis TTL
    IMAGE_LLM_FULL_PAGE_BATCH_SIZE: int = 8  # 整页扫描 VL 子任务批大小，避免 OCR 降级后单页 job 洪峰
    WRITING_IMAGE_LLM_WAIT_TIMEOUT: float = 600.0  # 写标图片 LLM 子任务等待超时
    WRITING_IMAGE_LLM_POLL_INTERVAL: float = 0.5   # 写标图片 LLM 子任务结果轮询间隔
    WRITING_IMAGE_LLM_RESULT_TTL: int = 3600       # 写标图片 LLM 子任务结果 Redis TTL
    OCR_SERVICE_URLS: str = ""            # 多 OCR 实例地址列表，逗号分隔（二期扩容用，空=使用 OCR_SERVICE_URL 单实例）
    OCR_POOL_HEALTH_INTERVAL: int = 30   # OCR 实例池健康检查间隔（秒），不健康实例按此间隔尝试恢复
    # 写标生成阶段 token 预算估算系数 (仅用于预占补差额判断):
    # 按字数估算(有目录时), 默认值按本地 billing_usages 写标样本校准
    WRITING_COMPOSE_EST_INPUT_PER_WORD: float = 25.0
    WRITING_COMPOSE_EST_OUTPUT_PER_WORD: float = 8.0
    # 按页数回退估算(无目录/字数为零时)
    WRITING_COMPOSE_EST_INPUT_PER_PAGE: int = 6000
    WRITING_COMPOSE_EST_OUTPUT_PER_PAGE: int = 4000

    # ==================== 邮箱验证 / 反多账号 ====================
    # 邮箱验证总开关: 开启后新用户必须先验证邮箱才能登录。
    EMAIL_VERIFICATION_ENABLED: bool = False
    EMAIL_VERIFY_TOKEN_TTL_HOURS: int = 24
    # 密码重置令牌有效期(分钟); 重置链路与邮箱验证开关无关, 配好 SMTP 即可用
    PASSWORD_RESET_TOKEN_TTL_MINUTES: int = 30
    # 前端基址, 用于拼接验证链接 {FRONTEND_BASE_URL}/verify-email?token=...
    FRONTEND_BASE_URL: str = "http://localhost:3000"
    # 一次性邮箱域名补充(逗号分隔), 追加到内置黑名单
    DISPOSABLE_EMAIL_EXTRA: str = ""

    # SMTP 发信(用于邮箱验证); 关键项(host/user/password)任一为空视为未配置,
    # 发送降级为仅记录日志, 不抛错, 便于灰度暗部署。
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""              # 发件人地址, 留空则回退 SMTP_USER
    SMTP_FROM_NAME: str = "BidMind"
    SMTP_USE_SSL: bool = True        # 465 端口用 SSL
    SMTP_USE_TLS: bool = False       # 587 端口用 STARTTLS 时设 True 并关闭 SMTP_USE_SSL

    # ==================== 串标审查配置 ====================
    # 文件限制
    COLLUSION_MAX_BID_DOCS: int = 15  # 投标文件最大数量
    COLLUSION_MIN_BID_DOCS: int = 2   # 投标文件最小数量

    # 向量相似度阈值（用于预筛选分级）
    COLLUSION_SIMILARITY_HIGH: float = 0.7   # 高相似度阈值 → 完整8维度分析
    COLLUSION_SIMILARITY_MEDIUM: float = 0.4  # 中相似度阈值 → 简化4维度分析
    # 低于 COLLUSION_SIMILARITY_MEDIUM → 仅算法对比（D1/D2/D8）
    COLLUSION_TENDER_WHITELIST_THRESHOLD: float = 0.7  # 招标白名单过滤阈值
    # 喂给 LLM / 落库展示的"高相似 chunk 片段对"入选下限。
    # 与 COLLUSION_SIMILARITY_MEDIUM（对级 LLM 分流闸门）解耦：分流仍用 0.4，
    # 但单片段证据下限抬到 0.6，避免最近邻硬配对（0.4 偏弱）诱发 LLM 脑补雷同。
    COLLUSION_CHUNK_PAIR_MIN_SCORE: float = 0.6

    # 智能筛对（文件数>5时启用，减少LLM调用量）
    COLLUSION_MAX_LLM_PAIRS: int = 30           # 智能筛对后最大LLM分析对数
    COLLUSION_SMART_FILTER_THRESHOLD: float = 0.3  # 文件数>5时进入LLM的最低相似度

    # 各阶段超时（秒）：云端模型
    COLLUSION_EXTRACT_TIMEOUT_CLOUD: int = 180   # S1信息提取超时（云端）
    COLLUSION_COMPARE_TIMEOUT_CLOUD: int = 300   # S3深度对比超时（云端）
    COLLUSION_SYNTHESIS_TIMEOUT_CLOUD: int = 180  # S4综合研判超时（云端）
    COLLUSION_GROUP_TIMEOUT_CLOUD: int = 180      # S4群组研判单次LLM超时（云端）

    # 各阶段超时（秒）：本地模型
    COLLUSION_EXTRACT_TIMEOUT_LOCAL: int = 300   # S1信息提取超时（本地）
    COLLUSION_COMPARE_TIMEOUT_LOCAL: int = 600   # S3深度对比超时（本地）
    COLLUSION_SYNTHESIS_TIMEOUT_LOCAL: int = 300  # S4综合研判超时（本地）
    COLLUSION_GROUP_TIMEOUT_LOCAL: int = 300      # S4群组研判单次LLM超时（本地）

    # 进度停滞检测阈值（比投标审查宽松）
    COLLUSION_STALL_TIMEOUT: int = 300

    # ==================== 串标审查 修复迭代灰度开关 ====================
    # P0-1: 群组研判 LLM 消费 S3.6 融合证据（fused_evidence）
    COLLUSION_FUSED_EVIDENCE_TO_LLM: bool = False
    COLLUSION_FUSED_EVIDENCE_TO_LLM_RATIO: float = 0.0  # 0.0~1.0 按 project_id 哈希灰度

    # P0-2/P0-3: 项目级综合注入 global_findings 穿透 D4/D8
    COLLUSION_GLOBAL_FINDINGS_ENRICH: bool = False
    COLLUSION_GLOBAL_FINDINGS_ENRICH_RATIO: float = 0.0

    # P1-1: D4 报价异常全链路覆盖（筛对 + 算法对比）
    COLLUSION_D4_FULL_COVERAGE: bool = False
    COLLUSION_D4_FULL_COVERAGE_RATIO: float = 0.0

    # P2-1: parser 补采 docx_structure 挂到 Document.metadata
    COLLUSION_DOCX_STRUCTURE_ENABLED: bool = False

    # P2-2: D6FormatAlgorithmicGate 严格模式（结构缺失时下发 certainty_cap）
    COLLUSION_D6_STRICT_MODE: bool = False
    COLLUSION_D6_STRICT_MODE_RATIO: float = 0.0

    class Config:
        env_file = ".env"
        case_sensitive = True

    @model_validator(mode='after')
    def validate_required_settings(self) -> 'Settings':
        """验证必需的配置项

        开发模式 (LOG_LEVEL=DEBUG) 允许使用不安全的默认值并发出警告；
        其他模式下若检测到占位符默认值则阻止启动。
        """
        if not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL 未配置！请在 .env 文件中设置，例如：\n"
                "DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/bidmind_lite"
            )

        _JWT_PLACEHOLDER = "your-super-secret-key-change-in-production"
        _PWD_PLACEHOLDER = "<SET_ADMIN_PASSWORD>"
        _is_debug = self.LOG_LEVEL.upper() == "DEBUG"

        if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY == _JWT_PLACEHOLDER:
            if _is_debug:
                import secrets
                _auto_key = secrets.token_urlsafe(32)
                object.__setattr__(self, 'JWT_SECRET_KEY', _auto_key)
                logger.warning(
                    "JWT_SECRET_KEY 未设置或为占位符，已自动生成随机密钥（仅本次运行有效，"
                    "重启后已登录用户需重新登录；多Worker部署不适用）"
                )
            else:
                raise ValueError(
                    "JWT_SECRET_KEY 未设置或仍为占位符默认值，非DEBUG模式拒绝启动。\n"
                    "请在 .env 中设置: JWT_SECRET_KEY=<随机安全密钥>\n"
                    "生成方式: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )

        if not self.ADMIN_PASSWORD:
            raise ValueError(
                "ADMIN_PASSWORD 未设置，拒绝启动。\n"
                "请在 .env 中设置: ADMIN_PASSWORD=<强密码>"
            )
        if self.ADMIN_PASSWORD == _PWD_PLACEHOLDER:
            if _is_debug:
                logger.warning(
                    "ADMIN_PASSWORD 使用占位符默认值，存在安全风险！"
                    "生产部署前务必替换为强密码"
                )
            else:
                raise ValueError(
                    "ADMIN_PASSWORD 仍为占位符默认值，非DEBUG模式拒绝启动。\n"
                    "请在 .env 中设置: ADMIN_PASSWORD=<强密码>"
                )

        # 生产环境禁止 CORS 通配符与 credentials 并存(浏览器规范禁止, 且有安全风险)
        if self.ENVIRONMENT.strip().lower() == "production":
            origins = [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]
            if "*" in origins or not origins:
                raise ValueError(
                    "生产环境 CORS_ORIGINS 不得为空或包含通配符 '*'(与 allow_credentials 冲突)。\n"
                    "请在 .env 中显式配置允许的前端域名白名单。"
                )

        return self

    def is_production(self) -> bool:
        """是否为生产环境(关闭公开文档、强化校验)。"""
        return self.ENVIRONMENT.strip().lower() == "production"

    def get_cors_origins(self) -> list[str]:
        """解析CORS允许域名列表"""
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def get_embedding_model_path(self) -> str:
        """获取向量模型路径
        
        优先使用本地路径，不存在时回退到HuggingFace名称。
        """
        if os.path.exists(self.EMBEDDING_MODEL):
            return self.EMBEDDING_MODEL
        return self.EMBEDDING_MODEL_FALLBACK
    
    def is_cloud_mode(self) -> bool:
        """检查是否使用云端模式"""
        return self.AI_MODE == "cloud"
    
    def is_local_mode(self) -> bool:
        """检查是否使用本地模式"""
        return self.AI_MODE == "local"


# 全局配置实例
settings = Settings()


# 运行时AI模式切换（不修改.env文件）
_runtime_ai_mode: Optional[str] = None


def get_ai_mode() -> str:
    """获取当前AI模式
    
    优先返回运行时设置，否则返回配置文件设置。
    """
    global _runtime_ai_mode
    if _runtime_ai_mode is not None:
        return _runtime_ai_mode
    return settings.AI_MODE


def set_ai_mode(mode: Literal["local", "cloud"]) -> None:
    """设置运行时AI模式

    Args:
        mode: AI模式 (local/cloud)
    """
    global _runtime_ai_mode
    _runtime_ai_mode = mode


def get_collusion_extract_timeout() -> int:
    """获取串标信息提取超时（秒），根据AI模式自动适配"""
    if get_ai_mode() == "cloud":
        return settings.COLLUSION_EXTRACT_TIMEOUT_CLOUD
    return settings.COLLUSION_EXTRACT_TIMEOUT_LOCAL


def get_collusion_compare_timeout() -> int:
    """获取串标深度对比超时（秒），根据AI模式自动适配"""
    if get_ai_mode() == "cloud":
        return settings.COLLUSION_COMPARE_TIMEOUT_CLOUD
    return settings.COLLUSION_COMPARE_TIMEOUT_LOCAL


def get_collusion_synthesis_timeout() -> int:
    """获取串标综合研判超时（秒），根据AI模式自动适配"""
    if get_ai_mode() == "cloud":
        return settings.COLLUSION_SYNTHESIS_TIMEOUT_CLOUD
    return settings.COLLUSION_SYNTHESIS_TIMEOUT_LOCAL


def get_collusion_group_timeout() -> int:
    """获取串标群组研判单次LLM超时（秒），根据AI模式自动适配"""
    if get_ai_mode() == "cloud":
        return settings.COLLUSION_GROUP_TIMEOUT_CLOUD
    return settings.COLLUSION_GROUP_TIMEOUT_LOCAL


def get_review_concurrency() -> int:
    """获取业务层审查并发上限

 此值用于 reviewer / bid_extractor / collusion 等业务层 Semaphore，
 作为宽松的资源保护上限。真正的流量控制由 Dispatcher 内置的
 AIMD 自适应并发控制器完成。

 云端模式: channel_count * REVIEW_CONCURRENCY_CLOUD_MAX
 本地模式: 固定 REVIEW_CONCURRENCY（默认 20）: 最终返回值统一收口到 REVIEW_CONCURRENCY_DB_SAFE_CAP, 防止
 通道数膨胀使单 job 并发协程数突破 DB 连接池容量。
 """
    safe_cap = max(int(getattr(settings, "REVIEW_CONCURRENCY_DB_SAFE_CAP", 8)), 1)
    if get_ai_mode() == "cloud":
        try:
            from app.core.ai.ai_factory import get_review_dispatcher
            dispatcher = get_review_dispatcher()
            if dispatcher and dispatcher.channel_count > 0:
                raw = dispatcher.channel_count * settings.REVIEW_CONCURRENCY_CLOUD_MAX
                return min(raw, safe_cap)
        except Exception:
            pass
        return min(settings.REVIEW_CONCURRENCY_CLOUD_MAX, safe_cap)
    return min(settings.REVIEW_CONCURRENCY, safe_cap)
