"""Database client abstractions for talking to Oracle."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from app.config import Settings

try:  # pragma: no cover - optional dependency for unit tests without Oracle
    import oracledb
except ImportError:  # pragma: no cover
    oracledb = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class OracleClient:
    """Thin wrapper around python-oracledb with Kerberos support."""

    def __init__(self, settings: Settings):
        if oracledb is None:
            raise RuntimeError(
                "python-oracledb is required to use OracleClient. Install the optional dependency"
            )

        self._settings = settings
        self._pool: Optional["oracledb.SessionPool"] = None
        self._pool_lock = threading.Lock()

        if settings.oracle_use_thick:
            logger.debug(
                "Initializing Oracle client in thick mode (config_dir=%s, lib_dir=%s)",
                settings.oracle_config_dir,
                settings.oracle_lib_dir,
            )
            oracledb.init_oracle_client(
                config_dir=settings.oracle_config_dir,
                lib_dir=settings.oracle_lib_dir,
            )

    def close(self) -> None:
        """Close the underlying session pool, if any."""

        if self._pool:
            logger.info("Closing Oracle session pool")
            self._pool.close()
            self._pool = None

    def _create_pool(self) -> "oracledb.SessionPool":
        settings = self._settings
        logger.info(
            "Creating Oracle session pool to %s (min=%s, max=%s, increment=%s)",
            settings.oracle_dsn,
            settings.oracle_pool_min,
            settings.oracle_pool_max,
            settings.oracle_pool_increment,
        )
        return oracledb.SessionPool(  # type: ignore[attr-defined]
            dsn=settings.oracle_dsn,
            externalauth=True,
            min=settings.oracle_pool_min,
            max=settings.oracle_pool_max,
            increment=settings.oracle_pool_increment,
            getmode=getattr(oracledb, "SPOOL_ATTRVAL_WAIT", None),
        )

    def _acquire_connection(self) -> "oracledb.Connection":
        if self._settings.oracle_use_pool:
            if self._pool is None:
                with self._pool_lock:
                    if self._pool is None:
                        self._pool = self._create_pool()
            return self._pool.acquire()
        logger.debug("Opening dedicated Oracle connection to %s", self._settings.oracle_dsn)
        return oracledb.connect(dsn=self._settings.oracle_dsn, externalauth=True)

    @contextmanager
    def connection(self):  # type: ignore[override]
        conn = self._acquire_connection()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def cursor(self):
        with self.connection() as conn:
            cursor = conn.cursor()
            cursor.arraysize = self._settings.oracle_fetch_arraysize
            try:
                yield cursor
            finally:
                cursor.close()

    def fetchall(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        with self.cursor() as cursor:
            cursor.execute(sql, params or {})
            if cursor.description is None:
                return []
            columns = [col[0].lower() for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def fetchone(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        with self.cursor() as cursor:
            cursor.execute(sql, params or {})
            row = cursor.fetchone()
            if row is None or cursor.description is None:
                return None
            columns = [col[0].lower() for col in cursor.description]
            return dict(zip(columns, row))

    def fetch_value(self, sql: str, params: Optional[Dict[str, Any]] = None) -> Any:
        result = self.fetchone(sql, params)
        if not result:
            return None
        return next(iter(result.values()))

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> None:
        with self.cursor() as cursor:
            cursor.execute(sql, params or {})


__all__ = ["OracleClient"]
