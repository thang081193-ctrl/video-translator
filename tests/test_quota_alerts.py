"""Tests for the suspicious-activity alert layer in pipeline/quota.py.

Covers:
- Rate spike detection: >SPIKE_RPM_THRESHOLD requests in SPIKE_WINDOW_SEC
- Quota threshold alerts: warn / critical / exhausted thresholds push entries
- Cooldown dedupe: same alert type doesn't re-fire within SPIKE_REPEAT_COOLDOWN_SEC
- /api/alerts endpoint contract
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pipeline import quota


@pytest.fixture(autouse=True)
def _reset_alerts(tmp_path, monkeypatch):
    """Isolate each test from real persisted quota state + clear alert feed."""
    # Redirect quota state file to tmp so we don't read/write real state
    monkeypatch.setattr(quota, "_STATE_PATH", tmp_path / "_q.json")
    quota.reset_alerts_for_tests()
    yield
    quota.reset_alerts_for_tests()


@pytest.fixture
def client():
    from web.app import create_app
    return TestClient(create_app())


class TestRateSpikeDetection:
    def test_normal_rate_does_not_trigger(self):
        """Just under the live spike threshold → no spike alert.

        SPIKE_RPM_THRESHOLD resolves from the active model's RPM cap, so
        derive the request count from it instead of hardcoding (a 10-RPM
        model alerts at 9; flash-lite at 18)."""
        for _ in range(quota.SPIKE_RPM_THRESHOLD - 1):
            quota.record_request("AIzaTest_Key1234")
        alerts = quota.get_recent_alerts()
        spike_alerts = [a for a in alerts if a["type"] == "spike"]
        assert spike_alerts == []

    def test_spike_above_threshold_fires_alert(self):
        """Burst above SPIKE_RPM_THRESHOLD within window → spike alert."""
        # Threshold resolves live from the active model's RPM cap
        for _ in range(quota.SPIKE_RPM_THRESHOLD + 1):
            quota.record_request("AIzaTest_Key1234")
        alerts = quota.get_recent_alerts()
        spike_alerts = [a for a in alerts if a["type"] == "spike"]
        assert len(spike_alerts) >= 1
        assert spike_alerts[0]["severity"] == "critical"
        assert "rotate" in spike_alerts[0]["message"].lower()

    def test_spike_alert_dedupes_within_cooldown(self):
        """Sustained spike across multiple calls only fires alert once per cooldown."""
        for _ in range(quota.SPIKE_RPM_THRESHOLD + 5):
            quota.record_request("AIzaTest_Key1234")
        # Continue firing — should NOT add another spike alert
        for _ in range(20):
            quota.record_request("AIzaTest_Key1234")
        spike_alerts = [a for a in quota.get_recent_alerts() if a["type"] == "spike"]
        assert len(spike_alerts) == 1, "cooldown should suppress re-fire"


class TestQuotaThresholdAlerts:
    """80% / 95% / 100% thresholds push alerts AND log inline."""

    def _set_count(self, kid: str, count: int) -> None:
        """Seed quota state to a specific count for a key."""
        with quota._lock:
            quota._save_state({"date": quota._pacific_today(), "counts": {kid: count}})

    def test_warn_threshold_fires_alert(self):
        # Seed at warn-1 then make one request to cross threshold
        warn_at = int(quota.DAILY_LIMIT_FREE * quota.WARN_THRESHOLD)
        kid = quota._key_id("AIzaTest_Key1234")
        self._set_count(kid, warn_at - 1)
        quota.record_request("AIzaTest_Key1234")
        alerts = [a for a in quota.get_recent_alerts() if a["type"] == "quota_warn"]
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "warn"

    def test_critical_threshold_fires_alert(self):
        crit_at = int(quota.DAILY_LIMIT_FREE * quota.CRITICAL_THRESHOLD)
        kid = quota._key_id("AIzaTest_Key2222")
        self._set_count(kid, crit_at - 1)
        quota.record_request("AIzaTest_Key2222")
        alerts = [a for a in quota.get_recent_alerts() if a["type"] == "quota_critical"]
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"

    def test_exhausted_429_fires_alert(self):
        quota.record_quota_exceeded("AIzaTest_Key3333", "RESOURCE_EXHAUSTED: 429 something")
        alerts = [a for a in quota.get_recent_alerts() if a["type"] == "quota_exhausted"]
        assert len(alerts) == 1
        assert alerts[0]["severity"] == "critical"


class TestAlertsEndpoint:
    def test_empty_returns_zero_count(self, client):
        r = client.get("/api/alerts")
        assert r.status_code == 200
        assert r.json() == {"alerts": [], "count": 0}

    def test_alerts_show_up_in_response(self, client):
        # Trigger 1 spike alert
        for _ in range(quota.SPIKE_RPM_THRESHOLD + 2):
            quota.record_request("AIzaTest_Key9999")
        r = client.get("/api/alerts")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        assert any(a["type"] == "spike" for a in body["alerts"])
        # Each alert has the contract the UI relies on
        for a in body["alerts"]:
            for field in ("timestamp", "iso_time", "severity", "type", "message"):
                assert field in a

    def test_alerts_returned_newest_first(self, client):
        # Push two distinct alerts via 2 different mechanisms with a tiny gap
        quota.record_quota_exceeded("AIzaTest_KeyAAAA", "first")
        time.sleep(0.01)
        for _ in range(quota.SPIKE_RPM_THRESHOLD + 1):
            quota.record_request("AIzaTest_KeyBBBB")
        r = client.get("/api/alerts")
        alerts = r.json()["alerts"]
        # Newest first: timestamps must be non-increasing
        timestamps = [a["timestamp"] for a in alerts]
        assert timestamps == sorted(timestamps, reverse=True)
