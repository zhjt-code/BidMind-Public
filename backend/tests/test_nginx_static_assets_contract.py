from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
NGINX_CONF = REPO_ROOT / "nginx" / "nginx.conf"


def _read_nginx_conf() -> str:
    """Read the production Nginx config for static contract tests."""
    return NGINX_CONF.read_text(encoding="utf-8")


def test_assets_location_returns_404_instead_of_spa_fallback() -> None:
    """Missing hash assets must return 404 instead of falling back to index."""
    nginx_conf = _read_nginx_conf()

    assert "location /assets/" in nginx_conf
    assert "try_files $uri =404;" in nginx_conf
    assert 'Cache-Control "public, max-age=31536000, immutable"' in nginx_conf


def test_index_html_disables_strong_browser_cache() -> None:
    """The entry HTML must avoid strong browser caching."""
    nginx_conf = _read_nginx_conf()

    assert "location = /index.html" in nginx_conf
    assert 'Cache-Control "no-cache, must-revalidate"' in nginx_conf


def test_upload_size_limit_is_200mb_on_cloud_and_local_gates() -> None:
    """云端 nginx 与本地 FastAPI / 写标素材上限必须同为 200MB，避免一层放行一层 413。"""
    from app.api.routers.files import _MAX_UPLOAD_SIZE_MB
    from app.core.config import settings

    nginx_conf = _read_nginx_conf()
    assert _MAX_UPLOAD_SIZE_MB == 200
    assert settings.WRITING_MAX_MATERIAL_SIZE_MB == 200
    assert f"client_max_body_size {_MAX_UPLOAD_SIZE_MB}M;" in nginx_conf
