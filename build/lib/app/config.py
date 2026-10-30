from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    master_key: SecretStr
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    allowlist: str = ""  # пусто — бот открыт всем; иначе Telegram ID через запятую (закрытая бета)
    poll_minutes: int = 30
    llm_daily_limit: int = 100
    rate_limit_per_min: int = 20
    hh_token: SecretStr = SecretStr("")
    internal_token: SecretStr = SecretStr("")
    user_agent: str = "job-hunter-bot/0.1 (+https://github.com/)"
    api_host: str = "127.0.0.1"
    api_port: int = 8080

    @property
    def allowlist_ids(self) -> list[int]:
        return [int(x) for x in self.allowlist.replace(" ", "").split(",") if x]
