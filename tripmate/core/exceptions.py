"""Application-specific exceptions with safe client-facing messages."""


class ApplicationError(Exception):
    """Base exception for expected application failures."""

    status_code = 500
    error_code: str | None = None
    public_message = "The request could not be completed."


class BadRequestError(ApplicationError):
    status_code = 400
    public_message = "The request is invalid."

    def __init__(self, public_message: str) -> None:
        self.public_message = public_message
        super().__init__(public_message)


class ItineraryPromptTooLargeError(ApplicationError):
    """The reconstructed itinerary prompt exceeds the safe provider budget."""

    status_code = 413
    error_code = "ITINERARY_PROMPT_TOO_LARGE"
    public_message = (
        "The itinerary request is too large to process. Please simplify your "
        "request and try again."
    )


class ItineraryGenerationError(ApplicationError):
    """The provider could not produce a complete, reviewable itinerary."""

    status_code = 502
    public_message = (
        "The itinerary could not be completed. Please try again with a shorter "
        "request. No draft was sent for approval."
    )


class AuthenticationError(ApplicationError):
    status_code = 401
    public_message = "Authentication is required."

    def __init__(self, category: str = "invalid_token") -> None:
        self.error_code = f"AUTH_{category.upper()}"
        super().__init__(self.public_message)


class ThreadNotFoundError(ApplicationError):
    status_code = 404
    public_message = "The requested resource was not found."


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
