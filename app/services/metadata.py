"""Service layer that inspects Oracle schema metadata."""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, List, Optional, Sequence

from app.config import Settings
from app.utils import qualify_identifier, quote_identifier
from .models import TableProfile

logger = logging.getLogger(__name__)

# Column names we inspect to infer recent activity when statistics are unavailable.
_ACTIVITY_COLUMN_PRIORITY: Sequence[str] = (
    "UPDATED_AT",
    "UPDATE_DATE",
    "MODIFIED_AT",
    "MODIFIED_DATE",
    "LAST_UPDATE_DATE",
    "LAST_UPDATED",
    "CREATED_AT",
    "CREATED_DATE",
    "CREATE_DATE",
    "CREATEDON",
    "INSERTED_AT",
)


@dataclass
class ColumnActivity:
    timestamp: datetime
    column: str


class OracleMetadataService:
    """Encapsulates metadata access patterns and filtering logic."""

    def __init__(self, client, settings: Settings):
        self._client = client
        self._settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_filtered_tables(
        self,
        schema: str,
        table_names: Optional[Sequence[str]] = None,
        months: Optional[int] = None,
        include_empty: bool = False,
    ) -> List[TableProfile]:
        """Return tables with recent activity within the given schema.

        Args:
            schema: Oracle schema/owner name.
            table_names: Optional subset of tables to evaluate; names are case insensitive.
            months: Number of months to consider "recent" activity (defaults to configuration).
            include_empty: When ``True`` empty tables are retained in the response.
        """

        normalized_schema = schema.upper()
        threshold = self._months_ago(months)
        available_tables = self._list_table_names(normalized_schema)

        if table_names:
            requested = {name.upper() for name in table_names}
            target_tables = [name for name in available_tables if name in requested]
            missing = requested.difference(available_tables)
            if missing:
                logger.warning("Requested tables not found in %s: %s", normalized_schema, ", ".join(sorted(missing)))
        else:
            target_tables = available_tables

        profiles: List[TableProfile] = []
        for table in target_tables:
            profile = self._build_table_profile(normalized_schema, table, threshold)
            if profile is None:
                continue
            if not include_empty and profile.is_empty:
                continue
            if threshold and not profile.has_recent_activity:
                continue
            profiles.append(profile)

        profiles.sort(key=lambda p: p.table_name)
        return profiles

    def generate_mdl(
        self,
        catalog: str,
        schema: str,
        tables: Optional[Sequence[str]] = None,
        apply_usage_filter: bool = True,
        months: Optional[int] = None,
        include_empty: bool = False,
    ) -> Dict[str, object]:
        """Build a WrenMDL manifest for the requested tables."""

        normalized_schema = schema.upper()
        months = months or self._settings.metadata_recent_months
        table_profiles: Dict[str, TableProfile] = {}

        if tables:
            requested_tables = [name.upper() for name in tables]
            available_tables = set(self._list_table_names(normalized_schema))
            missing = [name for name in requested_tables if name not in available_tables]
            if missing:
                raise ValueError(
                    f"Tables not found in schema {normalized_schema}: {', '.join(sorted(missing))}"
                )
        else:
            requested_tables = None

        if apply_usage_filter:
            profiles = self.get_filtered_tables(
                normalized_schema,
                table_names=requested_tables,
                months=months,
                include_empty=include_empty,
            )
            table_names = [profile.table_name for profile in profiles]
            table_profiles = {profile.table_name: profile for profile in profiles}
        else:
            if requested_tables is not None:
                table_names = requested_tables
            else:
                table_names = self._list_table_names(normalized_schema)
            threshold = self._months_ago(months)
            for table in table_names:
                profile = self._build_table_profile(normalized_schema, table, threshold)
                if profile:
                    table_profiles[table] = profile

        mdl = {"catalog": catalog, "schema": normalized_schema, "models": []}
        models = []
        for table_name in table_names:
            model_entry = self._build_model_entry(normalized_schema, table_name, table_profiles)
            if model_entry:
                models.append(model_entry)
        mdl["models"] = models
        return mdl

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _months_ago(self, months: Optional[int]) -> Optional[datetime]:
        if months is None:
            months = self._settings.metadata_recent_months
        if months <= 0:
            return None
        now = datetime.utcnow()
        year = now.year
        month = now.month - months
        while month <= 0:
            month += 12
            year -= 1
        day = min(now.day, calendar.monthrange(year, month)[1])
        return now.replace(year=year, month=month, day=day)

    def _list_table_names(self, schema: str) -> List[str]:
        sql = (
            "SELECT table_name FROM all_tables WHERE owner = :schema AND nested = 'NO'"
            " ORDER BY table_name"
        )
        rows = self._client.fetchall(sql, {"schema": schema})
        return [row["table_name"] for row in rows]

    def _build_table_profile(
        self, schema: str, table: str, threshold: Optional[datetime]
    ) -> Optional[TableProfile]:
        params = {"schema": schema, "table": table}
        stats = self._client.fetchone(
            """
            SELECT num_rows, last_analyzed
            FROM all_tab_statistics
            WHERE owner = :schema AND table_name = :table AND partition_name IS NULL
            """,
            params,
        )
        num_rows = self._coerce_int(stats.get("num_rows")) if stats else None
        last_analyzed = stats.get("last_analyzed") if stats else None
        is_empty = None
        if num_rows is not None:
            is_empty = num_rows == 0

        modification = None
        try:
            modification = self._client.fetchone(
                """
                SELECT MAX(timestamp) AS modified_at
                FROM all_tab_modifications
                WHERE table_owner = :schema AND table_name = :table
                """,
                params,
            )
        except Exception as exc:  # pragma: no cover - depends on Oracle permissions
            logger.debug(
                "Unable to read ALL_TAB_MODIFICATIONS for %s.%s: %s", schema, table, exc
            )

        recent_activity_at: Optional[datetime] = None
        activity_source: Optional[str] = None
        activity_column: Optional[str] = None

        if modification and modification.get("modified_at"):
            recent_activity_at = modification["modified_at"]
            activity_source = "ALL_TAB_MODIFICATIONS"

        if threshold and (recent_activity_at is None or recent_activity_at < threshold):
            column_activity = self._recent_timestamp_from_preferred_column(schema, table)
            if column_activity:
                recent_activity_at = column_activity.timestamp
                activity_source = "COLUMN"
                activity_column = column_activity.column

        has_recent_activity = bool(
            recent_activity_at and (threshold is None or recent_activity_at >= threshold)
        )

        if is_empty is None:
            has_rows = self._has_any_rows(schema, table)
            if has_rows is not None:
                is_empty = not has_rows

        profile = TableProfile(
            schema=schema,
            table_name=table,
            num_rows=num_rows,
            is_empty=is_empty,
            last_analyzed=last_analyzed,
            recent_activity_at=recent_activity_at,
            has_recent_activity=has_recent_activity,
            activity_source=activity_source,
            activity_column=activity_column,
        )
        return profile

    def _recent_timestamp_from_preferred_column(
        self, schema: str, table: str
    ) -> Optional[ColumnActivity]:
        priority_list = ", ".join(f"'{name}'" for name in _ACTIVITY_COLUMN_PRIORITY)
        order_clauses = " ".join(
            f"WHEN '{name}' THEN {idx}" for idx, name in enumerate(_ACTIVITY_COLUMN_PRIORITY)
        )
        sql = f"""
            SELECT column_name
            FROM all_tab_columns
            WHERE owner = :schema
              AND table_name = :table
              AND column_name IN ({priority_list})
              AND (data_type = 'DATE' OR data_type LIKE 'TIMESTAMP%')
            ORDER BY CASE column_name {order_clauses} ELSE {len(_ACTIVITY_COLUMN_PRIORITY)} END, column_id
            FETCH FIRST 1 ROWS ONLY
        """
        row = self._client.fetchone(sql, {"schema": schema, "table": table})
        if not row:
            return None
        column_name = row["column_name"]
        qualified_table = qualify_identifier(schema, table)
        column_identifier = quote_identifier(column_name)
        query = f"SELECT MAX({column_identifier}) AS max_value FROM {qualified_table}"
        try:
            max_value = self._client.fetch_value(query)
        except Exception as exc:  # pragma: no cover - depends on Oracle permissions
            logger.debug(
                "Failed to inspect column %s for %s.%s: %s", column_name, schema, table, exc
            )
            return None
        if max_value is None:
            return None
        if isinstance(max_value, datetime):
            timestamp = max_value
        elif isinstance(max_value, date):
            timestamp = datetime.combine(max_value, datetime.min.time())
        else:
            return None
        return ColumnActivity(timestamp=timestamp, column=column_name)

    def _has_any_rows(self, schema: str, table: str) -> Optional[bool]:
        qualified = qualify_identifier(schema, table)
        sql = f"SELECT 1 AS has_rows FROM {qualified} WHERE ROWNUM = 1"
        try:
            value = self._client.fetch_value(sql)
        except Exception as exc:  # pragma: no cover - depends on Oracle permissions
            logger.debug("Failed to probe table %s.%s for emptiness: %s", schema, table, exc)
            return None
        return value is not None

    def _build_model_entry(
        self, schema: str, table: str, profiles: Dict[str, TableProfile]
    ) -> Optional[Dict[str, object]]:
        columns = self._fetch_columns(schema, table)
        if not columns:
            logger.warning("No columns discovered for %s.%s; skipping from manifest", schema, table)
            return None
        pk_columns = self._fetch_primary_key(schema, table)
        model: Dict[str, object] = {
            "name": table,
            "baseObject": f"{schema}.{table}",
            "tableReference": {"schema": schema, "table": table},
            "columns": columns,
        }
        if pk_columns:
            if len(pk_columns) == 1:
                model["primaryKey"] = pk_columns[0]
            else:
                model["primaryKey"] = ", ".join(pk_columns)

        profile = profiles.get(table)
        properties: Dict[str, str] = {}
        if profile and profile.num_rows is not None:
            properties["numRows"] = str(profile.num_rows)
        if profile and profile.is_empty is not None:
            properties["isEmpty"] = str(profile.is_empty).lower()
        if profile and profile.recent_activity_at:
            properties["recentActivityAt"] = profile.recent_activity_at.isoformat()
        if profile and profile.activity_source:
            properties["recentActivitySource"] = profile.activity_source
        if profile and profile.activity_column:
            properties["recentActivityColumn"] = profile.activity_column
        if profile and profile.last_analyzed:
            properties["lastAnalyzed"] = profile.last_analyzed.isoformat()
        if properties:
            model["properties"] = properties
        return model

    def _fetch_columns(self, schema: str, table: str) -> List[Dict[str, object]]:
        sql = """
            SELECT
                column_name,
                data_type,
                data_length,
                data_precision,
                data_scale,
                nullable,
                data_default
            FROM all_tab_columns
            WHERE owner = :schema AND table_name = :table
            ORDER BY column_id
        """
        rows = self._client.fetchall(sql, {"schema": schema, "table": table})
        columns: List[Dict[str, object]] = []
        for row in rows:
            column_type = self._format_data_type(row)
            column_entry: Dict[str, object] = {
                "name": row["column_name"],
                "type": column_type,
                "notNull": row.get("nullable") == "N",
            }
            properties: Dict[str, str] = {}
            if row.get("data_default") is not None:
                properties["dataDefault"] = str(row["data_default"]).strip()
            if row.get("data_length") is not None and row.get("data_type") in {
                "VARCHAR2",
                "CHAR",
                "NVARCHAR2",
                "NCHAR",
            }:
                properties["length"] = str(row["data_length"])
            if row.get("data_precision") is not None:
                properties["precision"] = str(row["data_precision"])
            if row.get("data_scale") is not None:
                properties["scale"] = str(row["data_scale"])
            if properties:
                column_entry["properties"] = properties
            columns.append(column_entry)
        return columns

    def _format_data_type(self, column: Dict[str, object]) -> str:
        data_type = str(column.get("data_type") or "").upper()
        if data_type in {"VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"}:
            length = column.get("data_length")
            if length:
                return f"{data_type}({int(length)})"
        if data_type in {"NUMBER", "FLOAT", "DECIMAL"}:
            precision = column.get("data_precision")
            scale = column.get("data_scale")
            if precision is None:
                return data_type
            if scale is None:
                return f"{data_type}({int(precision)})"
            return f"{data_type}({int(precision)},{int(scale)})"
        return data_type or "UNKNOWN"

    def _fetch_primary_key(self, schema: str, table: str) -> List[str]:
        sql = """
            SELECT acc.column_name
            FROM all_constraints ac
            JOIN all_cons_columns acc
              ON ac.owner = acc.owner AND ac.constraint_name = acc.constraint_name
            WHERE ac.owner = :schema AND ac.table_name = :table AND ac.constraint_type = 'P'
            ORDER BY acc.position
        """
        rows = self._client.fetchall(sql, {"schema": schema, "table": table})
        return [row["column_name"] for row in rows]

    def _coerce_int(self, value: Optional[object]) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        try:
            return int(value)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            return None


__all__ = ["OracleMetadataService"]
