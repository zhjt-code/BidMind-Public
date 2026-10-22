"""AI设置管理路由

视觉和审查模型完全独立，各自管理自己的模型配置列表。
添加配置时自动根据API地址判断本地/云端类型。
每个用途: 本地模型最多2个, 云端模型最多3个。

API:
- GET    /settings/ai                          获取完整设置
- PUT    /settings/ai/{purpose}/model          选择模型
- POST   /settings/ai/{purpose}/configs        添加配置
- PUT    /settings/ai/{purpose}/configs/{id}   更新配置
- DELETE /settings/ai/{purpose}/configs/{id}   删除配置
- POST   /settings/ai/test                     测试连接
"""

from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from app.api.dependencies import require_admin
from app.core.ai import get_llm_client
from app.core.ai_settings import (
    ApiKeyItem,
    ModelConfigItem,
    add_config,
    delete_config,
    detect_config_type,
    duplicate_config,
    load_ai_settings,
    set_model_id,
    set_pool_ids,
    set_pool_priority,
    update_config,
)
from app.models.user import User
from app.schemas.common import ApiResponse

# ==================== 安全配置 ====================


def validate_api_base_url(url: str) -> str:
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError("无效的 URL 格式")
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        if parsed.scheme != "https":
            raise ValueError("云端 API 必须使用 HTTPS 协议")
    return url


router = APIRouter(prefix="/settings")
PurposePath = Literal["vision", "review", "judge", "extract", "scoring"]
PURPOSE_LABELS = {
    "vision": "视觉", "review": "审查", "judge": "复审",
    "extract": "提取", "scoring": "评分",
}


# ==================== 请求/响应模型 ====================

class ApiKeyResponse(BaseModel):
    """API Key 响应（隐藏完整密钥）"""
    label: str = ""
    enabled: bool = True
    key_preview: str = ""


class ModelConfigResponse(BaseModel):
    """模型配置响应（隐藏密钥）"""
    id: str
    api_base: str
    model_name: str
    config_type: str  # "local" or "cloud"
    api_key_configured: bool
    api_key_preview: Optional[str] = None
    key_count: int = 0
    api_keys_info: list[ApiKeyResponse] = []
    temperature: Optional[float] = None
    temperature_mode: str = "free"
    temperature_exact: Optional[float] = None
    temperature_min: Optional[float] = None
    temperature_max: Optional[float] = None
    # P0-C: 能力档案 effective 摘要(declared/observed/effective 三态 + 来源)
    capabilities: Optional[dict] = None


class PoolUpdateRequest(BaseModel):
    pool_ids: list[str] = Field(..., description="参与调度池的配置ID列表")


class PoolPriorityUpdateRequest(BaseModel):
    priority: dict[str, int] = Field(..., description="配置ID->优先级映射 {config_id: 0|100|999}")


class AISettingsResponse(BaseModel):
    """完整AI设置响应"""
    vision_model_id: str
    review_model_id: str
    judge_model_id: str = ""
    extract_model_id: str = ""
    scoring_model_id: str = ""
    vision_configs: list[ModelConfigResponse]
    review_configs: list[ModelConfigResponse]
    judge_configs: list[ModelConfigResponse] = []
    extract_configs: list[ModelConfigResponse] = []
    scoring_configs: list[ModelConfigResponse] = []
    vision_pool_ids: list[str] = []
    review_pool_ids: list[str] = []
    judge_pool_ids: list[str] = []
    extract_pool_ids: list[str] = []
    scoring_pool_ids: list[str] = []
    vision_pool_priority: dict[str, int] = {}
    review_pool_priority: dict[str, int] = {}
    judge_pool_priority: dict[str, int] = {}
    extract_pool_priority: dict[str, int] = {}
    scoring_pool_priority: dict[str, int] = {}


class ModelAssignRequest(BaseModel):
    model_id: str = Field(..., description="配置ID或空字符串(取消选择)")


class ConfigCreateRequest(BaseModel):
    api_base: str
    api_key: str = Field(default="", description="API密钥（本地模型可留空）")
    model_name: str
    api_keys: list = Field(default_factory=list, description="多Key列表")
    temperature: Optional[float] = Field(default=None, description="采样温度覆盖，None使用全局默认0.1")
    temperature_mode: Literal["free", "exact", "clamp"] = Field(default="free", description="温度约束模式")
    temperature_exact: Optional[float] = Field(default=None, description="固定温度值")
    temperature_min: Optional[float] = Field(default=None, description="最小温度")
    temperature_max: Optional[float] = Field(default=None, description="最大温度")
    capabilities: Optional[dict] = Field(
        default=None, description="P0-C 能力档案 declared 字段(observed 由探测专管)",
    )

    @field_validator('api_base')
    @classmethod
    def validate_api_base(cls, v: str) -> str:
        return validate_api_base_url(v)


class ApiKeyCreateItem(BaseModel):
    key: str
    label: str = ""
    enabled: bool = True


class ConfigDuplicateRequest(BaseModel):
    """复制配置请求体"""
    source_purpose: PurposePath = Field(..., description="源配置所属用途")
    source_config_id: str = Field(..., min_length=1, description="源配置ID")


class ConfigUpdateRequest(BaseModel):
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    api_keys: Optional[list] = None
    temperature: Optional[float] = None
    temperature_mode: Optional[Literal["free", "exact", "clamp"]] = None
    temperature_exact: Optional[float] = None
    temperature_min: Optional[float] = None
    temperature_max: Optional[float] = None
    capabilities: Optional[dict] = Field(
        default=None, description="P0-C 能力档案 declared 字段(observed 由探测专管)",
    )

    @field_validator('api_base')
    @classmethod
    def validate_api_base(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return validate_api_base_url(v)
        return v


# ==================== 辅助函数 ====================

def _key_preview(key: str) -> str:
    if not key or key == "ollama":
        return ""
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"


def _to_response(config: ModelConfigItem) -> ModelConfigResponse:
    from app.core.ai_capabilities import effective_profile_summary
    api_keys_info = [
        ApiKeyResponse(label=ki.label, enabled=ki.enabled, key_preview=_key_preview(ki.key))
        for ki in config.api_keys
    ]
    dispatch_count = sum(1 for k in config.api_keys if k.enabled and k.key)
    total_keys = (1 if config.api_key else 0) + dispatch_count
    return ModelConfigResponse(
        id=config.id,
        api_base=config.api_base,
        model_name=config.model_name,
        config_type=config.config_type,
        api_key_configured=bool(config.api_key) or dispatch_count > 0,
        api_key_preview=_key_preview(config.api_key),
        key_count=total_keys,
        api_keys_info=api_keys_info,
        temperature=config.temperature,
        temperature_mode=config.temperature_mode,
        temperature_exact=config.temperature_exact,
        temperature_min=config.temperature_min,
        temperature_max=config.temperature_max,
        capabilities=effective_profile_summary(config.capabilities),
    )


# ==================== API端点 ====================

@router.get("/ai", response_model=ApiResponse[AISettingsResponse])
async def get_ai_settings(admin_user: User = Depends(require_admin)) -> ApiResponse[AISettingsResponse]:
    """获取完整AI设置"""
    try:
        s = load_ai_settings()
        response = AISettingsResponse(
            vision_model_id=s.vision_model_id,
            review_model_id=s.review_model_id,
            judge_model_id=s.judge_model_id,
            extract_model_id=s.extract_model_id,
            scoring_model_id=s.scoring_model_id,
            vision_configs=[_to_response(c) for c in s.vision_configs],
            review_configs=[_to_response(c) for c in s.review_configs],
            judge_configs=[_to_response(c) for c in s.judge_configs],
            extract_configs=[_to_response(c) for c in s.extract_configs],
            scoring_configs=[_to_response(c) for c in s.scoring_configs],
            vision_pool_ids=s.vision_pool_ids,
            review_pool_ids=s.review_pool_ids,
            judge_pool_ids=s.judge_pool_ids,
            extract_pool_ids=s.extract_pool_ids,
            scoring_pool_ids=s.scoring_pool_ids,
            vision_pool_priority=s.vision_pool_priority,
            review_pool_priority=s.review_pool_priority,
            judge_pool_priority=s.judge_pool_priority,
            extract_pool_priority=s.extract_pool_priority,
            scoring_pool_priority=s.scoring_pool_priority,
        )
        return ApiResponse(code=200, message="success", data=response)
    except Exception as e:
        logger.error(f"获取AI设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/ai/{purpose}/model", response_model=ApiResponse)
async def set_purpose_model(
    purpose: PurposePath = Path(...),
    request: ModelAssignRequest = ...,
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """选择模型"""
    label = PURPOSE_LABELS[purpose]
    try:
        old = load_ai_settings()
        old_id = old.get_model_id(purpose)
        await set_model_id(purpose, request.model_id)
        if old_id != request.model_id:
            logger.info(f"{label}模型已切换: {old_id} -> {request.model_id}")
        return ApiResponse(code=200, message=f"{label}模型已设置",
                          data={"previous": old_id, "current": request.model_id})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"设置{label}模型失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/ai/{purpose}/pool", response_model=ApiResponse)
async def set_purpose_pool(
    purpose: PurposePath = Path(...),
    request: PoolUpdateRequest = ...,
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """设置调度池配置列表"""
    label = PURPOSE_LABELS[purpose]
    try:
        updated = await set_pool_ids(purpose, request.pool_ids)
        pool_ids = updated.get_pool_ids(purpose)
        logger.info(f"{label}调度池已更新: {pool_ids}")
        return ApiResponse(code=200, message=f"{label}调度池已更新 ({len(pool_ids)} 个模型)",
                          data={"pool_ids": pool_ids})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"设置{label}调度池失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/ai/{purpose}/pool-priority", response_model=ApiResponse)
async def set_purpose_pool_priority(
    purpose: PurposePath = Path(...),
    request: PoolPriorityUpdateRequest = ...,
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """设置调度池优先级 (0=默认首选, 100=普通, 999=末位备选)"""
    label = PURPOSE_LABELS[purpose]
    try:
        updated = await set_pool_priority(purpose, request.priority)
        priority = updated.get_pool_priority(purpose)
        logger.info(f"{label}调度优先级已更新: {priority}")
        return ApiResponse(code=200, message=f"{label}调度优先级已更新",
                          data={"priority": priority})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"设置{label}调度优先级失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/{purpose}/configs", response_model=ApiResponse[ModelConfigResponse])
async def create_config(
    purpose: PurposePath = Path(...),
    request: ConfigCreateRequest = ...,
    admin_user: User = Depends(require_admin),
) -> ApiResponse[ModelConfigResponse]:
    """添加模型配置（自动检测本地/云端）"""
    label = PURPOSE_LABELS[purpose]
    try:
        import time
        config_type = detect_config_type(request.api_base)
        config_id = f"{purpose}_{config_type}_{int(time.time() * 1000)}"

        # 本地模型: 如果没填密钥，默认设置为"ollama"
        api_key = request.api_key
        if config_type == "local" and not api_key:
            api_key = "ollama"

        api_key_items = [
            ApiKeyItem(key=ki.get("key","") if isinstance(ki,dict) else ki.key,
                       label=ki.get("label","") if isinstance(ki,dict) else ki.label,
                       enabled=ki.get("enabled",True) if isinstance(ki,dict) else ki.enabled)
            for ki in (request.api_keys or [])
        ]
        config = ModelConfigItem(
            id=config_id, api_base=request.api_base,
            api_key=api_key, api_keys=api_key_items,
            model_name=request.model_name,
            config_type=config_type,
            temperature=request.temperature,
            temperature_mode=request.temperature_mode,
            temperature_exact=request.temperature_exact,
            temperature_min=request.temperature_min,
            temperature_max=request.temperature_max,
        )
        await add_config(purpose, config)

        # P0-C: 创建时携带能力声明则一并落库
        if request.capabilities:
            from app.core.ai_capability_store import update_declared_capabilities
            await update_declared_capabilities(purpose, config_id, request.capabilities)
            refreshed = load_ai_settings().get_config_by_id(purpose, config_id)
            if refreshed:
                config = refreshed

        type_label = "本地" if config_type == "local" else "云端"
        logger.info(f"已添加{label}{type_label}配置: {request.model_name}")
        return ApiResponse(code=200, message=f"已添加{type_label}模型: {request.model_name}",
                          data=_to_response(config))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"添加{label}配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/{target_purpose}/configs/duplicate", response_model=ApiResponse[ModelConfigResponse])
async def duplicate_config_endpoint(
    target_purpose: PurposePath = Path(...),
    request: ConfigDuplicateRequest = ...,
    admin_user: User = Depends(require_admin),
) -> ApiResponse[ModelConfigResponse]:
    """复制模型配置（含明文密钥）到目标用途，自动重命名避免冲突。

    源配置不存在返回 404；上限/参数错误返回 400。
    """
    label = PURPOSE_LABELS[target_purpose]
    src_label = PURPOSE_LABELS[request.source_purpose]
    try:
        new_config = await duplicate_config(
            request.source_purpose, request.source_config_id, target_purpose,
        )
        logger.info(
            f"已从{src_label}/{request.source_config_id}复制到{label}: {new_config.model_name}"
        )
        return ApiResponse(
            code=200,
            message=f"已复制到{label}: {new_config.model_name}",
            data=_to_response(new_config),
        )
    except ValueError as e:
        msg = str(e)
        if msg.startswith("源配置不存在"):
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except Exception as e:
        logger.error(f"复制{label}配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/ai/{purpose}/configs/{config_id}", response_model=ApiResponse[ModelConfigResponse])
async def update_config_endpoint(
    config_id: str,
    purpose: PurposePath = Path(...),
    request: ConfigUpdateRequest = ...,
    admin_user: User = Depends(require_admin),
) -> ApiResponse[ModelConfigResponse]:
    """更新模型配置"""
    label = PURPOSE_LABELS[purpose]
    try:
        kwargs = {}
        if request.api_base is not None:
            kwargs["api_base"] = request.api_base
        if request.api_key is not None:
            kwargs["api_key"] = request.api_key
        if request.model_name is not None:
            kwargs["model_name"] = request.model_name
        if hasattr(request, 'api_keys') and request.api_keys is not None:
            kwargs["api_keys"] = [ki if isinstance(ki, dict) else {"key": ki.key, "label": ki.label, "enabled": ki.enabled} for ki in request.api_keys]
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.temperature_mode is not None:
            kwargs["temperature_mode"] = request.temperature_mode
        if request.temperature_exact is not None:
            kwargs["temperature_exact"] = request.temperature_exact
        if request.temperature_min is not None:
            kwargs["temperature_min"] = request.temperature_min
        if request.temperature_max is not None:
            kwargs["temperature_max"] = request.temperature_max

        updated = await update_config(purpose, config_id, **kwargs)

        # P0-C: 能力声明更新走独立存储链路(observed 由探测专管不受影响)
        if request.capabilities is not None:
            from app.core.ai_capability_store import update_declared_capabilities
            await update_declared_capabilities(purpose, config_id, request.capabilities)
            updated = load_ai_settings()

        config = updated.get_config_by_id(purpose, config_id)
        if not config:
            raise ValueError(f"配置更新后未找到: {config_id}")
        return ApiResponse(code=200, message=f"已更新{label}配置", data=_to_response(config))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"更新{label}配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/{purpose}/configs/{config_id}/verify", response_model=ApiResponse)
async def verify_config_capabilities(
    config_id: str,
    purpose: PurposePath = Path(...),
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """P0-C: 保存并验证——按用途执行完整能力探测套件, 结果写入 observed。

    vision: text_basic + vision_basic(真实图片);
    review/judge: text_basic + json_object + strict_schema。
    探测流量标记 admin_test, 不计入生产可靠性统计。
    """
    from app.core.ai.cloud_client import CloudClient
    from app.core.ai.health_probe import run_capability_probes
    from app.core.ai_capabilities import effective_profile_summary
    from app.core.ai_capability_store import record_probe_results

    label = PURPOSE_LABELS[purpose]
    s = load_ai_settings()
    config = s.get_config_by_id(purpose, config_id)
    if not config:
        raise HTTPException(status_code=404, detail=f"配置不存在: {config_id}")

    try:
        api_key = config.api_key or (
            "ollama" if config.config_type == "local"
            else (config.api_keys[0].key if config.api_keys else "")
        )
        client = CloudClient(
            base_url=config.api_base, api_key=api_key,
            model_name=config.model_name, timeout=60.0,
            default_temperature=config.temperature,
            temperature_mode=config.temperature_mode,
            temperature_exact=config.temperature_exact,
            temperature_min=config.temperature_min,
            temperature_max=config.temperature_max,
        )
        reports = await run_capability_probes(client, purpose=purpose)
        profile = await record_probe_results(purpose, config_id, reports)

        all_ok = all(r.ok for r in reports.values())
        failed = [k for k, r in reports.items() if not r.ok]
        message = (
            f"{label}模型 {config.model_name} 能力验证通过"
            if all_ok else
            f"{label}模型 {config.model_name} 部分能力验证失败: {', '.join(failed)}"
        )
        return ApiResponse(
            code=200 if all_ok else 207,
            message=message,
            data={
                "config_id": config_id,
                "all_ok": all_ok,
                "reports": {k: r.to_dict() for k, r in reports.items()},
                "capabilities": effective_profile_summary(profile),
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"能力验证失败: {purpose}/{config_id}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ai/{purpose}/configs/{config_id}", response_model=ApiResponse)
async def delete_config_endpoint(
    config_id: str,
    purpose: PurposePath = Path(...),
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """删除模型配置"""
    label = PURPOSE_LABELS[purpose]
    try:
        updated = await delete_config(purpose, config_id)
        return ApiResponse(code=200, message=f"已删除{label}配置",
                          data={"deleted_id": config_id, "current_model_id": updated.get_model_id(purpose)})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"删除{label}配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/test", response_model=ApiResponse)
async def test_ai_connection(
    config_id: Optional[str] = Query(None),
    purpose: Optional[str] = Query(None),
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """测试AI连接（主密钥 + 所有已启用调度密钥）

    P0-B: 按用途执行真实健康探测——vision 池发送真实小图并要求非空描述,
    review/judge 池要求非空文本正文; 空响应/截断/内容过滤均判失败。
    """
    from app.core.ai.attempt_recorder import current_traffic_class
    from app.core.ai.cloud_client import CloudClient
    from app.core.ai.health_probe import run_primary_probe

    # P0-A: 管理员连接测试不计入生产可靠性统计(请求级 task 结束即失效, 无需 reset)
    current_traffic_class.set("admin_test")
    probe_purpose = purpose or "review"

    async def _test_one_key(
        base_url: str,
        key: str,
        model: str,
        label: str,
        temperature: Optional[float] = None,
        temperature_mode: str = "free",
        temperature_exact: Optional[float] = None,
        temperature_min: Optional[float] = None,
        temperature_max: Optional[float] = None,
    ) -> dict:
        """测试单个密钥，返回 {label, status, error?}"""
        try:
            c = CloudClient(base_url=base_url, api_key=key, model_name=model,
                            timeout=60.0, default_temperature=temperature,
                            temperature_mode=temperature_mode,
                            temperature_exact=temperature_exact,
                            temperature_min=temperature_min,
                            temperature_max=temperature_max)
            report = await run_primary_probe(c, purpose=probe_purpose)
            if not report.ok:
                return {"label": label, "status": "disconnected", "error": report.error}
            return {"label": label, "status": "connected"}
        except Exception as exc:
            return {"label": label, "status": "disconnected", "error": str(exc)}

    if config_id and purpose:
        s = load_ai_settings()
        config = s.get_config_by_id(purpose, config_id)  # type: ignore
        if not config:
            raise HTTPException(status_code=404, detail=f"配置不存在: {config_id}")

        test_label = f"{config.model_name} ({config.config_type})"

        if config.config_type == "local":
            try:
                api_key = config.api_key or "ollama"
                client = CloudClient(
                    base_url=config.api_base, api_key=api_key,
                    model_name=config.model_name, timeout=60.0,
                    default_temperature=config.temperature,
                    temperature_mode=config.temperature_mode,
                    temperature_exact=config.temperature_exact,
                    temperature_min=config.temperature_min,
                    temperature_max=config.temperature_max,
                )
                report = await run_primary_probe(client, purpose=probe_purpose)
                if not report.ok:
                    raise RuntimeError(report.error or "探测失败")
                return ApiResponse(code=200, message=f"{test_label} 连接正常",
                                  data={"config_id": config_id, "status": "connected", "results": []})
            except Exception as e:
                return ApiResponse(code=503, message=f"{test_label} 连接失败: {str(e)}",
                                  data={"config_id": config_id, "status": "disconnected", "error": str(e), "results": []})

        keys_to_test: list[tuple[str, str]] = []
        if config.api_key:
            keys_to_test.append((config.api_key, "主密钥"))

        results = []
        empty_labels: list[str] = []
        for idx, ki in enumerate(config.api_keys):
            tag = f"调度密钥{idx + 1}" + (f" ({ki.label})" if ki.label else "")
            if not ki.key:
                empty_labels.append(tag)
                results.append({"label": tag, "status": "disconnected", "error": "密钥为空，请重新配置"})
            elif ki.enabled:
                keys_to_test.append((ki.key, tag))

        if not keys_to_test and not results:
            raise HTTPException(status_code=400, detail="该配置未设置任何API密钥")

        for key_val, key_label in keys_to_test:
            r = await _test_one_key(config.api_base, key_val, config.model_name, key_label,
                                    temperature=config.temperature,
                                    temperature_mode=config.temperature_mode,
                                    temperature_exact=config.temperature_exact,
                                    temperature_min=config.temperature_min,
                                    temperature_max=config.temperature_max)
            results.append(r)

        passed = sum(1 for r in results if r["status"] == "connected")
        total = len(results)
        if passed == total:
            status = "connected"
            msg = f"{test_label} 全部 {total} 个密钥连接正常"
        elif passed == 0:
            status = "disconnected"
            msg = f"{test_label} 全部 {total} 个密钥连接失败"
        else:
            status = "partial"
            failed_labels = [r["label"] for r in results if r["status"] != "connected"]
            msg = f"{test_label} {passed}/{total} 通过，失败: {', '.join(failed_labels)}"

        logger.info(f"AI连接测试: {msg}")
        code = 200 if status == "connected" else 503
        return ApiResponse(code=code, message=msg,
                          data={"config_id": config_id, "status": status, "results": results})
    else:
        test_label = "默认本地模型"
        try:
            client = get_llm_client(mode="local")
            report = await run_primary_probe(client, purpose=probe_purpose)
            if not report.ok:
                raise RuntimeError(report.error or "探测失败")
            logger.info(f"AI连接测试成功: {test_label}")
            return ApiResponse(code=200, message=f"{test_label} 连接正常",
                              data={"config_id": config_id, "status": "connected", "results": []})
        except Exception as e:
            logger.warning(f"AI连接测试失败: {test_label}, error={e}")
            return ApiResponse(code=503, message=f"{test_label} 连接失败: {str(e)}",
                              data={"config_id": config_id, "status": "disconnected", "error": str(e), "results": []})



@router.get("/ai/dispatcher-status", response_model=ApiResponse)
async def get_dispatcher_status(
    admin_user: User = Depends(require_admin),
) -> ApiResponse:
    """获取 AI 调度器运行状态（仅管理员）"""
    from app.core.ai.ai_factory import (
        get_extract_dispatcher,
        get_judge_dispatcher,
        get_review_dispatcher,
        get_vision_dispatcher,
    )
    review = get_review_dispatcher()
    vision = get_vision_dispatcher()
    judge = get_judge_dispatcher()
    extract = get_extract_dispatcher()
    data = {
        "review": review.get_status() if review else {"purpose": "review", "channel_count": 0, "channels": [], "metrics": {}},
        "vision": vision.get_status() if vision else {"purpose": "vision", "channel_count": 0, "channels": [], "metrics": {}},
        "judge": judge.get_status() if judge else {"purpose": "judge", "channel_count": 0, "channels": [], "metrics": {}},
        "extract": extract.get_status() if extract else {"purpose": "extract", "channel_count": 0, "channels": [], "metrics": {}},
    }
    return ApiResponse(code=200, message="ok", data=data)
