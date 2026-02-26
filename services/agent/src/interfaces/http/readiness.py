"""Readiness probe endpoint with dependency health checks."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.engine import get_db
from core.runtime.config import Settings
from core.runtime.service_factory import ServiceFactory
from interfaces.http.bootstrap import get_readiness_http_client

LOGGER = logging.getLogger(__name__)


def create_readiness_router(settings: Settings) -> APIRouter:
    """Create the APIRouter containing the /readyz endpoint.

    Args:
        settings: Application settings (used for environment and litellm_api_base).

    Returns:
        APIRouter with the /readyz GET endpoint registered.
    """
    router = APIRouter()

    @router.get("/readyz")
    async def readiness(
        request: Request,
        session: AsyncSession = Depends(get_db),
    ) -> JSONResponse:
        """Readiness probe that checks all dependencies.

        Returns HTTP 200 if all checks pass, HTTP 503 if any fail.
        """
        import asyncio

        checks: dict[str, dict[str, Any]] = {}
        all_ready = True

        # Database check
        async def check_database() -> dict[str, Any]:
            try:
                start = time.perf_counter()
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))
                latency = (time.perf_counter() - start) * 1000
                return {"status": "ok", "latency_ms": round(latency, 1)}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200]}

        # Qdrant check
        async def check_qdrant() -> dict[str, Any]:
            try:
                start = time.perf_counter()
                # Use shared Qdrant client from service factory
                factory = request.app.state.service_factory
                client = factory._qdrant_client

                # List collections to verify connection
                await asyncio.wait_for(client.get_collections(), timeout=2.0)
                latency = (time.perf_counter() - start) * 1000
                return {"status": "ok", "latency_ms": round(latency, 1)}
            except TimeoutError:
                return {"status": "error", "error": "timeout"}
            except AttributeError:
                return {"status": "unavailable", "error": "qdrant client not initialized"}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200]}

        # Skill registry check
        async def check_skills() -> dict[str, Any]:
            try:
                if hasattr(request.app.state, "service_factory"):
                    factory: ServiceFactory = request.app.state.service_factory
                    if hasattr(factory, "_skill_registry"):
                        registry = factory._skill_registry
                        if registry is not None:
                            count = len(registry.available())
                            return {"status": "ok", "count": count}
                return {"status": "unavailable", "error": "skill registry not initialized"}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200]}

        # LiteLLM check (use /health/liveliness for fast response)
        async def check_litellm() -> dict[str, Any]:
            try:
                start = time.perf_counter()
                http_client = get_readiness_http_client()
                if http_client is None:
                    return {"status": "error", "error": "http client not initialized"}
                litellm_url = str(settings.litellm_api_base).rstrip("/")
                response = await http_client.get(f"{litellm_url}/health/liveliness")
                latency = (time.perf_counter() - start) * 1000
                if response.status_code == 200:
                    return {"status": "ok", "latency_ms": round(latency, 1)}
                return {
                    "status": "error",
                    "error": f"HTTP {response.status_code}",
                }
            except (TimeoutError, httpx.TimeoutException):
                return {"status": "error", "error": "timeout"}
            except Exception as e:
                return {"status": "error", "error": str(e)[:200] or type(e).__name__}

        # Run all checks in parallel with timeout
        try:
            results = await asyncio.gather(
                check_database(),
                check_qdrant(),
                check_skills(),
                check_litellm(),
                return_exceptions=True,
            )

            # Cast results to proper types
            db_result = results[0]
            qdrant_result = results[1]
            skills_result = results[2]
            litellm_result = results[3]

            checks["database"] = (
                db_result
                if isinstance(db_result, dict)
                else {
                    "status": "error",
                    "error": str(db_result)[:200],
                }
            )
            checks["qdrant"] = (
                qdrant_result
                if isinstance(qdrant_result, dict)
                else {
                    "status": "error",
                    "error": str(qdrant_result)[:200],
                }
            )
            checks["skills"] = (
                skills_result
                if isinstance(skills_result, dict)
                else {
                    "status": "error",
                    "error": str(skills_result)[:200],
                }
            )
            checks["litellm"] = (
                litellm_result
                if isinstance(litellm_result, dict)
                else {
                    "status": "error",
                    "error": str(litellm_result)[:200],
                }
            )

            # Determine overall readiness
            for check_result in checks.values():
                if check_result.get("status") not in ("ok", "unavailable"):
                    all_ready = False
                    break

        except Exception:
            LOGGER.exception("Readiness check failed")
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "error": "Readiness check execution failed",
                    "checks": checks,
                },
            )

        status_code = 200 if all_ready else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ready" if all_ready else "not_ready",
                "environment": settings.environment,
                "checks": checks,
            },
        )

    return router


__all__ = ["create_readiness_router"]
