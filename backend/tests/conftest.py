"""测试基础设施

使用 FastAPI TestClient（同步模式），内部通过 anyio 桥接异步代码，
完全绕过 asyncpg + pytest-asyncio 在 Windows 上的 event loop 兼容问题。
"""

import os
import sys
import time
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# 保证仓库根在 sys.path，使 `from backend.scripts...` 形式的导入
# 与工作目录无关。此前从 backend/ 目录跑 pytest 会在收集期直接中断
# （test_scoring_holdout_admission_gate.py 报 No module named 'backend'），
# 而从仓库根跑又会让依赖相对路径的用例读错文件——两种调用互相排斥、
# 全量回归因此没有可跑通的统一入口。
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://bidmind:bidmind@localhost:5432/bidmind_lite",
)
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ["DB_USE_NULL_POOL"] = "1"
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-key-not-for-prod")
os.environ.setdefault("ADMIN_PASSWORD", "<TEST_PASSWORD>")
# 测试套件固定关闭邮箱验证硬拦截, 保证 register->login 的确定性, 不受开发 .env 开关影响;
# 邮箱验证/指纹等用例通过 monkeypatch 显式开启 EMAIL_VERIFICATION_ENABLED=True。
os.environ["EMAIL_VERIFICATION_ENABLED"] = "false"

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limit_storage():
    """Keep one test's synthetic client requests from throttling later tests."""
    from app.core.rate_limit import limiter

    limiter._storage.reset()


@pytest.fixture(autouse=True)
def _reset_timeout_avoids():
    """隔离 进程级超时粘滞，避免用例间互相污染选路。"""
    from app.core.ai.timeout_avoidance import reset_timeout_avoids
    reset_timeout_avoids()
    yield
    reset_timeout_avoids()


@pytest.fixture(autouse=True)
def _mark_regression_traffic():
    """测试流量统一标记为 regression, 不污染生产可靠性统计与错误日志。

 覆盖两条观测链路: llm_attempt_events(P0-A) 与 system_error_logs,
 二者共用 app.core.observability.traffic_class 的同一个 ContextVar。
 注意 contextvar 不跨线程传播, TestClient 的请求跑在 anyio portal 线程里,
 那部分由 resolve_traffic_class 的进程级兜底(检测 pytest)负责。
 """
    from app.core.observability.traffic_class import (
        TRAFFIC_CLASS_REGRESSION,
        current_traffic_class,
    )
    token = current_traffic_class.set(TRAFFIC_CLASS_REGRESSION)
    yield
    try:
        current_traffic_class.reset(token)
    except Exception:
        pass


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# --------------- Auth helpers ---------------

_RUN_ID = str(int(time.time()))[-6:]


def _unique_user() -> dict:
    _unique_user._counter = getattr(_unique_user, "_counter", 0) + 1
    tag = f"{_RUN_ID}_{_unique_user._counter}"
    return {
        "username": f"t{tag}",
        "email": f"t{tag}@example.com",
        "password": "TestPass@123",
        "confirm_password": "TestPass@123",
    }


@pytest.fixture(scope="session")
def registered_user(client: TestClient) -> dict:
    user_data = _unique_user()
    resp = client.post("/api/auth/register", json=user_data)
    assert resp.status_code == 200, f"注册失败: {resp.text}"
    user_data["id"] = resp.json()["data"]["id"]
    return user_data


@pytest.fixture(scope="session")
def auth_headers(client: TestClient, registered_user: dict) -> dict:
    resp = client.post("/api/auth/login", json={
        "username": registered_user["username"],
        "password": registered_user["password"],
    })
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def admin_headers(client: TestClient) -> dict:
    """管理员登录; 若账户被锁定（之前的测试累积失败），先重置锁定状态"""
    resp = client.post("/api/auth/login", json={
        "username": settings.ADMIN_USERNAME,
        "password": settings.ADMIN_PASSWORD,
    })
    if resp.status_code == 403 and "锁定" in resp.text:
        _reset_admin_lock()
        resp = client.post("/api/auth/login", json={
            "username": settings.ADMIN_USERNAME,
            "password": settings.ADMIN_PASSWORD,
        })
    assert resp.status_code == 200, f"管理员登录失败: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _reset_admin_lock():
    """直接通过同步 DB 操作重置管理员锁定状态"""
    from sqlalchemy import create_engine, text
    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        conn.execute(text(
            "UPDATE users SET login_attempts = 0, locked_until = NULL "
            "WHERE username = :username"
        ), {"username": settings.ADMIN_USERNAME})
        conn.commit()
    engine.dispose()


# ---------------------------------------------------------------------------
# 真实 app.core.ai 模块恢复 fixture
#
# 某些 test_tender_extractor_*.py 文件在模块级别（非 fixture 内）将
#   sys.modules["app.core.ai"]
#   sys.modules["app.core.ai.prompts"]
#   sys.modules["app.core.ai.prompts.tender_extract"]
# 替换为无 __path__ 的伪包，导致后续测试导入
#   app.core.ai.prompts.scoring_full_candidate
# 时抛出 ModuleNotFoundError。
#
# 此 fixture 清理伪包、重新导入真实模块；使用 autouse=False，
# 通过 pytestmark = pytest.mark.usefixtures("restore_real_ai_modules")
# 在受影响的测试模块中按需启用。
# ---------------------------------------------------------------------------

_AI_FAKE_KEYS = (
    "app.core.ai",
    "app.core.ai.prompts",
    "app.core.ai.prompts.tender_extract",
    "app.core.ai.prompts.scoring_full_candidate",
)


@pytest.fixture(autouse=True)
def restore_real_ai_modules():
    """清理无效伪包，重新导入真实 app.core.ai 模块。

 判定标准: sys.modules 中该 key 对应对象无 __file__ 且 __path__ 为空列表 → 伪包。
 仅清理伪包，保留已正确加载的真实模块。

 为什么改 autouse（-08-03）:
 6 个 `test_tender_extractor_*.py` 在**模块级**（即 collection 期）执行
 `sys.modules["app.core.ai"] = fake_pkg`，伪包只带 `get_review_client`/`LLMClient`。
 污染一旦发生就贯穿整个会话，此后任何测试 patch `app.core.ai.get_vision_client`
 即报 AttributeError、生产代码 `from app.core.ai import release_project_binding`
 即报 ImportError（"unknown location" 即伪包无 __file__）。
 实测: `test_document_image_llm_task.py` 单跑 7 项全过，
 前置 `test_tender_extractor_category_merge.py` 后立即失败。
 本 fixture 原本就能修复，只是需显式声明才生效——按需声明等于要求
 每个受害者预知自己会被污染，不成立；污染是全局的，防线也必须是全局的。
 """
    import importlib
    import sys

    # 收集需要清理的伪模块 key（包含伪包 __path__==[] 和伪叶模块 __file__==None）
    to_remove = []
    for key in list(sys.modules.keys()):
        if not key.startswith("app.core.ai"):
            continue
        mod = sys.modules[key]
        # 伪包: 无 __file__ 且 __path__ 为空列表（通过 fake_pkg.__path__ = [] 设置）
        is_fake_pkg = (
            getattr(mod, "__file__", None) is None
            and getattr(mod, "__path__", None) == []
        )
        # 伪叶模块: 无 __file__ 且无 __path__（types.ModuleType 直接注入）
        is_fake_leaf = (
            getattr(mod, "__file__", None) is None
            and not hasattr(mod, "__path__")
        )
        if is_fake_pkg or is_fake_leaf:
            to_remove.append(key)

    # 移除伪包（按从深到浅的顺序，避免父包先恢复又被子包覆盖）
    for key in sorted(to_remove, key=len, reverse=True):
        sys.modules.pop(key, None)

    # 按**实际清理的清单**逐个恢复，父包在前。
    # 原实现只恢复硬编码的 3 个模块，`app.core.ai.gates.gates` 这类被顺带清掉的
    # 子模块不会回来，后续 monkeypatch("app.core.ai.gates.gates.*") 即报
    # "'module' object at app.core.ai.gates has no attribute 'gates'"。
    # 改 autouse 后这条路径才被普遍触达，故一并修正。
    for mod_path in ["app.core.ai", *sorted(to_remove, key=len)]:
        try:
            importlib.import_module(mod_path)
        except ImportError:
            pass  # 伪包名可能没有对应真实模块，静默跳过

    # 重建父子属性绑定。
    # 弹出并重新导入父包会产生**新的**模块对象，而仍留在 sys.modules 里的真实子包
    # （如 app.core.ai.gates，非伪包故未被清理）不会自动绑到新父对象上，
    # 于是 monkeypatch("app.core.ai.gates.build_pipeline_for") 报
    # "'module' object at app.core.ai has no attribute 'gates'"。
    # import_module 对已在 sys.modules 的子模块是 no-op，不会补这层绑定，必须显式重绑。
    for key in sorted(k for k in sys.modules if k.startswith("app.core.ai.")):
        parent_name, _, child = key.rpartition(".")
        parent = sys.modules.get(parent_name)
        child_mod = sys.modules.get(key)
        if parent is not None and child_mod is not None:
            if getattr(parent, child, None) is not child_mod:
                setattr(parent, child, child_mod)

    yield

    # teardown: 不做额外清理（保持已加载的真实模块在 session 内复用）
