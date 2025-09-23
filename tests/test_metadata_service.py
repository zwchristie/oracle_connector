from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from app.config import Settings
from app.services.metadata import OracleMetadataService


class FakeOracleClient:
    def __init__(self, now: Optional[datetime] = None):
        self.now = now or datetime.utcnow()

    def fetchall(self, sql: str, params: Optional[Dict[str, Any]] = None):
        sql_lower = sql.lower()
        params = params or {}
        if "from all_tables" in sql_lower:
            return [
                {"table_name": "ACTIVE_TABLE"},
                {"table_name": "EMPTY_TABLE"},
                {"table_name": "STALE_TABLE"},
            ]
        if "from all_tab_columns" in sql_lower and "order by column_id" in sql_lower:
            table = params.get("table")
            if table == "ACTIVE_TABLE":
                return [
                    {
                        "column_name": "ID",
                        "data_type": "NUMBER",
                        "data_length": None,
                        "data_precision": 10,
                        "data_scale": 0,
                        "nullable": "N",
                        "data_default": None,
                    },
                    {
                        "column_name": "CREATED_DATE",
                        "data_type": "DATE",
                        "data_length": None,
                        "data_precision": None,
                        "data_scale": None,
                        "nullable": "Y",
                        "data_default": None,
                    },
                ]
            if table == "STALE_TABLE":
                return [
                    {
                        "column_name": "ID",
                        "data_type": "NUMBER",
                        "data_length": None,
                        "data_precision": 10,
                        "data_scale": 0,
                        "nullable": "N",
                        "data_default": None,
                    },
                    {
                        "column_name": "CREATED_DATE",
                        "data_type": "DATE",
                        "data_length": None,
                        "data_precision": None,
                        "data_scale": None,
                        "nullable": "Y",
                        "data_default": None,
                    },
                ]
            if table == "EMPTY_TABLE":
                return [
                    {
                        "column_name": "ID",
                        "data_type": "NUMBER",
                        "data_length": None,
                        "data_precision": 10,
                        "data_scale": 0,
                        "nullable": "N",
                        "data_default": None,
                    }
                ]
        if "from all_constraints" in sql_lower:
            table = params.get("table")
            if table in {"ACTIVE_TABLE", "STALE_TABLE", "EMPTY_TABLE"}:
                return [{"column_name": "ID"}]
        return []

    def fetchone(self, sql: str, params: Optional[Dict[str, Any]] = None):
        sql_lower = sql.lower()
        params = params or {}
        table = params.get("table")
        if "from all_tab_statistics" in sql_lower:
            if table == "ACTIVE_TABLE":
                return {
                    "num_rows": 42,
                    "last_analyzed": self.now - timedelta(days=10),
                }
            if table == "EMPTY_TABLE":
                return {
                    "num_rows": 0,
                    "last_analyzed": self.now - timedelta(days=10),
                }
            if table == "STALE_TABLE":
                return {
                    "num_rows": 10,
                    "last_analyzed": self.now - timedelta(days=400),
                }
        if "from all_tab_modifications" in sql_lower:
            if table == "ACTIVE_TABLE":
                return {"modified_at": self.now - timedelta(days=30)}
            if table == "EMPTY_TABLE":
                return {"modified_at": None}
            if table == "STALE_TABLE":
                return {"modified_at": self.now - timedelta(days=400)}
        if "from all_tab_columns" in sql_lower and "fetch first 1 rows only" in sql_lower:
            if table == "EMPTY_TABLE":
                return None
            return {"column_name": "CREATED_DATE"}
        return None

    def fetch_value(self, sql: str, params: Optional[Dict[str, Any]] = None):
        sql_lower = sql.lower()
        if "max" in sql_lower:
            if "\"active_table\"" in sql_lower:
                return self.now - timedelta(days=20)
            if "\"stale_table\"" in sql_lower:
                return self.now - timedelta(days=400)
        if "where rownum = 1" in sql_lower:
            if "\"empty_table\"" in sql_lower:
                return None
            return 1
        return None


@pytest.fixture
def service():
    settings = Settings(oracle_dsn="//testdb")
    client = FakeOracleClient(now=datetime.utcnow())
    return OracleMetadataService(client, settings)


def test_filtered_tables_removes_empty_and_stale(service):
    tables = service.get_filtered_tables("TEST")
    names = [table.table_name for table in tables]
    assert names == ["ACTIVE_TABLE"]


def test_generate_mdl_for_specific_tables(service):
    manifest = service.generate_mdl(
        catalog="Demo",
        schema="TEST",
        tables=["ACTIVE_TABLE"],
        apply_usage_filter=False,
    )
    assert manifest["catalog"] == "Demo"
    assert manifest["schema"] == "TEST"
    assert len(manifest["models"]) == 1
    model = manifest["models"][0]
    assert model["name"] == "ACTIVE_TABLE"
    column_names = [column["name"] for column in model["columns"]]
    assert column_names == ["ID", "CREATED_DATE"]
    assert model["primaryKey"] == "ID"


def test_generate_mdl_applies_filter(service):
    manifest = service.generate_mdl(catalog="Demo", schema="TEST")
    assert len(manifest["models"]) == 1
    assert manifest["models"][0]["name"] == "ACTIVE_TABLE"
