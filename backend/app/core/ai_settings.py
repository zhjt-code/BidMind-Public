"""AI设置持久化管理

数据结构:
- vision_pool_ids / review_pool_ids: 参与调度池的模型配置ID列表(多选)
- vision_model_id / review_model_id: 向后兼容的单选ID(池子为空时回退)
- vision_configs / review_configs: 各自独立的模型配置列表
- 每个配置自动根据API地址判断类型: local(localhost) / cloud(其他)
- 每个用途: 本地模型最多2个, 云端模型最多5个
- 每个云端配置支持多 API Key (api_keys 列表), 用于调度器多通道并发
"""

import asyncio
import base64
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlparse

# ==================== 加密工具 ====================
from cryptography.fernet import Fernet, InvalidToken
from loguru import logger
from pydantic import BaseModel, Field

from app.core.ai_capabilities import ModelCapabilityProfile
from app.core.ai_settings_extract_migrate import ensure_extract_pool_from_review
from app.core.ai_settings_purpose_ready import (
    sync_all_legacy_model_ids,
    sync_legacy_model_id,
)
from app.core.config import settings as app_settings

_OBFUSCATION_KEY = b"BidMind_AI_Lite_2024"

_fernet_instance: Optional[Fernet] = None
_fernet_temp_warned = False


def _get_fernet() -> Fernet:
    """获取 Fernet 实例,密钥来源: AI_SETTINGS_KEY 环境变量 > 临时生成"""
    global _fernet_instance, _fernet_temp_warned
    if _fernet_instance is not None:
        return _fernet_instance
    key = app_settings.AI_SETTINGS_KEY
    if key:
        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
    else:
        _fernet_instance = Fernet(Fernet.generate_key())
        if not _fernet_temp_warned:
            logger.warning(
                "AI_SETTINGS_KEY 未配置,使用临时密钥。"
                "重启后已保存的模型密钥将无法解密,需重新在设置页面配置。"
                "生成固定密钥: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
            _fernet_temp_warned = True
    return _fernet_instance


def _obfuscate(plain_text: str) -> str:
    if not plain_text:
        return ""
    return _get_fernet().encrypt(plain_text.encode('utf-8')).decode('ascii')


def _deobfuscate(obfuscated: str) -> str:
    if not obfuscated:
        return ""
    try:
        return _get_fernet().decrypt(obfuscated.encode('ascii')).decode('utf-8')
    except (InvalidToken, Exception):
        return _legacy_deobfuscate(obfuscated)


def _legacy_deobfuscate(obfuscated: str) -> str:
    """旧版 XOR 混淆解密,用于兼容已有配置文件的平滑迁移"""
    try:
        data = base64.b64decode(obfuscated.encode('ascii'))
        key = _OBFUSCATION_KEY
        result = bytes(d ^ key[i % len(key)] for i, d in enumerate(data))
        return result.decode('utf-8')
    except Exception:
        return obfuscated


# 路径和常量
SETTINGS_DIR = Path(app_settings.UPLOAD_DIR).parent
SETTINGS_FILE = SETTINGS_DIR / "ai_settings.json"

MAX_LOCAL_PER_PURPOSE = 2

_settings_lock = asyncio.Lock()
MAX_CLOUD_PER_PURPOSE = 5

Purpose = Literal["vision", "review", "judge", "extract", "scoring"]
ConfigType = Literal["local", "cloud"]
TemperatureMode = Literal["free", "exact", "clamp"]

PRIORITY_PREFERRED = 0       # 默认首选（最多1个）
PRIORITY_SECOND = 50         # 次选（最多1个）
PRIORITY_NORMAL = 100        # 普通（不限）
PRIORITY_BACKUP = 200        # 备用（最多1个）
PRIORITY_LAST_RESORT = 999   # 末位备选（最多1个）
VALID_PRIORITIES = {
    PRIORITY_PREFERRED,
    PRIORITY_SECOND,
    PRIORITY_NORMAL,
    PRIORITY_BACKUP,
    PRIORITY_LAST_RESORT,
}
PRIORITY_LABELS: dict[int, str] = {
    PRIORITY_PREFERRED: "默认首选",
    PRIORITY_SECOND: "次选",
    PRIORITY_NORMAL: "普通",
    PRIORITY_BACKUP: "备用",
    PRIORITY_LAST_RESORT: "末位备选",
}
PRIORITIES_SINGLETON: set[int] = {
    PRIORITY_PREFERRED,
    PRIORITY_SECOND,
    PRIORITY_BACKUP,
    PRIORITY_LAST_RESORT,
}

AI_SETTINGS_INVALIDATE_CHANNEL = "bidmind:ai_settings:invalidate"
_INVALIDATE_TYPE_RELOAD = "reload"
_INVALIDATE_TYPE_PRIORITY = "priority"

_invalidate_sub_task: Optional[asyncio.Task] = None
_invalidate_sub_stop: Optional[asyncio.Event] = None
_invalidate_sub_client: Any = None
_invalidate_sub_owns_client = False


def detect_config_type(api_base: str) -> ConfigType:
    """根据API地址自动判断配置类型"""
    try:
        parsed = urlparse(api_base)
        hostname = parsed.hostname or ""
        if hostname in ("localhost", "127.0.0.1"):
            return "local"
    except Exception:
        pass
    return "cloud"


# ==================== 数据模型 ====================

class ApiKeyItem(BaseModel):
    """单个 API Key 条目，用于同一模型配置下挂载多个密钥"""
    key: str = Field(..., description="API密钥")
    label: str = Field(default="", description="可选标签")
    enabled: bool = Field(default=True, description="是否启用")


class ModelConfigItem(BaseModel):
    """模型配置项（本地和云端统一结构）"""
    id: str = Field(..., description="配置唯一ID")
    api_base: str = Field(..., description="API基础URL")
    api_key: str = Field(default="", description="API密钥（向后兼容单Key）")
    api_keys: list[ApiKeyItem] = Field(default_factory=list, description="多Key列表，用于调度器多通道")
    model_name: str = Field(..., description="模型名称")
    config_type: ConfigType = Field(default="cloud", description="配置类型: local/cloud (自动推导)")
    # P0-C: 能力档案(旧 JSON 无此字段时默认 None 兼容加载);
    # 解析逻辑在 app/core/ai_capabilities.py, 持久化在 ai_capability_store.py
    capabilities: Optional[ModelCapabilityProfile] = Field(
        default=None, description="模型能力档案(declared/observed 三态)",
    )
    temperature: Optional[float] = Field(default=None, description="采样温度覆盖，None时使用全局默认0.1")
    temperature_mode: TemperatureMode = Field(
        default="free",
        description="温度约束模式: free=不限制, exact=固定值, clamp=区间裁剪",
    )
    temperature_exact: Optional[float] = Field(
        default=None,
        description="固定温度值（temperature_mode=exact 时生效）",
    )
    temperature_min: Optional[float] = Field(
        default=None,
        description="最小温度（temperature_mode=clamp 时生效）",
    )
    temperature_max: Optional[float] = Field(
        default=None,
        description="最大温度（temperature_mode=clamp 时生效）",
    )

    def get_all_keys(self) -> list[str]:
        """获取所有可用的 API Key（主密钥 + 已启用调度密钥）"""
        keys: list[str] = []
        if self.api_key:
            keys.append(self.api_key)
        for k in self.api_keys:
            if k.enabled and k.key and k.key != self.api_key:
                keys.append(k.key)
        return keys


class AISettingsModel(BaseModel):
    """AI设置模型"""
    vision_model_id: str = Field(default="", description="视觉模型: 配置ID或空")
    vision_configs: list[ModelConfigItem] = Field(default_factory=list)

    review_model_id: str = Field(default="", description="审查模型: 配置ID或空")
    review_configs: list[ModelConfigItem] = Field(default_factory=list)

    judge_model_id: str = Field(default="", description="复审模型: 配置ID或空")
    judge_configs: list[ModelConfigItem] = Field(default_factory=list)

    extract_model_id: str = Field(default="", description="提取模型: 配置ID或空")
    extract_configs: list[ModelConfigItem] = Field(default_factory=list)

    # 评分提取专用池(2026-08-15): chat-PDF直读救援目标解析消费, 池空回退review;
    # 无独立dispatcher消费者, 仅作为配置池存在
    scoring_model_id: str = Field(default="", description="评分模型: 配置ID或空")
    scoring_configs: list[ModelConfigItem] = Field(default_factory=list)

    # 调度池: 参与负载均衡的配置ID列表
    vision_pool_ids: list[str] = Field(default_factory=list, description="视觉调度池配置ID列表")
    review_pool_ids: list[str] = Field(default_factory=list, description="审查调度池配置ID列表")
    judge_pool_ids: list[str] = Field(default_factory=list, description="复审调度池配置ID列表")
    extract_pool_ids: list[str] = Field(default_factory=list, description="提取调度池配置ID列表")
    scoring_pool_ids: list[str] = Field(default_factory=list, description="评分调度池配置ID列表")

    # 调度优先级: {config_id: 0|100|999}
    vision_pool_priority: dict[str, int] = Field(default_factory=dict, description="视觉调度池优先级映射")
    review_pool_priority: dict[str, int] = Field(default_factory=dict, description="审查调度池优先级映射")
    judge_pool_priority: dict[str, int] = Field(default_factory=dict, description="复审调度池优先级映射")
    extract_pool_priority: dict[str, int] = Field(default_factory=dict, description="提取调度池优先级映射")
    scoring_pool_priority: dict[str, int] = Field(default_factory=dict, description="评分调度池优先级映射")

    # 兼容旧格式
    ai_mode: str = Field(default="local", description="[废弃]")
    cloud_api_base: str = Field(default="", description="[废弃]")
    cloud_api_key: str = Field(default="", description="[废弃]")
    cloud_model_name: str = Field(default="", description="[废弃]")
    cloud_configs: list[dict] = Field(default_factory=list, description="[废弃]")

    def get_configs(self, purpose: Purpose) -> list[ModelConfigItem]:
        if purpose == "vision":
            return self.vision_configs
        if purpose == "review":
            return self.review_configs
        if purpose == "judge":
            return self.judge_configs
        if purpose == "extract":
            return self.extract_configs
        if purpose == "scoring":
            return self.scoring_configs
        raise ValueError(f"未知 purpose: {purpose}")

    def set_configs(self, purpose: Purpose, configs: list[ModelConfigItem]) -> None:
        if purpose == "vision":
            self.vision_configs = configs
        elif purpose == "review":
            self.review_configs = configs
        elif purpose == "judge":
            self.judge_configs = configs
        elif purpose == "extract":
            self.extract_configs = configs
        elif purpose == "scoring":
            self.scoring_configs = configs
        else:
            raise ValueError(f"未知 purpose: {purpose}")

    def get_model_id(self, purpose: Purpose) -> str:
        if purpose == "vision":
            return self.vision_model_id
        if purpose == "review":
            return self.review_model_id
        if purpose == "judge":
            return self.judge_model_id
        if purpose == "extract":
            return self.extract_model_id
        if purpose == "scoring":
            return self.scoring_model_id
        raise ValueError(f"未知 purpose: {purpose}")

    def set_model_id(self, purpose: Purpose, model_id: str) -> None:
        if purpose == "vision":
            self.vision_model_id = model_id
        elif purpose == "review":
            self.review_model_id = model_id
        elif purpose == "judge":
            self.judge_model_id = model_id
        elif purpose == "extract":
            self.extract_model_id = model_id
        elif purpose == "scoring":
            self.scoring_model_id = model_id
        else:
            raise ValueError(f"未知 purpose: {purpose}")

    def get_config_by_id(self, purpose: Purpose, config_id: str) -> Optional[ModelConfigItem]:
        for config in self.get_configs(purpose):
            if config.id == config_id:
                return config
        return None

    def count_by_type(self, purpose: Purpose, config_type: ConfigType) -> int:
        return sum(1 for c in self.get_configs(purpose) if c.config_type == config_type)

    def get_active_review_config(self) -> Optional[ModelConfigItem]:
        if not self.review_model_id:
            return None
        return self.get_config_by_id("review", self.review_model_id)

    def get_active_review_mode(self) -> str:
        pool_ids = self.get_pool_ids("review")
        if pool_ids:
            for pid in pool_ids:
                cfg = self.get_config_by_id("review", pid)
                if cfg and cfg.config_type == "cloud":
                    return "cloud"
        config = self.get_active_review_config()
        if config:
            return "local" if config.config_type == "local" else "cloud"
        return "local"

    def get_pool_ids(self, purpose: "Purpose") -> list[str]:
        if purpose == "vision":
            return self.vision_pool_ids
        if purpose == "review":
            return self.review_pool_ids
        if purpose == "judge":
            return self.judge_pool_ids
        if purpose == "extract":
            return self.extract_pool_ids
        if purpose == "scoring":
            return self.scoring_pool_ids
        raise ValueError(f"未知 purpose: {purpose}")

    def set_pool_ids(self, purpose: "Purpose", ids: list[str]) -> None:
        if purpose == "vision":
            self.vision_pool_ids = ids
        elif purpose == "review":
            self.review_pool_ids = ids
        elif purpose == "judge":
            self.judge_pool_ids = ids
        elif purpose == "extract":
            self.extract_pool_ids = ids
        elif purpose == "scoring":
            self.scoring_pool_ids = ids
        else:
            raise ValueError(f"未知 purpose: {purpose}")

    def get_pool_priority(self, purpose: "Purpose") -> dict[str, int]:
        if purpose == "vision":
            return self.vision_pool_priority
        if purpose == "review":
            return self.review_pool_priority
        if purpose == "judge":
            return self.judge_pool_priority
        if purpose == "extract":
            return self.extract_pool_priority
        if purpose == "scoring":
            return self.scoring_pool_priority
        raise ValueError(f"未知 purpose: {purpose}")

    def set_pool_priority(self, purpose: "Purpose", priority: dict[str, int]) -> None:
        if purpose == "vision":
            self.vision_pool_priority = priority
        elif purpose == "review":
            self.review_pool_priority = priority
        elif purpose == "judge":
            self.judge_pool_priority = priority
        elif purpose == "extract":
            self.extract_pool_priority = priority
        elif purpose == "scoring":
            self.scoring_pool_priority = priority
        else:
            raise ValueError(f"未知 purpose: {purpose}")

    def get_priority_for(self, purpose: "Purpose", config_id: str) -> int:
        return self.get_pool_priority(purpose).get(config_id, PRIORITY_NORMAL)


# ==================== 内部工具 ====================

def _ensure_dir() -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def _obfuscate_config_list(configs: list[dict[str, Any]]) -> None:
    for config in configs:
        if config.get("api_key"):
            config["api_key"] = _obfuscate(config["api_key"])
        api_keys = config.get("api_keys", [])
        if not isinstance(api_keys, list):
            continue
        for key_item in api_keys:
            if isinstance(key_item, dict) and key_item.get("key"):
                key_item["key"] = _obfuscate(key_item["key"])


def _deobfuscate_config_list(configs: list[dict[str, Any]]) -> None:
    for config in configs:
        if config.get("api_key"):
            config["api_key"] = _deobfuscate(config["api_key"])
        api_keys = config.get("api_keys", [])
        if not isinstance(api_keys, list):
            continue
        for key_item in api_keys:
            if isinstance(key_item, dict) and key_item.get("key"):
                key_item["key"] = _deobfuscate(key_item["key"])


def _ensure_config_type(configs: list[dict[str, Any]]) -> None:
    """确保每个配置都有config_type字段"""
    for config in configs:
        if "config_type" not in config:
            config["config_type"] = detect_config_type(config.get("api_base", ""))


def _to_judge_config_id(config_id: str) -> str:
    """将历史 review 池配置 ID 转为 judge 池 ID。"""
    if config_id.startswith("review_"):
        return "judge_" + config_id[len("review_"):]
    return config_id


def _dedupe_config_id(candidate: str, used_ids: set[str]) -> str:
    """在同一用途配置列表内生成不冲突的 ID。"""
    if candidate not in used_ids:
        return candidate
    base = f"{candidate}_migrated"
    if base not in used_ids:
        return base
    index = 2
    while f"{base}_{index}" in used_ids:
        index += 1
    return f"{base}_{index}"


def _normalize_judge_config_ids(data: dict[str, Any]) -> None:
    """规范化 judge 历史克隆配置 ID，并同步 model/pool/priority 引用。"""
    configs = data.get("judge_configs", [])
    if not isinstance(configs, list) or not configs:
        return

    used_ids = {
        str(config.get("id", ""))
        for config in configs
        if isinstance(config, dict)
        if config.get("id") and not str(config.get("id", "")).startswith("review_")
    }
    id_map: dict[str, str] = {}

    for config in configs:
        if not isinstance(config, dict):
            continue
        old_id = str(config.get("id", "") or "")
        if not old_id or not old_id.startswith("review_"):
            if old_id:
                used_ids.add(old_id)
            continue

        new_id = _dedupe_config_id(_to_judge_config_id(old_id), used_ids)
        config["id"] = new_id
        used_ids.add(new_id)
        id_map[old_id] = new_id

    if not id_map:
        return

    judge_model_id = data.get("judge_model_id", "")
    if judge_model_id in id_map:
        data["judge_model_id"] = id_map[judge_model_id]

    judge_pool_ids = data.get("judge_pool_ids", [])
    if isinstance(judge_pool_ids, list):
        data["judge_pool_ids"] = [
            id_map.get(pool_id, pool_id)
            for pool_id in judge_pool_ids
        ]
    judge_pool_priority = data.get("judge_pool_priority", {})
    if isinstance(judge_pool_priority, dict):
        data["judge_pool_priority"] = {
            id_map.get(config_id, config_id): priority
            for config_id, priority in judge_pool_priority.items()
        }


def _migrate_old_format(data: dict[str, Any]) -> dict[str, Any]:
    """迁移旧格式到新格式"""
    if "vision_configs" in data and "review_configs" in data:
        # 已有新格式字段，确保config_type存在
        _ensure_config_type(data.get("vision_configs", []))
        _ensure_config_type(data.get("review_configs", []))
        # 将旧的 model_id="local" 清空（新架构不再支持硬编码local）
        if data.get("vision_model_id") == "local":
            data["vision_model_id"] = ""
        if data.get("review_model_id") == "local":
            data["review_model_id"] = ""
        # 自动补充 pool_ids: 旧数据没有此字段时从 model_id 迁移
        if "vision_pool_ids" not in data:
            vid = data.get("vision_model_id", "")
            data["vision_pool_ids"] = [vid] if vid else []
        if "review_pool_ids" not in data:
            rid = data.get("review_model_id", "")
            data["review_pool_ids"] = [rid] if rid else []
        if "vision_pool_priority" not in data:
            data["vision_pool_priority"] = {}
        if "review_pool_priority" not in data:
            data["review_pool_priority"] = {}
        # B5 D-019: judge 池首次加载自动克隆 review_configs
        if "judge_configs" not in data:
            import copy
            data["judge_configs"] = copy.deepcopy(data.get("review_configs", []))
            data["judge_model_id"] = data.get("review_model_id", "")
            data["judge_pool_ids"] = list(data.get("review_pool_ids", []))
            data["judge_pool_priority"] = dict(data.get("review_pool_priority", {}))
        _ensure_config_type(data.get("judge_configs", []))
        _normalize_judge_config_ids(data)
        if "judge_pool_ids" not in data:
            jid = data.get("judge_model_id", "")
            data["judge_pool_ids"] = [jid] if jid else []
        if "judge_pool_priority" not in data:
            data["judge_pool_priority"] = {}
        ensure_extract_pool_from_review(data)
        _ensure_config_type(data.get("extract_configs", []))
        if "extract_pool_ids" not in data:
            eid = data.get("extract_model_id", "")
            data["extract_pool_ids"] = [eid] if eid else []
        if "extract_pool_priority" not in data:
            data["extract_pool_priority"] = {}
        return data

    # 旧格式迁移
    old_shared = data.get("cloud_configs", [])
    old_api_base = data.get("cloud_api_base", "")
    old_api_key = data.get("cloud_api_key", "")
    old_model_name = data.get("cloud_model_name", "")
    old_mode = data.get("ai_mode", "local")

    review_configs: list[dict[str, Any]] = []
    review_model_id = ""

    if isinstance(old_shared, list) and old_shared:
        for config in old_shared:
            if isinstance(config, dict):
                config["config_type"] = detect_config_type(config.get("api_base", ""))
                review_configs.append(config)
        old_review_id = data.get("review_model_id", "")
        if old_review_id and old_review_id != "local":
            review_model_id = old_review_id
    elif old_api_base and old_model_name:
        migrated = {
            "id": "migrated_1",
            "api_base": old_api_base,
            "api_key": old_api_key,
            "model_name": old_model_name,
            "config_type": detect_config_type(old_api_base),
        }
        review_configs = [migrated]
        if old_mode == "cloud":
            review_model_id = "migrated_1"

    data["vision_configs"] = []
    data["review_configs"] = review_configs
    data["vision_model_id"] = ""
    data["review_model_id"] = review_model_id

    if review_configs:
        logger.info(f"已迁移旧设置: review_configs={len(review_configs)}")

    ensure_extract_pool_from_review(data)
    _ensure_config_type(data.get("extract_configs", []))
    if "extract_pool_ids" not in data:
        eid = data.get("extract_model_id", "")
        data["extract_pool_ids"] = [eid] if eid else []
    if "extract_pool_priority" not in data:
        data["extract_pool_priority"] = {}

    return data


def _create_env_defaults() -> AISettingsModel:
    """从.env创建默认设置"""
    model = AISettingsModel()

    # 从.env创建本地模型默认配置
    if app_settings.LLM_API_BASE:
        local_config = ModelConfigItem(
            id="env_local",
            api_base=app_settings.LLM_API_BASE,
            api_key="ollama",
            model_name=app_settings.LLM_MODEL_NAME,
            config_type="local",
        )
        model.vision_configs.append(local_config)
        model.vision_model_id = "env_local"

        # 审查也创建一份本地默认
        local_config_review = ModelConfigItem(
            id="env_local_review",
            api_base=app_settings.LLM_API_BASE,
            api_key="ollama",
            model_name=app_settings.LLM_MODEL_NAME,
            config_type="local",
        )
        model.review_configs.append(local_config_review)
        model.review_model_id = "env_local_review"

    # 从.env创建云端模型默认配置
    if app_settings.CLOUD_API_KEY and app_settings.CLOUD_API_BASE:
        cloud_config = ModelConfigItem(
            id="env_cloud",
            api_base=app_settings.CLOUD_API_BASE,
            api_key=app_settings.CLOUD_API_KEY,
            model_name=app_settings.CLOUD_MODEL_NAME,
            config_type="cloud",
        )
        model.review_configs.append(cloud_config)
        if app_settings.AI_MODE == "cloud":
            model.review_model_id = "env_cloud"

    return model


# ==================== 核心函数 ====================

def load_ai_settings() -> AISettingsModel:
    """加载AI设置"""
    _ensure_dir()

    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 解密旧格式
            if data.get("cloud_api_key"):
                data["cloud_api_key"] = _deobfuscate(data["cloud_api_key"])
            if "cloud_configs" in data:
                _deobfuscate_config_list(data["cloud_configs"])

            # 迁移
            before_has_extract = "extract_configs" in data
            before_migrate = json.dumps(data, ensure_ascii=False, sort_keys=True)
            data = _migrate_old_format(data)
            migrated = (
                json.dumps(data, ensure_ascii=False, sort_keys=True)
                != before_migrate
            )
            extract_migrated = (not before_has_extract) and ("extract_configs" in data)

            # 解密新格式
            _deobfuscate_config_list(data.get("vision_configs", []))
            _deobfuscate_config_list(data.get("review_configs", []))
            _deobfuscate_config_list(data.get("judge_configs", []))
            _deobfuscate_config_list(data.get("extract_configs", []))
            _deobfuscate_config_list(data.get("scoring_configs", []))

            model = AISettingsModel(**data)
            healed = sync_all_legacy_model_ids(model)
            if migrated:
                if extract_migrated and SETTINGS_FILE.exists():
                    bak_path = SETTINGS_DIR / "ai_settings.json.bak_extract_migrate"
                    if not bak_path.exists():
                        shutil.copy2(SETTINGS_FILE, bak_path)
                        logger.info(f"extract 池迁移前已备份: {bak_path}")
            if migrated or healed:
                _save_ai_settings_sync(model)
                if migrated:
                    logger.info("AI设置迁移已写回持久化文件")
                if healed:
                    logger.info(
                        f"AI设置遗留单选ID已从调度池回填: {','.join(healed)}"
                    )
            logger.debug(
                f"已加载AI设置: vision={model.vision_model_id}({len(model.vision_configs)}), "
                f"review={model.review_model_id}({len(model.review_configs)}), "
                f"judge={model.judge_model_id}({len(model.judge_configs)}), "
                f"extract={model.extract_model_id}({len(model.extract_configs)}), "
                f"scoring={model.scoring_model_id}({len(model.scoring_configs)})"
            )
            return model
        except Exception as e:
            logger.warning(f"加载AI设置失败，使用默认值: {e}")

    return _create_env_defaults()


def _save_ai_settings_sync(model: AISettingsModel) -> None:
    """同步写入AI设置文件（原子写入）"""
    _ensure_dir()
    data = model.model_dump()
    if data.get("cloud_api_key"):
        data["cloud_api_key"] = _obfuscate(data["cloud_api_key"])
    _obfuscate_config_list(data.get("cloud_configs", []))
    _obfuscate_config_list(data.get("vision_configs", []))
    _obfuscate_config_list(data.get("review_configs", []))
    _obfuscate_config_list(data.get("judge_configs", []))
    _obfuscate_config_list(data.get("extract_configs", []))
    _obfuscate_config_list(data.get("scoring_configs", []))

    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(SETTINGS_DIR), suffix='.tmp', prefix='ai_settings_'
    )
    try:
        with open(tmp_fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        if SETTINGS_FILE.exists():
            SETTINGS_FILE.unlink()
        Path(tmp_path).rename(str(SETTINGS_FILE))
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise


async def save_ai_settings(model: AISettingsModel) -> None:
    """保存AI设置（异步锁 + 线程池文件I/O, 防止阻塞事件循环）"""
    async with _settings_lock:
        try:
            await asyncio.to_thread(_save_ai_settings_sync, model)
        except Exception as e:
            logger.error(f"保存AI设置失败: {e}")
            raise


async def publish_runtime_invalidate(
    purpose: Optional[str] = None,
    *,
    reload_dispatcher: bool = True,
) -> None:
    """广播 AI 设置运行时失效事件, 通知其它进程刷新 Dispatcher。

    Redis 不可用或广播失败时仅记录 warning。当前进程已在保存函数内同步刷新,
    因此广播失败不应阻断用户保存设置。
    """
    event_type = _INVALIDATE_TYPE_RELOAD if reload_dispatcher else _INVALIDATE_TYPE_PRIORITY
    payload = {
        "type": event_type,
        "purpose": purpose,
        "reload_dispatcher": reload_dispatcher,
    }
    try:
        from app.core.redis import get_redis_pool

        redis_client = await get_redis_pool()
        if redis_client is None:
            logger.warning(
                f"AISettings: Redis 不可用, 无法广播运行时失效事件: {payload}"
            )
            return
        await redis_client.publish(
            AI_SETTINGS_INVALIDATE_CHANNEL,
            json.dumps(payload),
        )
        logger.info(
            f"AISettings: 已广播运行时失效事件 {payload} -> {AI_SETTINGS_INVALIDATE_CHANNEL}"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"AISettings: 广播运行时失效事件失败 (不影响保存): {payload}, err={e}"
        )


async def start_runtime_invalidate_subscriber(redis_client: Any) -> None:
    """启动 AI 设置运行时失效订阅, 收到事件后刷新本进程 Dispatcher。"""
    global _invalidate_sub_task
    global _invalidate_sub_stop
    global _invalidate_sub_client
    global _invalidate_sub_owns_client

    if _invalidate_sub_task is not None and not _invalidate_sub_task.done():
        logger.debug("AISettings: runtime invalidate subscriber 已在运行, 跳过重复启动")
        return

    sub_client = None
    owns_client = False
    try:
        from app.core.redis import create_redis_pubsub_client

        sub_client = await create_redis_pubsub_client()
        owns_client = sub_client is not None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"AISettings: 独立 Pub/Sub 客户端创建异常, 尝试复用传入 redis: {e}")

    if sub_client is None:
        sub_client = redis_client
    if sub_client is None:
        logger.warning(
            "AISettings: redis 不可用, runtime invalidate subscriber 未启动 "
            "(本进程 AI Dispatcher 无法随设置页面自动刷新)"
        )
        return

    _invalidate_sub_stop = asyncio.Event()
    _invalidate_sub_client = sub_client
    _invalidate_sub_owns_client = owns_client
    _invalidate_sub_task = asyncio.create_task(
        _runtime_invalidate_subscriber_loop(sub_client, _invalidate_sub_stop)
    )
    logger.info(
        f"AISettings: runtime invalidate subscriber 已启动, channel={AI_SETTINGS_INVALIDATE_CHANNEL}"
    )


async def stop_runtime_invalidate_subscriber() -> None:
    """停止 AI 设置运行时失效订阅。"""
    global _invalidate_sub_task
    global _invalidate_sub_stop
    global _invalidate_sub_client
    global _invalidate_sub_owns_client

    if _invalidate_sub_stop is not None:
        _invalidate_sub_stop.set()
    task = _invalidate_sub_task
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except asyncio.TimeoutError:
            task.cancel()
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug(f"AISettings: 订阅协程退出异常 (忽略): {e}")
    _invalidate_sub_task = None
    _invalidate_sub_stop = None
    if _invalidate_sub_owns_client and _invalidate_sub_client is not None:
        try:
            await _invalidate_sub_client.aclose()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"AISettings: Pub/Sub 客户端关闭异常 (忽略): {e}")
    _invalidate_sub_client = None
    _invalidate_sub_owns_client = False


async def _runtime_invalidate_subscriber_loop(
    redis_client: Any,
    stop_event: asyncio.Event,
) -> None:
    """订阅循环: 断连时指数退避重连, 直到 stop_event 置位。"""
    backoff = 1.0
    max_backoff = 30.0
    while not stop_event.is_set():
        pubsub = None
        try:
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(AI_SETTINGS_INVALIDATE_CHANNEL)
            logger.debug(
                f"AISettings subscriber: 已订阅 {AI_SETTINGS_INVALIDATE_CHANNEL}"
            )
            backoff = 1.0
            while not stop_event.is_set():
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue
                await _handle_runtime_invalidate_message(message)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"AISettings subscriber 异常, {backoff:.1f}s 后重连: {e}"
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, max_backoff)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(AI_SETTINGS_INVALIDATE_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass
    logger.info("AISettings subscriber 循环退出")


async def _handle_runtime_invalidate_message(message: dict) -> None:
    """处理单条 AI 设置失效消息。"""
    try:
        raw = message.get("data")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AISettings subscriber 消息解析失败 (忽略): {e}")
        return

    msg_type = payload.get("type")
    purpose = payload.get("purpose")
    if purpose not in (None, "vision", "review", "judge", "extract", "scoring"):
        logger.debug(f"AISettings subscriber: 未知 purpose, 忽略: {payload}")
        return

    try:
        if msg_type == _INVALIDATE_TYPE_RELOAD:
            _sync_to_runtime(load_ai_settings(), purpose=purpose)
            logger.info(
                f"AISettings subscriber: 收到 reload 事件, 已刷新 Dispatcher({purpose or 'all'})"
            )
        elif msg_type == _INVALIDATE_TYPE_PRIORITY:
            from app.core.ai.ai_factory import sync_channel_priorities

            sync_channel_priorities(purpose=purpose)
            logger.info(
                f"AISettings subscriber: 收到 priority 事件, 已热更新优先级({purpose or 'all'})"
            )
        else:
            logger.debug(f"AISettings subscriber: 未知消息类型, 忽略: {payload}")
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"AISettings subscriber: 刷新运行时配置失败 (不影响订阅): {e}"
        )


async def set_model_id(purpose: Purpose, model_id: str) -> AISettingsModel:
    """设置当前选中的模型"""
    current = load_ai_settings()

    if model_id and not current.get_config_by_id(purpose, model_id):
        raise ValueError(f"无效的模型ID: {model_id}")

    current.set_model_id(purpose, model_id)

    if purpose == "review":
        config = current.get_active_review_config()
        current.ai_mode = current.get_active_review_mode()
        if config and config.config_type == "cloud":
            current.cloud_api_base = config.api_base
            current.cloud_api_key = config.api_key
            current.cloud_model_name = config.model_name

    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)
    return current


async def add_config(purpose: Purpose, config: ModelConfigItem) -> AISettingsModel:
    """添加模型配置（自动检测类型并校验限制）"""
    config.config_type = detect_config_type(config.api_base)

    current = load_ai_settings()

    local_count = current.count_by_type(purpose, "local")
    cloud_count = current.count_by_type(purpose, "cloud")

    if config.config_type == "local" and local_count >= MAX_LOCAL_PER_PURPOSE:
        raise ValueError(f"本地模型配置已达上限（{MAX_LOCAL_PER_PURPOSE}个）")
    if config.config_type == "cloud" and cloud_count >= MAX_CLOUD_PER_PURPOSE:
        raise ValueError(f"云端模型配置已达上限（{MAX_CLOUD_PER_PURPOSE}个）")

    if current.get_config_by_id(purpose, config.id):
        raise ValueError(f"配置ID已存在: {config.id}")

    current.get_configs(purpose).append(config)
    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)

    type_label = "本地" if config.config_type == "local" else "云端"
    logger.info(f"已添加{purpose}{type_label}配置: id={config.id}, model={config.model_name}")
    return current


async def update_config(
    purpose: Purpose, config_id: str,
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    model_name: Optional[str] = None,
    api_keys: Optional[list[dict]] = None,
    temperature: Optional[float] = None,
    temperature_mode: Optional[TemperatureMode] = None,
    temperature_exact: Optional[float] = None,
    temperature_min: Optional[float] = None,
    temperature_max: Optional[float] = None,
) -> AISettingsModel:
    """更新模型配置"""
    current = load_ai_settings()
    config = current.get_config_by_id(purpose, config_id)
    if not config:
        raise ValueError(f"配置不存在: {config_id}")

    if api_base is not None:
        new_type = detect_config_type(api_base)
        if new_type != config.config_type:
            new_count = current.count_by_type(purpose, new_type)
            max_count = MAX_LOCAL_PER_PURPOSE if new_type == "local" else MAX_CLOUD_PER_PURPOSE
            if new_count >= max_count:
                type_label = "本地" if new_type == "local" else "云端"
                raise ValueError(f"{type_label}模型配置已达上限（{max_count}个）")
        config.api_base = api_base
        config.config_type = new_type
    if api_key is not None:
        config.api_key = api_key
    if model_name is not None:
        config.model_name = model_name
    if api_keys is not None:
        merged: list[ApiKeyItem] = []
        for i, item in enumerate(api_keys):
            if not item.get("key") and i < len(config.api_keys):
                existing = config.api_keys[i]
                if not existing.key:
                    continue
                existing.label = item.get("label", existing.label)
                existing.enabled = item.get("enabled", existing.enabled)
                merged.append(existing)
            elif item.get("key"):
                merged.append(ApiKeyItem(**item))
        config.api_keys = merged
    if temperature is not None:
        config.temperature = temperature
    if temperature_mode is not None:
        config.temperature_mode = temperature_mode
    if temperature_exact is not None:
        config.temperature_exact = temperature_exact
    if temperature_min is not None:
        config.temperature_min = temperature_min
    if temperature_max is not None:
        config.temperature_max = temperature_max

    if purpose == "review" and current.review_model_id == config_id:
        if config.config_type == "cloud":
            current.cloud_api_base = config.api_base
            current.cloud_api_key = config.api_key
            current.cloud_model_name = config.model_name

    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)
    return current


async def duplicate_config(
    source_purpose: Purpose,
    source_config_id: str,
    target_purpose: Purpose,
) -> ModelConfigItem:
    """复制模型配置（含明文密钥）到目标用途，返回新建配置。

    - 校验源配置存在；不存在抛 ValueError("源配置不存在: ...")
    - 完整复制 api_base / api_key / api_keys / temperature*
    - 目标分组中 model_name 重复时自动追加 " (副本)" / " (副本 N)"
    - 上限校验复用 add_config 内部逻辑（达到上限抛 ValueError）
    - 不复制 pool_ids / priority，避免误添加到调度池
    """
    import time
    import uuid

    current = load_ai_settings()
    source = current.get_config_by_id(source_purpose, source_config_id)
    if not source:
        raise ValueError(f"源配置不存在: {source_purpose}/{source_config_id}")

    target_configs = current.get_configs(target_purpose)
    existing_names = {c.model_name for c in target_configs}
    new_name = source.model_name
    if new_name in existing_names:
        n = 1
        candidate = f"{source.model_name} (副本)"
        while candidate in existing_names:
            n += 1
            candidate = f"{source.model_name} (副本 {n})"
        new_name = candidate

    config_type = detect_config_type(source.api_base)
    new_id = f"{target_purpose}_{config_type}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"

    new_config = ModelConfigItem(
        id=new_id,
        api_base=source.api_base,
        api_key=source.api_key,
        api_keys=[
            ApiKeyItem(key=k.key, label=k.label, enabled=k.enabled)
            for k in source.api_keys
        ],
        model_name=new_name,
        config_type=config_type,
        temperature=source.temperature,
        temperature_mode=source.temperature_mode,
        temperature_exact=source.temperature_exact,
        temperature_min=source.temperature_min,
        temperature_max=source.temperature_max,
        # P0-C: 能力档案随配置复制(同一端点+模型, 能力一致)
        capabilities=(
            source.capabilities.model_copy(deep=True)
            if source.capabilities else None
        ),
    )
    await add_config(target_purpose, new_config)
    logger.info(
        f"已复制配置: {source_purpose}/{source_config_id} -> {target_purpose}/{new_id} ({new_name})"
    )
    return new_config


async def delete_config(purpose: Purpose, config_id: str) -> AISettingsModel:
    """删除模型配置"""
    current = load_ai_settings()
    configs = current.get_configs(purpose)
    new_configs = [c for c in configs if c.id != config_id]

    if len(new_configs) == len(configs):
        raise ValueError(f"配置不存在: {config_id}")

    current.set_configs(purpose, new_configs)

    # 同步从调度池移除
    pool_ids = current.get_pool_ids(purpose)
    if config_id in pool_ids:
        current.set_pool_ids(purpose, [pid for pid in pool_ids if pid != config_id])
    # 同步清理 priority
    priority = current.get_pool_priority(purpose)
    if config_id in priority:
        del priority[config_id]
        current.set_pool_priority(purpose, priority)

    deleted_selected = current.get_model_id(purpose) == config_id
    if deleted_selected and purpose == "review":
        current.ai_mode = "local"
    sync_legacy_model_id(current, purpose)
    if deleted_selected:
        logger.warning(
            f"{purpose}模型配置被删除，遗留单选已对齐: "
            f"{current.get_model_id(purpose) or '空'}"
        )

    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)
    logger.info(f"已删除{purpose}配置: id={config_id}")
    return current


def _sync_to_runtime(
    model: AISettingsModel, purpose: Optional[str] = None
) -> None:
    """同步到运行时

    Args:
        model: 当前 AI 设置模型
        purpose: 指定仅重建 "review" 或 "vision" 侧的 Dispatcher,
                 None 则全量重建(启动阶段使用)
    """
    from app.core.ai.cloud_client import reset_cloud_client, reset_local_client
    from app.core.config import set_ai_mode

    set_ai_mode(model.get_active_review_mode())

    review_config = model.get_active_review_config()
    if review_config and review_config.config_type == "cloud":
        app_settings.CLOUD_API_BASE = review_config.api_base
        app_settings.CLOUD_API_KEY = review_config.api_key
        app_settings.CLOUD_MODEL_NAME = review_config.model_name

    reset_cloud_client()
    reset_local_client()

    try:
        from app.core.ai.ai_factory import reset_config_clients
        reset_config_clients(purpose=purpose)
    except ImportError:
        pass


async def set_pool_ids(purpose: Purpose, pool_ids: list[str]) -> AISettingsModel:
    """设置调度池配置ID列表"""
    current = load_ai_settings()
    configs = current.get_configs(purpose)
    valid_ids = {c.id for c in configs}
    invalid = [pid for pid in pool_ids if pid not in valid_ids]
    if invalid:
        raise ValueError(f"无效的配置ID: {', '.join(invalid)}")
    current.set_pool_ids(purpose, pool_ids)
    # 清理不在池中的 priority 条目
    pool_set = set(pool_ids)
    old_priority = current.get_pool_priority(purpose)
    cleaned = {k: v for k, v in old_priority.items() if k in pool_set}
    current.set_pool_priority(purpose, cleaned)
    sync_legacy_model_id(current, purpose)
    await save_ai_settings(current)
    _sync_to_runtime(current, purpose=purpose)
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=True)
    logger.info(f"{purpose}调度池已更新: {pool_ids}")
    return current


async def set_pool_priority(purpose: Purpose, priority: dict[str, int]) -> AISettingsModel:
    """设置调度池优先级映射 {config_id: 0|100|999}"""
    current = load_ai_settings()
    pool_ids = set(current.get_pool_ids(purpose))
    invalid_ids = [k for k in priority if k not in pool_ids]
    if invalid_ids:
        raise ValueError(f"配置ID不在调度池中: {', '.join(invalid_ids)}")
    allowed_str = "/".join(str(v) for v in sorted(VALID_PRIORITIES))
    invalid_vals = [f"{k}={v}" for k, v in priority.items() if v not in VALID_PRIORITIES]
    if invalid_vals:
        raise ValueError(f"无效优先级(允许{allowed_str}): {', '.join(invalid_vals)}")
    for level in PRIORITIES_SINGLETON:
        members = [k for k, v in priority.items() if v == level]
        if len(members) > 1:
            label = PRIORITY_LABELS.get(level, str(level))
            raise ValueError(f"{label}最多设置1个")
    current.set_pool_priority(purpose, priority)
    await save_ai_settings(current)
    try:
        from app.core.ai.ai_factory import sync_channel_priorities
        sync_channel_priorities(purpose=purpose)
    except ImportError:
        pass
    await publish_runtime_invalidate(purpose=purpose, reload_dispatcher=False)
    logger.info(f"{purpose}调度优先级已更新: {priority}")
    return current


def init_ai_settings() -> None:
    """初始化AI设置"""
    model = load_ai_settings()
    _sync_to_runtime(model)
    logger.info(
        f"AI设置初始化完成: vision={model.vision_model_id}({len(model.vision_configs)}), "
        f"review={model.review_model_id}({len(model.review_configs)})"
    )


# ==================== 兼容旧接口 ====================

# 保留旧名称别名
CloudConfigItem = ModelConfigItem

async def update_ai_settings(
    ai_mode: Optional[str] = None, cloud_api_base: Optional[str] = None,
    cloud_api_key: Optional[str] = None, cloud_model_name: Optional[str] = None
) -> AISettingsModel:
    current = load_ai_settings()
    if ai_mode is not None:
        current.ai_mode = ai_mode
    if cloud_api_base is not None:
        current.cloud_api_base = cloud_api_base
    if cloud_api_key is not None:
        current.cloud_api_key = cloud_api_key
    if cloud_model_name is not None:
        current.cloud_model_name = cloud_model_name
    await save_ai_settings(current)
    _sync_to_runtime(current)
    await publish_runtime_invalidate(purpose=None, reload_dispatcher=True)
    return current

async def update_model_assignment(
    vision_model_id: Optional[str] = None, review_model_id: Optional[str] = None
) -> AISettingsModel:
    if vision_model_id is not None:
        await set_model_id("vision", vision_model_id)
    if review_model_id is not None:
        await set_model_id("review", review_model_id)
    return load_ai_settings()
