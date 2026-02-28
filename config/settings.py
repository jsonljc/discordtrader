from __future__ import annotations

import json
import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROFILES_DIR = Path(__file__).parent / "profiles"


class Settings(BaseSettings):
    """
    Typed configuration loaded from environment variables / .env file.
    Secrets (tokens, account IDs) live here only — never in TOML profiles.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Discord ───────────────────────────────────────────────
    discord_bot_token: str = Field(default="", repr=False)
    discord_allowed_guild_ids: list[str] = Field(default_factory=list)
    discord_allowed_channel_ids: list[str] = Field(default_factory=list)
    discord_allowed_role_ids: list[str] = Field(default_factory=list)

    # ── IBKR ──────────────────────────────────────────────────
    # TWS paper=7497, TWS live=7496, Gateway paper=4002, Gateway live=4001
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1
    ibkr_account_id: str = Field(default="", repr=False)

    # ── Risk ──────────────────────────────────────────────────
    sleeve_value: Decimal = Decimal("100000")
    min_position_pct: Decimal = Decimal("0.03")
    max_position_pct: Decimal = Decimal("0.07")
    max_open_positions: int = 10
    max_daily_drawdown_pct: Decimal = Decimal("0.05")

    # ── Mode ──────────────────────────────────────────────────
    paper_mode: bool = True
    profile: str = "discord_equities"

    # ── LLM (OpenAI) ──────────────────────────────────────────
    # Used by the Interpreter Agent for narrative signal parsing.
    openai_api_key: str = Field(default="", repr=False)   # never logged
    llm_model: str = "gpt-4o-mini"
    llm_enabled: bool = True
    llm_min_clarity: int = 50          # drop signals scored below this (0-100)
    llm_timeout_seconds: float = 8.0

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"
    log_file: str | None = None

    @field_validator(
        "discord_allowed_guild_ids",
        "discord_allowed_channel_ids",
        "discord_allowed_role_ids",
        mode="before",
    )
    @classmethod
    def _parse_id_list(cls, v: Any) -> list[str]:
        """Accept comma-separated strings, JSON arrays, or plain lists."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return [str(item) for item in json.loads(v)]
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(item) for item in v]
        return []


def load_settings(profile: str | None = None) -> Settings:
    """
    Load Settings from .env, then overlay non-secret values from the TOML
    profile (config/profiles/<profile>.toml).  Profile overrides risk %,
    ports, and strategy params — never secrets.
    """
    base = Settings()
    effective_profile = profile or base.profile
    profile_path = _PROFILES_DIR / f"{effective_profile}.toml"

    if not profile_path.exists():
        return base

    with open(profile_path, "rb") as f:
        raw = tomllib.load(f)

    # Flatten all TOML sections except the [profile] metadata block
    overrides: dict[str, Any] = {}
    for section_name, section_val in raw.items():
        if section_name == "profile":
            continue
        if isinstance(section_val, dict):
            overrides.update(section_val)

    valid_keys = set(Settings.model_fields.keys())
    filtered = {k: v for k, v in overrides.items() if k in valid_keys}

    if not filtered:
        return base

    # Merge: base (env values) wins for secrets; profile wins for strategy params
    merged: dict[str, Any] = {**base.model_dump(), **filtered}
    return Settings.model_validate(merged)
