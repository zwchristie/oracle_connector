"""FastAPI application exposing metadata endpoints."""
from __future__ import annotations

import logging
from threading import Lock
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, validator

from app.config import Settings, get_settings
from app.db import OracleClient
from app.services.metadata import OracleMetadataService
from app.services.models import TableProfile

logger = logging.getLogger(__name__)

app = FastAPI(title="Oracle Metadata Connector", version="1.0.0")

_client_lock = Lock()
_client_instance: Optional[OracleClient] = None
_service_lock = Lock()
_service_instance: Optional[OracleMetadataService] = None


class TableSummary(BaseModel):
    schema: str
    table_name: str = Field(..., alias="tableName")
    num_rows: Optional[int] = Field(None, alias="numRows")
    is_empty: Optional[bool] = Field(None, alias="isEmpty")
    has_recent_activity: bool = Field(..., alias="hasRecentActivity")
    recent_activity_at: Optional[str] = Field(None, alias="recentActivityAt")
    activity_source: Optional[str] = Field(None, alias="activitySource")
    activity_column: Optional[str] = Field(None, alias="activityColumn")
    last_analyzed: Optional[str] = Field(None, alias="lastAnalyzed")

    class Config:
        allow_population_by_field_name = True

    @classmethod
    def from_profile(cls, profile: TableProfile) -> "TableSummary":
        return cls(
            schema=profile.schema,
            tableName=profile.table_name,
            numRows=profile.num_rows,
            isEmpty=profile.is_empty,
            hasRecentActivity=profile.has_recent_activity,
            recentActivityAt=profile.recent_activity_at.isoformat()
            if profile.recent_activity_at
            else None,
            activitySource=profile.activity_source,
            activityColumn=profile.activity_column,
            lastAnalyzed=profile.last_analyzed.isoformat() if profile.last_analyzed else None,
        )


class MetadataRequest(BaseModel):
    catalog: str
    tables: Optional[List[str]] = None
    apply_usage_filter: bool = Field(True, alias="applyUsageFilter")
    months: Optional[int] = None
    include_empty: bool = Field(False, alias="includeEmpty")

    class Config:
        allow_population_by_field_name = True

    @validator("catalog")
    def _catalog_not_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("catalog must not be empty")
        return value


def _get_oracle_client(settings: Settings) -> OracleClient:
    global _client_instance
    with _client_lock:
        if _client_instance is None:
            logger.info("Initializing Oracle client")
            _client_instance = OracleClient(settings)
    return _client_instance


def _get_metadata_service(settings: Settings) -> OracleMetadataService:
    global _service_instance
    with _service_lock:
        if _service_instance is None:
            client = _get_oracle_client(settings)
            _service_instance = OracleMetadataService(client, settings)
    return _service_instance


def get_metadata_service(
    settings: Settings = Depends(get_settings),
) -> OracleMetadataService:
    try:
        return _get_metadata_service(settings)
    except RuntimeError as exc:  # pragma: no cover - requires missing dependency
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/schemas/{schema}/tables", response_model=List[TableSummary])
def list_filtered_tables(
    schema: str,
    months: Optional[int] = Query(None, ge=0),
    include_empty: bool = Query(False, alias="includeEmpty"),
    table: Optional[List[str]] = Query(None),
    service: OracleMetadataService = Depends(get_metadata_service),
) -> List[TableSummary]:
    profiles = service.get_filtered_tables(
        schema=schema,
        table_names=table,
        months=months,
        include_empty=include_empty,
    )
    return [TableSummary.from_profile(profile) for profile in profiles]


@app.post("/schemas/{schema}/metadata")
def build_metadata(
    schema: str,
    request: MetadataRequest,
    service: OracleMetadataService = Depends(get_metadata_service),
) -> Dict[str, Any]:
    try:
        manifest = service.generate_mdl(
            catalog=request.catalog,
            schema=schema,
            tables=request.tables,
            apply_usage_filter=request.apply_usage_filter,
            months=request.months,
            include_empty=request.include_empty,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return manifest


@app.on_event("shutdown")
def shutdown_event() -> None:
    global _client_instance, _service_instance
    if _client_instance is not None:
        _client_instance.close()
        _client_instance = None
    _service_instance = None


__all__ = ["app"]
