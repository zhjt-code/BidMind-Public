"""Pydantic 输出校验器

提供 LLM 输出的结构化校验功能。

支持三种输出类型:
1. 招标要求 (ExtractedRequirement)
2. 投标响应 (ExtractedBidResponse)
3. 审查结果 (ExtractedReviewResult)
"""

import json
import re
from typing import Any, Generic, Optional, TypeVar

from loguru import logger
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# 错误摘要进日志的条目上限（: 只报错误个数会让根因不可见——评分项审查因
# 输出截断导致必填字段缺失, 日志连续数月只显示"数据校验失败: 4个错误", 无法区分
# 截断与内容不合规）。只取字段路径与 Pydantic 原因, 不取 input 值以免带出文档内容。
_ERROR_DIGEST_LIMIT = 3


def _errors_digest(errors: list[dict]) -> str:
    """前若干条校验错误的"字段路径: 原因"摘要, 超出部分只计数。"""
    parts: list[str] = []
    for err in errors[:_ERROR_DIGEST_LIMIT]:
        loc = ".".join(str(item) for item in err.get("loc", ())) or "root"
        parts.append(f"{loc}: {err.get('msg', '')}")
    remaining = len(errors) - _ERROR_DIGEST_LIMIT
    if remaining > 0:
        parts.append(f"另 {remaining} 条")
    return "; ".join(parts)


class ValidationException(Exception):
    """校验异常
    
    当 LLM 输出校验失败时抛出。
    """
    
    def __init__(
        self,
        message: str,
        raw_output: str,
        errors: Optional[list[dict]] = None,
        suggestions: Optional[list[str]] = None
    ):
        super().__init__(message)
        self.message = message
        self.raw_output = raw_output
        self.errors = errors or []
        self.suggestions = suggestions or []
    
    def get_retry_hint(self) -> str:
        """生成重试提示
        
        Returns:
            str: 附加给 Prompt 的重试提示
        """
        hints = ["\n\n[校验错误，请修正以下问题:]"]
        
        for error in self.errors:
            loc = " -> ".join(str(part) for part in error.get("loc", []))
            msg = error.get("msg", "")
            hints.append(f"- {loc}: {msg}")
        
        if self.suggestions:
            hints.append("\n[修正建议:]")
            hints.extend(f"- {s}" for s in self.suggestions)
        
        return "\n".join(hints)


class OutputValidator(Generic[T]):
    """LLM 输出校验器
    
    通用的 Pydantic 模型校验器，支持 JSON 解析和重试提示生成。
    
    示例:
        >>> from app.schemas.review import ExtractedReviewResult
        >>> validator = OutputValidator(ExtractedReviewResult)
        >>> result = validator.validate(raw_json_string)
        >>> if result.success:
        >>>     data = result.data
    """
    
    def __init__(self, model_class: type[T]):
        """初始化校验器
        
        Args:
            model_class: Pydantic 模型类
        """
        self.model_class = model_class
    
    def validate(self, raw_output: str) -> "ValidationResult[T]":
        """校验 LLM 输出
        
        Args:
            raw_output: LLM 原始输出字符串
            
        Returns:
            ValidationResult: 校验结果
        """
        # 1. 提取 JSON
        json_str = self._extract_json(raw_output)
        if not json_str:
            return ValidationResult(
                success=False,
                data=None,
                error=ValidationException(
                    message="无法从输出中提取有效的JSON",
                    raw_output=raw_output,
                    errors=[{"loc": ["root"], "msg": "JSON提取失败"}],
                    suggestions=[
                        "请确保输出仅包含JSON格式的数据",
                        "不要在JSON前后添加额外的文字说明",
                        "确保JSON语法正确（引号、逗号、括号匹配）"
                    ]
                )
            )
        
        # 2. 解析 JSON
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ValidationResult(
                success=False,
                data=None,
                error=ValidationException(
                    message=f"JSON解析失败: {e.msg}",
                    raw_output=raw_output,
                    errors=[{
                        "loc": ["root", f"position_{e.pos}"],
                        "msg": f"JSON语法错误: {e.msg}"
                    }],
                    suggestions=[
                        "检查JSON语法是否正确",
                        "确保所有字符串使用双引号",
                        "确保没有尾随逗号",
                        f"错误位置附近: {raw_output[max(0, e.pos-20):e.pos+20]}"
                    ]
                )
            )
        
        # 3. Pydantic 校验
        try:
            validated = self.model_class.model_validate(data)
            return ValidationResult(success=True, data=validated, error=None)
            
        except ValidationError as e:
            errors = []
            suggestions = []
            
            for err in e.errors():
                errors.append({
                    "loc": err["loc"],
                    "msg": err["msg"],
                    "type": err["type"]
                })
                
                # 生成修正建议
                suggestion = self._generate_suggestion(err)
                if suggestion:
                    suggestions.append(suggestion)
            
            return ValidationResult(
                success=False,
                data=None,
                error=ValidationException(
                    message=(
                        f"数据校验失败: {len(errors)}个错误"
                        f" [{_errors_digest(errors)}]"
                    ),
                    raw_output=raw_output,
                    errors=errors,
                    suggestions=suggestions
                )
            )
    
    def _extract_json(self, raw_output: str) -> Optional[str]:
        """从原始输出中提取 JSON
        
        支持多种格式:
        1. 纯 JSON
        2. markdown 代码块包裹
        3. 文字 + JSON 混合
        
        Args:
            raw_output: 原始输出字符串
            
        Returns:
            Optional[str]: 提取的 JSON 字符串
        """
        text = raw_output.strip()
        
        # 尝试1: 直接解析
        if text.startswith(("{", "[")):
            return text
        
        # 尝试2: 提取 markdown 代码块
        code_block_pattern = r"```(?:json)?\s*\n?([\s\S]*?)\n?```"
        matches = re.findall(code_block_pattern, text, re.IGNORECASE)
        if matches:
            return matches[0].strip()
        
        # 尝试3: 提取 JSON 对象/数组
        json_pattern = r"(\{[\s\S]*\}|\[[\s\S]*\])"
        matches = re.findall(json_pattern, text)
        if matches:
            # 返回最长的匹配（最可能是完整JSON）
            return max(matches, key=len)
        
        return None
    
    def _generate_suggestion(self, error: dict) -> Optional[str]:
        """根据错误类型生成修正建议
        
        Args:
            error: Pydantic 错误字典
            
        Returns:
            Optional[str]: 修正建议
        """
        err_type = error.get("type", "")
        loc = " -> ".join(str(part) for part in error.get("loc", []))
        
        # 类型错误
        if err_type == "string_type":
            return f"字段 '{loc}' 必须是字符串类型"
        elif err_type == "int_type":
            return f"字段 '{loc}' 必须是整数类型"
        elif err_type == "bool_type":
            return f"字段 '{loc}' 必须是布尔类型 (true/false)"
        elif err_type == "list_type":
            return f"字段 '{loc}' 必须是数组类型"
        
        # 值限制
        elif err_type == "literal_error":
            return f"字段 '{loc}' 的值不在允许范围内，请检查可选值"
        elif err_type in ("greater_than_equal", "less_than_equal"):
            return f"字段 '{loc}' 的数值超出允许范围"
        elif err_type == "string_too_short":
            return f"字段 '{loc}' 的字符串太短"
        elif err_type == "string_too_long":
            return f"字段 '{loc}' 的字符串太长"
        
        # 必填字段
        elif err_type == "missing":
            return f"缺少必填字段 '{loc}'"
        
        return None


class ValidationResult(Generic[T]):
    """校验结果
    
    封装校验成功/失败的结果。
    """
    
    def __init__(
        self,
        success: bool,
        data: Optional[T],
        error: Optional[ValidationException]
    ):
        """初始化校验结果
        
        Args:
            success: 是否成功
            data: 校验后的数据（成功时）
            error: 校验异常（失败时）
        """
        self.success = success
        self.data = data
        self.error = error
    
    def unwrap(self) -> T:
        """获取校验后的数据
        
        Returns:
            T: 校验后的数据
            
        Raises:
            ValidationException: 校验失败时抛出
        """
        if not self.success or self.data is None:
            raise self.error or ValidationException(
                message="校验失败",
                raw_output="",
                errors=[{"loc": ["root"], "msg": "未知错误"}]
            )
        return self.data
    
    def get_retry_hint(self) -> str:
        """获取重试提示
        
        Returns:
            str: 重试提示（失败时）或空字符串（成功时）
        """
        if self.error:
            return self.error.get_retry_hint()
        return ""


# 预定义的校验器实例
def get_requirement_validator():
    """获取招标要求校验器"""
    from app.schemas.requirement import ExtractedRequirement
    return OutputValidator(ExtractedRequirement)


def get_requirement_list_validator():
    """获取招标要求列表校验器"""
    from app.schemas.requirement import ExtractedRequirementList
    return OutputValidator(ExtractedRequirementList)


def get_bid_response_validator():
    """获取投标响应校验器"""
    from app.schemas.bid_response import ExtractedBidResponse
    return OutputValidator(ExtractedBidResponse)


def get_review_result_validator():
    """获取审查结果校验器"""
    from app.schemas.review import ExtractedReviewResult
    return OutputValidator(ExtractedReviewResult)


def get_review_judgement_validator():
    """获取审查判断载荷校验器（最小输出契约）"""
    from app.schemas.review import ReviewJudgementPayload
    return OutputValidator(ReviewJudgementPayload)


async def validate_with_retry(
    llm_call: Any,
    validator: OutputValidator[T],
    max_retries: int = 3,
    base_prompt: str = ""
) -> T:
    """带重试的校验流程
    
    封装 LLM 调用 + Pydantic 校验 + 自动重试的完整流程。
    
    Args:
        llm_call: 异步 LLM 调用函数 (接受 prompt 参数)
        validator: 输出校验器
        max_retries: 最大重试次数
        base_prompt: 基础 Prompt
        
    Returns:
        T: 校验后的数据
        
    Raises:
        ValidationException: 多次重试后仍失败
        
    示例:
        >>> async def call_llm(prompt):
        >>>     return await vllm_client.chat(prompt)
        >>> 
        >>> validator = get_review_result_validator()
        >>> result = await validate_with_retry(call_llm, validator, base_prompt="...")
    """
    current_prompt = base_prompt
    last_error: Optional[ValidationException] = None
    
    for attempt in range(max_retries):
        try:
            # 调用 LLM
            raw_output = await llm_call(current_prompt)
            
            # 校验输出
            result = validator.validate(raw_output)
            
            if result.success:
                logger.debug(f"校验成功 (attempt {attempt + 1})")
                return result.unwrap()
            
            # 校验失败，准备重试
            last_error = result.error
            retry_hint = result.get_retry_hint()
            current_prompt = base_prompt + retry_hint
            
            logger.warning(
                f"校验失败 (attempt {attempt + 1}/{max_retries}): "
                f"{last_error.message if last_error else '未知错误'}"
            )
            
        except Exception as e:
            logger.error(f"LLM调用异常 (attempt {attempt + 1}): {e}")
            last_error = ValidationException(
                message=f"LLM调用失败: {str(e)}",
                raw_output="",
                errors=[{"loc": ["llm_call"], "msg": str(e)}]
            )
    
    # 所有重试都失败
    raise last_error or ValidationException(
        message="校验失败，已达最大重试次数",
        raw_output="",
        errors=[{"loc": ["root"], "msg": "未知错误"}]
    )
