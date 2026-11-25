"""Start the full InvaPlan release behind a browser login on a hosted service."""
from base64 import b64encode
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from secrets import compare_digest

from fastapi import FastAPI, Request
from starlette.responses import Response

from .main import app as research_app
from .sample import FULL_RELEASE_ID


def create_app():
    mode = os.environ.get("INVAPLAN_ACCESS_MODE", "private")
    if mode not in {"private", "public"}:
        raise ValueError("INVAPLAN_ACCESS_MODE must be private or public.")
    token = None
    if mode == "private":
        password = os.environ.get("INVAPLAN_REVIEW_PASSWORD")
        if not password or not password.strip():
            raise ValueError("Private access requires INVAPLAN_REVIEW_PASSWORD.")
        token = b64encode(("reviewer:" + password).encode("utf-8"))

    cities = Path(os.environ["INVAPLAN_AREA_DIR"])
    # The city index is uploaded last. Keep SFTP available even after a partial first transfer.
    waiting = mode == "private" and not (cities / "area-cities.json").exists()

    @asynccontextmanager
    async def lifespan(app):
        if waiting:
            logging.getLogger("uvicorn.error").info("Waiting for the first data upload. Restart after transfer.")
            yield
            return
        async with research_app.router.lifespan_context(research_app):
            catalog = research_app.state.area_comparison
            if (research_app.state.sample.dataset.release_id != FULL_RELEASE_ID
                    or catalog is None or len(catalog.entries) != 62):
                raise RuntimeError("Hosted InvaPlan requires the full 39-species and 62-city release.")
            yield

    hosted = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @hosted.middleware("http")
    async def check_access(request: Request, call_next):
        if request.url.path == "/healthz" and request.method in {"GET", "HEAD"}:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})
        if token is not None:
            scheme, _, value = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "basic" or not compare_digest(value.encode("latin-1"), token):
                return Response(status_code=401, headers={
                    "WWW-Authenticate": 'Basic realm="InvaPlan", charset="UTF-8"',
                    "Cache-Control": "private, no-store",
                })
        if waiting:
            return Response(status_code=503, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    hosted.mount("/", research_app)
    return hosted
