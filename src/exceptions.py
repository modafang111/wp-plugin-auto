"""Fail-safe exceptions. Unexpected states stop the pipeline."""

from __future__ import annotations


class PipelineError(Exception):
    """Base error for a processing stage."""

    def __init__(self, stage: str, message: str, *, needs_review: bool = False) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.needs_review = needs_review


class SkipPlugin(PipelineError):
    """Plugin is out of scope or already handled. Not a crash."""


class NeedsHumanReview(PipelineError):
    """Stop automation and notify a human. Never bypass auth/CAPTCHA."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(stage, message, needs_review=True)
