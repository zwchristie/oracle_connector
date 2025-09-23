"""Application configuration driven by environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass
class Settings:
    """Runtime settings for the metadata service."""

    oracle_dsn: str
    oracle_config_dir: Optional[str] = None
    oracle_lib_dir: Optional[str] = None
    oracle_use_thick: bool = True
    oracle_use_pool: bool = True
    oracle_pool_min: int = 1
    oracle_pool_max: int = 5
    oracle_pool_increment: int = 1
    oracle_fetch_arraysize: int = 100
    metadata_recent_months: int = 6

    def __post_init__(self) -> None:
        if not self.oracle_dsn:
            raise ValueError("oracle_dsn must not be empty")
        if self.oracle_pool_max < self.oracle_pool_min:
            raise ValueError("oracle_pool_max must be greater than or equal to oracle_pool_min")
        if self.oracle_pool_increment <= 0:
            raise ValueError("oracle_pool_increment must be positive")
        if self.metadata_recent_months < 0:
            raise ValueError("metadata_recent_months must be non-negative")

    @classmethod
    def from_env(cls) -> "Settings":
        dsn = os.getenv("ORACLE_DSN")
        if not dsn:
            raise ValueError("ORACLE_DSN environment variable is required")
        return cls(
            oracle_dsn=dsn,
            oracle_config_dir=os.getenv("ORACLE_CONFIG_DIR"),
            oracle_lib_dir=os.getenv("ORACLE_LIB_DIR"),
            oracle_use_thick=_env_bool("ORACLE_USE_THICK", True),
            oracle_use_pool=_env_bool("ORACLE_USE_POOL", True),
            oracle_pool_min=_env_int("ORACLE_POOL_MIN", 1),
            oracle_pool_max=_env_int("ORACLE_POOL_MAX", 5),
            oracle_pool_increment=_env_int("ORACLE_POOL_INCREMENT", 1),
            oracle_fetch_arraysize=_env_int("ORACLE_FETCH_ARRAYSIZE", 100),
            metadata_recent_months=_env_int("METADATA_RECENT_MONTHS", 6),
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings.from_env()


__all__ = ["Settings", "get_settings"]
