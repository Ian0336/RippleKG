"""Runtime configuration, read from environment (see .env.example)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    arango_url: str = "http://localhost:8529"
    arango_db: str = "ripplekg"
    arango_user: str = "root"
    arango_password: str = "ripplekg-dev"


settings = Settings()
