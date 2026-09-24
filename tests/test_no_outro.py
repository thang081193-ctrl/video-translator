"""Tests for `append_outro=False` — ship the body only, with no brand card.

Needed when the app's own outro does not exist yet and will be concatenated
later: the default path would otherwise append the generated Pillow card, and
every audio path would keep playing under a card that isn't there.

All fixtures are synthetic (lavfi) — no network, small encodes.
"""
from __future__ import annotations

import pytest

import pipeline.brand_pass as bp
from pipeline.brand_pass import brand_pass_video
from tests.test_bgm_under_outsize import _outro, _probe, _rms_db, _src, _track


class TestNoOutro:
    def test_duration_is_the_body_alone(self, tmp_path):
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=1.5),
                         output_path=str(out), transcript="", watermark_text="",
                         append_outro=False, random_seed=3)
        v, a, dur = _probe(str(out))
        assert (v["width"], v["height"]) == (1080, 1920)
        assert a is not None
        assert dur == pytest.approx(1.5, abs=0.2)

    def test_default_still_appends_the_generated_card(self, tmp_path):
        """The switch must be opt-in: same call without it grows by the outro."""
        src = _src(tmp_path / "s.mp4", dur=1.5)
        with_card, bare = tmp_path / "with.mp4", tmp_path / "bare.mp4"
        brand_pass_video(input_path=src, output_path=str(with_card), transcript="",
                         watermark_text="", random_seed=3)
        brand_pass_video(input_path=src, output_path=str(bare), transcript="",
                         watermark_text="", append_outro=False, random_seed=3)
        assert _probe(str(with_card))[2] > _probe(str(bare))[2] + 1.5

    def test_source_audio_reaches_the_last_second(self, tmp_path):
        """Passthrough audio must not be padded with silence or cut short."""
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=2.0),
                         output_path=str(out), transcript="", watermark_text="",
                         append_outro=False, random_seed=3)
        assert _rms_db(str(out), 1.5, 0.4) > -40.0

    def test_replacement_track_stops_with_the_body(self, tmp_path):
        """A looping replacement bed is rendered to working_dur + outro_dur; with no
        outro that must be the body length, not body + a phantom card."""
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=1.5),
                         output_path=str(out), transcript="", watermark_text="",
                         bgm_replace_path=_track(tmp_path / "b.mp3"),
                         append_outro=False, random_seed=3)
        assert _probe(str(out))[2] == pytest.approx(1.5, abs=0.2)

    def test_trim_to_and_no_outro_compose(self, tmp_path):
        out = tmp_path / "o.mp4"
        brand_pass_video(input_path=_src(tmp_path / "s.mp4", dur=2.0),
                         output_path=str(out), transcript="", watermark_text="",
                         trim_to=1.2, append_outro=False, random_seed=3)
        assert _probe(str(out))[2] == pytest.approx(1.2, abs=0.2)

    def test_no_card_is_generated(self, tmp_path, monkeypatch):
        called = []
        monkeypatch.setattr(bp, "_generate_outro_frame",
                            lambda *a, **k: called.append(1))
        brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                         output_path=str(tmp_path / "o.mp4"), transcript="",
                         watermark_text="", append_outro=False, random_seed=3)
        assert called == []

    def test_rejects_a_supplied_outro(self, tmp_path):
        with pytest.raises(ValueError, match="contradicts outro_video"):
            brand_pass_video(input_path=_src(tmp_path / "s.mp4"),
                             output_path=str(tmp_path / "o.mp4"), transcript="",
                             watermark_text="", append_outro=False,
                             outro_video=_outro(tmp_path / "otr.mp4"))
