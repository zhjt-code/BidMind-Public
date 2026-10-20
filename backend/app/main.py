"""FastAPI 应用入口

BidMind AI Lite 后端服务主入口。
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

# Windows 兼容性补丁: starlette Config._read_file 不指定 encoding,
# Windows 默认 GBK 解码 UTF-8 含中文字符的 .env 文件会失败。
# 必须在 import slowapi 之前 monkey patch (slowapi.Limiter __init__ 会读 .env)。
def _starlette_config_read_file_utf8(self, file_name) -> dict[str, str]:
    file_values: dict[str, str] = {}
    with open(file_name, encoding="utf-8") as input_file:
        for line in input_file.readlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                file_values[key] = value
    return file_values


from starlette.config import Config as _StarletteConfig
_StarletteConfig._read_file = _starlette_config_read_file_utf8

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.core.rate_limit import limiter
from prometheus_fastapi_instrumentator import Instrumentator
import sys

from app.core.config import settings
from app.core.database import init_db, close_db
from app.core.ai_settings import init_ai_settings
from app.core.middleware import (
    RequestIdMiddleware,
    get_request_id,
    get_log_user_id,
    get_log_project_id,
    get_incident_id,
)

# CPU 预算前置应用: routers import 会传递性触发 torch / sentence_transformers,
# 必须在此之前把 OMP/MKL/ORT 线程上限写入环境变量, 否则 PyTorch OMP 池已建好,
# 后续设置不再生效。失败仅警告, 退化为系统默认线程数。
try:
    from app.core.runtime.cpu_budget import apply_env_thread_limits

    apply_env_thread_limits()
except Exception as _cpu_budget_exc:  # noqa: BLE001
    import logging as _logging

    _logging.getLogger(__name__).warning(
        f"CPU 预算环境变量应用失败 (退化为系统默认): {_cpu_budget_exc}"
    )

from app.api.routers import projects, health, files, search, requirements, reviews, document_qa, auth, users, presence, collusion, feedback, audit_logs, prompt_versions, error_logs, writing, writing_diagrams, writing_outline, knowledge_base, llm_center, messages, admin_broadcasts, admin_ops, project_timeline, ops_project_logs, llm_advisor
from app.api.routers import settings as settings_router


def _log_patcher(record: dict) -> None:
    """为每条日志注入 request_id / user_id / project_id / incident_id

    优先级约定: logger.contextualize 已写入的 extra 字段最高, ContextVar 仅在 extra 缺失时回落。
    """
    record["extra"]["request_id"] = get_request_id()
    record["extra"].setdefault("user_id", get_log_user_id())
    # project_id 同样回落 ContextVar (HTTP 路径) 或保留 contextualize 写入值 (后台任务)
    if record["extra"].get("project_id") is None:
        ctx_project_id = get_log_project_id()
        if ctx_project_id is not None:
            record["extra"]["project_id"] = ctx_project_id
    if "incident_id" not in record["extra"]:
        ctx_incident_id = get_incident_id()
        if ctx_incident_id:
            record["extra"]["incident_id"] = ctx_incident_id


logger.remove()
if settings.LOG_FORMAT == "json":
    logger.add(sys.stderr, level=settings.LOG_LEVEL, serialize=True, backtrace=False)
else:
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<dim>{extra[request_id]}</dim> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
logger.configure(patcher=_log_patcher)

from app.core.log_sink import db_log_sink  # noqa: E402
logger.add(db_log_sink, level="WARNING", format="{message}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理
    
    启动时初始化数据库连接和AI设置，关闭时释放资源。
    """
    logger.info("BidMind AI Lite 服务启动中...")
    
    # 初始化AI设置（从持久化文件加载）
    try:
        init_ai_settings()
    except Exception as e:
        logger.warning(f"AI设置初始化失败，使用默认配置: {e}")
    
    # 预构建AI调度通道 + 连通性探测
    try:
        from app.core.ai.ai_factory import init_dispatchers, probe_dispatchers
        init_dispatchers()
        await probe_dispatchers()
    except Exception as e:
        logger.warning(f"AI通道预热失败，运行时将按需初始化: {e}")
    
    # 启动时初始化数据库（允许失败）
    try:
        await init_db()
        logger.info("数据库连接已建立")
    except Exception as e:
        logger.warning(f"数据库连接失败，服务将以降级模式运行: {e}")
    
    # LLM Center: registry sync + 缓存预热 (非阻塞, 失败不影响启动)
    try:
        from app.core.ai.prompt_registry import get_registry
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            stats = await get_registry().registry_sync(db)
            logger.info(f"LLM Center Registry 同步完成: {stats}")
    except Exception as e:
        logger.warning(f"LLM Center Registry 同步失败, 不影响业务运行: {e}")

    # CPU encode 并发 AIMD 控制器: 挂载 metric handler 到 encode_gate
    try:
        from app.core.vectorizer.cpu_encode_concurrency import (
            get_cpu_encode_concurrency_controller,
        )
        get_cpu_encode_concurrency_controller()
    except Exception as e:
        logger.warning(f"CPU encode 并发控制器挂载失败 (退化为静态容量): {e}")

    # MetricsRegistry: 扫描业务 package 触发 @instrument 装饰器登记, 同步到 metric_catalog
    try:
        from app.core.database import AsyncSessionLocal
        from app.core.observability.scanner import scan_packages, upsert_catalog, publish_invalidate
        reg_size = scan_packages()
        async with AsyncSessionLocal() as db:
            stats = await upsert_catalog(db)
        await publish_invalidate()
        logger.info(f"MetricsRegistry 同步完成: registry={reg_size} catalog={stats}")
    except Exception as e:
        logger.warning(f"MetricsRegistry 同步失败, 不影响业务运行: {e}")

    # 预热 Redis 连接 (非阻塞, 失败不影响启动)
    try:
        from app.core.redis import get_redis_pool
        pool = await get_redis_pool()
        if pool:
            logger.info("Redis 连接已建立 (ARQ 模式)")
        else:
            logger.info("Redis 不可用, 使用 asyncio 任务模式")
    except Exception:
        logger.info("Redis 不可用, 使用 asyncio 任务模式")
        pool = None

    # 启动 PromptRegistry 跨进程失效订阅 (非阻塞)
    try:
        from app.core.ai.prompt_registry import get_registry
        await get_registry().start_invalidate_subscriber(pool)
    except Exception as e:
        logger.warning(f"PromptRegistry invalidate subscriber 启动失败 (不影响业务): {e}")

    # 启动 AI 设置跨进程运行时失效订阅 (模型池/优先级变更后刷新本进程 Dispatcher)
    try:
        from app.core.ai_settings import start_runtime_invalidate_subscriber
        await start_runtime_invalidate_subscriber(pool)
    except Exception as e:
        logger.warning(f"AISettings runtime invalidate subscriber 启动失败 (不影响业务): {e}")

    # 错误日志指纹自动重算 (算法版本变化时触发, 非阻塞)
    try:
        from app.services.error_log_service import ErrorLogService
        from app.core.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await ErrorLogService.auto_recompute_if_needed(db)
    except Exception as e:
        logger.warning(f"错误日志指纹自动重算失败, 不影响业务运行: {e}")

    yield

    # 停止 PromptRegistry 订阅
    try:
        from app.core.ai.prompt_registry import get_registry
        await get_registry().stop_invalidate_subscriber()
    except Exception as e:
        logger.debug(f"PromptRegistry subscriber 停止异常 (忽略): {e}")

    # 停止 AI 设置运行时失效订阅
    try:
        from app.core.ai_settings import stop_runtime_invalidate_subscriber
        await stop_runtime_invalidate_subscriber()
    except Exception as e:
        logger.debug(f"AISettings subscriber 停止异常 (忽略): {e}")

    # 先取消所有 asyncio 后台任务,避免 Ctrl+C 后 VL/解析等仍长时间运行
    try:
        from app.core.tasks import cancel_all_background_tasks
        await cancel_all_background_tasks()
    except Exception as e:
        logger.warning(f"取消后台任务时出现异常(继续关闭): {e}")

    # 关闭时清理
    try:
        from app.core.redis import close_redis_pool
        await close_redis_pool()
    except Exception:
        pass
    try:
        await close_db()
        logger.info("数据库连接已关闭")
    except Exception:
        pass
    logger.info("BidMind AI Lite 服务已停止")


# 创建 FastAPI 应用
# 生产环境关闭公开 API 文档(/docs、/redoc、/openapi.json), 收紧暴露面
_docs_enabled = not settings.is_production()
app = FastAPI(
    title="BidMind AI Lite",
    description="招标文档智能审查系统 - 通过对标招标文件审核投标文件的响应情况",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# 配置 CORS（白名单模式，允许域名通过 CORS_ORIGINS 环境变量配置）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Request ID 中间件（在 CORS 之后，每个请求注入唯一标识）
app.add_middleware(RequestIdMiddleware)

# 配置请求限流(limiter 单例见 app.core.rate_limit)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# HTTPException 处理器（保留 detail 信息）
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP异常处理器
    
    保留 HTTPException 的 detail 信息，返回给前端。
    """
    logger.warning(f"HTTP异常: {exc.status_code} - {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": "请求处理失败",
            "detail": exc.detail
        }
    )


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器
    
    捕获未处理的异常，返回统一格式的错误响应。
    """
    logger.error(f"未处理的异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "服务器内部错误",
            "detail": str(exc) if settings.LOG_LEVEL == "DEBUG" else "请联系管理员"
        }
    )


# 注册路由
app.include_router(health.router, tags=["健康检查"])
app.include_router(auth.router, prefix="/api", tags=["用户认证"])
app.include_router(users.router, prefix="/api", tags=["用户管理"])
app.include_router(presence.router, prefix="/api", tags=["在线用户状态"])
app.include_router(projects.router, prefix="/api", tags=["项目管理"])
app.include_router(files.router, prefix="/api", tags=["文件管理"])
app.include_router(search.router, prefix="/api", tags=["语义检索"])
app.include_router(requirements.router, prefix="/api", tags=["招标要求"])
app.include_router(reviews.router, prefix="/api", tags=["审查管理"])
app.include_router(document_qa.router, prefix="/api", tags=["文档质检"])
# reports.py 路由已弃用：download 与 reviews.py 重复，generate 前端未使用
app.include_router(settings_router.router, prefix="/api", tags=["系统设置"])
app.include_router(collusion.router, prefix="/api", tags=["串标审查"])
app.include_router(feedback.router, prefix="/api", tags=["用户反馈"])
app.include_router(audit_logs.router, prefix="/api", tags=["审计日志"])
app.include_router(error_logs.router, prefix="/api", tags=["错误日志"])
app.include_router(project_timeline.router, prefix="/api", tags=["项目时间线"])
app.include_router(ops_project_logs.router, prefix="/api", tags=["运维监控-项目日志"])
app.include_router(prompt_versions.router, prefix="/api", tags=["Prompt管理"])
app.include_router(writing.router, prefix="/api", tags=["标书编写"])
app.include_router(writing_diagrams.router, prefix="/api", tags=["标书编写"])
app.include_router(writing_outline.router, prefix="/api", tags=["标书编写"])
app.include_router(knowledge_base.router, prefix="/api", tags=["知识库"])
app.include_router(llm_center.router, prefix="/api", tags=["LLM Center"])
app.include_router(messages.router, prefix="/api", tags=["站内信"])
app.include_router(admin_broadcasts.router, prefix="/api", tags=["广播管理"])
app.include_router(admin_ops.router, prefix="/api", tags=["运维监控"])
app.include_router(llm_advisor.router, prefix="/api", tags=["运维监控-周度建议"])

# touch 触发 --reload 重建连接池, 让 alembic 新增列被新连接感知 (修复后必要)
# Prometheus 指标采集（始终 instrument;仅 METRICS_ENABLED=true 时暴露 /metrics 端点）
_instrumentator = Instrumentator().instrument(app)
if settings.METRICS_ENABLED:
    _instrumentator.expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/", include_in_schema=False)
async def root():
    """根路径重定向到文档"""
    return {
        "message": "BidMind AI Lite API",
        "docs": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )
