"""Canonical model-catalogue endpoint.

The list itself lives in ``backend.shared.config.models`` — this router just
serialises it for the frontend dropdowns.
"""
from fastapi import APIRouter

from backend.shared.config.models import DEFAULT_MODEL_ID, available_models

router = APIRouter(prefix="/api", tags=["models"])


@router.get("/models")
async def list_models(only_configured: bool = False):
    """Return the selectable model catalogue.

    ``only_configured=true`` drops options whose provider API key is absent.
    Each option carries ``available`` so the UI can grey out unconfigured ones.
    """
    return {
        "models": available_models(only_configured=only_configured),
        "default": DEFAULT_MODEL_ID,
    }
