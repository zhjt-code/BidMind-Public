from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.prod.yml"
DOCKERFILE_PATH = REPO_ROOT / "backend" / "Dockerfile"
DOCKERIGNORE_PATH = REPO_ROOT / "backend" / ".dockerignore"

EXPECTED_TORCH_FIND_LINKS = "${TORCH_FIND_LINKS:-}"
BACKEND_IMAGE_SERVICES = (
    "backend",
    "worker-process",
    "worker-llm",
    "worker-maintenance",
)


def _load_prod_compose() -> dict:
    """加载生产 compose, 锁定 backend/worker torch 源契约。"""
    with COMPOSE_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def test_prod_backend_services_share_optional_torch_find_links() -> None:
    compose = _load_prod_compose()
    for name in BACKEND_IMAGE_SERVICES:
        args = compose["services"][name]["build"]["args"]
        assert args["TORCH_FIND_LINKS"] == EXPECTED_TORCH_FIND_LINKS, name


def test_backend_dockerfile_uses_find_links_for_optional_wheel_directory() -> None:
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "ARG TORCH_FIND_LINKS" in text
    assert "--find-links" in text
    assert "download.pytorch.org/whl/cu124" in text
    assert "download-r2.pytorch.org" not in text
    assert "pip install /tmp/torch.whl" not in text
    assert "torch==2.6.0+cu124" in text


def test_backend_dockerignore_excludes_host_wheels_dir() -> None:
    text = DOCKERIGNORE_PATH.read_text(encoding="utf-8")
    assert "wheels/" in text
