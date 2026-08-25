from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/ai_note_taker"
    cors_origins: str = "http://localhost:5173"
    deepgram_api_key: str = ""

    # "provider:model" — passed straight to langchain's init_chat_model, so
    # swapping providers is just changing this string + setting the matching
    # API key env var (OPENAI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY, ...).
    # Used for notes writing, chat replies, and the profile compile.
    llm_model: str = "openai:gpt-5.6-luna"
    # Separate (usually cheaper) model for the yes/no "should we touch the
    # notes?" router — keep this off the main model so routing stays cheap
    # and doesn't burn the chat/notes budget.
    routing_llm_model: str = "openai:gpt-5.4-nano"
    openai_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
