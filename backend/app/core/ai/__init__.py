"""AI 调度模块

提供 LLM 调用、Prompt 管理、审查流程编排和输出校验功能。
支持本地模型（Ollama）和云端模型（OpenAI/DeepSeek等）。
支持按用途独立获取客户端（审查模型 / 视觉模型）。
"""

from app.core.ai import task_prompt_snapshot
from app.core.ai.ai_factory import (
    AIClientManager,
    LLMClient,
    get_ai_manager,
    get_available_modes,
    get_client_by_config_id,
    get_extract_client,
    get_extract_dispatcher,
    get_judge_client,
    get_judge_dispatcher,
    get_llm_client,
    get_review_client,
    get_review_dispatcher,
    get_vision_client,
    get_vision_dispatcher,
    init_dispatchers,
    probe_dispatchers,
    reset_config_clients,
)
from app.core.ai.cloud_client import (
    CloudClient,
    get_cloud_client,
    get_local_client,
    reset_cloud_client,
    reset_local_client,
)
from app.core.ai.dispatcher import AIDispatcher
from app.core.ai.input_guard import (
    is_input_too_long_error,
    retry_on_input_too_long_400,
)
from app.core.ai.reviewer import ReviewOrchestrator, get_review_orchestrator


async def release_project_binding(project_id: int) -> None:
    """释放项目在所有 dispatcher 中的模型组绑定（任务完成/失败时调用）"""
    for dispatcher in (
        get_review_dispatcher(),
        get_vision_dispatcher(),
        get_judge_dispatcher(),
        get_extract_dispatcher(),
    ):
        if dispatcher:
            await dispatcher.release_project_binding(project_id)
from app.core.ai.validators import (
    OutputValidator,
    ValidationException,
    ValidationResult,
    get_bid_response_validator,
    get_requirement_list_validator,
    get_requirement_validator,
    get_review_result_validator,
    validate_with_retry,
)

__all__ = [
    # 统一LLM客户端
    "CloudClient",
    "LLMClient",
    "get_cloud_client",
    "get_local_client",
    "reset_cloud_client",
    "reset_local_client",
    # 统一客户端工厂
    "get_llm_client",
    "get_client_by_config_id",
    "get_review_client",
    "get_vision_client",
    "get_judge_client",
    "get_extract_client",
    "reset_config_clients",
    "get_available_modes",
    "AIClientManager",
    "get_ai_manager",
    "task_prompt_snapshot",
    # 通道预热
    "init_dispatchers",
    "probe_dispatchers",
    # 调度器
    "AIDispatcher",
    "is_input_too_long_error",
    "retry_on_input_too_long_400",
    "get_review_dispatcher",
    "get_vision_dispatcher",
    "get_judge_dispatcher",
    "get_extract_dispatcher",
    "release_project_binding",
    # 审查编排器
    "ReviewOrchestrator",
    "get_review_orchestrator",
    # 输出校验器
    "OutputValidator",
    "ValidationResult",
    "ValidationException",
    "validate_with_retry",
    "get_requirement_validator",
    "get_requirement_list_validator",
    "get_bid_response_validator",
    "get_review_result_validator",
]
