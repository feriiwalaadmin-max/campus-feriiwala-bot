"""Flexible Bangla/Banglish/English daily-plan parser."""

from __future__ import annotations

import json
import os
import re
import asyncio
import traceback
import unicodedata
from datetime import datetime, timedelta
from difflib import SequenceMatcher, get_close_matches
from typing import Any
from zoneinfo import ZoneInfo

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_SPACE_RE = re.compile(r"\s+")
_TIME_TOKEN_RE = re.compile(r"(?P<hour>\d{1,2})(?:\s*[:.]\s*(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?|am|pm)?", re.I)
_BANGLA_PHONETICS = str.maketrans({
    "অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng", "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n", "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "f", "ব": "b", "ভ": "bh", "ম": "m", "য": "y", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh", "স": "s", "হ": "h",
    "ড়": "r", "ঢ়": "rh", "য়": "y", "ং": "ng", "ঃ": "h", "ঁ": "n", "্": "",
    "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou",
})
_PERIODS = {
    "রাত": "night", "রাত্রি": "night", "rat": "night", "raat": "night",
    "সন্ধ্যা": "evening", "সন্ধ্যায়": "evening", "shondha": "evening", "shondhay": "evening",
    "সকাল": "morning", "সকালে": "morning", "shokal": "morning", "shokale": "morning",
    "দুপুর": "afternoon", "দুপুরে": "afternoon", "dupur": "afternoon", "dupure": "afternoon",
}
_PERIOD_ALIASES = {
    "rate": "night", "raate": "night", "rattire": "night",
    "sondha": "evening", "sondhay": "evening",
    "sokal": "morning", "sokale": "morning", "shokalbela": "morning", "sokalbela": "morning",
    "vhor": "morning", "bhor": "morning", "vhore": "morning", "bhore": "morning",
    "dupure": "afternoon",
    "\u09ad\u09cb\u09b0": "morning", "\u09ad\u09cb\u09b0\u09c7": "morning",
    "\u09b8\u0995\u09be\u09b2": "morning", "\u09b8\u0995\u09be\u09b2\u09c7": "morning",
    "\u09b0\u09be\u09a4\u09c7": "night",
    "\u09b0\u09be\u09a4": "night", "\u09b0\u09be\u09a4\u09cd\u09b0\u09bf": "night",
    "\u09b8\u09a8\u09cd\u09a7\u09cd\u09af\u09be": "evening",
    "\u09b8\u09a8\u09cd\u09a7\u09cd\u09af\u09be\u09df": "evening",
    "\u09ac\u09bf\u0995\u09be\u09b2": "evening", "\u09ac\u09bf\u0995\u09be\u09b2\u09c7": "evening",
    "\u09a6\u09c1\u09aa\u09c1\u09b0": "afternoon",
    "\u09a6\u09c1\u09aa\u09c1\u09b0\u09c7": "afternoon",
}
_AM_RE = re.compile(r"(?<![a-z])a\.?m\.?(?![a-z])", re.I)
_PM_RE = re.compile(r"(?<![a-z])p\.?m\.?(?![a-z])", re.I)
_DAYS = {
    "shonibar": 5, "sat": 5, "saturday": 5,
    "robibar": 6, "robi": 6, "sun": 6, "sunday": 6,
    "shom": 0, "shombar": 0, "mon": 0, "monday": 0,
    "mongol": 1, "mongal": 1, "mangal": 1, "mongolbar": 1, "mongalbar": 1, "tue": 1, "tuesday": 1,
    "budh": 2, "budhbar": 2, "wed": 2, "wednesday": 2,
    "brihoshpoti": 3, "brihoshpotibar": 3, "thu": 3, "thursday": 3,
    "shukro": 4, "shukrobar": 4, "fri": 4, "friday": 4,
    "aj": 0, "today": 0, "kal": 1, "tomorrow": 1, "porshu": 2,
}


def _result(*, success: bool, scheduled_posts: list[dict[str, Any]] | None = None, needs_clarification: bool = False, clarification_message: str | None = None) -> dict[str, Any]:
    return {"success": success, "scheduled_posts": scheduled_posts or [], "needs_clarification": needs_clarification, "clarification_message": clarification_message}


def _clean(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_DIGITS).casefold()
    return _SPACE_RE.sub(" ", text).strip()


def _product_identity(product: dict[str, Any]) -> tuple[str, str, list[str]]:
    name = str(product.get("name", "")).strip()
    product_id = str(product.get("id", name)).strip()
    aliases = [str(alias).strip() for alias in product.get("aliases", [])]
    return product_id, name, aliases


def _phonetic_key(value: Any) -> str:
    """Make English, Bangla, and speech-to-text spellings comparable."""
    text = _clean(value).translate(_BANGLA_PHONETICS)
    return re.sub(r"[^a-z0-9]+", "", text)


def _candidate_variants(product: dict[str, Any]) -> tuple[str, str, list[str]]:
    product_id, name, aliases = _product_identity(product)
    category = str(product.get("category", "")).strip()
    raw = [product_id, name, *aliases, category]
    # Include short/name tokens so category-like mentions such as "mouse" or
    # "cleaner" can resolve without a product-specific if/else table.
    raw.extend(token for value in (name, category) for token in re.findall(r"[\w]+", value, flags=re.UNICODE) if len(token) >= 3)
    variants = list(dict.fromkeys(value for value in raw if value))
    return product_id, name, variants


def _token_score(query_key: str, candidate_keys: list[str]) -> float:
    query_tokens = set(re.findall(r"[a-z0-9]+", query_key))
    if not query_tokens:
        return 0.0
    candidate_tokens = set(candidate_keys)
    scores = []
    for query_token in query_tokens:
        best = max((SequenceMatcher(None, query_token, item).ratio() for item in candidate_tokens), default=0.0)
        if any(item.startswith(query_token[:4]) or query_token.startswith(item[:4]) for item in candidate_tokens if len(item) >= 4):
            best = max(best, 0.86)
        scores.append(best)
    return sum(scores) / len(scores)


def _resolve_product(value: Any, products: list[Any]) -> tuple[str, str] | None:
    query = _clean(value)
    if not query:
        return None
    query_key = _phonetic_key(query)
    if not query_key:
        return None
    candidates: list[tuple[float, str, str]] = []
    for product in products:
        if isinstance(product, dict):
            product_id, name, variants = _candidate_variants(product)
            candidate_keys = [_phonetic_key(candidate) for candidate in variants]
            candidate_keys = [key for key in candidate_keys if key]
            if not candidate_keys:
                continue
            exact = max((1.0 if query_key == key else 0.0 for key in candidate_keys), default=0.0)
            close = max((SequenceMatcher(None, query_key, key).ratio() for key in candidate_keys), default=0.0)
            token = _token_score(query_key, candidate_keys)
            containment = max((0.92 if query_key in key or key in query_key and len(query_key) >= 4 else 0.0 for key in candidate_keys), default=0.0)
            score = max(exact, close * 0.92, token * 0.95, containment)
            candidates.append((score, product_id, name))
    if not candidates:
        return None
    # get_close_matches supplies a conservative fuzzy shortlist; scoring above
    # then handles dropped characters, extra digits, and token/category hits.
    close_keys = get_close_matches(query_key, [item[1] for item in candidates], n=3, cutoff=0.55)
    best = max(candidates, key=lambda item: (item[0], item[1] in close_keys))
    return (best[1], best[2]) if best[0] >= 0.58 else None


def _resolve_group(value: Any, groups: list[Any]) -> str | None:
    query = _clean(value)
    if not query:
        return None
    options: list[tuple[str, str]] = []
    for group in groups:
        if isinstance(group, dict):
            code = str(group.get("code", "")).strip()
            options.extend((candidate, code) for candidate in (code, group.get("name", ""), group.get("campus", "")) if candidate)
    for candidate, code in options:
        if query == _clean(candidate):
            return code
    best = max(((SequenceMatcher(None, query, _clean(candidate)).ratio(), code) for candidate, code in options), default=(0, None))
    return best[1] if best[0] >= 0.68 else None


def _period(text: str) -> str | None:
    cleaned = _clean(text)
    if _AM_RE.search(cleaned):
        return "morning"
    if _PM_RE.search(cleaned):
        return "evening"
    for token, period in {**_PERIODS, **_PERIOD_ALIASES}.items():
        if re.search(rf"(?<!\w){re.escape(token)}(?!\w)", cleaned):
            return period
    return None


def _parse_time(value: Any, context: str = "") -> str | None:
    text = " ".join(part for part in (_clean(value), _clean(context)) if part)
    if not text:
        return None
    direct = re.search(r"(?<!\d)([01]?\d|2[0-3])\s*[:.]\s*([0-5]\d)(?!\d)", text)
    if direct:
        hour, minute = int(direct.group(1)), int(direct.group(2))
        period = _period(text)
        if period == "morning":
            if hour == 12:
                hour = 0
        elif period is None and 1 <= hour <= 11:
            hour += 12
        return f"{hour:02d}:{minute:02d}"
    match = None
    for candidate in _TIME_TOKEN_RE.finditer(text):
        if int(candidate.group("hour")) <= 23:
            match = candidate
            break
    if match is None:
        return None
    hour, minute = int(match.group("hour")), int(match.group("minute") or 0)
    ampm = (match.group("ampm") or "").replace(".", "")
    period = _period(text)
    if period == "morning":
        if hour == 12:
            hour = 0
    elif ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif period in {"night", "evening", "afternoon"} and hour < 12:
        hour += 12
    elif not ampm and not period and match.group("minute") and 1 <= hour <= 11:
        # In this posting context, a bare clock time such as "7:30" conventionally
        # means the evening slot; explicit 24-hour values were handled above.
        hour += 12
    return f"{hour:02d}:{minute:02d}" if 0 <= hour <= 23 else None


def _catalog_text(items: list[Any]) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"), default=str)


def _clarification(message: str) -> dict[str, Any]:
    return _result(success=False, needs_clarification=True, clarification_message=message)


def _weekly_fallback(user_text: str, products: list[Any], groups: list[Any]) -> list[dict[str, Any]]:
    """Parse comma-separated weekly plans deterministically when Gemini is unavailable."""
    text = _clean(user_text)
    split_pattern = r"\s*(?:,|;|\n|\b(?:and|ar)\b|\u0986\u09b0)\s*"
    segments = [segment.strip() for segment in re.split(split_pattern, text, flags=re.I) if segment.strip()]
    if len(segments) == 1 and not any(re.search(rf"(?<!\w){re.escape(day)}(?!\w)", text) for day in _DAYS):
        return []
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    posts: list[dict[str, Any]] = []
    for segment in segments:
        day_offset = None
        weekday = None
        for token, value in sorted(_DAYS.items(), key=lambda item: len(item[0]), reverse=True):
            if not re.search(rf"(?<!\w){re.escape(token)}(?!\w)", segment):
                continue
            if token in {"aj", "today"}:
                day_offset = 0
            elif token in {"kal", "tomorrow"}:
                day_offset = 1
            elif token == "porshu":
                day_offset = 2
            else:
                weekday = value
            break
        if day_offset is None:
            if weekday is None:
                day_offset = 0
            else:
                day_offset = (weekday - today.weekday()) % 7
        target_date = today + timedelta(days=day_offset)
        product = _resolve_product(segment, products)
        time = _parse_time("", segment)
        if time is None:
            # A weekly slot such as "Mongal shondhay t88" has a clear period
            # even when the speaker omits an hour; use the center of that
            # period so the whole timetable remains schedulable.
            period_defaults = {"morning": "09:00", "afternoon": "14:00", "evening": "18:00", "night": "21:00"}
            time = period_defaults.get(_period(segment))
        if product is None or time is None:
            continue
        group_code = None
        for group in groups:
            if not isinstance(group, dict):
                continue
            code = str(group.get("code", "")).strip()
            if any(_clean(candidate) and _clean(candidate) in segment for candidate in (code, group.get("name", ""), group.get("campus", ""))):
                group_code = code
                break
        posts.append({
            "product_id": product[0],
            "product_name": product[1],
            "time": time,
            "date": target_date.isoformat(),
            "target_date": target_date.isoformat(),
            "group_code": group_code,
            "instructions": user_text.strip(),
        })
    return posts


def _local_fallback(user_text: str, products: list[Any], groups: list[Any]) -> dict[str, Any]:
    """Keep common plans usable when the model/API is unavailable."""
    weekly_posts = _weekly_fallback(user_text, products, groups)
    if weekly_posts:
        return _result(success=True, scheduled_posts=weekly_posts)
    cleaned = _clean(user_text)
    product_match = None
    best_length = 0
    for product in products:
        if not isinstance(product, dict):
            continue
        product_id, name, aliases = _product_identity(product)
        for candidate in (name, *aliases):
            candidate_clean = _clean(candidate)
            if candidate_clean and candidate_clean in cleaned and len(candidate_clean) > best_length:
                product_match = (product_id, name)
                best_length = len(candidate_clean)
    time = _parse_time("", user_text)
    group_code = None
    for group in groups:
        if not isinstance(group, dict):
            continue
        code = str(group.get("code", "")).strip()
        candidates = (code, str(group.get("name", "")), str(group.get("campus", "")))
        if any(_clean(candidate) and _clean(candidate) in cleaned for candidate in candidates):
            group_code = code
            break
    if product_match and time:
        return _result(success=True, scheduled_posts=[{
            "product_id": product_match[0],
            "product_name": product_match[1],
            "time": time,
            "group_code": group_code,
            "instructions": user_text.strip(),
        }])
    return _clarification("পরিকল্পনাটি বোঝা যায়নি। পণ্য, নির্দিষ্ট সময় (যেমন ২১:০০) এবং গ্রুপের কোড দিয়ে আবার লিখুন।")


def _parse_daily_plan_sync(user_text: str, available_products: list[Any], available_groups: list[Any]) -> dict[str, Any]:
    if not isinstance(user_text, str) or not user_text.strip():
        return _clarification("কোন পণ্য, কখন এবং কোন গ্রুপে পোস্ট করতে হবে তা লিখুন।")
    if not isinstance(available_products, list) or not isinstance(available_groups, list):
        return _clarification("পণ্য ও গ্রুপের তালিকা পাওয়া যায়নি।")
    if _period(user_text) and not re.search(r"\d", user_text.translate(_DIGITS)):
        return _clarification("কোন সময়টি বোঝাচ্ছেন? যেমন: সন্ধ্যা ৭টা বা রাত ৯টা লিখুন।")
    try:
        from google import genai
        from google.genai import types
        from config import GEMINI_API_KEY
        from core.gemini_client import DEFAULT_GEMINI_MODEL, generate_content_with_retry

        client = genai.Client(api_key=GEMINI_API_KEY)
        catalog = [
            {
                **product,
                "id": str(product.get("id", product.get("name", ""))).strip(),
                "category": str(product.get("category", "")).strip(),
            }
            for product in available_products
            if isinstance(product, dict)
        ]
        system_prompt = f"""You are a fluent Bangla, Banglish, English and mixed-language campus posting-plan parser.
Treat all time tokens case-insensitively. Never convert 'shokal/sokal/am/vhor' into PM/night hours. 'shokal 9ta', '9am', 'SOKAL 9' MUST strictly be '09:00'. Understand Bangla numerals and colloquial times. Convert all times to Asia/Dhaka local 24-hour HH:MM.
Support comma-separated weekly plans and relative days (aj, kal, porshu). Resolve Banglish day names such as shonibar, robibar, shom, mongol, budh, brihoshpoti, shukro and English short names. Return every item with an exact Asia/Dhaka target date in both date and target_date when a day is provided.
Resolve product names, aliases, typos and groups only to exact catalog entries. Given any loose mention, voice-transcribed typo, category name, or partial alias, map it to the most probable product_id from the catalog. Use the catalog IDs and target categories below; never invent a product_id. Product IDs are catalog id, or exact product name when no id exists.
For vague periods such as 'dupure' or 'shondhay' without an hour, set needs_clarification true and ask briefly in conversational Bangla.
Return ONLY a JSON object with exactly this shape:
{{"success":true,"scheduled_posts":[{{"product_id":"str","product_name":"str","time":"HH:MM","date":"YYYY-MM-DD or null","target_date":"YYYY-MM-DD or null","group_code":"str or null","instructions":"str"}}],"needs_clarification":false,"clarification_message":null}}
Complete product catalog (including IDs and target categories): {_catalog_text(catalog)}
Groups: {_catalog_text(available_groups)}"""
        response = generate_content_with_retry(
            client,
            model=DEFAULT_GEMINI_MODEL,
            contents=f"{system_prompt}\n\nUser plan: {user_text}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        parsed = json.loads(response.text or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Model response was not a JSON object")
    except Exception as exc:
        print(f"[parser] Gemini/parsing failure: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        fallback = _local_fallback(user_text, available_products, available_groups)
        if fallback.get("success"):
            print("[parser] Used deterministic fallback parser.", flush=True)
        return fallback

    posts: list[dict[str, Any]] = []
    issues: list[str] = []
    raw_posts = parsed.get("scheduled_posts", [])
    for item in raw_posts if isinstance(raw_posts, list) else []:
        if not isinstance(item, dict):
            continue
        product = _resolve_product(item.get("product_id") or item.get("product_name") or item.get("product"), available_products)
        if product is None:
            issues.append(f"‘{item.get('product_name') or item.get('product_id', '')}’ কোন পণ্য তা পরিষ্কার নয়")
            continue
        context = " ".join(str(item.get(key, "")) for key in ("time", "instructions")) + " " + user_text
        time = _parse_time(item.get("time"), context)
        if time is None:
            issues.append(f"{product[1]}-এর নির্দিষ্ট সময় দিন, যেমন রাত ৯টা")
        group_value = item.get("group_code") or item.get("group")
        group_code = _resolve_group(group_value, available_groups)
        if group_value and group_code is None:
            issues.append(f"‘{group_value}’ কোন গ্রুপ তা পরিষ্কার নয়")
        post_date = str(item.get("date") or item.get("target_date") or "").strip()
        posts.append({"product_id": product[0], "product_name": product[1], "time": time or "", "date": post_date, "target_date": post_date, "group_code": group_code, "instructions": str(item.get("instructions") or user_text).strip()})
    needs = bool(parsed.get("needs_clarification")) or bool(issues)
    if needs:
        model_message = str(parsed.get("clarification_message") or "").strip()
        parts = issues or ([model_message] if model_message else [])
        return _result(success=False, scheduled_posts=posts, needs_clarification=True, clarification_message="অনুগ্রহ করে এগুলো পরিষ্কার করুন: " + "; ".join(dict.fromkeys(parts)) + "।")
    return _result(success=bool(posts), scheduled_posts=posts, needs_clarification=not bool(posts), clarification_message=None if posts else "কোন পণ্য ও সময় শনাক্ত করা যায়নি।")


async def parse_daily_plan(user_text: str, available_products: list[Any], available_groups: list[Any]) -> dict[str, Any]:
    """Parse a plan without blocking Telegram's event loop."""
    return await asyncio.to_thread(_parse_daily_plan_sync, user_text, available_products, available_groups)


__all__ = ["parse_daily_plan", "_parse_daily_plan_sync", "_parse_time", "_resolve_product", "_resolve_group"]
