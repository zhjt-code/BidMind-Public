"""模型能力档案 (P0-C)

职责: 定义模型能力的三态模型(declared/observed/effective)与收紧优先解析
不做: 不发网络请求、不选择通道、不持久化(持久化归 ai_capability_store)

放置说明: 位于 app/core 顶层(而非 app/core/ai 包内), 因为 ai_settings 需要
引用本模块, 而 app.core.ai.__init__ 反向依赖 ai_settings, 放包内会循环导入。

三态语义:
- declared: 管理员在模型配置中声明(默认 auto 不声明);
- observed: 能力探测(health_probe)的实测结果(带时间戳);
- effective: 解析结果, 收紧优先——任一侧 unsupported 即 unsupported,
  数值型取两侧较小值; 都缺省时用安全默认。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

TriState = Literal["auto", "supported", "unsupported"]
ThinkingPolicy = Literal["auto", "disabled", "enabled"]
ReasoningEffort = Literal["default", "low", "medium", "high", "max"]
# chat直传PDF方言: auto=未探测(不参与PDF救援) / none=实测不支持 /
# openai_file=消息内type:"file"文件块 / ark_file_url=方舟type:"file_url" /
# dashscope_fileid=百炼Files API+fileid引用 / extract_text=Files API解析文本回填(Kimi/阶跃式)
PdfFileInputDialect = Literal[
    "auto", "none", "openai_file", "ark_file_url", "dashscope_fileid", "extract_text"
]


class ObservedCapability(BaseModel):
    """一次能力探测的实测结论"""

    ok: bool
    probed_at: str = Field(default="", description="ISO 时间戳")
    evidence: str = Field(default="", description="失败原因/证据摘要(截断)")


class ModelCapabilityProfile(BaseModel):
    """单个模型配置的能力档案(挂载在 ModelConfigItem.capabilities)"""

    # ---- 数值上限(None=未声明, 不主动裁剪) ----
    context_window_tokens: Optional[int] = Field(
        default=None, ge=1024, description="上下文窗口(输入+输出)总 token 上限",
    )
    max_output_tokens: Optional[int] = Field(
        default=None, ge=256, description="单次最大输出 token 上限",
    )

    # ---- 生成行为 ----
    thinking_policy: ThinkingPolicy = Field(
        default="auto",
        description="思考策略: auto=不发参数按服务端默认 / disabled / enabled",
    )
    reasoning_effort: ReasoningEffort = Field(
        default="default", description="默认推理强度(仅支持的 provider 生效)",
    )

    # ---- 能力声明(declared) ----
    supports_vision: TriState = "auto"
    supports_seed: TriState = "auto"
    json_object: TriState = "auto"
    json_schema: TriState = "auto"
    strict_schema: TriState = "auto"
    pdf_file_input: PdfFileInputDialect = Field(
        default="auto",
        description=(
            "chat直传PDF方言(由探针脚本或管理员写入); auto=未探测——已知厂商"
            "主机按内置映射推断参与, 未知厂商不参与; none=显式排除"
        ),
    )

    # ---- 探测实测(observed, 由 capability_store 写入) ----
    observed: dict[str, ObservedCapability] = Field(
        default_factory=dict,
        description="探测结果: text_basic/json_object/strict_schema/vision_basic",
    )
    observed_at: Optional[str] = Field(
        default=None, description="最近一次完整探测时间(ISO)",
    )


# 探测项 -> 能力字段 的映射(observed 收紧 declared 时使用)
_PROBE_TO_CAPABILITY = {
    "vision_basic": "supports_vision",
    "json_object": "json_object",
    "strict_schema": "strict_schema",
}


def resolve_tristate(
    declared: TriState,
    observed: Optional[ObservedCapability],
) -> TriState:
    """收紧优先解析单个三态能力。

    - declared=unsupported: 管理员显式禁用, 永远 unsupported;
    - observed 失败: 即使 declared=supported 也收紧为 unsupported
      (实测证据优先于人工声明的放宽方向);
    - observed 成功: declared=auto 时晋升为 supported;
    - 两侧都缺省: 保持 auto(调用方按现状自动探测/降级)。
    """
    if declared == "unsupported":
        return "unsupported"
    if observed is not None:
        if not observed.ok:
            return "unsupported"
        return "supported"
    return declared


def effective_capability(
    profile: Optional[ModelCapabilityProfile], field: str,
) -> TriState:
    """解析某个能力字段的 effective 值(profile 缺省时为 auto)"""
    if profile is None:
        return "auto"
    declared: TriState = getattr(profile, field, "auto")
    probe_kind = next(
        (k for k, v in _PROBE_TO_CAPABILITY.items() if v == field), None,
    )
    observed = profile.observed.get(probe_kind) if probe_kind else None
    return resolve_tristate(declared, observed)


def effective_max_output(profile: Optional[ModelCapabilityProfile]) -> Optional[int]:
    """最大输出上限的 effective 值(未声明返回 None=不限制)"""
    if profile is None:
        return None
    return profile.max_output_tokens


def effective_profile_summary(
    profile: Optional[ModelCapabilityProfile],
) -> dict:
    """输出前端展示用的 effective 摘要(含来源标注)"""
    prof = profile or ModelCapabilityProfile()

    def _entry(field: str) -> dict:
        declared = getattr(prof, field)
        probe_kind = next(
            (k for k, v in _PROBE_TO_CAPABILITY.items() if v == field), None,
        )
        observed = prof.observed.get(probe_kind) if probe_kind else None
        effective = resolve_tristate(declared, observed)
        source = "default"
        if declared == "unsupported":
            source = "declared"
        elif observed is not None:
            source = "observed"
        elif declared != "auto":
            source = "declared"
        return {
            "declared": declared,
            "observed": observed.model_dump() if observed else None,
            "effective": effective,
            "source": source,
        }

    return {
        "context_window_tokens": prof.context_window_tokens,
        "max_output_tokens": prof.max_output_tokens,
        "thinking_policy": prof.thinking_policy,
        "reasoning_effort": prof.reasoning_effort,
        "pdf_file_input": prof.pdf_file_input,
        "capabilities": {
            field: _entry(field)
            for field in (
                "supports_vision", "supports_seed",
                "json_object", "json_schema", "strict_schema",
            )
        },
        "observed_at": prof.observed_at,
    }


def merge_probe_reports(
    profile: Optional[ModelCapabilityProfile],
    reports: dict,
) -> ModelCapabilityProfile:
    """把 health_probe 的探测报告合并进能力档案(纯函数, 返回新对象)"""
    prof = (profile or ModelCapabilityProfile()).model_copy(deep=True)
    for kind, report in reports.items():
        ok = bool(getattr(report, "ok", False))
        error = getattr(report, "error", None) or ""
        probed_at = getattr(report, "probed_at", "") or ""
        prof.observed[kind] = ObservedCapability(
            ok=ok, probed_at=probed_at, evidence=str(error)[:300],
        )
    prof.observed_at = datetime.now(timezone.utc).isoformat()
    return prof

