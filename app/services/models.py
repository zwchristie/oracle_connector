"""Shared dataclasses for the metadata service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class TableProfile:
    """Summary of usage information for a single table."""

    schema: str
    table_name: str
    num_rows: Optional[int] = None
    is_empty: Optional[bool] = None
    last_analyzed: Optional[datetime] = None
    recent_activity_at: Optional[datetime] = None
    has_recent_activity: bool = False
    activity_source: Optional[str] = None
    activity_column: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.table_name}"


__all__ = ["TableProfile"]
