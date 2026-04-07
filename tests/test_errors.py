"""Tests for pipeline/errors.py and pipeline/utils.py."""

import os
import pytest
from pipeline.errors import PipelineError, TransientError, FatalError, DegradedError
from pipeline.utils import temp_dir, check_disk_space, retry


class TestExceptionHierarchy:
    """Test custom exception classes."""

    def test_transient_is_pipeline_error(self):
        assert issubclass(TransientError, PipelineError)

    def test_fatal_is_pipeline_error(self):
        assert issubclass(FatalError, PipelineError)

    def test_degraded_is_pipeline_error(self):
        assert issubclass(DegradedError, PipelineError)

    def test_transient_retry_after(self):
        e = TransientError("rate limit", retry_after=10)
        assert e.retry_after == 10
        assert str(e) == "rate limit"

    def test_degraded_feature(self):
        e = DegradedError("OCR not installed", feature="ocr")
        assert e.feature == "ocr"

    def test_catch_all_pipeline_errors(self):
        errors = [TransientError("a"), FatalError("b"), DegradedError("c")]
        for e in errors:
            with pytest.raises(PipelineError):
                raise e


class TestTempDir:
    """Test temp_dir context manager."""

    def test_creates_and_cleans(self, tmp_path):
        with temp_dir("test", base_dir=str(tmp_path)) as td:
            assert os.path.isdir(td)
            # Write something in it
            with open(os.path.join(td, "test.txt"), "w") as f:
                f.write("hello")
        # Should be cleaned up
        assert not os.path.isdir(td)

    def test_cleans_on_exception(self, tmp_path):
        td_path = None
        try:
            with temp_dir("test", base_dir=str(tmp_path)) as td:
                td_path = td
                assert os.path.isdir(td)
                raise ValueError("oops")
        except ValueError:
            pass
        assert td_path is not None
        assert not os.path.isdir(td_path)


class TestDiskSpaceCheck:
    """Test disk space checking."""

    def test_has_space(self, tmp_path):
        # Should have at least 1 byte free
        assert check_disk_space(str(tmp_path), min_bytes=1) is True

    def test_unreasonable_requirement(self, tmp_path):
        # 1 PB should fail
        assert check_disk_space(str(tmp_path), min_bytes=10**15) is False


class TestRetryDecorator:
    """Test retry decorator with exponential backoff."""

    def test_succeeds_first_try(self):
        call_count = 0
        @retry(max_attempts=3, base_delay=0.01)
        def success():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert success() == "ok"
        assert call_count == 1

    def test_retries_on_transient(self):
        call_count = 0
        @retry(max_attempts=3, base_delay=0.01)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TransientError("temp fail")
            return "ok"

        assert flaky() == "ok"
        assert call_count == 3

    def test_no_retry_on_fatal(self):
        call_count = 0
        @retry(max_attempts=3, base_delay=0.01)
        def fatal():
            nonlocal call_count
            call_count += 1
            raise FatalError("permanent")

        with pytest.raises(FatalError):
            fatal()
        assert call_count == 1  # No retries

    def test_exhausted_retries(self):
        @retry(max_attempts=2, base_delay=0.01)
        def always_fail():
            raise TransientError("still failing")

        with pytest.raises(TransientError):
            always_fail()
