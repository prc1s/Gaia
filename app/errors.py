class ToolError(Exception):
    retryable: bool = False


class TransientToolError(ToolError):
    retryable = True


class AddressTakenError(TransientToolError):
    """Concurrent address allocation; retry must re-scan."""


class AccountNotVisibleError(TransientToolError):
    """Directory propagation delay. Step 4 parks the run instead of retrying."""


class PermanentToolError(ToolError):
    retryable = False


class GuardrailViolation(Exception):
    retryable = False


class RetriesExhausted(Exception):
    """The step kept failing retryably until its attempts ran out."""

    retryable = False

    def __init__(self, error_class: str, attempts: int) -> None:
        super().__init__(f"retries_exhausted: {error_class}")
        self.error_class = error_class
        self.attempts = attempts


class PauseSignal(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
