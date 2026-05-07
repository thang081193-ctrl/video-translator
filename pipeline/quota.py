"""Free-tier quota tracking for Gemini API.

Free tier limit is 1,000 requests/day per project (per key) on Flash-Lite/Flash.
Tracks per-key daily counts, resets at midnight Pacific (Google's reset time),
and emits warnings at 80% / 95% / 100% to keep the user inside free-tier ($0).

State is persisted to `_quota_state.json` at project root so counts survive
process restarts. Log lines tagged "[QUOTA]" so they're easy to grep / surface
in the web UI.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.logger import get_logger

log = get_logger("Quota")

DAILY_LIMIT_FREE = 1000          # RPD per project, Gemini Flash-Lite/Flash free tier
WARN_THRESHOLD = 0.80
CRITICAL_THRESHOLD = 0.95

# Suspicious activity detection
# A normal batch run sits around 10-20 RPM (one batch every ~3-5s × 2 keys).
# Sustained 30+ RPM crossing the 60s window means something automated is
# hammering the key — likely a leak being abused, not normal user activity.
SPIKE_WINDOW_SEC = 60
SPIKE_RPM_THRESHOLD = 30
SPIKE_REPEAT_COOLDOWN_SEC = 120  # Don't re-fire same alert more than once per 2min

_lock = threading.Lock()
_STATE_PATH = Path(__file__).resolve().parent.parent / "_quota_state.json"

# In-memory rolling window for spike detection + recent alerts feed
_recent_request_times: deque[float] = deque(maxlen=500)
_alerts: list[dict] = []
_MAX_ALERTS = 50


# ─── Pacific time helpers (DST-aware, no external deps) ─────────────────────

def _is_us_dst(now_utc: datetime) -> bool:
    """US DST: 2nd Sunday March 02:00 local through 1st Sunday November 02:00 local."""
    year = now_utc.year
    # 2nd Sunday of March
    d = datetime(year, 3, 8)
    while d.weekday() != 6:  # 6 = Sunday
        d += timedelta(days=1)
    dst_start_utc = d + timedelta(hours=10)  # 02:00 PST → UTC
    # 1st Sunday of November
    d = datetime(year, 11, 1)
    while d.weekday() != 6:
        d += timedelta(days=1)
    dst_end_utc = d + timedelta(hours=9)     # 02:00 PDT → UTC
    naive = now_utc.replace(tzinfo=None)
    return dst_start_utc <= naive < dst_end_utc


def _pacific_offset_hours(now_utc: datetime | None = None) -> int:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return -7 if _is_us_dst(now_utc) else -8


def _pacific_today() -> str:
    now_utc = datetime.now(timezone.utc)
    pt = now_utc + timedelta(hours=_pacific_offset_hours(now_utc))
    return pt.strftime("%Y-%m-%d")


def _next_reset_utc() -> datetime:
    """UTC moment of the next Pacific midnight (= when Google free-tier resets)."""
    now_utc = datetime.now(timezone.utc)
    offset = _pacific_offset_hours(now_utc)
    pt = now_utc + timedelta(hours=offset)
    next_pt_midnight = pt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return (next_pt_midnight - timedelta(hours=offset)).replace(tzinfo=timezone.utc)


def _hours_until_reset() -> float:
    return (_next_reset_utc() - datetime.now(timezone.utc)).total_seconds() / 3600


def _format_reset() -> str:
    """Concrete reset time string in BOTH local and Pacific.

    e.g. '14:00 07/05/2026 (giờ máy) | 00:00 PT | còn ~20.3h'
    """
    next_utc = _next_reset_utc()
    local = next_utc.astimezone()  # system local timezone (Vietnam = +07)
    hrs = _hours_until_reset()
    return (
        f"{local.strftime('%H:%M %d/%m/%Y')} {local.strftime('%z')} "
        f"| 00:00 PT | còn ~{hrs:.1f}h"
    )


def _key_id(api_key: str) -> str:
    return f"...{api_key[-6:]}" if api_key else "unknown"


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning(f"Could not persist quota state: {e}")


def _push_alert(severity: str, alert_type: str, message: str, **extra) -> None:
    """Append an alert to the in-memory feed. Dedupe within cooldown window."""
    now = time.time()
    # Skip if we already pushed this same type within the cooldown window.
    # Prevents log/UI flood when a sustained spike crosses the threshold every batch.
    last_same = next(
        (a for a in reversed(_alerts) if a["type"] == alert_type),
        None,
    )
    if last_same and (now - last_same["timestamp"]) < SPIKE_REPEAT_COOLDOWN_SEC:
        return
    alert = {
        "timestamp": now,
        "iso_time": datetime.now().isoformat(timespec="seconds"),
        "severity": severity,           # "warn" | "critical"
        "type": alert_type,             # "spike" | "quota_warn" | "quota_critical" | "quota_exhausted"
        "message": message,
        **extra,
    }
    _alerts.append(alert)
    if len(_alerts) > _MAX_ALERTS:
        del _alerts[: len(_alerts) - _MAX_ALERTS]


def _check_rate_spike() -> None:
    """Detect sustained burst that might indicate key abuse.

    Called inside the lock by record_request after the timestamp is added.
    Trims _recent_request_times to the last SPIKE_WINDOW_SEC and fires an
    alert if the count crosses SPIKE_RPM_THRESHOLD.
    """
    now = time.time()
    cutoff = now - SPIKE_WINDOW_SEC
    while _recent_request_times and _recent_request_times[0] < cutoff:
        _recent_request_times.popleft()
    rpm = len(_recent_request_times)
    if rpm >= SPIKE_RPM_THRESHOLD:
        msg = (
            f"Sustained rate spike: {rpm} req in last {SPIKE_WINDOW_SEC}s "
            f"(threshold {SPIKE_RPM_THRESHOLD}). If you're not running a batch, "
            f"a key may be leaking — rotate immediately."
        )
        _push_alert("critical", "spike", msg, rpm=rpm, window_sec=SPIKE_WINDOW_SEC)
        log.error(f"[QUOTA-ALERT] {msg}")


def record_request(api_key: str) -> None:
    """Increment counter for this key. Logs threshold warnings inline."""
    today = _pacific_today()
    kid = _key_id(api_key)
    with _lock:
        state = _load_state()
        if state.get("date") != today:
            state = {"date": today, "counts": {}}
        counts = state.setdefault("counts", {})
        counts[kid] = counts.get(kid, 0) + 1
        count = counts[kid]
        _save_state(state)

        # Suspicious-activity tracking — inside lock so timestamps stay ordered
        _recent_request_times.append(time.time())
        _check_rate_spike()

    warn_at = int(DAILY_LIMIT_FREE * WARN_THRESHOLD)
    crit_at = int(DAILY_LIMIT_FREE * CRITICAL_THRESHOLD)

    if count == warn_at:
        msg = (
            f"Key {kid} hit {WARN_THRESHOLD*100:.0f}% of free-tier daily limit "
            f"({count}/{DAILY_LIMIT_FREE} req). Reset: {_format_reset()}"
        )
        log.warning(f"[QUOTA] {msg}")
        _push_alert("warn", "quota_warn", msg, key=kid, count=count)
    elif count == crit_at:
        msg = (
            f"Key {kid} at {CRITICAL_THRESHOLD*100:.0f}% of free-tier limit "
            f"({count}/{DAILY_LIMIT_FREE}). Only {DAILY_LIMIT_FREE - count} req left. "
            f"Reset: {_format_reset()}"
        )
        log.error(f"[QUOTA] {msg}")
        _push_alert("critical", "quota_critical", msg, key=kid, count=count)
    elif count == DAILY_LIMIT_FREE:
        msg = (
            f"Key {kid} reached 100% ({count}/{DAILY_LIMIT_FREE}). "
            f"Next request will likely fail with 429. Reset: {_format_reset()}"
        )
        log.error(f"[QUOTA] {msg}")
        _push_alert("critical", "quota_exhausted", msg, key=kid, count=count)


def record_quota_exceeded(api_key: str, error_msg: str) -> None:
    """Called when API returns 429 RESOURCE_EXHAUSTED."""
    kid = _key_id(api_key)
    msg = (
        f"FREE-TIER EXHAUSTED on key {kid}. Reset: {_format_reset()}. "
        f"To stay $0: wait for reset, or add more keys (one per GCP project)."
    )
    log.error(f"[QUOTA] {msg} API said: {error_msg[:200]}")
    _push_alert("critical", "quota_exhausted", msg, key=kid, api_error=error_msg[:200])


def get_recent_alerts(limit: int = 20) -> list[dict]:
    """Return up to `limit` most-recent alerts, newest first.

    Used by /api/alerts and the UI banner. Alerts persist only in memory,
    so a server restart clears them — that's intentional, alerts older than
    the current process aren't actionable anyway.
    """
    with _lock:
        return list(reversed(_alerts[-limit:]))


def reset_alerts_for_tests() -> None:
    """Test-only: clear in-memory alert feed and rate window."""
    with _lock:
        _alerts.clear()
        _recent_request_times.clear()


def get_usage_summary() -> dict:
    """Return current state for monitoring / future UI integration."""
    today = _pacific_today()
    next_reset = _next_reset_utc()
    local = next_reset.astimezone()
    with _lock:
        state = _load_state()
    counts = state.get("counts", {}) if state.get("date") == today else {}
    return {
        "date": today,
        "counts": counts,
        "limit_per_key": DAILY_LIMIT_FREE,
        "reset_at_utc": next_reset.isoformat(),
        "reset_at_local": local.strftime("%H:%M %d/%m/%Y %z"),
        "hours_until_reset": round(_hours_until_reset(), 2),
    }
