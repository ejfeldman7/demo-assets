from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Dict, List, Optional

from config.settings import AppSettings, get_settings, qualify_table
from utils.db_connection import execute_query, execute_update

logger = logging.getLogger(__name__)


@dataclass
class SetupStatus:
    """Track the results of provisioning required schemas and tables."""

    setup_complete: bool = False
    catalog_ready: bool = False
    schema_ready: bool = False
    tables_ready: bool = False
    permissions_ready: bool = False
    errors: List[str] = field(default_factory=list)
    created_objects: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class AutoSetupManager:
    """Provision schemas and tables required by the access management app."""

    def __init__(self, settings: Optional[AppSettings] = None) -> None:
        self.settings = settings or get_settings()

    def ensure_setup_complete(self) -> SetupStatus:
        """Ensure the catalog, schema, and tables exist and are accessible."""
        status = SetupStatus()
        try:
            status.catalog_ready = self._ensure_catalog_exists()
            status.schema_ready = self._ensure_schema_exists()
            table_status = self._ensure_tables_exist()
            status.tables_ready = table_status["all_exist"]
            status.created_objects.extend(table_status["created_objects"])
            status.errors.extend(table_status["errors"])
            perm_status = self._validate_permissions()
            status.permissions_ready = perm_status["valid"]
            status.errors.extend(perm_status["errors"])
            status.setup_complete = all(
                [
                    status.catalog_ready,
                    status.schema_ready,
                    status.tables_ready,
                    status.permissions_ready,
                ]
            )
        except Exception as exc:  # pragma: no cover - runtime error surface
            logger.error("Setup validation failed: %s", exc)
            status.errors.append(f"Setup validation failed: {exc}")
        return status

    def _ensure_catalog_exists(self) -> bool:
        """Create the catalog if needed."""
        catalog = self.settings.catalog
        try:
            execute_update(f"CREATE CATALOG IF NOT EXISTS {catalog}")
            return True
        except Exception as exc:
            logger.warning("Catalog check failed for %s: %s", catalog, exc)
            return False

    def _ensure_schema_exists(self) -> bool:
        """Create the schema if needed."""
        catalog = self.settings.catalog
        schema = self.settings.schema
        try:
            execute_update(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
            return True
        except Exception as exc:
            logger.error("Schema check failed for %s.%s: %s", catalog, schema, exc)
            return False

    def _ensure_tables_exist(self) -> Dict[str, object]:
        """Create required tables if they do not exist."""
        results = {"all_exist": True, "created_objects": [], "errors": []}
        table_definitions = {
            self.settings.access_table: self._access_table_ddl(),
            self.settings.audit_table: self._audit_table_ddl(),
        }
        for table_name, ddl in table_definitions.items():
            try:
                if self._table_exists(table_name):
                    continue
                execute_update(ddl)
                results["created_objects"].append(
                    f"Table: {qualify_table(table_name, self.settings)}"
                )
            except Exception as exc:
                results["all_exist"] = False
                message = f"Failed to create table {table_name}: {exc}"
                results["errors"].append(message)
                logger.error(message)
        return results

    def _table_exists(self, table_name: str) -> bool:
        """Return True when a table exists in Unity Catalog."""
        query = """
            SELECT COUNT(*) AS table_count
            FROM system.information_schema.tables
            WHERE table_catalog = :catalog
              AND table_schema = :schema
              AND table_name = :table_name
        """
        rows = execute_query(
            query,
            {
                "catalog": self.settings.catalog,
                "schema": self.settings.schema,
                "table_name": table_name,
            },
        )
        if not rows:
            return False
        return int(rows[0].get("table_count", 0)) > 0

    def _validate_permissions(self) -> Dict[str, object]:
        """Validate basic read access to the required tables."""
        results = {"valid": True, "errors": []}
        checks = [
            f"SELECT COUNT(*) FROM {qualify_table(self.settings.access_table, self.settings)}",
            f"SELECT COUNT(*) FROM {qualify_table(self.settings.audit_table, self.settings)}",
        ]
        for statement in checks:
            try:
                execute_query(statement)
            except Exception as exc:
                results["valid"] = False
                results["errors"].append(f"Permission check failed: {exc}")
        return results

    def _access_table_ddl(self) -> str:
        """Return DDL for the group access table."""
        table_name = qualify_table(self.settings.access_table, self.settings)
        return f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGINT GENERATED ALWAYS AS IDENTITY,
                group_name STRING,
                customer_ids ARRAY<INT>,
                access_type STRING,
                effective_date DATE,
                expiration_date DATE,
                notes STRING,
                created_by STRING,
                created_at TIMESTAMP,
                modified_by STRING,
                modified_at TIMESTAMP
            )
        """

    def _audit_table_ddl(self) -> str:
        """Return DDL for the audit log table."""
        table_name = qualify_table(self.settings.audit_table, self.settings)
        return f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id BIGINT GENERATED ALWAYS AS IDENTITY,
                timestamp TIMESTAMP,
                user STRING,
                action_type STRING,
                object_type STRING,
                object_name STRING,
                old_value STRING,
                new_value STRING,
                notes STRING
            )
        """
