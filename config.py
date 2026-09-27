"""Application configuration loaded from environment variables."""

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")


def _required_env(name: str) -> str:
    """Return a non-empty environment variable that is not a template value."""
    value = os.getenv(name, "").strip()
    if not value or value.lower().startswith("your_"):
        raise ValueError(
            f"{name} is missing or still contains its template value. "
            "Set it in the .env file."
        )
    return value


TELEGRAM_BOT_TOKEN: str = _required_env("TELEGRAM_BOT_TOKEN")

try:
    TELEGRAM_ADMIN_CHAT_ID: int = int(_required_env("TELEGRAM_ADMIN_CHAT_ID"))
except ValueError as exc:
    raise ValueError("TELEGRAM_ADMIN_CHAT_ID must be a valid integer.") from exc

GEMINI_API_KEY: str = _required_env("GEMINI_API_KEY")
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Dhaka").strip() or "Asia/Dhaka"

if not DATA_DIR.is_dir():
    raise FileNotFoundError(f"Data directory does not exist: {DATA_DIR}")
