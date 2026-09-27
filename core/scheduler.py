"""AsyncIO scheduling and persistence for Campus Feriiwala posts."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from datetime import date as date_type
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from config import TIMEZONE


Callback = Callable[[dict[str, Any]], Any]
MORNING_JOB_ID = "morning_checkin"
BD_TZ = ZoneInfo("Asia/Dhaka")
MISFIRE_GRACE_TIME = 60


class PostScheduler:
    """Manage in-memory APScheduler jobs with a small JSON persistence layer."""

    def __init__(self, timezone_name: str = TIMEZONE, store_path: Path | None = None) -> None:
        self.timezone = BD_TZ
        configured_store = os.getenv("SCHEDULE_STORE_PATH", "").strip()
        self.store_path = store_path or Path(configured_store or (Path(__file__).resolve().parent.parent / "data" / "scheduled_jobs.json"))
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.active_schedule_registry: dict[str, dict[str, Any]] = {}
        self._callbacks: dict[str, Callback] = {}
        self._morning_callback: Callback | None = None
        self._started = False
        self._load_registry()
        self.scheduler.add_listener(self._on_job_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    @property
    def is_running(self) -> bool:
        return self._started

    def _load_registry(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Ephemeral/container deployments can still run in-memory when
            # their filesystem is read-only; persistence is best effort.
            return
        try:
            if not self.store_path.exists() or self.store_path.stat().st_size == 0:
                self.active_schedule_registry = {}
                return
        except OSError:
            self.active_schedule_registry = {}
            return
        try:
            with self.store_path.open("r", encoding="utf-8") as file:
                entries = json.load(file)
            if not isinstance(entries, list):
                raise ValueError("scheduled_jobs.json must contain a JSON array.")
            self.active_schedule_registry = {
                str(entry["post_id"]): entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("post_id")
            }
        except (OSError, json.JSONDecodeError, ValueError):
            self.active_schedule_registry = {}

    def _persist_registry(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            entries = list(self.active_schedule_registry.values())
            temporary_path = self.store_path.with_suffix(".tmp")
            with temporary_path.open("w", encoding="utf-8") as file:
                json.dump(entries, file, ensure_ascii=False, indent=2)
                file.write("\n")
            temporary_path.replace(self.store_path)
        except OSError as exc:
            print(f"[scheduler] Persistence unavailable; continuing in memory: {exc}", flush=True)

    def _parse_dispatch_time(self, post_data: dict[str, Any], minutes_before: int = 0) -> datetime:
        scheduled_time = str(post_data.get("scheduled_time", "")).strip()
        post_date = post_data.get("date")
        try:
            parsed_time = datetime.strptime(scheduled_time, "%H:%M").time()
            if isinstance(post_date, datetime):
                local_date = post_date.astimezone(BD_TZ).date() if post_date.tzinfo else post_date.date()
            elif isinstance(post_date, date_type):
                local_date = post_date
            else:
                local_date = datetime.strptime(str(post_date), "%Y-%m-%d").date()
        except (TypeError, ValueError) as exc:
            raise ValueError("date must be YYYY-MM-DD and scheduled_time must be HH:MM.") from exc

        return datetime.combine(local_date, parsed_time, tzinfo=BD_TZ) - timedelta(minutes=minutes_before)

    async def _run_callback(self, job_id: str) -> None:
        entry = self.active_schedule_registry.get(job_id)
        callback = self._callbacks.get(job_id)
        if not entry or callback is None:
            return
        result = callback(entry["post_data"])
        if inspect.isawaitable(result):
            await result
        self.active_schedule_registry.pop(job_id, None)
        self._callbacks.pop(job_id, None)
        self._persist_registry()

    def _on_job_event(self, event: Any) -> None:
        # APScheduler reports missed/error events through its listener. The
        # job remains persisted so operators can inspect or reschedule it.
        if getattr(event, "exception", None):
            post_id = str(getattr(event, "job_id", ""))
            if post_id in self.active_schedule_registry:
                self.active_schedule_registry[post_id]["last_error"] = str(event.exception)
                self._persist_registry()

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("PostScheduler has not been started. Call start() first.")

    def _schedule_job(self, post_id: str, dispatch_time: datetime) -> None:
        now = datetime.now(BD_TZ)
        if dispatch_time <= now:
            return
        self.scheduler.add_job(
            self._run_callback,
            trigger="date",
            run_date=dispatch_time,
            args=[post_id],
            id=post_id,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
            max_instances=1,
        )

    def seconds_until_post(self, post_data: dict[str, Any]) -> float:
        """Return seconds until T-0 in the scheduler timezone."""
        return (self._parse_dispatch_time(post_data, 0) - datetime.now(BD_TZ)).total_seconds()

    def plan_stages(self, post_data: dict[str, Any]) -> list[tuple[str, datetime]]:
        """Choose only future reminder stages for the post's current lead time."""
        target = self._parse_dispatch_time(post_data, 0)
        now = datetime.now(BD_TZ)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return []
        if remaining > 10 * 60:
            offsets = (("stage_1", 10), ("stage_2", 5), ("stage_3", 0))
        elif remaining > 5 * 60:
            offsets = (("stage_2", 5), ("stage_3", 0))
        else:
            offsets = (("stage_3", 0),)
        return [
            (stage, target - timedelta(minutes=minutes_before))
            for stage, minutes_before in offsets
            if target - timedelta(minutes=minutes_before) > now
        ]

    def restore_scheduled_posts(self, callback_func: Callback) -> int:
        """Re-register persisted posts after a process restart.

        Callback functions cannot be serialized to JSON, so the application
        supplies the callback again during startup. Persisted jobs retain their
        original dispatch timestamp and APScheduler applies the grace window.
        """
        self._ensure_started()
        if not callable(callback_func):
            raise TypeError("callback_func must be callable.")
        restored = 0
        expired: list[str] = []
        for post_id, entry in self.active_schedule_registry.items():
            try:
                dispatch_time = datetime.fromisoformat(str(entry["dispatch_time"]))
                if dispatch_time.tzinfo is None:
                    dispatch_time = dispatch_time.replace(tzinfo=BD_TZ)
                else:
                    dispatch_time = dispatch_time.astimezone(BD_TZ)
                if dispatch_time <= datetime.now(BD_TZ):
                    expired.append(post_id)
                    continue
                job_id = str(entry.get("job_id", post_id))
                self._callbacks[job_id] = callback_func
                self._schedule_job(job_id, dispatch_time)
                restored += 1
            except (KeyError, TypeError, ValueError):
                continue
        for post_id in expired:
            self.active_schedule_registry.pop(post_id, None)
        if expired:
            self._persist_registry()
        return restored

    def add_scheduled_post(self, post_data: dict, callback_func: Callback) -> str:
        """Persist and schedule independent T-10, T-5, and T-0 jobs."""
        self._ensure_started()
        if not isinstance(post_data, dict):
            raise TypeError("post_data must be a dictionary.")
        if not callable(callback_func):
            raise TypeError("callback_func must be callable.")
        for field in ("product_id", "group_code", "scheduled_time", "date"):
            if field not in post_data:
                raise ValueError(f"Missing required post field: {field}")

        post_id = str(post_data.get("post_id") or uuid.uuid4())
        target_plan = self.plan_stages(post_data)
        for stage, dispatch_time in target_plan:
            job_id = f"{post_id}:{stage}"
            stage_data = {**post_data, "reminder_stage": stage, "parent_post_id": post_id, "reminder_job_id": job_id}
            self.active_schedule_registry[job_id] = {
                "post_id": post_id,
                "job_id": job_id,
                "stage": stage,
                "post_data": stage_data,
                "dispatch_time": dispatch_time.isoformat(),
                "scheduled_time": str(post_data["scheduled_time"]),
                "date": str(post_data["date"]),
            }
            self._callbacks[job_id] = callback_func
            self._schedule_job(job_id, dispatch_time)
        self._persist_registry()
        return post_id

    def update_post_copy(self, post_id: str, caption: str, first_comment: str) -> None:
        """Persist the latest reviewed copy across every stage of a post."""
        parent_id = str(post_id)
        changed = False
        for entry in self.active_schedule_registry.values():
            if str(entry.get("post_id", "")) != parent_id and str(entry.get("post_data", {}).get("parent_post_id", "")) != parent_id:
                continue
            entry.setdefault("post_data", {})["last_caption"] = str(caption)
            entry["post_data"]["last_first_comment"] = str(first_comment)
            changed = True
        if changed:
            self._persist_registry()

    def cancel_scheduled_post(self, post_id: str) -> bool:
        """Remove a scheduled post from APScheduler and persistent storage."""
        self._ensure_started()
        post_id = str(post_id)
        job_ids = [job_id for job_id, entry in self.active_schedule_registry.items() if job_id == post_id or entry.get("post_id") == post_id]
        removed = bool(job_ids)
        for job_id in job_ids:
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass
            self.active_schedule_registry.pop(job_id, None)
            self._callbacks.pop(job_id, None)
        self._persist_registry()
        return removed

    def reschedule_post(self, post_id: str, new_time: str, callback_func: Callback) -> bool:
        """Update a post's scheduled time and re-register its seven-minute dispatch job."""
        self._ensure_started()
        post_id = str(post_id)
        entry = next((item for item in self.active_schedule_registry.values() if item.get("post_id") == post_id or item.get("job_id") == post_id), None)
        if entry is None:
            return False
        post_data = {key: value for key, value in entry["post_data"].items() if key not in {"reminder_stage", "parent_post_id"}}
        post_data["scheduled_time"] = new_time
        parent_id = str(entry.get("post_id", post_id))
        self.cancel_scheduled_post(parent_id)
        self.add_scheduled_post({**post_data, "post_id": parent_id}, callback_func)
        return True

    def setup_morning_cron(self, morning_callback_func: Callback, hour: int = 9, minute: int = 0) -> None:
        """Schedule a daily Dhaka-time morning check-in."""
        self._ensure_started()
        if not callable(morning_callback_func):
            raise TypeError("morning_callback_func must be callable.")
        self._morning_callback = morning_callback_func
        self.scheduler.add_job(
            self._run_morning_callback,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=self.timezone),
            id=MORNING_JOB_ID,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_TIME,
            coalesce=True,
            max_instances=1,
        )

    async def _run_morning_callback(self) -> None:
        if self._morning_callback is None:
            return
        result = self._morning_callback({"job_id": MORNING_JOB_ID})
        if inspect.isawaitable(result):
            await result

    def start(self, callback_func: Callback | None = None) -> None:
        """Start APScheduler on the current asyncio event loop."""
        if not self._started:
            self.scheduler.start()
            self._started = True
        if callback_func is not None:
            self.restore_scheduled_posts(callback_func)

    async def startup(self) -> None:
        """Async lifecycle hook suitable for an application startup handler."""
        self.start()

    def shutdown(self, wait: bool = False) -> None:
        """Stop APScheduler cleanly without cancelling persisted registry data."""
        if self._started:
            self.scheduler.shutdown(wait=wait)
            self._started = False

    async def shutdown_async(self, wait: bool = False) -> None:
        """Async lifecycle hook suitable for an application shutdown handler."""
        self.shutdown(wait=wait)


scheduler = PostScheduler()


def add_scheduled_post(post_data: dict, callback_func: Callback) -> str:
    return scheduler.add_scheduled_post(post_data, callback_func)


def cancel_scheduled_post(post_id: str) -> bool:
    return scheduler.cancel_scheduled_post(post_id)


def reschedule_post(post_id: str, new_time: str, callback_func: Callback) -> bool:
    return scheduler.reschedule_post(post_id, new_time, callback_func)


def setup_morning_cron(morning_callback_func: Callback, hour: int = 9, minute: int = 0) -> None:
    scheduler.setup_morning_cron(morning_callback_func, hour, minute)
