"""Campus Feriiwala: text-only campus copywriter and reminder bot."""

from __future__ import annotations

import os
import threading
import time
from urllib.request import urlopen
import uuid
from datetime import datetime
from typing import Any

from flask import Flask
from telegram import LinkPreviewOptions, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from zoneinfo import ZoneInfo

from config import TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_BOT_TOKEN
from core.database import (
    get_group_by_code,
    get_product_by_name_or_alias,
    get_recent_history,
    load_groups,
    load_products,
    save_content_history,
    save_performance_log,
)
from core.generator import generate_post_content, refine_caption
from core.parser import parse_daily_plan
from core.scheduler import scheduler

_application: Application | None = None
_refinement_parents: set[str] = set()
active_post_context: dict[int, dict[str, Any]] = {}
BD_TZ = ZoneInfo("Asia/Dhaka")
NO_LINK_PREVIEW = LinkPreviewOptions(is_disabled=True)
app = Flask(__name__)


@app.route("/")
def health() -> tuple[str, int]:
    return "Campus Feriiwala Bot is running!", 200


def _start_health_server() -> None:
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def _keep_health_server_awake() -> None:
    port = int(os.environ.get("PORT", "10000"))
    while True:
        time.sleep(300)
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=5):
                pass
        except Exception:
            pass


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == TELEGRAM_ADMIN_CHAT_ID


def _local_now() -> datetime:
    return datetime.now(BD_TZ)


def _group_name(code: str | None) -> str:
    group = get_group_by_code(code or "")
    return str(group.get("name", code)) if group else str(code or "নির্দিষ্ট গ্রুপ নেই")


def _feedback_metrics(text: str) -> dict[str, Any] | None:
    lowered = text.casefold()
    if not any(term in lowered for term in ("order", "orders", "inquiry", "inquiries", "অর্ডার", "ইনকোয়ারি")):
        return None
    return {
        "orders": None,
        "inquiries": int("inquir" in lowered or "ইনকোয়ারি" in lowered),
        "order_received": "আসেনি" not in lowered and "not" not in lowered,
        "raw_feedback": text,
    }


async def _send_post_dispatch(post_data: dict[str, Any]) -> None:
    """Deliver one non-blocking text-only reminder stage."""
    if _application is None:
        return
    global _refinement_parents
    stage = str(post_data.get("reminder_stage", "stage_3"))
    name = str(post_data.get("product_name", post_data.get("product_id", "প্রোডাক্ট")))
    group = get_group_by_code(str(post_data.get("group_code", "")))
    group_name = str(group.get("name", "")) if group else ""
    group_suffix = f" ({group_name})" if group_name else ""
    target_time = str(post_data.get("scheduled_time", ""))

    if stage == "stage_1":
        await _application.bot.send_message(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            link_preview_options=NO_LINK_PREVIEW,
            text=f"🔔 তোমার {target_time}-এ {name} নিয়ে পোস্ট আছে{group_suffix}। পোস্টের প্রস্তুতি নাও!",
        )
        return

    product = get_product_by_name_or_alias(name)
    if product is None:
        return
    saved_caption = str(post_data.get("last_caption", "")).strip()
    saved_comment = str(post_data.get("last_first_comment", "")).strip()
    generated = (
        {"caption": saved_caption, "first_comment": saved_comment}
        if saved_caption
        else generate_post_content(
            product,
            group or {"code": post_data.get("group_code"), "name": group_name},
            past_history=get_recent_history(7),
            custom_instruction=post_data.get("instructions") or None,
        )
    )
    package = f"ক্যাপশন:\n{generated.get('caption', '')}\n\nফার্স্ট কমেন্ট:\n{generated.get('first_comment', '')}"

    if stage == "stage_2":
        scheduler.update_post_copy(str(post_data.get("parent_post_id", "")), generated.get("caption", ""), generated.get("first_comment", ""))
        parent_id = str(post_data.get("parent_post_id", ""))
        _refinement_parents.add(parent_id)
        active_post_context[int(TELEGRAM_ADMIN_CHAT_ID)] = {
            "parent_post_id": parent_id,
            "product_id": product.get("id"),
            "product_name": product.get("name", name),
            "group": group or {},
            "old_caption": generated.get("caption", ""),
            "first_comment": generated.get("first_comment", ""),
            "review_delivered": True,
            "target_time": post_data.get("scheduled_time", ""),
        }
        await _application.bot.send_message(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            link_preview_options=NO_LINK_PREVIEW,
            text=(
                "📝 পোস্টের আর ৫ মিনিট বাকি! আজকের ক্যাপশন নিচে দেওয়া হলো, পড়ে দেখে নাও:\n\n"
                f"{package}\n\n"
                "💡 বদল চাইলে এখনই লিখে জানাও—যেমন ‘আরেকটু ছোট করো’ বা ‘এক্সাম নিয়ে হুক দাও’।"
            ),
        )
        return

    await _application.bot.send_message(
        chat_id=TELEGRAM_ADMIN_CHAT_ID,
        link_preview_options=NO_LINK_PREVIEW,
        text=f"🚀 সময় হয়ে গেছে! ফাইনাল ক্যাপশনটা কপি করে গ্রুপে পোস্ট করে দাও।\n\n{package}",
    )
    parent_id = str(post_data.get("parent_post_id", ""))
    active_post_context.pop(int(TELEGRAM_ADMIN_CHAT_ID), None)
    _refinement_parents.discard(parent_id)


async def _morning_callback(_: dict[str, Any]) -> None:
    if _application is not None:
        await _application.bot.send_message(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            link_preview_options=NO_LINK_PREVIEW,
            text="☀️ আজ কোন প্রোডাক্ট আর কোন সময়ে পোস্ট দিতে চাও?",
        )


async def _reply(update: Update, text: str) -> None:
    await update.message.reply_text(text, link_preview_options=NO_LINK_PREVIEW)


RESET_CONFIRMATION = "সব পূর্ববর্তী শিডিউল বাতিল করা হয়েছে এবং মেমোরি সম্পূর্ণ ক্লিয়ার করা হয়েছে।"
RESET_TEXTS = {"স্টপ", "সব ক্লিয়ার করো", "ক্লিয়ার", "সব ক্লিয়ার"}


def _is_reset_request(text: str) -> bool:
    return text.casefold().strip() in RESET_TEXTS


async def _clear_all_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or update.message is None:
        return
    scheduler.clear_all_scheduled_posts()
    active_post_context.clear()
    _refinement_parents.clear()
    await _reply(update, RESET_CONFIRMATION)


def _chat_id(update: Update) -> int | None:
    return update.effective_chat.id if update.effective_chat else None


async def _handle_text_legacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or update.message is None or not update.message.text:
        return
    text = update.message.text.strip()
    if _is_reset_request(text):
        await _clear_all_state(update, context)
        return
    metrics = _feedback_metrics(text)
    if metrics is not None:
        save_performance_log(metrics)
        await _reply(update, "ফিডব্যাক সেভ হয়েছে।")
        return

    feedback_terms = ("rewrite", "আরেকটু ছোট", "ছোট করো", "হুক দাও", "বদল", "rewrite করো")
    pending = next(
        (entry for entry in scheduler.active_schedule_registry.values() if entry.get("stage") == "stage_3"),
        None,
    )
    if pending and any(term.casefold() in text.casefold() for term in feedback_terms):
        pending_data = pending.get("post_data", {})
        product = get_product_by_name_or_alias(str(pending_data.get("product_name", "")))
        if product is not None:
            group = get_group_by_code(str(pending_data.get("group_code", ""))) or {}
            generated = generate_post_content(
                product,
                group,
                past_history=get_recent_history(7),
                custom_instruction=f"{pending_data.get('instructions', '')}\nUser feedback: {text}",
            )
            await _reply(update,
                f"আপডেটেড কপি:\n\nক্যাপশন:\n{generated.get('caption', '')}\n\nফার্স্ট কমেন্ট:\n{generated.get('first_comment', '')}"
            )
            return

    chat_id = _chat_id(update)
    if chat_id is not None:
        active_post_context.pop(chat_id, None)
    _refinement_parents.clear()
    try:
        parsed = await parse_daily_plan(text, load_products(), load_groups())
    except Exception:
        await _reply(update, "প্ল্যানটা বুঝতে পারিনি। প্রোডাক্ট, সময় আর গ্রুপ কোডসহ আবার লিখে দাও।")
        return

    if not parsed.get("success"):
        await _reply(update, parsed.get("clarification_message") or "আরও একটু তথ্য দাও।")
        return

    today = _local_now().date().isoformat()
    confirmations: list[str] = []
    for post in parsed.get("scheduled_posts", []):
        post_data = {
            "post_id": str(uuid.uuid4()),
            "product_id": post["product_id"],
            "product_name": post["product_name"],
            "group_code": post.get("group_code"),
            "scheduled_time": post["time"],
            "date": str(post.get("date") or post.get("target_date") or today),
            "instructions": post.get("instructions", text),
        }
        scheduler.add_scheduled_post(post_data, _send_post_dispatch)
        save_content_history({**post_data, "status": "SCHEDULED"})
        confirmations.append(f"• {post_data['date']} {post_data['scheduled_time']} — {post_data['product_name']} — {_group_name(post_data['group_code'])}")
    if parsed.get("schedule_days"):
        first_day_posts = [post for post in parsed.get("scheduled_posts", []) if int(post.get("day_number", 0)) == 1]
        first_day_posts.sort(key=lambda post: post.get("time", ""))
        first_time = first_day_posts[0].get("time", "") if first_day_posts else ""
        summary = f"মোট {parsed.get('schedule_days')} দিনের {parsed.get('total_events', len(confirmations))}টি পোস্ট সফলভাবে শিডিউল করা হয়েছে। দিন ১ শুরু হবে আগামীকাল {first_time} টায়।"
        await _reply(update, summary + ("\n\n" + "\n".join(confirmations) if confirmations else ""))
    else:
        await _reply(update, "📋 শিডিউল কনফার্মড:\n" + "\n".join(confirmations) if confirmations else "কোনো valid post পাওয়া যায়নি।")


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route review feedback before attempting to parse a new schedule."""
    global _refinement_parents
    if not _authorized(update) or update.message is None or not update.message.text:
        return
    text = update.message.text.strip()
    if _is_reset_request(text):
        await _clear_all_state(update, context)
        return
    metrics = _feedback_metrics(text)
    if metrics is not None:
        save_performance_log(metrics)
        await _reply(update, "ফিডব্যাক সেভ হয়েছে।")
        return

    feedback_terms = ("rewrite", "ছোট করো", "আরেকটু ছোট করো", "আরেকটু বড় করো", "ডিটেইল করো", "বাসের জ্যামের কথা বাদ দাও", "হুক বদলাও", "বদল", "হুক")
    pending = _active_refinement_entry(_chat_id(update))
    is_refinement = bool(pending and any(term.casefold() in text.casefold() for term in feedback_terms))
    if is_refinement:
        pending_data = pending.get("post_data", {})
        product = get_product_by_name_or_alias(str(pending_data.get("product_name", "")))
        if product is not None:
            group = get_group_by_code(str(pending_data.get("group_code", ""))) or {}
            old_caption = str(pending_data.get("last_caption", "")).strip()
            if not old_caption:
                old_caption = generate_post_content(product, group, past_history=get_recent_history(7), custom_instruction=pending_data.get("instructions") or None).get("caption", "")
            generated = refine_caption(
                {"product": product, "old_caption": old_caption, "group": group, "parent_post_id": pending_data.get("parent_post_id")},
                text,
            )
            parent_id = str(pending_data.get("parent_post_id", pending.get("post_id", "")))
            scheduler.update_post_copy(parent_id, generated.get("caption", ""), generated.get("first_comment", ""))
            if _chat_id(update) is not None:
                active_post_context[_chat_id(update)] = {
                    **active_post_context.get(_chat_id(update), {}),
                    "parent_post_id": parent_id,
                    "old_caption": generated.get("caption", ""),
                    "first_comment": generated.get("first_comment", ""),
                    "review_delivered": True,
                }
            await _reply(update,
                f"আপডেটেড কপি:\n\nক্যাপশন:\n{generated.get('caption', '')}\n\nফার্স্ট কমেন্ট:\n{generated.get('first_comment', '')}"
            )
            return
        if pending.get("stage") == "stage_2":
            await _reply(update, "এই পোস্টের প্রোডাক্টটা খুঁজে পাইনি, তাই কপিটা বদলাতে পারিনি।")
            return

    chat_id = _chat_id(update)
    if chat_id is not None:
        active_post_context.pop(chat_id, None)
    _refinement_parents.clear()
    try:
        parsed = await parse_daily_plan(text, load_products(), load_groups())
    except Exception:
        await _reply(update, "প্ল্যানটা বুঝতে পারিনি। প্রোডাক্ট, সময় আর গ্রুপ কোডসহ আবার লিখে দাও।")
        return
    if not parsed.get("success"):
        await _reply(update, parsed.get("clarification_message") or "আরও একটু তথ্য দাও।")
        return

    today = _local_now().date().isoformat()
    confirmations: list[str] = []
    for post in parsed.get("scheduled_posts", []):
        post_data = {
            "post_id": str(uuid.uuid4()),
            "product_id": post["product_id"],
            "product_name": post["product_name"],
            "group_code": post.get("group_code"),
            "scheduled_time": post["time"],
            "date": str(post.get("date") or post.get("target_date") or today),
            "instructions": post.get("instructions", text),
        }
        remaining = scheduler.seconds_until_post(post_data)
        if remaining <= 0:
            confirmations.append(f"সময়টি ইতিমধ্যে পেরিয়ে গেছে — {post_data['product_name']} আবার নতুন সময় দিয়ে শিডিউল করুন।")
            continue
        scheduler.add_scheduled_post(post_data, _send_post_dispatch)
        save_content_history({**post_data, "status": "SCHEDULED"})
        if remaining <= 10 * 60 and remaining > 5 * 60:
            confirmations.append("পোস্ট শিডিউল করা হয়েছে! ৫ মিনিট আগে ক্যাপশন পেয়ে যাবে।")
        elif remaining <= 5 * 60 and remaining > 0:
            product = get_product_by_name_or_alias(post_data["product_name"])
            group = get_group_by_code(str(post_data.get("group_code", ""))) or {}
            if product is not None:
                generated = generate_post_content(product, group, past_history=get_recent_history(7), custom_instruction=post_data.get("instructions") or None)
                scheduler.update_post_copy(post_data["post_id"], generated.get("caption", ""), generated.get("first_comment", ""))
                _refinement_parents.add(post_data["post_id"])
                if chat_id is not None:
                    active_post_context[chat_id] = {
                        "parent_post_id": post_data["post_id"],
                        "product_id": product.get("id"),
                        "product_name": product.get("name", post_data["product_name"]),
                        "group": group,
                        "old_caption": generated.get("caption", ""),
                        "first_comment": generated.get("first_comment", ""),
                        "review_delivered": True,
                        "target_time": post_data.get("scheduled_time", ""),
                    }
                confirmations.append(
                    "পোস্ট শিডিউল করা হয়েছে! এখনই ক্যাপশন প্যাকেজ দেখে নাও:\n\n"
                    f"ক্যাপশন:\n{generated.get('caption', '')}\n\nফার্স্ট কমেন্ট:\n{generated.get('first_comment', '')}"
                )
            else:
                confirmations.append("পোস্টের সময় খুব কাছাকাছি, কিন্তু প্রোডাক্টটি খুঁজে পাওয়া যায়নি।")
        else:
            confirmations.append(f"• {post_data['date']} {post_data['scheduled_time']} — {post_data['product_name']} — {_group_name(post_data['group_code'])}")
    if parsed.get("schedule_days"):
        first_day_posts = [post for post in parsed.get("scheduled_posts", []) if int(post.get("day_number", 0)) == 1]
        first_day_posts.sort(key=lambda post: post.get("time", ""))
        first_time = first_day_posts[0].get("time", "") if first_day_posts else ""
        summary = f"মোট {parsed.get('schedule_days')} দিনের {parsed.get('total_events', len(confirmations))}টি পোস্ট সফলভাবে শিডিউল করা হয়েছে। দিন ১ শুরু হবে আগামীকাল {first_time} টায়।"
        await _reply(update, summary + ("\n\n" + "\n".join(confirmations) if confirmations else ""))
    else:
        await _reply(update, "📋 শিডিউল কনফার্মড:\n" + "\n".join(confirmations) if confirmations else "কোনো valid post পাওয়া যায়নি।")


def _active_refinement_entry(chat_id: int | None) -> dict[str, Any] | None:
    """Return only a reviewed, not-yet-due Stage 3 post."""
    context = active_post_context.get(chat_id) if chat_id is not None else None
    if not context or not context.get("review_delivered"):
        return None
    context_parent = str(context.get("parent_post_id", ""))
    for entry in scheduler.active_schedule_registry.values():
        if entry.get("stage") != "stage_3":
            continue
        post_data = entry.get("post_data", {})
        parent_id = str(post_data.get("parent_post_id", entry.get("post_id", "")))
        if parent_id != context_parent or not str(post_data.get("last_caption", "")).strip():
            continue
        try:
            if scheduler.seconds_until_post(post_data) > 0:
                return entry
        except (TypeError, ValueError):
            continue
    return None


async def _post_init(application: Application) -> None:
    global _application
    _application = application
    scheduler.start(_send_post_dispatch)
    scheduler.setup_morning_cron(_morning_callback)


async def _post_shutdown(_: Application) -> None:
    global _application
    scheduler.shutdown(wait=False)
    _application = None


def build_application() -> Application:
    return (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )


def main() -> None:
    threading.Thread(target=_start_health_server, name="health-server", daemon=True).start()
    threading.Thread(target=_keep_health_server_awake, name="health-keepalive", daemon=True).start()
    application = build_application()
    application.add_handler(CommandHandler(("stop", "clear"), _clear_all_state))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
    application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)


if __name__ == "__main__":
    main()
