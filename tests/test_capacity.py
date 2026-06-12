"""Tests for pipeline/capacity.py — key estimator + rate-limit registry."""

from __future__ import annotations

import pytest

from pipeline import capacity, quota


class TestModelRateLimits:
    """Rate-limit registry should match ENFORCED free-tier limits (from 429s)."""

    def test_flash_lite_default(self):
        assert quota.DEFAULT_MODEL == "gemini-2.5-flash-lite"
        lim = quota.MODEL_RATE_LIMITS["gemini-2.5-flash-lite"]
        assert lim["rpm"] == 20
        # Enforced limit from a real 429 payload, NOT the published 1000.
        assert lim["rpd"] == 20

    def test_flash_lite_rpd_cut_below_flash(self):
        """Enforced free tier inverts the published ordering: flash-lite gets
        only 20 RPD vs flash's 250 (verified via 429 quotaId
        GenerateRequestsPerDayPerProjectPerModel-FreeTier). Flash still has
        the tighter RPM."""
        lite = quota.MODEL_RATE_LIMITS["gemini-2.5-flash-lite"]
        flash = quota.MODEL_RATE_LIMITS["gemini-2.5-flash"]
        assert lite["rpd"] < flash["rpd"]
        assert flash["rpm"] < lite["rpm"]

    def test_pro_limits(self):
        pro = quota.MODEL_RATE_LIMITS["gemini-2.5-pro"]
        assert pro["rpm"] == 5
        assert pro["rpd"] == 100

    def test_2_0_flash_has_no_free_tier(self):
        """429 payload says limit: 0 for gemini-2.0-flash."""
        assert quota.MODEL_RATE_LIMITS["gemini-2.0-flash"]["rpd"] == 0

    def test_unknown_model_falls_back_to_default(self):
        """Lookup for an unknown model returns the default model's caps."""
        unknown = quota.model_limits("gemini-9.9-fictional")
        assert unknown == quota.MODEL_RATE_LIMITS[quota.DEFAULT_MODEL]


class TestSpikeThreshold:
    """Spike alert threshold must be derived from active model RPM, not hardcoded."""

    def test_threshold_is_below_rpm_cap(self):
        """Headroom: alert before Google starts 429ing, not after."""
        threshold = quota._spike_threshold()
        rpm_cap = quota.MODEL_RATE_LIMITS[quota.DEFAULT_MODEL]["rpm"]
        assert threshold <= rpm_cap

    def test_threshold_at_least_one(self):
        """Even a 1-RPM model shouldn't produce a 0 threshold (would alert always)."""
        assert quota._spike_threshold() >= 1


class TestEstimateKeysNeeded:
    """Capacity planning math."""

    def test_small_batch_one_lang(self):
        """5 short videos, 1 lang — should fit comfortably in 1+1 = 2 keys."""
        e = capacity.estimate_keys_needed(videos=5, langs=1, avg_segments=20)
        assert e.keys_for_rpd == 1
        assert e.keys_recommended == 2  # 1 + safety

    def test_large_rpd_dominated_batch(self):
        """5000 calls at 250 RPD/key (flash) → 20 keys for daily, +1 safety = 21."""
        e = capacity.estimate_keys_needed(
            videos=200, langs=5, avg_segments=100, batch_size=20,
            model="gemini-2.5-flash",
        )
        # 200 * 5 langs * ceil(100/20)=5 batches = 5000 calls
        assert e.total_calls == 5000
        assert e.keys_for_rpd == 20
        assert e.keys_recommended >= 21

    def test_safety_margin_added(self):
        e1 = capacity.estimate_keys_needed(videos=10, langs=1, safety_margin=0)
        e2 = capacity.estimate_keys_needed(videos=10, langs=1, safety_margin=3)
        assert e2.keys_recommended - e1.keys_recommended == 3

    def test_max_of_rpm_and_rpd(self):
        """Recommended must satisfy both caps — never less than the larger."""
        e = capacity.estimate_keys_needed(videos=500, langs=10, avg_segments=200)
        assert e.keys_recommended >= e.keys_for_rpm
        assert e.keys_recommended >= e.keys_for_rpd

    def test_model_switch_changes_recommendation(self):
        """Switching flash (RPD=250) → pro (RPD=100) needs more keys."""
        e_flash = capacity.estimate_keys_needed(
            videos=50, langs=3, model="gemini-2.5-flash",
        )
        e_pro = capacity.estimate_keys_needed(
            videos=50, langs=3, model="gemini-2.5-pro",
        )
        assert e_pro.keys_recommended > e_flash.keys_recommended

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            capacity.estimate_keys_needed(videos=0, langs=1)
        with pytest.raises(ValueError):
            capacity.estimate_keys_needed(videos=10, langs=0)

    def test_no_free_tier_model_raises(self):
        """RPD=0 model must fail loud, not divide by zero."""
        with pytest.raises(ValueError, match="no free-tier"):
            capacity.estimate_keys_needed(videos=1, langs=1, model="gemini-2.0-flash")

    def test_explain_contains_key_numbers(self):
        e = capacity.estimate_keys_needed(videos=12, langs=3)
        text = e.explain()
        assert "12 videos" in text
        assert "3 langs" in text
        assert str(e.keys_recommended) in text
        assert "20 RPM" in text  # default model's RPM cap


class TestDailyLimitFor:
    def test_default_model_matches_legacy_constant(self):
        """DAILY_LIMIT_FREE is a live alias — always equals daily_limit_for(),
        even when GEMINI_MODEL overrides the configured model."""
        assert quota.daily_limit_for() == quota.DAILY_LIMIT_FREE

    def test_explicit_model_overrides(self):
        assert quota.daily_limit_for("gemini-2.5-pro") == 100
        assert quota.daily_limit_for("gemini-2.5-flash-lite") == 20
