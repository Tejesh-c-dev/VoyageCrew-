"""Environment-backed application settings."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import certifi
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from tripmate.core.exceptions import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_url: str | None
    groq_api_key: str | None
    tavily_api_key: str | None
    aviation_stack_api_key: str | None
    openweather_api_key: str | None
    groq_model: str = DEFAULT_GROQ_MODEL
    clerk_issuer: str | None = None
    clerk_audience: str | None = None
    clerk_publishable_key: str | None = None
    clerk_frontend_api_url: str | None = None
    clerk_jwt_template: str = "VoyageCrew"

    def require_database_url(self) -> str:
        if not self.database_url:
            raise ConfigurationError(
                "DATABASE_URL is missing. Add a PostgreSQL connection URL "
                "to the project .env file."
            )

        connection_options = conninfo_to_dict(self.database_url)
        sslmode = connection_options.get("sslmode") or "require"
        parsed_url = urlsplit(self.database_url)
        if parsed_url.scheme.lower() in {"postgres", "postgresql"}:
            query_options = parse_qsl(
                parsed_url.query,
                keep_blank_values=True,
            )
            sslmode_options = [
                value
                for key, value in query_options
                if key.lower() == "sslmode"
            ]
            if len(sslmode_options) == 1 and sslmode_options[0] == sslmode:
                return self.database_url

            query_options = [
                (key, value)
                for key, value in query_options
                if key.lower() != "sslmode"
            ]
            query_options.append(("sslmode", sslmode))
            return urlunsplit(
                parsed_url._replace(query=urlencode(query_options))
            )

        return make_conninfo(self.database_url, sslmode=sslmode)

    def require(self, name: str, value: str | None) -> str:
        if not value:
            raise ConfigurationError(
                f"{name} is missing. Add {name}=your_key to the project .env file."
            )
        return value

    def require_groq_model(self) -> str:
        model = self.groq_model.strip()
        if not model:
            raise ConfigurationError(
                "GROQ_MODEL is missing. Set it to a model available to the "
                "configured Groq account."
            )
        return model


def load_settings(project_root: Path = PROJECT_ROOT) -> Settings:
    load_dotenv(project_root / ".env")
    return Settings(
        project_root=project_root,
        database_url=os.getenv("DATABASE_URL"),
        groq_api_key=os.getenv("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL,
        tavily_api_key=os.getenv("TAVILY_API_KEY"),
        aviation_stack_api_key=(
            os.getenv("AVIATION_STACK_API_KEY")
            or os.getenv("AVIATIONSTACK_API_KEY")
        ),
        openweather_api_key=os.getenv("OPENWEATHER_API_KEY"),
        clerk_issuer=os.getenv("CLERK_ISSUER"),
        clerk_audience=os.getenv("CLERK_AUDIENCE"),
        clerk_publishable_key=os.getenv("CLERK_PUBLISHABLE_KEY"),
        clerk_frontend_api_url=os.getenv("CLERK_FRONTEND_API_URL"),
        clerk_jwt_template=os.getenv("CLERK_JWT_TEMPLATE", "VoyageCrew"),
    )


def configure_ssl_certificates() -> None:
    certificate_path = certifi.where()
    os.environ["SSL_CERT_FILE"] = certificate_path
    os.environ["REQUESTS_CA_BUNDLE"] = certificate_path
