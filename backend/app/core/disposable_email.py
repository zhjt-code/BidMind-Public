"""一次性邮箱域名识别

职责: 判定邮箱域名是否属于一次性/临时邮箱(注册阶段拦截刷号)
不做: 邮件发送/验证流程(归 AuthService / email_sender)

内置常见一次性域名表(data/disposable_email_domains.txt), 可经
settings.DISPOSABLE_EMAIL_EXTRA(逗号分隔)追加。表缺失时退化为仅用配置补充,
不抛错。
"""

from functools import lru_cache
from pathlib import Path

from loguru import logger

from app.core.config import settings

_DATA_FILE = Path(__file__).parent / "data" / "disposable_email_domains.txt"


@lru_cache(maxsize=1)
def _load_domains() -> frozenset[str]:
    domains: set[str] = set()
    try:
        if _DATA_FILE.exists():
            for line in _DATA_FILE.read_text(encoding="utf-8").splitlines():
                d = line.strip().lower()
                if d and not d.startswith("#"):
                    domains.add(d)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"加载一次性邮箱域名表失败(降级为仅配置补充): {e}")

    extra = getattr(settings, "DISPOSABLE_EMAIL_EXTRA", "") or ""
    for d in extra.split(","):
        d = d.strip().lower()
        if d:
            domains.add(d)
    return frozenset(domains)


def is_disposable_email(email: str) -> bool:
    """邮箱域名是否在一次性黑名单中"""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower()
    return domain in _load_domains()
