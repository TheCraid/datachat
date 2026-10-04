"""Application settings, read from environment variables or a .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM provider: any OpenAI-compatible chat completions API. Default: Groq.
    groq_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    sql_model: str = "openai/gpt-oss-120b"        # writes and fixes SQL
    fallback_model: str = "openai/gpt-oss-20b"    # used when the main model errors or is rate-limited
    insight_model: str = "openai/gpt-oss-20b"     # writes the short insight and SQL explanations
    llm_timeout_s: float = 60.0
    # When the provider says "rate limit reached, try again in N s", wait and retry.
    rate_limit_retries: int = 1       # the app waits once; the evaluation raises this
    rate_limit_max_wait_s: float = 10.0

    # Price per 1M tokens (USD), only used to report the cost of each question.
    price_in_per_m: float = 0.15
    price_out_per_m: float = 0.60
    small_price_in_per_m: float = 0.075
    small_price_out_per_m: float = 0.30

    # Agent
    max_fix_attempts: int = 2      # extra tries after a failed or rejected query
    history_turns: int = 3         # earlier questions given to the model for follow-ups

    # Query execution
    max_result_rows: int = 1000
    query_timeout_s: float = 10.0

    # Uploads
    max_upload_mb: int = 10
    max_upload_rows: int = 200_000
    max_sessions: int = 25         # uploaded datasets kept in memory at once
    session_ttl_min: int = 60

    # Storage for history and pinned charts. SQLite by default; set a PostgreSQL URL in production.
    database_url: str = "sqlite:///./datachat.db"

    # Limits
    max_question_chars: int = 500
    rate_limit_per_min: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
