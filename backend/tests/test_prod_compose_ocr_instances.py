from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.prod.yml"

EXPECTED_OCR_URLS = [
    "http://ocr-1:9000",
    "http://ocr-2:9000",
    "http://ocr-3:9000",
    "http://ocr-4:9000",
    "http://ocr-5:9000",
]
EXPECTED_OCR_SERVICES = {f"ocr-{idx}" for idx in range(1, 6)}


def _load_prod_compose() -> dict:
    """加载生产 compose 文件, 用于锁定部署配置契约。"""
    with COMPOSE_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def _extract_default_urls(value: str) -> list[str]:
    """从 Docker Compose env 插值表达式中提取默认 OCR URL 列表。"""
    prefix = "${OCR_SERVICE_URLS:-"
    suffix = "}"
    assert value.startswith(prefix)
    assert value.endswith(suffix)
    return value.removeprefix(prefix).removesuffix(suffix).split(",")


def _has_nvidia_gpu(service: dict) -> bool:
    """服务是否声明 NVIDIA GPU reservation。"""
    devices = (
        service.get("deploy", {})
        .get("resources", {})
        .get("reservations", {})
        .get("devices", [])
    )
    return any(
        item.get("driver") == "nvidia" and "gpu" in item.get("capabilities", [])
        for item in devices
    )


def test_prod_compose_defines_five_ocr_services() -> None:
    compose = _load_prod_compose()
    services = set(compose["services"].keys())

    assert EXPECTED_OCR_SERVICES.issubset(services)


def test_llm_worker_embedding_inherits_gpu_auto_path() -> None:
    """LLM worker 上的在线 encode（审查检索/提取/串标/写标匹配）走 GPU auto，不再钉死 CPU。"""
    compose = _load_prod_compose()
    llm_env = compose["services"]["worker-llm"]["environment"]
    process_env = compose["services"]["worker-process"]["environment"]

    assert llm_env["EMBEDDING_DEVICE"] == "auto"
    assert llm_env["EMBEDDING_BACKEND"] == "pytorch"
    assert _has_nvidia_gpu(compose["services"]["worker-llm"])
    # 同形态第二例：离线文档向量化 worker 仍是 auto + GPU
    assert process_env["EMBEDDING_DEVICE"] == "auto"
    assert _has_nvidia_gpu(compose["services"]["worker-process"])


def test_worker_and_backend_default_to_five_ocr_urls() -> None:
    compose = _load_prod_compose()

    worker_urls = _extract_default_urls(
        compose["x-worker-common-env"]["OCR_SERVICE_URLS"]
    )
    backend_env = compose["services"]["backend"]["environment"]
    backend_ocr_env = next(
        item for item in backend_env if item.startswith("OCR_SERVICE_URLS=")
    )
    backend_urls = _extract_default_urls(
        backend_ocr_env.removeprefix("OCR_SERVICE_URLS=")
    )

    assert worker_urls == EXPECTED_OCR_URLS
    assert backend_urls == EXPECTED_OCR_URLS
