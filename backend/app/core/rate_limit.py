"""限流器单例

职责: 提供全局唯一的 slowapi Limiter, 供 main.py 注册与各路由 @limiter.limit 复用。
不做: 不定义具体限流策略数值(由 settings.RATE_LIMIT_* 驱动)、不处理业务鉴权。
"""

# slowapi.Limiter 初始化会触发 starlette Config 读取 .env;
# Windows 默认 GBK 解码含中文的 UTF-8 .env 会失败, 故在 import slowapi 前
# 幂等地打 utf-8 补丁(与 main.py 同源, 谁先导入都安全)。
from starlette.config import Config as _StarletteConfig


def _read_file_utf8(self, file_name) -> dict[str, str]:
    file_values: dict[str, str] = {}
    with open(file_name, encoding="utf-8") as input_file:
        for line in input_file.readlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                file_values[key.strip()] = value.strip().strip("\"'")
    return file_values


_StarletteConfig._read_file = _read_file_utf8

from slowapi import Limiter  # noqa: E402
from slowapi.util import get_remote_address  # noqa: E402

limiter = Limiter(key_func=get_remote_address)
