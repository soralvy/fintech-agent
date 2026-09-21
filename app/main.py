"""FastAPI application entrypoint.

Lifespan owns the shared resources; route handlers stay thin
(docs/DECISIONS.md section 3.1).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel

from app.config import DatabaseConfig
from app.db import Pool, check_database, create_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the connection pool for the life of the application."""
    pool = create_pool(DatabaseConfig.from_env())
    await pool.open()
    app.state.pool = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="FinTech Research Agent", lifespan=lifespan)


def get_pool(request: Request) -> Pool:
    """Hand the pooled database to a route."""
    pool: Pool = request.app.state.pool
    return pool


class HealthResponse(BaseModel):
    """Public payload of ``GET /health``."""

    status: Literal["ok"]
    database: Literal["ok"]


@app.get("/health")
async def health(pool: Annotated[Pool, Depends(get_pool)]) -> HealthResponse:
    """Report that the process is serving and the database answers a query.

    OpenAI and the market-data provider are deliberately not checked
    (docs/SPEC.md section 6.1).
    """
    try:
        await check_database(pool)
    except psycopg.Error:
        # The driver's message can carry the connection string, so it is
        # neither returned nor logged here (docs/SPEC.md section 13).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from None
    return HealthResponse(status="ok", database="ok")
