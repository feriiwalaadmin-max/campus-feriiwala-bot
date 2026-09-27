"""Design pipeline intentionally disabled: Campus Feriiwala is text-only."""

from __future__ import annotations

from typing import Any


class ImageValidationError(RuntimeError):
    """Compatibility exception retained for callers migrating off graphics."""


def find_product_image(product_id: str, product_name: str = "") -> None:
    """Return no asset; text reminders never load or attach images."""
    return None


def generate_design_with_gemini(product: dict[str, Any], **_: Any) -> None:
    """Compatibility no-op for the removed design engine."""
    return None


__all__ = ["ImageValidationError", "find_product_image", "generate_design_with_gemini"]
