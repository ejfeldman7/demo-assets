"""
Permission Explorer page — search for any workspace user and see their full
permissions across all object types (Direct and Inherited).

Backed by the nightly permission snapshot in Lakebase (built by
jobs/build_permission_snapshot.py). A user lookup is now a single indexed query
against pre-computed, fully-resolved ACL + membership tables — no per-object
Permissions/SCIM API fan-out at request time. This fixes both:
  - latency (thousands of live API calls -> one Lakebase query), and
  - completeness (all UC levels incl. table/volume, all workspace objects, and
    FULL transitive group nesting are captured by the snapshot).

Data is as fresh as the last successful snapshot (shown in the header). Use the
job schedule for nightly refresh, or trigger the job on demand.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import streamlit as st
from databricks.sdk import WorkspaceClient

from src import lakebase
from src.scim import UserInfo, list_all_users

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom CSS for group badges
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .group-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        background: #1E3A5F;
        color: #90CAF9;
        font-size: 0.75rem;
        margin: 2px 2px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Cached resources
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _ws_client() -> WorkspaceClient:
    return WorkspaceClient()


@st.cache_data(ttl=300, show_spinner=False)
def _load_all_users() -> list[UserInfo]:
    return list_all_users()


# ---------------------------------------------------------------------------
# Lakebase reads
# ---------------------------------------------------------------------------

def _fetch_snapshot_for_user(user: UserInfo) -> tuple[pd.DataFrame, list[dict], Optional[object]]:
    """Return (permissions_df, group_detail_rows, last_snapshot_ts) for a user."""
    w = _ws_client()
    with lakebase.connect(w) as conn:
        last_ts = lakebase.last_snapshot_ts(conn)

        # 1. the user's transitive groups (ids + names)
        group_ids, group_detail = lakebase.fetch_user_group_identifiers(
            conn, user.scim_id, user.user_name
        )

        # 2. everything the user can reach: own identifiers + all group identifiers
        identifiers = {user.scim_id, user.user_name, *user.emails} | group_ids
        identifiers.discard("")
        acl_rows = lakebase.fetch_user_permissions(conn, list(identifiers))

    df = _build_dataframe(acl_rows, user, group_ids)
    return df, group_detail, last_ts


def _build_dataframe(acl_rows: list[dict], user: UserInfo, group_ids: set[str]) -> pd.DataFrame:
    cols = ["Object Type", "Object Name", "Permission Level", "Grant Type", "Inherited Via"]
    if not acl_rows:
        return pd.DataFrame(columns=cols)

    user_ids = {user.scim_id, user.user_name, *user.emails}
    rows = []
    for r in acl_rows:
        grantee = r.get("grantee", "")
        is_direct = grantee in user_ids
        rows.append({
            "Object Type": r.get("object_type", ""),
            "Object Name": r.get("object_name", ""),
            "Permission Level": r.get("permission", ""),
            "Grant Type": "Direct" if is_direct else "Inherited",
            "Inherited Via": "" if is_direct else grantee,
        })
    df = pd.DataFrame(rows)
    # A permission can be granted both directly and via one or more groups; collapse
    # to one row per (object, permission): a direct grant wins; otherwise merge the
    # inheriting group names. Vectorised aggregation (no groupby.apply) so it works
    # across pandas versions.
    key = ["Object Type", "Object Name", "Permission Level"]
    agg = df.groupby(key, as_index=False).agg(
        _has_direct=("Grant Type", lambda s: (s == "Direct").any()),
        _vias=("Inherited Via", lambda s: sorted({v for v in s if v})),
    )
    agg["Grant Type"] = agg["_has_direct"].map(lambda d: "Direct" if d else "Inherited")
    agg["Inherited Via"] = agg.apply(
        lambda r: "" if r["_has_direct"] else ", ".join(r["_vias"]), axis=1
    )
    return agg.sort_values(key)[cols]


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

TAB_FILTERS: dict[str, set[str]] = {
    "Unity Catalog": {"Catalog", "Schema", "Table", "Volume", "Function", "Connection",
                       "External Location", "Storage Credential", "Metastore"},
    "Compute": {"Cluster", "Cluster Policy"},
    "Workflows": {"Job", "Pipeline"},
    "SQL & BI": {"SQL Warehouse", "Dashboard"},
    "Apps & Genies": {"App", "Genie Space"},
}


def _render_group_badges(group_detail: list[dict]) -> None:
    if not group_detail:
        st.caption("No group memberships found in the latest snapshot.")
        return
    html = [
        f'<span class="group-badge">{g.get("group_name") or g.get("group_id")}</span>'
        for g in group_detail
    ]
    st.markdown(" ".join(html), unsafe_allow_html=True)


def _render_permissions_tab(df: pd.DataFrame, label: str) -> None:
    if df.empty:
        st.info(f"No {label} permissions found for this user.")
        return
    st.caption(f"**{len(df)}** permission(s) across **{df['Object Type'].nunique()}** object type(s)")
    filter_text = st.text_input(
        "Filter rows", key=f"perm_filter_{label}",
        placeholder="Search any column...", label_visibility="collapsed",
    )
    if filter_text:
        mask = df.apply(lambda row: row.astype(str).str.contains(filter_text, case=False).any(), axis=1)
        df = df[mask]
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
_PREFIX = "perm_explorer_"
for key, default in {
    f"{_PREFIX}last_scim_id": None,
    f"{_PREFIX}selected_user": None,
    f"{_PREFIX}group_detail": [],
    f"{_PREFIX}permissions_df": None,
    f"{_PREFIX}snapshot_ts": None,
    f"{_PREFIX}error": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------

st.title("Permission Explorer")
st.caption("Search for any workspace user and see their full permissions — Direct and Inherited.")

# Freshness badge
_ts = st.session_state[f"{_PREFIX}snapshot_ts"]
if _ts is not None:
    st.caption(f"Snapshot last refreshed: **{_ts:%Y-%m-%d %H:%M UTC}**")

left_col, right_col = st.columns([1, 2])

# --- LEFT PANEL ---
with left_col:
    st.subheader("Find User")
    all_users: list[UserInfo] = _load_all_users()

    if not all_users:
        st.warning("Could not load workspace users. Check SCIM API permissions.")
    else:
        labels = [f"{u.display_name} ({u.user_name})" for u in all_users]
        selected_idx = st.selectbox(
            "Search by name or email",
            options=range(len(all_users)),
            format_func=lambda i: labels[i],
            index=None,
            placeholder="Type to search...",
        )
        new_scim_id = all_users[selected_idx].scim_id if selected_idx is not None else None
        if new_scim_id != st.session_state[f"{_PREFIX}last_scim_id"]:
            st.session_state[f"{_PREFIX}last_scim_id"] = new_scim_id
            st.session_state[f"{_PREFIX}permissions_df"] = None
            st.session_state[f"{_PREFIX}error"] = None
            if new_scim_id is not None:
                u = all_users[selected_idx]
                st.session_state[f"{_PREFIX}selected_user"] = u
                with st.spinner("Loading permissions from snapshot..."):
                    try:
                        df, group_detail, last_ts = _fetch_snapshot_for_user(u)
                        st.session_state[f"{_PREFIX}permissions_df"] = df
                        st.session_state[f"{_PREFIX}group_detail"] = group_detail
                        st.session_state[f"{_PREFIX}snapshot_ts"] = last_ts
                    except Exception as exc:
                        st.session_state[f"{_PREFIX}error"] = str(exc)
                st.rerun()
            else:
                st.session_state[f"{_PREFIX}selected_user"] = None
                st.session_state[f"{_PREFIX}group_detail"] = []

    if st.session_state[f"{_PREFIX}selected_user"]:
        st.divider()
        st.markdown("**Group Memberships** (transitive)")
        _render_group_badges(st.session_state[f"{_PREFIX}group_detail"])

# --- RIGHT PANEL ---
with right_col:
    selected: Optional[UserInfo] = st.session_state[f"{_PREFIX}selected_user"]
    if not selected:
        st.info("Select a user from the left panel to explore their permissions.")
        st.stop()

    if st.session_state[f"{_PREFIX}error"]:
        st.error(f"Could not load snapshot: {st.session_state[f'{_PREFIX}error']}")
        st.caption("Has the snapshot job run yet? Run `python -m jobs.build_permission_snapshot`.")
        st.stop()

    status_badge = "Active" if selected.active else "Inactive"
    st.subheader(f"{selected.display_name}  ({status_badge})")
    st.markdown(
        f"**Email:** `{selected.user_name}`  &nbsp;|&nbsp; **User ID:** `{selected.scim_id}`",
        unsafe_allow_html=True,
    )

    df_all: pd.DataFrame = st.session_state[f"{_PREFIX}permissions_df"]
    if df_all is None:
        st.stop()

    if not df_all.empty:
        csv_bytes = df_all.to_csv(index=False).encode()
        st.download_button(
            label="Export CSV", data=csv_bytes,
            file_name=f"{selected.user_name}_permissions.csv", mime="text/csv",
            key="perm_export_csv",
        )

    tab_labels = ["All Permissions"] + list(TAB_FILTERS.keys())
    tabs = st.tabs(tab_labels)
    with tabs[0]:
        _render_permissions_tab(df_all, "All")
    for i, (tab_label, obj_types) in enumerate(TAB_FILTERS.items(), start=1):
        with tabs[i]:
            tab_df = df_all[df_all["Object Type"].isin(obj_types)] if not df_all.empty else df_all
            _render_permissions_tab(tab_df, tab_label)
