"""Application settings, loaded from environment variables and .env.

Construct `Settings()` at app entry points (API, UI, CLI). Pass specific
fields down to the modules that need them — don't import this singleton
from inside business-logic modules, that's how env coupling leaks everywhere.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Postgres (pgvector) ---
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/amazon_scraper"
    )

    # --- OpenAI ---
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"

    # --- Proxy (fallback: any HTTP proxy) ---
    proxy_username: str = ""
    proxy_password: str = ""
    proxy_server: str = ""

    # --- Bright Data Web Unlocker (preferred) ---
    brightdata_token: str = ""
    brightdata_zone: str = ""

    # --- App ---
    log_level: str = "INFO"

    @property
    def proxy_url(self) -> str | None:
        """Assemble the proxy URL, or None if credentials are not configured."""
        if not (self.proxy_username and self.proxy_password and self.proxy_server):
            return None
        return f"http://{self.proxy_username}:{self.proxy_password}@{self.proxy_server}"

    @property
    def has_brightdata(self) -> bool:
        return bool(self.brightdata_token and self.brightdata_zone)
