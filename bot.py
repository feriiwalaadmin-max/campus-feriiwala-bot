"""Campus Feriiwala: text-only campus copywriter and reminder bot."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytz
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from config import TELEGRAM_ADMIN_CHAT_ID, TELEGRAM_BOT_TOKEN, TIMEZONE
from core.database import (
    get_group_by_code,
    get_product_by_name_or_alias,
    get_recent_history,
    load_groups,
    load_products,
    save_content_history,
    save_performance_log,
)
from core.generator import generate_post_content
from core.parser import parse_daily_plan
from core.scheduler import scheduler

_application: Application | None = None


def _authorized(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == TELEGRAM_ADMIN_CHAT_ID


def _local_now() -> datetime:
    return datetime.now(pytz.timezone(TIMEZONE))


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
    stage = str(post_data.get("reminder_stage", "stage_3"))
    name = str(post_data.get("product_name", post_data.get("product_id", "প্রোডাক্ট")))
    group = get_group_by_code(str(post_data.get("group_code", "")))
    group_name = str(group.get("name", "")) if group else ""
    group_suffix = f" ({group_name})" if group_name else ""
    target_time = str(post_data.get("scheduled_time", ""))

    if stage == "stage_1":
        await _application.bot.send_message(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            text=f"🔔 তোমার {target_time}-এ {name} নিয়ে পোস্ট আছে{group_suffix}। পোস্টের প্রস্তুতি নাও!",
        )
        return

    product = get_product_by_name_or_alias(name)
    if product is None:
        return
    generated = generate_post_content(
        product,
        group or {"code": post_data.get("group_code"), "name": group_name},
        past_history=get_recent_history(7),
        custom_instruction=post_data.get("instructions") or None,
    )
    package = f"ক্যাপশন:\n{generated.get('caption', '')}\n\nফার্স্ট কমেন্ট:\n{generated.get('first_comment', '')}"

    if stage == "stage_2":
        await _application.bot.send_message(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            text=(
                "📝 পোস্টের আর ৫ মিনিট বাকি! আজকের ক্যাপশন নিচে দেওয়া হলো, পড়ে দেখে নাও:\n\n"
                f"{package}\n\n"
                "💡 বদল চাইলে এখনই লিখে জানাও—যেমন ‘আরেকটু ছোট করো’ বা ‘এক্সাম নিয়ে হুক দাও’।"
            ),
        )
        return

    await _application.bot.send_message(
        chat_id=TELEGRAM_ADMIN_CHAT_ID,
        text=f"🚀 সময় হয়ে গেছে! ফাইনাল ক্যাপশনটা কপি করে গ্রুপে পোস্ট করে দাও।\n\n{package}",
    )


async def _morning_callback(_: dict[str, Any]) -> None:
    if _application is not None:
        await _application.bot.send_message(
            chat_id=TELEGRAM_ADMIN_CHAT_ID,
            text="☀️ আজ কোন প্রোডাক্ট আর কোন সময়ে পোস্ট দিতে চাও?",
        )


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update) or update.message is None or not update.message.text:
        return
    text = update.message.text.strip()
    metrics = _feedback_metrics(text)
    if metrics is not None:
        save_performance_log(metrics)
        await update.message.reply_text("ফিডব্যাক সেভ হয়েছে।")
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
            await update.message.reply_text(
                f"আপডেটেড কপি:\n\nক্যাপশন:\n{generated.get('caption', '')}\n\nফার্স্ট কমেন্ট:\n{generated.get('first_comment', '')}"
            )
            return

    try:
        parsed = await parse_daily_plan(text, load_products(), load_groups())
    except Exception:
        await update.message.reply_text("প্ল্যানটা বুঝতে পারিনি। প্রোডাক্ট, সময় আর গ্রুপ কোডসহ আবার লিখে দাও।")
        return

    if not parsed.get("success"):
        await update.message.reply_text(parsed.get("clarification_message") or "আরও একটু তথ্য দাও।")
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
    await update.message.reply_text("📋 শিডিউল কনফার্মড:\n" + "\n".join(confirmations) if confirmations else "কোনো valid post পাওয়া যায়নি।")


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
    application = build_application()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
    application.run_polling(allowed_updates=Update.ALL_TYPES, close_loop=False)


if __name__ == "__main__":
    main()
