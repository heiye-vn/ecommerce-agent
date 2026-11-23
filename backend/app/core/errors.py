from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    """统一业务异常，向前端提供稳定的机器可读错误码与中文提示。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail or message
        self.headers = headers


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "detail": exc.detail},
            headers=exc.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        if request.url.path.rstrip("/") == "/api/auth/login":
            return JSONResponse(
                status_code=422,
                content={
                    "code": "INVALID_LOGIN_REQUEST",
                    "message": "请检查账号和密码的格式。",
                    "detail": "Invalid login request",
                },
            )
        return await request_validation_exception_handler(request, exc)
