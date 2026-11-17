"""Classify model failures without exposing provider responses or credentials."""

from ..storage.postgres import DatabaseOperationError


class BeforeDispatchUnavailable(DatabaseOperationError):
    """The database failed while preparing a model call that was never sent."""

    code = "database_before_model_dispatch"

    def __init__(self, error: DatabaseOperationError):
        super().__init__(error.role, error.state, error.detail, retryable=error.retryable)

    def as_dict(self):
        return {"code": self.code, "role": self.role, "sqlstate": self.state, "retryable": self.retryable}


class ModelFailure(ValueError):
    """A model operation failed before it could provide a usable candidate."""

    def __init__(self, code, message, *, retryable=False, retry_after_seconds=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds

    def as_dict(self):
        failure = {"code": self.code, "retryable": self.retryable}
        if self.retry_after_seconds is not None:
            failure["retry_after_seconds"] = self.retry_after_seconds
        return failure


class ModelOutputError(ModelFailure):
    """A recorded model response failed its task's candidate contract."""

    def __init__(self, message, run, *, code="invalid_output"):
        super().__init__(code, message)
        self.metadata = run.metadata
        self.transcript = run.transcript
