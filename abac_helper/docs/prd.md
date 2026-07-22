9:45 AMPRD: Unity Catalog Access Management AppOverview
A Databricks Lakehouse App built with Streamlit that provides a user-friendly interface for managing Row Level Security policies and Unity Catalog governed tags. The app enables security administrators to manage group-to-customer mappings and tag assignments without writing SQL.Target Users

Security Administrators
Data Governance Teams
Compliance Officers
Core Features1. Group Access Management
Purpose: Manage which customer IDs each security group can accessFeatures:

View current group-to-customer mappings in a table
Add new group access rules
Edit existing rules (customer IDs, dates, access type)
Delete/expire access rules
Bulk operations (add multiple customer IDs at once)
Search and filter by group name or customer ID
Data Model: Works with security.group_customer_access table
sqlCREATE TABLE security.group_customer_access (
  id BIGINT GENERATED ALWAYS AS IDENTITY,
  group_name STRING,
  customer_ids ARRAY<INT>,
  access_type STRING,  -- 'INCLUDE' or 'EXCLUDE'
  effective_date DATE,
  expiration_date DATE,
  notes STRING,
  created_by STRING,
  created_at TIMESTAMP,
  modified_by STRING,
  modified_at TIMESTAMP
);2. Tag Management
Purpose: Apply and manage Unity Catalog governed tags on tables and columnsFeatures:

Browse catalog/schema/table hierarchy
View current tag assignments on tables/columns
Apply governed tags to tables or columns
Remove tag assignments
Bulk tag operations
Search for tagged objects
Unity Catalog Operations: Uses SQL commands like:

ALTER TABLE ... SET TAGS
ALTER TABLE ... UNSET TAGS
Query system.information_schema.table_tags and column_tags
3. Audit & Reporting
Purpose: Track changes and visualize current access stateFeatures:

Change history log (who changed what and when)
Access matrix view (groups × customers)
Tag coverage report (which tables/columns are tagged)
Export reports to CSV
Filter by date range, group, or user
Data Model: Audit table
sqlCREATE TABLE security.access_audit_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY,
  timestamp TIMESTAMP,
  user STRING,
  action_type STRING,  -- 'INSERT', 'UPDATE', 'DELETE', 'TAG_APPLY', 'TAG_REMOVE'
  object_type STRING,  -- 'GROUP_ACCESS', 'TABLE_TAG', 'COLUMN_TAG'
  object_name STRING,
  old_value STRING,
  new_value STRING,
  notes STRING
);4. Validation & Safety
Features:

Validate group names exist in Databricks account
Prevent deletion of active rules (must expire them first)
Confirmation dialogs for destructive operations
Preview changes before applying
Rollback capability (via audit log)
Technical ArchitectureTechnology Stack

Framework: Streamlit
Deployment: Databricks Lakehouse Apps
Database: Unity Catalog (Databricks SQL)
Authentication: Databricks workspace authentication (inherited)
Authorization: App checks if user is in admin group
App Structure
app/
├── app.py                      # Main Streamlit app entry point
├── requirements.txt            # Python dependencies
├── pages/
│   ├── 1_Group_Access.py      # Group-customer mapping management
│   ├── 2_Tag_Management.py    # UC tag management
│   └── 3_Audit_Reports.py     # Audit logs and reports
├── utils/
│   ├── db_connection.py       # Databricks SQL connection helper
│   ├── access_manager.py      # CRUD operations for group access
│   ├── tag_manager.py         # UC tag operations
│   ├── audit_logger.py        # Audit logging functions
│   └── validators.py          # Input validation and safety checks
└── config/
    └── settings.py            # App configuration (catalog, schema names)Key Dependencies
txtstreamlit>=1.28.0
databricks-sql-connector>=3.0.0
pandas>=2.0.0
plotly>=5.17.0Page SpecificationsPage 1: Group Access ManagementLayout:

Sidebar: Filters (group name, customer ID, active/expired/all)
Main Area:

Summary metrics (total groups, total rules, expiring soon)
Data table with current mappings (sortable, searchable)
Action buttons per row (Edit, Expire, Delete)
"Add New Rule" button (opens modal)


Add/Edit Rule Modal:

Group name (dropdown of existing groups + free text)
Customer IDs (multi-input: comma-separated or range like "100-150")
Access type (radio: INCLUDE/EXCLUDE)
Effective date (date picker, default today)
Expiration date (date picker, optional)
Notes (text area)
Preview affected customer count
Save/Cancel buttons
Bulk Operations:

Upload CSV with columns: group_name, customer_ids, access_type, effective_date, expiration_date, notes
Preview changes before committing
Validation and error reporting
Page 2: Tag ManagementLayout:

Sidebar:

Catalog/Schema/Table browser (tree view)
Tag filter (show only tagged objects)


Main Area:

Selected object info (type, full name, current tags)
Tag operations panel:

Available governed tags (dropdown)
Tag value (text input)
Apply/Remove buttons


Current tags table (tag name, value, applied date, applied by)


Features:

Search for tables/columns by name
Bulk tag application (select multiple tables)
Tag templates (save common tag configurations)
Page 3: Audit & ReportsLayout:

Sidebar:

Date range filter
Action type filter
User filter
Object type filter


Main Area:

Tab 1: Change History

Timeline of changes (table view)
Expandable details per change
Export to CSV


Tab 2: Access Matrix

Heatmap: Groups (rows) × Customer ID ranges (columns)
Color coding for access levels
Drill-down to details


Tab 3: Tag Coverage

Tables/columns with/without tags
Tag usage statistics
Compliance metrics




User WorkflowsWorkflow 1: Grant New Customer Access

Admin navigates to Group Access page
Clicks "Add New Rule"
Selects/enters group name (e.g., "Group_A")
Enters customer IDs (e.g., "200,201,202" or "200-210")
Sets access type to INCLUDE
Sets effective date (default today)
Adds note: "Q1 2026 customer expansion"
Clicks Save
System validates, logs audit entry, updates table
Success message shows, table refreshes
Workflow 2: Apply Governed Tag

Admin navigates to Tag Management page
Browses to table (e.g., main.sales.transactions)
Selects column (e.g., customer_id)
Chooses tag from dropdown (e.g., secure_contracts)
Enters tag value (e.g., pii)
Clicks Apply
System executes ALTER TABLE ... SET TAGS, logs audit
Success message, tag appears in current tags list
Workflow 3: Bulk Import Customer Access

Admin downloads CSV template
Fills in: group_name, customer_ids (array), access_type, dates, notes
Navigates to Group Access page, clicks "Bulk Upload"
Uploads CSV
System validates all rows (checks groups exist, IDs are valid)
Preview table shows what will be inserted
Admin reviews, clicks Confirm
System inserts all rows, logs bulk audit entry
Summary shows success/failure counts
Security & PermissionsApp-Level Authorization

App checks if current_user() is member of access_admin group
Non-admins see read-only view or error message
All write operations check permissions before executing
Database Permissions
The service principal or user running the app needs:

SELECT on security.group_customer_access
INSERT, UPDATE, DELETE on security.group_customer_access
SELECT on system.information_schema.table_tags, column_tags
USE CATALOG, USE SCHEMA on target catalogs
MODIFY on tables where tags will be applied
SELECT, INSERT on security.access_audit_log
Audit Trail

Every operation logs to access_audit_log
Captures: timestamp, user, action, object, old/new values
Immutable log (no deletes allowed)
Error HandlingValidation Errors

Group doesn't exist in Databricks → Warning with option to create anyway
Customer IDs not integers → Highlight invalid entries
Date conflicts (expiration before effective) → Block save with message
Duplicate rules → Warn and ask to update existing
Database Errors

Connection failures → Retry with exponential backoff, show status
SQL errors → Log full error, show user-friendly message
Permission errors → Show specific missing permission
User Guidance

Tooltips on all form fields
Help text for each page
Example values in placeholders
Inline validation with green checkmarks / red X's
Performance ConsiderationsCaching

Cache group list (refresh every 15 minutes)
Cache catalog/schema/table hierarchy (refresh on demand)
Cache current tag assignments (refresh after changes)
Pagination

Group access table: paginate after 100 rows
Audit log: paginate after 500 rows
Tag browser: lazy load tables in schema
Async Operations

Bulk uploads run in background with progress bar
Large tag operations show spinner
Allow cancellation of long-running ops
Future Enhancements (Out of Scope for V1)
Approval workflows (pending changes require approval)
Integration with Slack/email for notifications
Scheduled reports
Row-level tag support
Customer ID auto-complete based on actual data
Access certification campaigns (periodic review)
Integration with external IdPs to show group members
Success Metrics
Time to add new customer access: < 2 minutes (vs. 10+ minutes with SQL)
Tag application errors reduced by 80%
100% of access changes captured in audit log
Self-service adoption: 80% of access changes done via app vs. SQL