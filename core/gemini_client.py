"""Small reliability wrapper for Google Gemini generation calls."""

from __future__ import annotations

import time
import os
from typing import Any

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "models/gemma-4-26b-a4b-it")
DEFAULT_GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", DEFAULT_GEMINI_MODEL)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    name = type(exc).__name__.casefold()
    return status in {429, 500, 502, 503, 504} or "resourceexhausted" in name or "tempor" in name


def generate_content_with_retry(client: Any, *, max_retries: int = 2, **kwargs: Any) -> Any:
    """Call Gemini, retrying transient capacity/quota errors with 2s backoff."""
    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = 2 ** (attempt + 1)
            print(
                f"[gemini] transient {type(exc).__name__}; retrying in {delay}s "
                f"({attempt + 1}/{max_retries})",
                flush=True,
            )
            time.sleep(delay)
