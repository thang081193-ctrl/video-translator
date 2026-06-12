"""Free-tier quota tracking for Gemini API.

Daily limits are per-PROJECT and per-MODEL (see MODEL_RATE_LIMITS — e.g.
flash 250 RPD but flash-lite only 20 RPD). Tracks per-key daily counts,
resets at midnight Pacific (Google's reset time), and emits warnings at
80% / 95% / 100% to keep the user inside free-tier ($0).

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

# ─── Google Gemini free-tier rate limits (per project, per model) ───────────
# Docs: https://ai.google.dev/gemini-api/docs/rate-limits — but published RPD
# lags enforcement. Real limits come from 429 payloads: trigger one and read
# `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier` + `limit`
# (docs claimed 1000 RPD for flash-lite; enforced limit observed 2026-05-11
# is 20). Each project gets one bucket — even with many keys on the same
# project they share the cap. Add new GCP projects (no billing) to scale.
MODEL_RATE_LIMITS: dict[str, dict[str, int]] = {
    "gemini-2.5-flash-lite": {"rpm": 20,  "tpm": 250_000,   "rpd": 20},   # 429 says limit: 20
    "gemini-2.5-flash":      {"rpm": 10,  "tpm": 250_000,   "rpd": 250},  # observed — use for batches
    "gemini-2.5-pro":        {"rpm": 5,   "tpm": 250_000,   "rpd": 100},
    "gemini-2.0-flash":      {"rpm": 15,  "tpm": 1_000_000, "rpd": 0},    # 429 says limit: 0 — NO free tier
}
DEFAULT_MODEL = "gemini-2.5-flash-lite"


def model_limits(model: str) -> dict[str, int]:
    """Return RPM/TPM/RPD for a model name; fall back to flash-lite if unknown."""
    return MODEL_RATE_LIMITS.get(model, MODEL_RATE_LIMITS[DEFAULT_MODEL])


# RPD limit for the active model — `cfg.translate.gemini_model` is the source
# of truth for which model is in use, but quota.py is imported standalone in
# some places so we resolve lazily via a getter.
def daily_limit_for(model: str | None = None) -> int:
    """RPD cap for the currently-configured model (or explicit override)."""
    if model is None:
        try:
            from pipeline.config import cfg
            model = cfg.translate.gemini_model
        except Exception:
            model = DEFAULT_MODEL
    return model_limits(model)["rpd"]


WARN_THRESHOLD = 0.80
CRITICAL_THRESHOLD = 0.95

# Suspicious activity detection — must stay BELOW the actual RPM cap so we
# alert before Google starts 429ing. Threshold derives from the ACTIVE
# model's RPM (e.g. flash at 10 RPM → alert at sustained 9+ in 60s).
SPIKE_WINDOW_SEC = 60
_SPIKE_RPM_HEADROOM = 0.9  # alert at 90% of model cap
SPIKE_REPEAT_COOLDOWN_SEC = 120  # Don't re-fire same alert more than once per 2min


def _spike_threshold() -> int:
    """Compute spike alert threshold from the active model's RPM cap.

    Re-evaluated on each request so a config change (e.g. switching to
    gemini-2.5-pro at 5 RPM) immediately tightens the alert window.
    """
    try:
        from pipeline.config import cfg
        rpm = model_limits(cfg.translate.gemini_model)["rpm"]
    except Exception:
        rpm = MODEL_RATE_LIMITS[DEFAULT_MODEL]["rpm"]
    return max(1, int(rpm * _SPIKE_RPM_HEADROOM))


# Legacy aliases — DAILY_LIMIT_FREE / SPIKE_RPM_THRESHOLD predate per-model
# limits. Resolved live (PEP 562) instead of snapshotted at import, so a
# GEMINI_MODEL override can't strand them at the default model's caps while
# record_request() enforces the configured model's.
def __getattr__(name: str):
    if name == "DAILY_LIMIT_FREE":
        return daily_limit_for()
    if name == "SPIKE_RPM_THRESHOLD":
        return _spike_threshold()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
    alert if the count crosses 90% of the active model's RPM cap.
    """
    now = time.time()
    cutoff = now - SPIKE_WINDOW_SEC
    while _recent_request_times and _recent_request_times[0] < cutoff:
        _recent_request_times.popleft()
    rpm = len(_recent_request_times)
    threshold = _spike_threshold()
    if rpm >= threshold:
        msg = (
            f"Sustained rate spike: {rpm} req in last {SPIKE_WINDOW_SEC}s "
            f"(threshold {threshold}, ~90% of model RPM cap). If you're not "
            f"running a batch, a key may be leaking — rotate immediately."
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

    daily_limit = daily_limit_for()
    warn_at = int(daily_limit * WARN_THRESHOLD)
    crit_at = int(daily_limit * CRITICAL_THRESHOLD)

    if count == warn_at:
        msg = (
            f"Key {kid} hit {WARN_THRESHOLD*100:.0f}% of free-tier daily limit "
            f"({count}/{daily_limit} req). Reset: {_format_reset()}"
        )
        log.warning(f"[QUOTA] {msg}")
        _push_alert("warn", "quota_warn", msg, key=kid, count=count)
    elif count == crit_at:
        msg = (
            f"Key {kid} at {CRITICAL_THRESHOLD*100:.0f}% of free-tier limit "
            f"({count}/{daily_limit}). Only {daily_limit - count} req left. "
            f"Reset: {_format_reset()}"
        )
        log.error(f"[QUOTA] {msg}")
        _push_alert("critical", "quota_critical", msg, key=kid, count=count)
    elif count == daily_limit:
        msg = (
            f"Key {kid} reached 100% ({count}/{daily_limit}). "
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
        "limit_per_key": daily_limit_for(),
        "reset_at_utc": next_reset.isoformat(),
        "reset_at_local": local.strftime("%H:%M %d/%m/%Y %z"),
        "hours_until_reset": round(_hours_until_reset(), 2),
    }
