import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic_settings import BaseSettings, SettingsConfigDict


# envs/: common.env (every service) and sweeper.env (the master key, for the sweepers only).
# Run locally, every process reads both; in Docker only the sweepers get sweeper.env (docker-compose.yml).
# The environment wins over the files.
ENVS = Path(__file__).resolve().parent.parent / "envs"
ENV_FILES = tuple(str(ENVS / name) for name in ("common.env", "sweeper.env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HDPAY_", env_file=ENV_FILES, extra="ignore")

    # MongoDB, shared by the API, the watchers and the sweepers
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "hdpay"

    poll_interval_seconds: int = 5

    # Defaults for a new project; each project can change its own in the admin
    deposit_ttl_minutes: int = 30
    # after an underpayment, how long the address waits for the rest before support takes over
    remainder_window_minutes: int = 1440
    amount_tolerance_bps: int = 50

    # Admin sign-in. JWT_SECRET signs the short-lived access tokens: a long random string, kept secret.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    # wrong passwords allowed per email and IP before sign-in pauses for the window
    max_login_attempts: int = 5
    login_window_minutes: int = 15

    # Project events kept for a project to catch up on after a disconnect
    event_retention_days: int = 30

    # API server and the browser apps allowed to call it (hdpay web and admin)
    port: int = 8090
    # development only: restart the API whenever a file in the hdpay package changes
    reload: bool = False
    cors_origins: list[str] = ["http://localhost:4500"]
    # proxies trusted to say who the caller is (X-Forwarded-For), comma separated: the admin's nginx in
    # docker compose. Anyone else's header is ignored, so a caller cannot pass for an allowed IP.
    forwarded_allow_ips: str = "127.0.0.1"

    # Root key (m, depth 0) of the HD wallet: sweeper host and `python -m api.keys` only, never the API.
    # Every project's addresses and fee funders are derived from it under m/<project number>'/<coin>'.
    master_xprv: str = ""

    # chains and tokens (YAML); empty means chains.yaml next to the hdpay package (else chains.example.yaml).
    # Each chain's RPC is read from HDPAY_RPC_URL_<KEY>, falling back to the file's public default.
    chains_file: str = ""


@lru_cache
def _dotenv() -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    for path in ENV_FILES:
        values.update(dotenv_values(path))
    return values


def env_value(name: str) -> str:
    """A setting whose name is only known at run time (e.g. HDPAY_RPC_URL_<chain>): the environment
    first, then the envs/ files, the same order the fixed settings above use."""
    return os.environ.get(name) or _dotenv().get(name) or ""


settings = Settings()
