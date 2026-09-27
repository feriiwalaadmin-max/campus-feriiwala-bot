"""JSON-backed data access utilities for products, groups, and content history."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PRODUCTS_FILE = DATA_DIR / "products.json"
GROUPS_FILE = DATA_DIR / "groups.json"
CONTENT_HISTORY_FILE = DATA_DIR / "content_history.json"
PERFORMANCE_LOG_FILE = DATA_DIR / "performance_logs.json"
PRODUCT_IMAGES_FILE = DATA_DIR / "product_images.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists() or path.stat().st_size == 0:
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_products() -> list[dict[str, Any]]:
    """Load and return all product records."""
    products = _load_json(PRODUCTS_FILE, [])
    if not isinstance(products, list):
        raise ValueError("products.json must contain a JSON array.")
    return products


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def get_product_by_name_or_alias(query: str) -> dict[str, Any] | None:
    """Return the closest product matching its name or any configured alias."""
    query_key = _normalize(query.strip())
    if not query_key:
        return None

    products = load_products()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for product in products:
        names = [product.get("name", ""), *product.get("aliases", [])]
        for name in names:
            name_key = _normalize(str(name))
            if not name_key:
                continue
            if query_key == name_key:
                return product
            score = SequenceMatcher(None, query_key, name_key).ratio()
            if query_key in name_key or name_key in query_key:
                score = max(score, 0.8)
            candidates.append((score, product))

    if not candidates:
        return None
    score, product = max(candidates, key=lambda candidate: candidate[0])
    return product if score >= 0.55 else None


def load_groups() -> list[dict[str, Any]]:
    """Load and return all target campus groups."""
    groups = _load_json(GROUPS_FILE, [])
    if not isinstance(groups, list):
        raise ValueError("groups.json must contain a JSON array.")
    return groups


def load_product_images() -> list[dict[str, Any]]:
    """Return image records; product_id is the authoritative identity."""
    images = _load_json(PRODUCT_IMAGES_FILE, [])
    if not isinstance(images, list):
        raise ValueError("product_images.json must contain a JSON array.")
    return images


def register_product_image(
    *,
    product_id: str,
    product_name: str,
    image_id: str,
    image_type: str,
    file_path: str,
    active: bool = True,
) -> dict[str, Any]:
    """Register an explicitly identified image without fuzzy inference."""
    if not str(product_id).strip() or not str(image_id).strip() or not str(file_path).strip():
        raise ValueError("product_id, image_id, and file_path are required.")
    images = load_product_images()
    record = {
        "image_id": str(image_id),
        "product_id": str(product_id),
        "product_name": str(product_name),
        "image_type": str(image_type),
        "file_path": str(Path(file_path).resolve()),
        "upload_date": datetime.now(timezone.utc).isoformat(),
        "active": bool(active),
    }
    images = [item for item in images if not (isinstance(item, dict) and item.get("image_id") == record["image_id"])]
    images.append(record)
    PRODUCT_IMAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PRODUCT_IMAGES_FILE.open("w", encoding="utf-8") as file:
        json.dump(images, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return record


def get_verified_product_images(product_id: str) -> list[dict[str, Any]]:
    """Return only active, existing images with an exact product_id match."""
    expected = str(product_id).strip()
    if not expected:
        return []
    verified: list[dict[str, Any]] = []
    for image in load_product_images():
        if not isinstance(image, dict) or str(image.get("product_id", "")).strip() != expected:
            continue
        if not image.get("active", False):
            continue
        path = Path(str(image.get("file_path", ""))).resolve()
        if path.is_file():
            verified.append({**image, "file_path": str(path)})
    return verified


def get_product_image_by_path(file_path: str) -> dict[str, Any] | None:
    expected = str(Path(file_path).resolve())
    for image in load_product_images():
        if isinstance(image, dict) and str(Path(str(image.get("file_path", ""))).resolve()) == expected:
            return image
    return None


def get_group_by_code(code: str) -> dict[str, Any] | None:
    """Return the group whose code matches case-insensitively."""
    normalized_code = code.strip().casefold()
    return next(
        (
            group
            for group in load_groups()
            if str(group.get("code", "")).strip().casefold() == normalized_code
        ),
        None,
    )


def save_content_history(entry: dict[str, Any]) -> dict[str, Any]:
    """Append an entry to content history and return the stored entry."""
    if not isinstance(entry, dict):
        raise TypeError("History entry must be a dictionary.")

    history = _load_json(CONTENT_HISTORY_FILE, [])
    if not isinstance(history, list):
        raise ValueError("content_history.json must contain a JSON array.")

    stored_entry = dict(entry)
    stored_entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    history.append(stored_entry)
    CONTENT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CONTENT_HISTORY_FILE.open("w", encoding="utf-8") as file:
        json.dump(history, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return stored_entry


def get_recent_history(days: int = 7) -> list[dict[str, Any]]:
    """Return history entries timestamped within the last ``days`` days."""
    if days < 0:
        raise ValueError("days must be non-negative.")

    history = _load_json(CONTENT_HISTORY_FILE, [])
    if not isinstance(history, list):
        raise ValueError("content_history.json must contain a JSON array.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        timestamp = entry.get("timestamp", entry.get("created_at"))
        if not timestamp:
            continue
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed >= cutoff:
                recent.append(entry)
        except ValueError:
            continue
    return recent


def mark_latest_posted(post_id: str | None = None) -> dict[str, Any] | None:
    """Mark the latest matching scheduled history entry as POSTED."""
    history = _load_json(CONTENT_HISTORY_FILE, [])
    if not isinstance(history, list):
        raise ValueError("content_history.json must contain a JSON array.")

    for entry in reversed(history):
        if not isinstance(entry, dict) or entry.get("status") == "POSTED":
            continue
        if post_id is not None and str(entry.get("post_id")) != str(post_id):
            continue
        entry["status"] = "POSTED"
        entry["posted_at"] = datetime.now(timezone.utc).isoformat()
        with CONTENT_HISTORY_FILE.open("w", encoding="utf-8") as file:
            json.dump(history, file, ensure_ascii=False, indent=2)
            file.write("\n")
        return entry
    return None


def save_performance_log(metrics: dict[str, Any]) -> dict[str, Any]:
    """Append feedback metrics to the performance log."""
    if not isinstance(metrics, dict):
        raise TypeError("Performance metrics must be a dictionary.")
    logs = _load_json(PERFORMANCE_LOG_FILE, [])
    if not isinstance(logs, list):
        raise ValueError("performance_logs.json must contain a JSON array.")
    stored = dict(metrics)
    stored.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    logs.append(stored)
    PERFORMANCE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PERFORMANCE_LOG_FILE.open("w", encoding="utf-8") as file:
        json.dump(logs, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return stored
