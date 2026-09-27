"""Discover and verify a text-capable Gemini model for this API key."""

from __future__ import annotations

import os
from pathlib import Path

from google import genai
from google.genai import types

from config import GEMINI_API_KEY


def _model_name(model: object) -> str:
    return str(getattr(model, "name", ""))


def _supports_text_generation(model: object) -> bool:
    actions = getattr(model, "supported_actions", None)
    if actions is None:
        actions = getattr(model, "supported_generation_methods", None)
    if actions is None:
        return True
    return any(str(action).casefold() in {"generatecontent", "generate_content"} for action in actions)


def main() -> None:
    client = genai.Client(api_key=GEMINI_API_KEY)
    models = list(client.models.list())
    print(f"Accessible Gemini models: {len(models)}")
    for model in models:
        name = _model_name(model)
        actions = getattr(model, "supported_actions", None) or getattr(model, "supported_generation_methods", None) or []
        print(f"- {name} | {list(actions)}")

    candidates = [model for model in models if _supports_text_generation(model)]
    for model in candidates:
        name = _model_name(model)
        if not name:
            continue
        try:
            response = client.models.generate_content(
                model=name,
                contents="Hello",
                config=types.GenerateContentConfig(temperature=0),
            )
            text = (response.text or "").strip()
            if text:
                print(f"WORKING_MODEL={name}")
                print("STATUS=200 OK")
                print(f"RESPONSE={text}")
                return
        except Exception as exc:
            print(f"FAILED={name} | {type(exc).__name__}: {exc}")
    raise SystemExit("No accessible text-generation model returned a text response.")


if __name__ == "__main__":
    main()
