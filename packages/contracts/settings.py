from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RF_", env_file=".env", extra="ignore")

    assemblyai_api_key: str | None = None
    policy_pack_path: Path = Path("config/policy/default.yaml")
    dry_run: bool = True
    retain_transcripts: bool = False
    asr_provider: Literal["assemblyai", "null"] = "null"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"  # structured logs by default (T-7.5)
    session_secret: str | None = None  # HMAC key for auth tokens (T-7.1b); unset => ephemeral
    # postgres DSN for the identity store (T-7.2a); unset => in-memory
    database_url: str | None = None
    # ingress rate limiting (T-7.4), per client IP per minute
    ratelimit_enabled: bool = True
    ratelimit_per_min: int = 120  # everything except the auth surface
    ratelimit_auth_per_min: int = 20  # /auth/* and /orgs/* — brute-force budget
    # Fernet key(s) for at-rest column encryption (T-7.4); comma-separated,
    # first is used to encrypt, all are tried to decrypt (rotation). Unset =>
    # sensitive columns are stored in clear (dev only).
    data_encryption_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
