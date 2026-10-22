"""业务异常定义

Service 层使用领域异常, Router 层捕获后转换为 HTTPException。
"""


class ServiceError(Exception):
    """Service 层基础异常

 error_code/error_data 为可选的 machine-readable 扩展：
 仅当异常需要前端程序化识别（如余额不足）时填写，
 默认 None 时 HTTP 层输出与历史字节级一致的字符串 detail。
 """

    def __init__(
        self,
        detail: str,
        status_code: int = 400,
        *,
        error_code: str | None = None,
        error_data: dict | None = None,
    ):
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code
        self.error_data = error_data
        super().__init__(detail)


class NotFoundError(ServiceError):
    """资源不存在"""

    def __init__(self, detail: str = "资源不存在"):
        super().__init__(detail, 404)


class ForbiddenError(ServiceError):
    """无权限"""

    def __init__(self, detail: str = "无权限操作"):
        super().__init__(detail, 403)


class ConflictError(ServiceError):
    """资源冲突 (重复等)"""

    def __init__(self, detail: str = "资源冲突"):
        super().__init__(detail, 409)


class AuthenticationError(ServiceError):
    """认证失败"""

    def __init__(self, detail: str = "认证失败"):
        super().__init__(detail, 401)
