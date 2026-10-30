from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    alisagpt_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_owner_id: int = 0
    alice_skill_id: str = ""
    alice_webhook_token: str = ""
    database_path: str = "data/alisagpt.sqlite3"
    default_model: str = "gpt-6-luna"
    allowed_models: tuple[str, ...] = ("gpt-6-luna", "gpt-4.1-mini", "gpt-4.1", "gpt-4.1-nano")
    daily_limit: int = 100
    web_search_daily_limit: int = 20
    voice_daily_limit: int = 10

