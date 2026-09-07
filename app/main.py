from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR
from app.api.health import router as health_router
from app.api.v1.router import router as v1_router

def create_app() -> FastAPI:
    application = FastAPI(
        title="CDR STL Forecast & Agent Scheduling API",
        description=(
            "Decoupled, Asynchronous, and Optimized API for Contact Centre Demand "
            "Forecasting using STL Decomposition and Erlang C Staffing."
        ),
        version="4.1.0",
    )

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    application.include_router(health_router)
    application.include_router(v1_router)

    return application

app = create_app()
