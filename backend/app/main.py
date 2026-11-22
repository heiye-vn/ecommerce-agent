from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.agent import router as agent_router
from app.api.auth import router as auth_router
from app.api.catalog import router as catalog_router
from app.api.health import router as health_router
from app.api.orders import router as orders_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        engine = getattr(application.state, "database_engine", None)
        if engine is not None:
            await engine.dispose()
            del application.state.database_engine


app = FastAPI(title="CommerceAgent Backend", lifespan=lifespan)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(catalog_router)
app.include_router(orders_router)
app.include_router(agent_router)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.rstrip("/") == "/api/auth/login":
        return JSONResponse({"detail": "Invalid login request"}, status_code=422)
    return await request_validation_exception_handler(request, exc)
