from __future__ import annotations


class AppError(Exception):
    """Known user-facing error."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, exit_code: int = 1,
                 operation: str | None = None, phase: str | None = None,
                 attempts: int | None = None, outcome: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.exit_code = exit_code
        self.details = {key: value for key, value in {
            "operation": operation, "phase": phase, "attempts": attempts, "outcome": outcome,
        }.items() if value is not None}


class ConfigError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("config_invalid", message)

