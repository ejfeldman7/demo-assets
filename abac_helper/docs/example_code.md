Implementation Guide for Cursor
Step 1: Set Up Project Structure
Create the folder structure:
bashmkdir -p databricks-access-app/{pages,utils,config}
cd databricks-access-app
touch app.py requirements.txt
touch pages/{1_Group_Access.py,2_Tag_Management.py,3_Audit_Reports.py}
touch utils/{db_connection.py,access_manager.py,tag_manager.py,audit_logger.py,validators.py}
touch config/settings.py
Step 2: Configuration (config/settings.py)
python# Databricks connection settings
CATALOG = "security"
SCHEMA = "access_control"
ACCESS_TABLE = "group_customer_access"
AUDIT_TABLE = "access_audit_log"

# Admin group
ADMIN_GROUP = "access_admin"

# App settings
APP_TITLE = "Unity Catalog Access Management"
PAGE_ICON = "🔐"
Step 3: Database Connection (utils/db_connection.py)
Create a helper to connect to Databricks SQL:
pythonimport streamlit as st
from databricks import sql
import os

@st.cache_resource
def get_connection():
    """Get Databricks SQL connection using workspace authentication"""
    return sql.connect(
        server_hostname=os.getenv("DATABRICKS_SERVER_HOSTNAME"),
        http_path=os.getenv("DATABRICKS_HTTP_PATH"),
        # When deployed as Lakehouse App, uses workspace auth automatically
    )

def execute_query(query, params=None):
    """Execute a query and return results as list of dicts"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(query, params or {})
    columns = [desc[0] for desc in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    return results

def execute_update(query, params=None):
    """Execute an INSERT/UPDATE/DELETE and return row count"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(query, params or {})
    row_count = cursor.rowcount
    conn.commit()
    cursor.close()
    return row_count
Step 4: Validators (utils/validators.py)
pythonfrom utils.db_connection import execute_query
import re

def validate_group_exists(group_name):
    """Check if group exists in Databricks account"""
    # Note: This requires appropriate permissions
    query = """
    SELECT COUNT(*) as count 
    FROM system.access.groups 
    WHERE name = :group_name
    """
    result = execute_query(query, {"group_name": group_name})
    return result[0]['count'] > 0

def validate_customer_ids(customer_ids_str):
    """
    Parse and validate customer IDs from string input
    Supports: "1,2,3" or "1-10" or "1,2,5-8"
    Returns: (is_valid, parsed_array or error_message)
    """
    if not customer_ids_str or customer_ids_str.strip() == "":
        return True, []
    
    try:
        ids = []
        parts = customer_ids_str.split(',')
        for part in parts:
            part = part.strip()
            if '-' in part:
                # Range like "100-110"
                start, end = part.split('-')
                ids.extend(range(int(start), int(end) + 1))
            else:
                # Single ID
                ids.append(int(part))
        return True, sorted(list(set(ids)))  # Remove duplicates
    except ValueError as e:
        return False, f"Invalid customer ID format: {str(e)}"

def validate_dates(effective_date, expiration_date):
    """Validate date logic"""
    if expiration_date and effective_date:
        if expiration_date <= effective_date:
            return False, "Expiration date must be after effective date"
    return True, None
Step 5: Access Manager (utils/access_manager.py)
pythonfrom utils.db_connection import execute_query, execute_update
from utils.audit_logger import log_action
from config.settings import CATALOG, SCHEMA, ACCESS_TABLE
import streamlit as st

def get_all_access_rules(filters=None):
    """Retrieve all access rules with optional filters"""
    query = f"""
    SELECT 
        id,
        group_name,
        customer_ids,
        access_type,
        effective_date,
        expiration_date,
        notes,
        created_by,
        created_at,
        modified_by,
        modified_at
    FROM {CATALOG}.{SCHEMA}.{ACCESS_TABLE}
    WHERE 1=1
    """
    
    params = {}
    if filters:
        if filters.get('group_name'):
            query += " AND group_name = :group_name"
            params['group_name'] = filters['group_name']
        if filters.get('status') == 'active':
            query += " AND (expiration_date IS NULL OR expiration_date > CURRENT_DATE())"
        elif filters.get('status') == 'expired':
            query += " AND expiration_date <= CURRENT_DATE()"
    
    query += " ORDER BY group_name, effective_date DESC"
    return execute_query(query, params)

def add_access_rule(group_name, customer_ids, access_type, effective_date, 
                    expiration_date, notes):
    """Add a new access rule"""
    user = st.experimental_user.get('email', 'unknown')
    
    # Convert list to array string for SQL
    customer_ids_array = f"array({','.join(map(str, customer_ids))})" if customer_ids else "NULL"
    
    query = f"""
    INSERT INTO {CATALOG}.{SCHEMA}.{ACCESS_TABLE} 
    (group_name, customer_ids, access_type, effective_date, expiration_date, 
     notes, created_by, created_at, modified_by, modified_at)
    VALUES (
        :group_name,
        {customer_ids_array},
        :access_type,
        :effective_date,
        :expiration_date,
        :notes,
        :user,
        CURRENT_TIMESTAMP(),
        :user,
        CURRENT_TIMESTAMP()
    )
    """
    
    params = {
        'group_name': group_name,
        'access_type': access_type,
        'effective_date': effective_date,
        'expiration_date': expiration_date,
        'notes': notes,
        'user': user
    }
    
    row_count = execute_update(query, params)
    
    # Log audit
    log_action(
        action_type='INSERT',
        object_type='GROUP_ACCESS',
        object_name=group_name,
        new_value=f"customer_ids: {customer_ids}, access_type: {access_type}",
        notes=notes
    )
    
    return row_count > 0

def update_access_rule(rule_id, customer_ids, access_type, effective_date,
                       expiration_date, notes):
    """Update an existing access rule"""
    user = st.experimental_user.get('email', 'unknown')
    
    customer_ids_array = f"array({','.join(map(str, customer_ids))})" if customer_ids else "NULL"
    
    query = f"""
    UPDATE {CATALOG}.{SCHEMA}.{ACCESS_TABLE}
    SET 
        customer_ids = {customer_ids_array},
        access_type = :access_type,
        effective_date = :effective_date,
        expiration_date = :expiration_date,
        notes = :notes,
        modified_by = :user,
        modified_at = CURRENT_TIMESTAMP()
    WHERE id = :rule_id
    """
    
    params = {
        'rule_id': rule_id,
        'access_type': access_type,
        'effective_date': effective_date,
        'expiration_date': expiration_date,
        'notes': notes,
        'user': user
    }
    
    row_count = execute_update(query, params)
    
    # Log audit
    log_action(
        action_type='UPDATE',
        object_type='GROUP_ACCESS',
        object_name=str(rule_id),
        new_value=f"customer_ids: {customer_ids}",
        notes=notes
    )
    
    return row_count > 0

def expire_access_rule(rule_id):
    """Set expiration date to today for a rule"""
    user = st.experimental_user.get('email', 'unknown')
    
    query = f"""
    UPDATE {CATALOG}.{SCHEMA}.{ACCESS_TABLE}
    SET 
        expiration_date = CURRENT_DATE(),
        modified_by = :user,
        modified_at = CURRENT_TIMESTAMP()
    WHERE id = :rule_id
    """
    
    row_count = execute_update(query, {'rule_id': rule_id, 'user': user})
    
    log_action(
        action_type='EXPIRE',
        object_type='GROUP_ACCESS',
        object_name=str(rule_id),
        new_value='expired',
        notes='Manually expired via UI'
    )
    
    return row_count > 0
Step 6: Tag Manager (utils/tag_manager.py)
pythonfrom utils.db_connection import execute_query, execute_update
from utils.audit_logger import log_action
import streamlit as st

def get_catalogs():
    """Get list of catalogs"""
    query = "SHOW CATALOGS"
    return execute_query(query)

def get_schemas(catalog):
    """Get list of schemas in a catalog"""
    query = f"SHOW SCHEMAS IN {catalog}"
    return execute_query(query)

def get_tables(catalog, schema):
    """Get list of tables in a schema"""
    query = f"SHOW TABLES IN {catalog}.{schema}"
    return execute_query(query)

def get_table_tags(catalog, schema, table):
    """Get tags applied to a table"""
    query = f"""
    SELECT tag_name, tag_value
    FROM system.information_schema.table_tags
    WHERE catalog_name = :catalog
      AND schema_name = :schema
      AND table_name = :table
    """
    return execute_query(query, {
        'catalog': catalog,
        'schema': schema,
        'table': table
    })

def get_column_tags(catalog, schema, table):
    """Get tags applied to columns in a table"""
    query = f"""
    SELECT column_name, tag_name, tag_value
    FROM system.information_schema.column_tags
    WHERE catalog_name = :catalog
      AND schema_name = :schema
      AND table_name = :table
    """
    return execute_query(query, {
        'catalog': catalog,
        'schema': schema,
        'table': table
    })

def apply_table_tag(catalog, schema, table, tag_name, tag_value):
    """Apply a tag to a table"""
    user = st.experimental_user.get('email', 'unknown')
    full_name = f"{catalog}.{schema}.{table}"
    
    query = f"ALTER TABLE {full_name} SET TAGS ('{tag_name}' = '{tag_value}')"
    
    try:
        execute_update(query)
        log_action(
            action_type='TAG_APPLY',
            object_type='TABLE_TAG',
            object_name=full_name,
            new_value=f"{tag_name}={tag_value}",
            notes=f"Applied by {user}"
        )
        return True, "Tag applied successfully"
    except Exception as e:
        return False, f"Error applying tag: {str(e)}"

def remove_table_tag(catalog, schema, table, tag_name):
    """Remove a tag from a table"""
    user = st.experimental_user.get('email', 'unknown')
    full_name = f"{catalog}.{schema}.{table}"
    
    query = f"ALTER TABLE {full_name} UNSET TAGS ('{tag_name}')"
    
    try:
        execute_update(query)
        log_action(
            action_type='TAG_REMOVE',
            object_type='TABLE_TAG',
            object_name=full_name,
            new_value=tag_name,
            notes=f"Removed by {user}"
        )
        return True, "Tag removed successfully"
    except Exception as e:
        return False, f"Error removing tag: {str(e)}"

def apply_column_tag(catalog, schema, table, column, tag_name, tag_value):
    """Apply a tag to a column"""
    user = st.experimental_user.get('email', 'unknown')
    full_name = f"{catalog}.{schema}.{table}.{column}"
    
    query = f"ALTER TABLE {catalog}.{schema}.{table} ALTER COLUMN {column} SET TAGS ('{tag_name}' = '{tag_value}')"
    
    try:
        execute_update(query)
        log_action(
            action_type='TAG_APPLY',
            object_type='COLUMN_TAG',
            object_name=full_name,
            new_value=f"{tag_name}={tag_value}",
            notes=f"Applied by {user}"
        )
        return True, "Tag applied successfully"
    except Exception as e:
        return False, f"Error applying tag: {str(e)}"
Step 7: Audit Logger (utils/audit_logger.py)
pythonfrom utils.db_connection import execute_update
from config.settings import CATALOG, SCHEMA, AUDIT_TABLE
import streamlit as st

def log_action(action_type, object_type, object_name, old_value=None, 
               new_value=None, notes=None):
    """Log an action to the audit table"""
    user = st.experimental_user.get('email', 'unknown')
    
    query = f"""
    INSERT INTO {CATALOG}.{SCHEMA}.{AUDIT_TABLE}
    (timestamp, user, action_type, object_type, object_name, old_value, new_value, notes)
    VALUES (
        CURRENT_TIMESTAMP(),
        :user,
        :action_type,
        :object_type,
        :object_name,
        :old_value,
        :new_value,
        :notes
    )
    """
    
    execute_update(query, {
        'user': user,
        'action_type': action_type,
        'object_type': object_type,
        'object_name': object_name,
        'old_value': old_value,
        'new_value': new_value,
        'notes': notes
    })

def get_audit_log(filters=None):
    """Retrieve audit log with optional filters"""
    query = f"""
    SELECT 
        timestamp,
        user,
        action_type,
        object_type,
        object_name,
        old_value,
        new_value,
        notes
    FROM {CATALOG}.{SCHEMA}.{AUDIT_TABLE}
    WHERE 1=1
    """
    
    params = {}
    if filters:
        if filters.get('start_date'):
            query += " AND timestamp >= :start_date"
            params['start_date'] = filters['start_date']
        if filters.get('end_date'):
            query += " AND timestamp <= :end_date"
            params['end_date'] = filters['end_date']
        if filters.get('user'):
            query += " AND user = :user"
            params['user'] = filters['user']
        if filters.get('action_type'):
            query += " AND action_type = :action_type"
            params['action_type'] = filters['action_type']
    
    query += " ORDER BY timestamp DESC LIMIT 1000"
    return execute_query(query, params)
Step 8: Main App (app.py)
pythonimport streamlit as st
from config.settings import APP_TITLE, PAGE_ICON, ADMIN_GROUP
from utils.db_connection import execute_query

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=PAGE_ICON,
    layout="wide"
)

def check_admin_access():
    """Check if current user is an admin"""
    try:
        query = f"SELECT is_account_group_member('{ADMIN_GROUP}') as is_admin"
        result = execute_query(query)
        return result[0]['is_admin']
    except:
        return False

def main():
    st.title(f"{PAGE_ICON} {APP_TITLE}")
    
    # Check admin access
    if not check_admin_access():
        st.error("⛔ Access Denied")
        st.warning(f"You must be a member of the '{ADMIN_GROUP}' group to use this application.")
        st.info("Please contact your administrator to request access.")
        st.stop()
    
    st.markdown("""
    Welcome to the Unity Catalog Access Management application.
    
    Use this tool to:
    - 🔑 Manage group access to customer data
    - 🏷️ Apply and manage Unity Catalog tags
    - 📊 View audit logs and access reports
    
    Select a page from the sidebar to get started.
    """)
    
    # Display quick stats
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Active Access Rules", "...")  # TODO: Query actual count
    
    with col2:
        st.metric("Tagged Tables", "...")  # TODO: Query actual count
    
    with col3:
        st.metric("Recent Changes (7d)", "...")  # TODO: Query actual count

if __name__ == "__main__":
    main()
Step 9: Group Access Page (pages/1_Group_Access.py)
pythonimport streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils.access_manager import (
    get_all_access_rules, add_access_rule, update_access_rule, expire_access_rule
)
from utils.validators import validate_customer_ids, validate_dates, validate_group_exists
from config.settings import APP_TITLE, PAGE_ICON

st.set_page_config(page_title=f"{APP_TITLE} - Group Access", page_icon=PAGE_ICON, layout="wide")

st.title("🔑 Group Access Management")

# Sidebar filters
st.sidebar.header("Filters")
group_filter = st.sidebar.text_input("Group Name")
status_filter = st.sidebar.selectbox("Status", ["all", "active", "expired"])

filters = {}
if group_filter:
    filters['group_name'] = group_filter
if status_filter != "all":
    filters['status'] = status_filter

# Load data
rules = get_all_access_rules(filters)
df = pd.DataFrame(rules)

# Display summary metrics
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Rules", len(df))
with col2:
    active_count = len(df[df['expiration_date'].isna() | (df['expiration_date'] > date.today())])
    st.metric("Active Rules", active_count)
with col3:
    expiring_soon = len(df[
        (df['expiration_date'].notna()) & 
        (df['expiration_date'] <= date.today() + timedelta(days=7))
    ])
    st.metric("Expiring Soon (7d)", expiring_soon)

# Add new rule button
if st.button("➕ Add New Rule", type="primary"):
    st.session_state['show_add_modal'] = True

# Display rules table
st.subheader("Current Access Rules")

if not df.empty:
    # Format customer_ids array for display
    df['customer_ids_display'] = df['customer_ids'].apply(
        lambda x: ', '.join(map(str, x[:5])) + f" (+{len(x)-5} more)" if len(x) > 5 else ', '.join(map(str, x))
    )
    
    display_df = df[[
        'id', 'group_name', 'customer_ids_display', 'access_type', 
        'effective_date', 'expiration_date', 'notes'
    ]]
    
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": "ID",
            "group_name": "Group",
            "customer_ids_display": "Customer IDs",
            "access_type": "Type",
            "effective_date": "Effective",
            "expiration_date": "Expires",
            "notes": "Notes"
        }
    )
    
    # Action buttons per row
    st.subheader("Actions")
    selected_id = st.selectbox("Select rule to modify:", df['id'].tolist())
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("✏️ Edit"):
            st.session_state['edit_rule_id'] = selected_id
            st.session_state['show_edit_modal'] = True
    with col2:
        if st.button("⏰ Expire"):
            if expire_access_rule(selected_id):
                st.success("Rule expired successfully!")
                st.rerun()
    with col3:
        if st.button("🗑️ Delete", type="secondary"):
            st.warning("Delete functionality requires confirmation - implement with care")
else:
    st.info("No access rules found matching the current filters.")

# Add rule modal
if st.session_state.get('show_add_modal'):
    with st.form("add_rule_form"):
        st.subheader("Add New Access Rule")
        
        group_name = st.text_input("Group Name*", placeholder="e.g., Group_A")
        customer_ids_input = st.text_input(
            "Customer IDs*", 
            placeholder="e.g., 100,101,102 or 100-110",
            help="Enter comma-separated IDs or ranges (e.g., 1,2,5-10)"
        )
        access_type = st.radio("Access Type*", ["INCLUDE", "EXCLUDE"])
        effective_date = st.date_input("Effective Date*", value=date.today())
        expiration_date = st.date_input("Expiration Date", value=None)
        notes = st.text_area("Notes")
        
        col1, col2 = st.columns(2)
        with col1:
            submit = st.form_submit_button("💾 Save", type="primary")
        with col2:
            cancel = st.form_submit_button("❌ Cancel")
        
        if submit:
            # Validate
            errors = []
            
            if not group_name:
                errors.append("Group name is required")
            elif not validate_group_exists(group_name):
                st.warning(f"⚠️ Group '{group_name}' may not exist in Databricks")
            
            is_valid, result = validate_customer_ids(customer_ids_input)
            if not is_valid:
                errors.append(result)
            else:
                customer_ids = result
            
            date_valid, date_error = validate_dates(effective_date, expiration_date)
            if not date_valid:
                errors.append(date_error)
            
            if errors:
                for error in errors:
                    st.error(error)
            else:
                # Save
                if add_access_rule(
                    group_name, customer_ids, access_type, 
                    effective_date, expiration_date, notes
                ):
                    st.success("✅ Access rule added successfully!")
                    st.session_state['show_add_modal'] = False
                    st.rerun()
                else:
                    st.error("Failed to add access rule")
        
        if cancel:
            st.session_state['show_add_modal'] = False
            st.rerun()

# Similar edit modal would go here (omitted for brevity, follows same pattern)
Step 10: Tag Management Page (pages/2_Tag_Management.py)
pythonimport streamlit as st
from utils.tag_manager import (
    get_catalogs, get_schemas, get_tables, 
    get_table_tags, get_column_tags,
    apply_table_tag, remove_table_tag, apply_column_tag
)
from config.settings import APP_TITLE, PAGE_ICON

st.set_page_config(page_title=f"{APP_TITLE} - Tag Management", page_icon=PAGE_ICON, layout="wide")

st.title("🏷️ Tag Management")

# Sidebar: Browse hierarchy
st.sidebar.header("Browse Objects")

catalogs = get_catalogs()
catalog_names = [c['catalog'] for c in catalogs]
selected_catalog = st.sidebar.selectbox("Catalog", catalog_names)

if selected_catalog:
    schemas = get_schemas(selected_catalog)
    schema_names = [s['databaseName'] for s in schemas]
    selected_schema = st.sidebar.selectbox("Schema", schema_names)
    
    if selected_schema:
        tables = get_tables(selected_catalog, selected_schema)
        table_names = [t['tableName'] for t in tables]
        selected_table = st.sidebar.selectbox("Table", table_names)
    else:
        selected_table = None
else:
    selected_schema = None
    selected_table = None

# Main area: Tag operations
if selected_table:
    st.subheader(f"Tags for {selected_catalog}.{selected_schema}.{selected_table}")
    
    # Show current table tags
    st.markdown("### Table Tags")
    table_tags = get_table_tags(selected_catalog, selected_schema, selected_table)
    if table_tags:
        for tag in table_tags:
            col1, col2, col3 = st.columns([3, 3, 1])
            with col1:
                st.text(tag['tag_name'])
            with col2:
                st.text(tag['tag_value'])
            with col3:
                if st.button("🗑️", key=f"remove_{tag['tag_name']}"):
                    success, msg = remove_table_tag(
                        selected_catalog, selected_schema, selected_table, tag['tag_name']
                    )
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
    else:
        st.info("No tags applied to this table")
    
    # Add new table tag
    st.markdown("### Apply New Tag")
    with st.form("apply_table_tag"):
        tag_name = st.text_input("Tag Name", placeholder="e.g., secure_contracts")
        tag_value = st.text_input("Tag Value", placeholder="e.g., pii")
        
        if st.form_submit_button("Apply Tag", type="primary"):
            if tag_name and tag_value:
                success, msg = apply_table_tag(
                    selected_catalog, selected_schema, selected_table, 
                    tag_name, tag_value
                )
                if success:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
            else:
                st.error("Both tag name and value are required")
    
    # Show column tags
    st.markdown("### Column Tags")
    column_tags = get_column_tags(selected_catalog, selected_schema, selected_table)
    if column_tags:
        import pandas as pd
        df = pd.DataFrame(column_tags)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No column tags found")
    
    # Add column tag (simplified - full implementation would list columns first)
    with st.expander("Apply Column Tag"):
        with st.form("apply_column_tag"):
            column_name = st.text_input("Column Name")
            col_tag_name = st.text_input("Tag Name")
            col_tag_value = st.text_input("Tag Value")
            
            if st.form_submit_button("Apply"):
                if column_name and col_tag_name and col_tag_value:
                    success, msg = apply_column_tag(
                        selected_catalog, selected_schema, selected_table,
                        column_name, col_tag_name, col_tag_value
                    )
                    if success:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)

else:
    st.info("👈 Select a table from the sidebar to manage tags")
Step 11: Audit Reports Page (pages/3_Audit_Reports.py)
pythonimport streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils.audit_logger import get_audit_log
from config.settings import APP_TITLE, PAGE_ICON

st.set_page_config(page_title=f"{APP_TITLE} - Audit", page_icon=PAGE_ICON, layout="wide")

st.title("📊 Audit & Reports")

# Sidebar filters
st.sidebar.header("Filters")
start_date = st.sidebar.date_input("Start Date", value=date.today() - timedelta(days=30))
end_date = st.sidebar.date_input("End Date", value=date.today())
action_filter = st.sidebar.multiselect(
    "Action Type", 
    ["INSERT", "UPDATE", "DELETE", "EXPIRE", "TAG_APPLY", "TAG_REMOVE"]
)
user_filter = st.sidebar.text_input("User Email")

filters = {
    'start_date': start_date,
    'end_date': end_date
}
if action_filter:
    filters['action_type'] = action_filter
if user_filter:
    filters['user'] = user_filter

# Tabs
tab1, tab2, tab3 = st.tabs(["📜 Change History", "🗺️ Access Matrix", "🏷️ Tag Coverage"])

with tab1:
    st.subheader("Change History")
    
    audit_data = get_audit_log(filters)
    if audit_data:
        df = pd.DataFrame(audit_data)
        
        # Summary metrics
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Changes", len(df))
        with col2:
            unique_users = df['user'].nunique()
            st.metric("Unique Users", unique_users)
        with col3:
            most_common = df['action_type'].mode()[0] if not df.empty else "N/A"
            st.metric("Most Common Action", most_common)
        
        # Display table
        st.dataframe(
            df,
            use_container_width=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm:ss"),
                "user": "User",
                "action_type": "Action",
                "object_type": "Object Type",
                "object_name": "Object",
                "old_value": "Old Value",
                "new_value": "New Value",
                "notes": "Notes"
            }
        )
        
        # Export button
        csv = df.to_csv(index=False)
        st.download_button(
            "📥 Export to CSV",
            csv,
            "audit_log.csv",
            "text/csv",
            key='download-csv'
        )
    else:
        st.info("No audit records found for the selected filters")

with tab2:
    st.subheader("Access Matrix")
    st.info("🚧 Access matrix visualization coming soon - will show group × customer heatmap")
    # TODO: Implement heatmap using plotly

with tab3:
    st.subheader("Tag Coverage")
    st.info("🚧 Tag coverage report coming soon - will show tagged vs untagged tables")
    # TODO: Query system.information_schema for tag statistics
Step 12: Requirements File
txtstreamlit>=1.30.0
databricks-sql-connector>=3.0.0
pandas>=2.0.0
plotly>=5.17.0
Step 13: Deploy to Databricks
Create the necessary tables first:
sql-- Create access control table
CREATE TABLE IF NOT EXISTS security.access_control.group_customer_access (
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
);

-- Create audit log table
CREATE TABLE IF NOT EXISTS security.access_control.access_audit_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY,
  timestamp TIMESTAMP,
  user STRING,
  action_type STRING,
  object_type STRING,
  object_name STRING,
  old_value STRING,
  new_value STRING,
  notes STRING
);
