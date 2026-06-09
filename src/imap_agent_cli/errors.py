from __future__ import annotations


class AppError(Exception):
    """Known user-facing error."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.exit_code = exit_code


class ConfigError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("config_invalid", message)

