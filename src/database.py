"""
Database operations module for Unity Catalog Image Gallery.

Handles PostgreSQL connections, OAuth token management, and data queries
for the Lakebase integration.
"""

import time
from typing import Optional, List, Tuple, Any
import streamlit as st
from databricks import sdk
from psycopg import sql
from psycopg_pool import ConnectionPool

from .config import (
    TABLE_NAME, PATH_COLUMN, TOKEN_REFRESH_INTERVAL,
    CONNECTION_POOL_MIN_SIZE, CONNECTION_POOL_MAX_SIZE,
    get_pg_connection_params
)

# Import DEFAULT_SCHEMA as mutable for dynamic updates
from . import config


class DatabaseManager:
    """Manages database connections and operations."""
    
    def __init__(self):
        self.workspace_client = sdk.WorkspaceClient()
        self.postgres_password = None
        self.last_password_refresh = 0
        self.connection_pool = None
    
    def refresh_oauth_token(self) -> None:
        """Refresh OAuth token if expired."""
        if (self.postgres_password is None or 
            time.time() - self.last_password_refresh > TOKEN_REFRESH_INTERVAL):
            
            print("Refreshing PostgreSQL OAuth token")
            try:
                self.postgres_password = self.workspace_client.config.oauth_token().access_token
                self.last_password_refresh = time.time()
            except Exception as e:
                st.error(f"❌ Failed to refresh OAuth token: {str(e)}")
                st.stop()
    
    def get_connection_pool(self) -> ConnectionPool:
        """Get or create the connection pool."""
        if self.connection_pool is None:
            self.refresh_oauth_token()
            
            # Build connection string
            params = get_pg_connection_params()
            conn_string = (
                f"dbname={params['dbname']} "
                f"user={params['user']} "
                f"password={self.postgres_password} "
                f"host={params['host']} "
                f"port={params['port']} "
                f"sslmode={params['sslmode']} "
                f"application_name={params['application_name']}"
            )
            
            self.connection_pool = ConnectionPool(
                conn_string,
                min_size=CONNECTION_POOL_MIN_SIZE,
                max_size=CONNECTION_POOL_MAX_SIZE
            )
        
        return self.connection_pool
    
    def get_connection(self):
        """Get a connection from the pool."""
        # Recreate pool if token expired
        if (self.postgres_password is None or 
            time.time() - self.last_password_refresh > TOKEN_REFRESH_INTERVAL):
            if self.connection_pool:
                self.connection_pool.close()
                self.connection_pool = None
        
        return self.get_connection_pool().connection()
    
    def find_table_schema(self, table: str = TABLE_NAME) -> Optional[str]:
        """Find which schema contains the target table."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT table_schema
                        FROM information_schema.tables 
                        WHERE table_name = %s
                        ORDER BY table_schema
                        LIMIT 1
                    """, (table,))
                    result = cur.fetchone()
                    return result[0] if result else None
        except Exception:
            # Silently handle table schema search errors
            return None
    
    def check_table_exists(self, schema: str = None, table: str = TABLE_NAME) -> bool:
        """Check if the required table exists in the database."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    # Check current database and schema
                    cur.execute("SELECT current_database(), current_schema()")
                    cur.fetchone()  # Execute but don't store unused result
                    
                    # List all schemas
                    cur.execute("SELECT schema_name FROM information_schema.schemata")
                    cur.fetchall()  # Execute but don't store unused result
                    
                    # First try the configured schema
                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_schema = %s 
                            AND table_name = %s
                        )
                    """, (schema, table))
                    result = cur.fetchone()[0]
                    
                    if result:
                        return True
                        
                    # If not found, search across all schemas
                    found_schema = self.find_table_schema(table)
                    if found_schema:
                        config.DEFAULT_SCHEMA = found_schema
                        return True
                        
                    return False
        except Exception as e:
            st.error(f"❌ Database connection error: {str(e)}")
            return False
    
    def get_image_paths(self, limit: int = 50, offset: int = 0, 
                       search_term: Optional[str] = None,
                       label: Optional[str] = None,
                       label_detail: Optional[str] = None,
                       min_score: Optional[float] = None,
                       max_score: Optional[float] = None,
                       schema: str = None) -> List[Tuple[Any, ...]]:
        """Fetch image paths from the database with optional filtering and pagination."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
            
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                table_ref = sql.Identifier(schema, TABLE_NAME)
                
                # Build WHERE conditions
                conditions = []
                params = []
                
                if search_term:
                    conditions.append(f"{PATH_COLUMN} ILIKE %s")
                    params.append(f"%{search_term}%")
                
                if label:
                    conditions.append("label = %s")
                    params.append(label)
                
                if label_detail:
                    conditions.append('"labelDetail" = %s') 
                    params.append(label_detail)
                
                if min_score is not None:
                    conditions.append("score >= %s")
                    params.append(min_score)
                
                if max_score is not None:
                    conditions.append("score <= %s")
                    params.append(max_score)
                
                # Build query
                base_query = f"SELECT {PATH_COLUMN} FROM {{}} "
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)
                    base_query += where_clause + " "
                
                base_query += f"ORDER BY {PATH_COLUMN} LIMIT %s OFFSET %s"
                
                query = sql.SQL(base_query).format(table_ref)
                params.extend([limit, offset])
                cur.execute(query, params)
                
                return cur.fetchall()
    
    def get_all_image_paths(self, 
                           label: Optional[str] = None,
                           label_detail: Optional[str] = None,
                           min_score: Optional[float] = None,
                           max_score: Optional[float] = None,
                           schema: str = None) -> List[str]:
        """Get all image paths with optional filtering for dropdown selection."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
            
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                table_ref = sql.Identifier(schema, TABLE_NAME)
                
                # Build WHERE conditions
                conditions = []
                params = []
                
                if label:
                    conditions.append("label = %s")
                    params.append(label)
                
                if label_detail:
                    conditions.append('"labelDetail" = %s')
                    params.append(label_detail)
                
                if min_score is not None:
                    conditions.append("score >= %s")
                    params.append(min_score)
                
                if max_score is not None:
                    conditions.append("score <= %s")
                    params.append(max_score)
                
                # Build query
                base_query = f"SELECT {PATH_COLUMN} FROM {{}} "
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)
                    base_query += where_clause + " "
                
                base_query += f"ORDER BY {PATH_COLUMN}"
                
                query = sql.SQL(base_query).format(table_ref)
                cur.execute(query, params)
                
                # Return list of paths (each record is a tuple with one element)
                return [record[0] for record in cur.fetchall()]
    
    def get_distinct_labels(self, schema: str = None) -> List[str]:
        """Get distinct label values from the database."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
            
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                table_ref = sql.Identifier(schema, TABLE_NAME)
                
                query = sql.SQL("""
                    SELECT DISTINCT label FROM {} 
                    WHERE label IS NOT NULL 
                    ORDER BY label
                """).format(table_ref)
                cur.execute(query)
                
                return [record[0] for record in cur.fetchall()]
    
    def get_distinct_label_details(self, label: Optional[str] = None, schema: str = None) -> List[str]:
        """Get distinct labelDetail values, optionally filtered by label."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
            
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                table_ref = sql.Identifier(schema, TABLE_NAME)
                
                if label:
                    query = sql.SQL("""
                        SELECT DISTINCT "labelDetail" FROM {} 
                        WHERE "labelDetail" IS NOT NULL 
                        AND label = %s
                        ORDER BY "labelDetail"
                    """).format(table_ref)
                    cur.execute(query, (label,))
                else:
                    query = sql.SQL("""
                        SELECT DISTINCT "labelDetail" FROM {} 
                        WHERE "labelDetail" IS NOT NULL 
                        ORDER BY "labelDetail"
                    """).format(table_ref)
                    cur.execute(query)
                
                return [record[0] for record in cur.fetchall()]
    
    def get_score_range(self, schema: str = None) -> Tuple[float, float]:
        """Get the min and max score values from the database."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
            
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                table_ref = sql.Identifier(schema, TABLE_NAME)
                
                query = sql.SQL("""
                    SELECT MIN(score), MAX(score) FROM {} 
                    WHERE score IS NOT NULL
                """).format(table_ref)
                cur.execute(query)
                
                result = cur.fetchone()
                if result and result[0] is not None and result[1] is not None:
                    return (float(result[0]), float(result[1]))
                else:
                    return (0.0, 1.0)  # Default range
    
    def get_total_image_count(self, 
                             search_term: Optional[str] = None,
                             label: Optional[str] = None,
                             label_detail: Optional[str] = None,
                             min_score: Optional[float] = None,
                             max_score: Optional[float] = None,
                             schema: str = None) -> int:
        """Get total count of images with filtering for pagination."""
        if schema is None:
            schema = config.DEFAULT_SCHEMA
            
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                table_ref = sql.Identifier(schema, TABLE_NAME)
                
                # Build WHERE conditions
                conditions = []
                params = []
                
                if search_term:
                    conditions.append(f"{PATH_COLUMN} ILIKE %s")
                    params.append(f"%{search_term}%")
                
                if label:
                    conditions.append("label = %s")
                    params.append(label)
                
                if label_detail:
                    conditions.append('"labelDetail" = %s')
                    params.append(label_detail)
                
                if min_score is not None:
                    conditions.append("score >= %s")
                    params.append(min_score)
                
                if max_score is not None:
                    conditions.append("score <= %s")
                    params.append(max_score)
                
                # Build query
                base_query = "SELECT COUNT(*) FROM {} "
                if conditions:
                    where_clause = "WHERE " + " AND ".join(conditions)
                    base_query += where_clause
                
                query = sql.SQL(base_query).format(table_ref)
                cur.execute(query, params)
                
                return cur.fetchone()[0]
    


# Global database manager instance
db_manager = DatabaseManager()


# Convenience functions
def check_database_connection() -> bool:
    """Check if we can connect to the database and table exists."""
    return db_manager.check_table_exists()


def get_all_image_paths(label: Optional[str] = None, 
                        label_detail: Optional[str] = None,
                        min_score: Optional[float] = None,
                        max_score: Optional[float] = None) -> List[str]:
    """Get all image paths with optional filtering for dropdown selection."""
    return db_manager.get_all_image_paths(label, label_detail, min_score, max_score)


def get_distinct_labels() -> List[str]:
    """Get distinct label values from the database."""
    return db_manager.get_distinct_labels()


def get_distinct_label_details(label: Optional[str] = None) -> List[str]:
    """Get distinct labelDetail values, optionally filtered by label."""
    return db_manager.get_distinct_label_details(label)


def get_score_range() -> Tuple[float, float]:
    """Get the min and max score values from the database."""
    return db_manager.get_score_range()
