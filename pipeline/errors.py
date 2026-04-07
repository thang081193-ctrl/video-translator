"""Custom exception hierarchy for the pipeline.

Three categories:
- TransientError: retry-able (rate limit, timeout, network)
- FatalError: abort (invalid input, bad config, missing dependency)
- DegradedError: skip feature, continue (OCR unavailable, single TTS fail)
"""


class PipelineError(Exception):
    """Base class for all pipeline errors."""
    pass


class TransientError(PipelineError):
    """Retry-able error: rate limit, timeout, temporary network issue.

    The operation can be retried after a delay.
    """
    def __init__(self, message: str, retry_after: int = 5):
        super().__init__(message)
        self.retry_after = retry_after


class FatalError(PipelineError):
    """Non-recoverable error: invalid input, missing config, bad file.

    The job should be aborted with a clear error message.
    """
    pass


class DegradedError(PipelineError):
    """Feature unavailable but pipeline can continue.

    Examples: OCR module not installed, single TTS segment failed,
    Demucs GPU OOM (can fallback to no-separation).
    """
    def __init__(self, message: str, feature: str = ""):
        super().__init__(message)
        self.feature = feature
