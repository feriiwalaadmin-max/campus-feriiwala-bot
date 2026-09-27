"""Generate Campus Feriiwala promotional content with Google Gemini."""

from __future__ import annotations

import json
import os
import re
from typing import Any


ANGLES = (
    "student_struggle",
    "desk_setup",
    "commute_transit",
    "problem_solution",
    "practical_recommendation",
)

BASE_BANNED_PHRASES = (
    "দৈনন্দিন জীবন",
    "স্মার্ট লাইফস্টাইল",
    "এখনই অর্ডার করুন",
    "মিস করবেন না",
    "limited stock",
    "সহযাত্রী",
    "আপনজন",
    "পাশে থাকবেন",
    "মনে করতে পারেন",
    "প্রিয় বন্ধু",
)
_PRICING_RE = re.compile(
    r"(?i)(?:৳|\u09f3|\b(?:tk|bdt|price|pricing|cost|দাম|মূল্য)\b)\s*[:=-]?\s*[\d,]*(?:\.\d+)?|\b(?:tk|bdt|price|pricing|cost)\b"
)


def _without_pricing(value: Any, product_price: Any = None) -> str:
    """Remove all money/currency references before content reaches users."""
    text = str(value or "")
    text = _PRICING_RE.sub("", text)
    if product_price not in (None, ""):
        text = re.sub(rf"(?<!\d){re.escape(str(product_price))}(?!\d)", "", text)
        bangla_digits = str(product_price).translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))
        text = text.replace(bangla_digits, "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _public_product(product: dict[str, Any]) -> dict[str, Any]:
    """Copy product facts without exposing price to the generation prompt."""
    return {key: value for key, value in product.items() if key.casefold() not in {"price", "cost", "currency"}}


def _hook(product_name: str) -> str:
    return (
        "ক্লাস, বাসের জ্যাম আর ডেডলাইনের ভিড়ে ছোট্ট একটা ঝামেলাও বড় লাগে।\n"
        "এই ছোট্ট গ্যাজেটটা ডেইলি লাইফে সত্যিই কাজে লাগবে।"
    )


def _ensure_hook(caption: str, product_name: str) -> str:
    body = _without_pricing(caption)
    hook = _hook(product_name)
    return f"{hook}\n\n{body}" if not body.startswith(hook) else body


def _fallback_content(product: dict[str, Any], selected_angle: str) -> dict[str, str]:
    name = str(product.get("name", "এই product"))
    warranty = str(product.get("warranty", "প্রযোজ্য warranty"))
    category = str(product.get("category", "daily essentials"))
    caption = _ensure_hook(
        f"Campus-er class, commute আর study desk—সব জায়গায় practical {category} দরকার হয়। "
        f"{name} student life-er ছোট ছোট কাজকে গুছিয়ে রাখতে সাহায্য করে। "
        f"Catalog-e thaka verified features অনুযায়ী এটি simple, useful এবং everyday use-er jonno তৈরি। "
        f"Campus delivery available, আর warranty assurance হলো {warranty}. "
        "ভাইয়া/আপু, নিজের routine-er সঙ্গে মিললে inbox-e জানাতে পারেন।",
        name,
    )
    return {
        "angle": selected_angle,
        "caption": caption,
        "first_comment": _without_pricing(f"{name} নিয়ে প্রশ্ন থাকলে product-er নাম লিখে message দিন। Campus delivery এবং {warranty} assurance থাকছে।"),
        "suggested_design_prompt": "Clean studio campus aesthetic with product name, verified feature bullets, soft contrast, and a utility-focused badge.",
    }


def _fallback_content(product: dict[str, Any], selected_angle: str) -> dict[str, str]:
    """Offline copy fallback in natural Bengali campus language."""
    name = str(product.get("name", "এই প্রোডাক্ট"))
    warranty = str(product.get("warranty", "প্রযোজ্য ওয়ারেন্টি"))
    caption = _ensure_hook(
        f"ক্লাসের নোট, এসাইনমেন্ট আর বাসের জ্যাম—সব মিলিয়ে ডেইলি লাইফে একটু স্মার্ট সাপোর্ট দরকার। {name} সেই ব্যস্ত সময়ের কাজে লাগার মতো একটি গ্যাজেট। "
        f"ক্যাম্পাসে দ্রুত ডেলিভারি আছে, সঙ্গে {warranty} ওয়ারেন্টি নিশ্চয়তা থাকছে। ভাইয়া বা আপু, আপনার রুটিনে এটা কাজে লাগবে মনে হলে মেসেজে জানিয়ে দিন।",
        name,
    )
    return {
        "angle": selected_angle,
        "caption": caption,
        "first_comment": f"এই গ্যাজেট নিয়ে প্রশ্ন থাকলে মেসেজে জানিয়ে দিন। ক্যাম্পাস ডেলিভারি আর {warranty} ওয়ারেন্টি নিশ্চয়তা থাকছে।",
        "suggested_design_prompt": "পরিষ্কার ক্যাম্পাস-স্টাইল, প্রোডাক্টের নাম, ফিচারের ছোট তালিকা এবং ইউটিলিটি ব্যাজ।",
    }


def _tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "generate_campus_post",
            "description": "Generate a factual Campus Feriiwala social-media post.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "angle": {"type": "string", "enum": list(ANGLES)},
                    "caption": {"type": "string"},
                    "first_comment": {"type": "string"},
                    "suggested_design_prompt": {"type": "string"},
                },
                "required": [
                    "angle",
                    "caption",
                    "first_comment",
                    "suggested_design_prompt",
                ],
            },
        },
    }


def _as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _recent_angles(past_history: list[Any] | None) -> list[str]:
    angles: list[str] = []
    for entry in (past_history or [])[-7:]:
        if isinstance(entry, dict) and entry.get("angle") in ANGLES:
            angles.append(str(entry["angle"]))
    return angles


def _choose_angle(recent_angles: list[str]) -> str:
    for angle in ANGLES:
        if angle not in recent_angles:
            return angle
    # If every angle appeared recently, continue the rotation from the oldest
    # recorded angle rather than repeating the newest one.
    if recent_angles:
        return ANGLES[(ANGLES.index(recent_angles[-1]) + 1) % len(ANGLES)]
    return ANGLES[0]


def _banned_phrases(custom_instruction: str | None) -> tuple[str, ...]:
    phrases = list(BASE_BANNED_PHRASES)
    if not custom_instruction or "অফার" not in custom_instruction:
        phrases.append("অফার")
    return tuple(phrases)


def _validate_output(
    output: dict[str, Any],
    selected_angle: str,
    custom_instruction: str | None,
) -> str | None:
    required = ("angle", "caption", "first_comment", "suggested_design_prompt")
    if any(not isinstance(output.get(key), str) for key in required):
        return "সব আউটপুট ফিল্ড string হতে হবে।"
    if output["angle"] != selected_angle:
        return "নির্ধারিত rotation angle ব্যবহার করা হয়নি।"
    caption = output["caption"].strip()
    if len(caption.splitlines()) < 2:
        return "caption must start with a two-line hook"
    if re.search(r"(?i)(?:৳|\u09f3|\b(?:tk|bdt|price|pricing|cost|দাম|মূল্য)\b)", "\n".join(str(output[key]) for key in ("caption", "first_comment", "suggested_design_prompt"))):
        return "pricing content detected"
    word_count = len(re.findall(r"\S+", caption))
    if word_count < 150 or word_count > 250:
        return "caption অবশ্যই ১৫০–২৫০ শব্দের হতে হবে।"
    generated_text = "\n".join(
        output[key] for key in ("caption", "first_comment", "suggested_design_prompt")
    )
    lowered = generated_text.casefold()
    for phrase in _banned_phrases(custom_instruction):
        if phrase.casefold() in lowered:
            return f"নিষিদ্ধ phrase ব্যবহার হয়েছে: {phrase}"
    # Count common emoji ranges; a few contextual emojis are acceptable.
    emoji_count = len(
        re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", generated_text)
    )
    if emoji_count > 4:
        return "caption-এ অতিরিক্ত emoji ব্যবহার হয়েছে।"
    return None


def _error_result(message: str) -> dict[str, str]:
    return {
        "angle": "",
        "caption": "",
        "first_comment": "",
        "suggested_design_prompt": message,
    }


def generate_post_content(
    product: dict,
    group: dict,
    past_history: list = None,
    custom_instruction: str = None,
) -> dict:
    """Generate one factual, medium-length promotional post.

    Product and group dictionaries are embedded in the prompt as the sole
    source of product facts. The structured tool call is parsed as JSON and
    validated locally before it is returned.
    """
    if not isinstance(product, dict) or not isinstance(group, dict):
        return _error_result("সঠিক product এবং group dictionary দিতে হবে।")

    recent_angles = _recent_angles(past_history)
    selected_angle = _choose_angle(recent_angles)
    banned = _banned_phrases(custom_instruction)
    instruction = custom_instruction or "কোনো অতিরিক্ত নির্দেশনা নেই।"

    system_prompt = (
        "You are a real Bangladeshi university student running Campus Feriiwala. "
        "Write warm, grounded, practical Bangla mixed naturally with standard English tech terms. "
        "Address customers as ভাইয়া/আপু when natural; never use তুমি in promotional or selling text. "
        "Use exactly the requested angle. The caption must contain 150–250 whitespace-separated words. "
        "Start with a relatable campus/student hook, transition naturally to the product, explain only "
        "benefits supported by the supplied product dictionary, mention warranty and campus delivery, "
        "and end with a soft natural CTA. Never invent battery hours, ANC values, colors, features, "
        "money, currency, pricing, or warranty details. Do not mention facts absent from the product dictionary. "
        f"Strictly avoid these banned phrases: {_as_json(list(banned))}. "
        "Do not use fake scarcity or urgency, and use at most a few emojis."
    )
    system_prompt += (
        " Output must be written in pure Bengali script, with natural campus loan words such as ইউজ, ডেইলি লাইফ, ক্লাস, এক্সাম, এসাইনমেন্ট, বাসের জ্যাম, চার্জ ব্যাকআপ. "
        "Never use sadhu or textbook wording such as ব্যবহার, দৈনন্দিন জীবন, or ক্রয় করুন. Tailor the two-line hook and body to the user's custom topic or style instruction when provided."
    )
    user_prompt = (
        f"Required angle: {selected_angle}\n"
        f"Recent angles from the last 7 days: {_as_json(recent_angles)}\n"
        f"Product facts (only source of product facts): {_as_json(_public_product(product))}\n"
        f"Target group: {_as_json(group)}\n"
        f"Custom instruction: {instruction}\n"
        "Create the caption, a useful first comment, and a concise visual design prompt."
    )

    try:
        from google import genai
        from google.genai import types
        from config import GEMINI_API_KEY
        from core.gemini_client import DEFAULT_GEMINI_MODEL, generate_content_with_retry

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = generate_content_with_retry(
            client,
            model=DEFAULT_GEMINI_MODEL,
            contents=(
                f"{system_prompt}\n\n{user_prompt}\n\n"
                "Return only valid JSON with exactly these keys: "
                "angle, caption, first_comment, suggested_design_prompt."
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.7,
            ),
        )
        output = json.loads(response.text or "{}")
        if not isinstance(output, dict):
            raise ValueError("The structured response was not a JSON object.")
    except Exception:
        return {"product_id": str(product.get("id", product.get("name", ""))), **_fallback_content(product, selected_angle)}

    output["caption"] = _ensure_hook(output.get("caption", ""), str(product.get("name", "product")))
    output["first_comment"] = _without_pricing(output.get("first_comment", ""), product.get("price"))
    output["suggested_design_prompt"] = _without_pricing(output.get("suggested_design_prompt", ""), product.get("price"))
    validation_error = _validate_output(output, selected_angle, custom_instruction)
    if validation_error:
        return {"product_id": str(product.get("id", product.get("name", ""))), **_fallback_content(product, selected_angle)}

    return {
        "product_id": str(product.get("id", product.get("name", ""))),
        "angle": output["angle"],
        "caption": _finalize_caption(output["caption"].strip(), str(product.get("name", "এই প্রোডাক্ট")), product),
        "first_comment": _copy_safe(output["first_comment"].strip()),
        "suggested_design_prompt": _without_pricing(output["suggested_design_prompt"].strip(), product.get("price")),
    }


_COPY_CLICHES = (
    "এই ছোট্ট গ্যাজেটটি ডেইলি লাইফে সত্যিই কাজে লাগবে",
    "ভাইয়া বা আপু",
    "কাজে লাগবে মনে হলে মেসেজ করে জানিয়ে দিন",
    "কাজে লাগবে মনে হলে মেসেজে জানিয়ে দিন",
    "দেরি না করে এখনি সংগ্রহ করুন",
)


def _copy_safe(value: Any) -> str:
    text = _without_pricing(value)
    for phrase in _COPY_CLICHES:
        text = text.replace(phrase, "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _hook(product_name: str) -> str:
    return "বাসের জ্যাম, ক্লাস আর ডেডলাইনের ভিড়ে কোন ঝামেলাটা সবচেয়ে বেশি?\nআজকের কপিটা ঠিক সেই ক্যাম্পাস মুহূর্ত থেকেই শুরু হোক।"


def _ensure_hook(caption: str, product_name: str) -> str:
    body = _copy_safe(caption)
    hook = _hook(product_name)
    if body.startswith(hook):
        return body
    return f"{hook}\n\n{body}" if body else hook


def _fallback_content(product: dict[str, Any], selected_angle: str) -> dict[str, str]:
    warranty = str(product.get("warranty", "প্রযোজ্য ওয়ারেন্টি"))
    name = str(product.get("name", "এই প্রোডাক্ট"))
    specs = product.get("specs", {})
    detail = ", ".join(f"{key}: {value}" for key, value in specs.items() if value not in (None, False))
    caption = _ensure_hook(
        f"ক্লাসের নোট, এসাইনমেন্ট আর বাসের জ্যাম—এই সবের মাঝে {name}-এর ফিচারগুলো বাস্তবেই কাজে আসে। {detail[:260]}। "
        f"ক্যাম্পাসে ডেলিভারি আছে, আর ওয়ারেন্টি থাকছে {warranty}।\n\nঅর্ডার বা ডিটেইলসে: [www.feriiwala.com](http://www.feriiwala.com)",
        name,
    )
    return {
        "angle": selected_angle,
        "caption": caption,
        "first_comment": "তোমার ক্যাম্পাস রুটিনে কোন ফিচারটা সবচেয়ে দরকারি মনে হচ্ছে? কমেন্টে বলো।",
        "suggested_design_prompt": "পরিষ্কার ক্যাম্পাস কপি, প্রোডাক্টের বাস্তব ফিচার, কোনো সেলসি ভাষা নয়।",
    }


def _refinement_fallback(product: dict[str, Any], old_caption: str, user_instruction: str) -> dict[str, str]:
    base = _fallback_content(product, "practical_recommendation")
    text = _copy_safe(old_caption) or base["caption"]
    instruction = user_instruction.casefold()
    if "বাসের জ্যাম" in user_instruction and ("বাদ" in user_instruction or "remove" in instruction):
        text = text.replace("বাসের জ্যাম", "ক্যাম্পাসের যাতায়াত")
    if any(term in instruction for term in ("ছোট", "short")):
        lines = text.splitlines()
        text = "\n".join(lines[:6])
    elif any(term in instruction for term in ("বড়", "ডিটেইল", "detail")):
        specs = product.get("specs", {})
        extra = "\n".join(f"{key}: {value}" for key, value in specs.items() if value not in (None, False))
        text = f"{text}\n{extra[:320]}"
    final_caption = _finalize_caption(text, str(product.get("name", "এই প্রোডাক্ট")), product)
    if "বাসের জ্যাম" in user_instruction and ("বাদ" in user_instruction or "remove" in instruction):
        final_caption = final_caption.replace("বাসের জ্যাম", "ক্যাম্পাসের যাতায়াত")
    return {
        "caption": final_caption,
        "first_comment": "এই ভার্সনটা কেমন লাগল? কোন অংশটা আরও স্বাভাবিক করা যায়, বলো।",
    }


def refine_caption(product: dict[str, Any], old_caption: str, user_instruction: str) -> dict[str, str]:
    """Revise the active copy without routing feedback through the scheduler parser."""
    if not isinstance(product, dict):
        return {"caption": _copy_safe(old_caption), "first_comment": "কপিটা দেখে মতামত জানাও।"}
    try:
        from google import genai
        from google.genai import types
        from config import GEMINI_API_KEY
        from core.gemini_client import DEFAULT_GEMINI_MODEL, generate_content_with_retry

        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = (
            "তুমি Campus Feriiwala-র ক্যাম্পাস কপি এডিটর। আগের কপিটা রেখে ব্যবহারকারীর পরিবর্তনের নির্দেশ মেনে নতুন কপি লেখো। "
            "শুধু স্বাভাবিক বাংলা, ক্যাম্পাসের কথ্য টোন, দুই লাইনের হুক, ৩-৪টি কথোপকথনের লাইন, ওয়ারেন্টি এবং ক্যাম্পাস ডেলিভারি রাখবে। "
            "ভাইয়া বা আপু, সেলসি বুলি, দাম, টাকা, খরচ, পুশি CTA এবং ক্রয়-ধরনের ভাষা লিখবে না। শেষে এই লাইনটি রাখবে: অর্ডার বা ডিটেইলসে: [www.feriiwala.com](http://www.feriiwala.com)\n"
            f"প্রোডাক্ট: {_as_json(_public_product(product))}\nআগের কপি:\n{old_caption}\nব্যবহারকারীর নির্দেশ: {user_instruction}\n"
            "JSON দাও: {\"caption\":\"...\",\"first_comment\":\"...\"}"
        )
        response = generate_content_with_retry(
            client,
            model=DEFAULT_GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4),
        )
        result = json.loads(response.text or "{}")
        if isinstance(result, dict) and result.get("caption"):
            safe_revision = _refinement_fallback(product, _copy_safe(result["caption"]), user_instruction)
            return {
                "caption": safe_revision["caption"],
                "first_comment": _copy_safe(result.get("first_comment", safe_revision["first_comment"])),
            }
    except Exception:
        pass
    return _refinement_fallback(product, old_caption, user_instruction)


def _finalize_caption(caption: str, product_name: str, product: dict[str, Any]) -> str:
    """Apply the same peer-to-peer structure to model and offline copy."""
    finalized = _ensure_hook(caption, product_name)
    if "www.feriiwala.com" not in finalized:
        warranty = str(product.get("warranty", "প্রযোজ্য ওয়ারেন্টি"))
        finalized = f"{finalized}\n\nওয়ারেন্টি: {warranty}। ক্যাম্পাস ডেলিভারি আছে।\nঅর্ডার বা ডিটেইলসে: [www.feriiwala.com](http://www.feriiwala.com)"
    return _copy_safe(finalized)
