"""Settings, from example/api/.env (see .env.example) or the environment."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    hdpay_url: str = "http://localhost:8090"
    hdpay_secret_key: str = ""

    host: str = "127.0.0.1"
    port: int = 8400
    reload: bool = False   # development: restart when source/ changes
    web_url: str = "http://localhost:4400"
    cors_origins: list[str] = ["http://localhost:4400"]

    database: Path = ROOT / "example.db"
    shop_name: str = "Example Shop"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "Example Shop <shop@example.com>"
    smtp_starttls: bool = True
