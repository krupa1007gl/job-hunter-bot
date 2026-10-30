"""Служебный HTTP API (REQ-016). В v1.1 сюда добавится API для Mini App."""

import hmac

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from .models import Delivery, Offer, User


def create_app(sessionmaker, internal_token: str) -> FastAPI:
    app = FastAPI(title="job-hunter-bot", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health():
        try:
            async with sessionmaker() as s:
                await s.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ok"}

    @app.get("/internal/stats")
    async def stats(x_internal_token: str | None = Header(default=None)):
        if not internal_token or not hmac.compare_digest((x_internal_token or "").encode(), internal_token.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")
        async with sessionmaker() as s:
            return {
                "users": await s.scalar(select(func.count()).select_from(User)),
                "offers": await s.scalar(select(func.count()).select_from(Offer)),
                "sent": await s.scalar(select(func.count()).select_from(Delivery).where(Delivery.status == "sent")),
            }

    return app
