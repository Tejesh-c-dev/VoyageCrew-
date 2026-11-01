"""Application-specific exceptions with safe client-facing messages."""


class ApplicationError(Exception):
    """Base exception for expected application failures."""

    status_code = 500
    public_message = "The request could not be completed."


class BadRequestError(ApplicationError):
    status_code = 400
    public_message = "The request is invalid."

    def __init__(self, public_message: str) -> None:
        self.public_message = public_message
        super().__init__(public_message)


class ServiceUnavailableError(ApplicationError):
    status_code = 503
    public_message = "A required service is temporarily unavailable."


class DatabaseUnavailableError(ServiceUnavailableError):
    public_message = "The database is temporarily unavailable."


class ExternalServiceError(ApplicationError):
    status_code = 502
    public_message = "An external service is temporarily unavailable."


class ConfigurationError(ServiceUnavailableError):
    """Raised when required application configuration is missing."""
