"""FastAPI application entrypoint."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="FinTech Research Agent")


class HealthResponse(BaseModel):
    """Public payload of ``GET /health``."""

    status: Literal["ok"]


@app.get("/health")
async def health() -> HealthResponse:
    """Report that the application process is serving requests."""
    return HealthResponse(status="ok")
