"""LLM JSON 返回值形态契约守卫工具

职责: 校验 LLM JSON 返回值形态契约，按调用方期望的形态做守卫。
不做: 修改 chat_json 本体；JSON 字段语义校验；内容补全；归一化转换。

背景:
 LLMClient.chat_json 接口对外契约声明返回 dict，但其实现路径
 (_try_repair_json 返回 list | dict | None、_extract_first_json_payload
 的数组保护、原始 json.loads 解析 [...]) 实际可能返回 list。
 按 dict 假设直接 .get / **解包 的调用方，遇到 list 会抛
 AttributeError / TypeError，并被外层 except Exception 兜底为
 "未找到响应" 一类业务结论 (与 同型)。

 本模块提供调用点级别的形态契约守卫: 非 dict 返回值统一抛出
 json.JSONDecodeError, 让调用方现有的 JSON 重试链接管, 把可恢复的
 格式问题暴露为显式失败而非伪装为业务缺失。
"""

import json
from typing import Any

from loguru import logger


def require_dict_response(response: Any, *, operation_name: str) -> dict:
    """要求 LLM 返回值为 dict，否则抛 json.JSONDecodeError 触发上层重试。

    Args:
        response: chat_json 的原始返回值
        operation_name: 调用场景描述，用于日志定位 (例如 "要求 21 提取")

    Returns:
        dict: 原样返回的 dict 响应

    Raises:
        json.JSONDecodeError: 当 response 不是 dict 时抛出，错误信息包含
            实际类型与前 200 字预览，便于排查 LLM 真实输出
    """
    if isinstance(response, dict):
        return response

    type_name = type(response).__name__
    try:
        preview = json.dumps(response, ensure_ascii=False, default=str)[:200]
    except (TypeError, ValueError):
        preview = repr(response)[:200]

    logger.warning(
        "[{}] LLM 返回非 dict 形态 (got={}, preview={}), 按 JSON 格式异常处理触发重试",
        operation_name,
        type_name,
        preview,
    )
    raise json.JSONDecodeError(
        f"[{operation_name}] expected JSON object but got {type_name}",
        preview,
        0,
    )
