from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.agent import router as agent_router
from app.api.approvals import router as approvals_router
from app.api.auth import router as auth_router
from app.api.catalog import router as catalog_router
from app.api.health import router as health_router
from app.api.inventory import router as inventory_router
from app.api.jobs import router as jobs_router
from app.api.notifications import router as notifications_router
from app.api.orders import router as orders_router
from app.api.promotions import router as promotions_router
from app.api.refunds import router as refunds_router
from app.core.clock import BusinessClock
from app.core.config import AppSettings
from app.core.db import create_database_engine, create_session_factory
from app.core.errors import register_error_handlers


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = AppSettings.load()
    application.state.settings = settings
    application.state.clock = BusinessClock(settings.sandbox_business_date)
    if settings.database_url:
        engine = create_database_engine(settings.database_url)
        application.state.database_engine = engine
        application.state.session_factory = create_session_factory(engine)
    try:
        yield
    finally:
        active_engine = getattr(application.state, "database_engine", None)
        if active_engine is not None:
            await active_engine.dispose()
            del application.state.database_engine
        if hasattr(application.state, "session_factory"):
            del application.state.session_factory
        if hasattr(application.state, "settings"):
            del application.state.settings
        if hasattr(application.state, "clock"):
            del application.state.clock


app = FastAPI(title="CommerceAgent Backend", lifespan=lifespan)
register_error_handlers(app)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(catalog_router)
app.include_router(inventory_router)
app.include_router(orders_router)
app.include_router(refunds_router)
app.include_router(promotions_router)
app.include_router(approvals_router)
app.include_router(jobs_router)
app.include_router(notifications_router)
app.include_router(agent_router)
