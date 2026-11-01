"""Shared Groq model construction."""

import httpx
from langchain_groq import ChatGroq
from pydantic import SecretStr

from tripmate.core.config import (
    Settings,
    configure_ssl_certificates,
    load_settings,
)

GROQ_CONNECT_TIMEOUT_SECONDS = 5.0
GROQ_RESPONSE_TIMEOUT_SECONDS = 30.0
GROQ_MAX_RETRIES = 2


def create_llm(settings: Settings | None = None) -> ChatGroq:
    active_settings = settings or load_settings()
    api_key = active_settings.require(
        "GROQ_API_KEY",
        active_settings.groq_api_key,
    )
    model = active_settings.require_groq_model()
    configure_ssl_certificates()
    return ChatGroq(
        model=model,
        api_key=SecretStr(api_key),
        timeout=httpx.Timeout(
            GROQ_RESPONSE_TIMEOUT_SECONDS,
            connect=GROQ_CONNECT_TIMEOUT_SECONDS,
        ),
        max_retries=GROQ_MAX_RETRIES,
    )
