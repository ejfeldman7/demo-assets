"""
scim.py — User search and group resolution via the SCIM API.

Key design notes:
- The SCIM API returns an 'id' field (numeric string) that is the principal's
  internal workspace ID.  The Permissions API ACL entries use this same 'id'
  as the 'user_name' or 'group_name' field — NOT the human-readable userName/email.
  We therefore return both the SCIM id AND the human-readable userName so that
  downstream resolvers can match against either.
- Group nesting: we resolve one level of nesting.  If a group's member is itself
  a group we mark it "Nested (2+ levels)" rather than recursing further.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from databricks.sdk.service.iam import Group

from src.auth import get_sp_client

log = logging.getLogger(__name__)


@dataclass
class UserInfo:
    """Lightweight struct returned by search_users."""
    scim_id: str           # SCIM internal id — used when matching against ACLs
    user_name: str         # login name / email
    display_name: str
    active: bool
    emails: list[str] = field(default_factory=list)


@dataclass
class GroupInfo:
    """Resolved group membership entry."""
    scim_id: str
    display_name: str
    nesting_level: str   # "direct", "nested-1", or "Nested (2+ levels)"


def list_all_users(max_count: int = 500) -> list[UserInfo]:
    """Return all workspace users sorted by display name, up to max_count.

    Used to populate the dropdown selector.  Results are cached by the caller
    (app.py) with @st.cache_data.
    """
    w = get_sp_client()
    results: list[UserInfo] = []
    try:
        for u in w.users.list(count=max_count):
            emails = [e.value for e in (u.emails or []) if e.value]
            results.append(
                UserInfo(
                    scim_id=u.id or "",
                    user_name=u.user_name or "",
                    display_name=u.display_name or u.user_name or "",
                    active=u.active if u.active is not None else True,
                    emails=emails,
                )
            )
    except Exception as exc:
        log.warning("Failed to list all users: %s", exc)
    # Sort alphabetically by display name client-side
    results.sort(key=lambda u: u.display_name.lower())
    return results


def search_users(query: str) -> list[UserInfo]:
    """Return workspace users whose displayName or emails match *query*.

    Uses SCIM filter syntax supported by the SDK.  Returns at most 50 results.
    """
    w = get_sp_client()
    try:
        # SCIM filter — case-insensitive substring match on displayName or userName
        scim_filter = (
            f'displayName co "{query}" or userName co "{query}"'
        )
        results: list[UserInfo] = []
        for u in w.users.list(filter=scim_filter, count=50):
            emails = [e.value for e in (u.emails or []) if e.value]
            results.append(
                UserInfo(
                    scim_id=u.id or "",
                    user_name=u.user_name or "",
                    display_name=u.display_name or u.user_name or "",
                    active=u.active if u.active is not None else True,
                    emails=emails,
                )
            )
        return results
    except Exception as exc:
        log.warning("SCIM user search failed: %s", exc)
        return []


def get_user_groups(user_scim_id: str) -> list[GroupInfo]:
    """Return all groups (direct + one level of nesting) that contain the user.

    Strategy:
    1. List all workspace groups and filter to those whose 'members' include the user id.
    2. For each direct group, list its members; if any member is itself a group, check
       if that parent group also contains the user — flag those as "nested-1".
       Members that are groups whose members are further groups are flagged as
       "Nested (2+ levels)" without further traversal.
    """
    w = get_sp_client()

    try:
        all_groups: list[Group] = list(w.groups.list(attributes="id,displayName,members"))
    except Exception as exc:
        log.warning("Failed to list groups: %s", exc)
        return []

    # Build lookup: group scim_id -> GroupInfo
    group_by_id: dict[str, Group] = {g.id: g for g in all_groups if g.id}

    # Find groups that directly contain this user
    direct_group_ids: set[str] = set()
    for grp in all_groups:
        for member in grp.members or []:
            if member.value == user_scim_id:
                direct_group_ids.add(grp.id)
                break

    result: list[GroupInfo] = []

    for gid in direct_group_ids:
        grp = group_by_id.get(gid)
        if not grp:
            continue
        result.append(GroupInfo(
            scim_id=gid,
            display_name=grp.display_name or gid,
            nesting_level="direct",
        ))

    # One level of nesting: groups that contain a direct group
    for outer_grp in all_groups:
        if outer_grp.id in direct_group_ids:
            continue  # already captured as direct
        for member in outer_grp.members or []:
            if member.value in direct_group_ids:
                result.append(GroupInfo(
                    scim_id=outer_grp.id,
                    display_name=outer_grp.display_name or outer_grp.id,
                    nesting_level="nested-1",
                ))
                break

    return result


# ---------------------------------------------------------------------------
# Full-workspace membership graph (for the nightly snapshot job)
# ---------------------------------------------------------------------------

@dataclass
class MembershipGraph:
    """Flattened identity graph built once from a single SCIM group listing.

    - group_name_by_id / group_id_by_name: resolve the opaque group UUIDs that
      appear as `grantee` in system.information_schema.*_privileges.
    - transitive_groups[user_id]: the FULL set of group ids a user belongs to,
      directly OR through any depth of group-in-group nesting (the old
      get_user_groups capped this at one level).
    - user_name_by_id: SCIM id -> login name, so grants keyed by either form resolve.
    """
    group_name_by_id: dict[str, str]
    group_id_by_name: dict[str, str]
    transitive_groups: dict[str, set[str]]   # principal id -> {group_id, ...}
    user_name_by_id: dict[str, str]


def build_membership_graph() -> MembershipGraph:
    """List every group once and compute transitive membership for all principals.

    Complexity note: the SDK listing pulls all groups with their direct members;
    we then walk the group->group edges to full depth. A visited-set guards against
    cyclic group definitions (Databricks permits them; unbounded recursion would hang).
    """
    w = get_sp_client()
    try:
        all_groups: list[Group] = list(w.groups.list(attributes="id,displayName,members"))
    except Exception as exc:
        log.warning("Failed to list groups for membership graph: %s", exc)
        return MembershipGraph({}, {}, {}, {})

    group_name_by_id: dict[str, str] = {}
    group_id_by_name: dict[str, str] = {}
    # direct edges: group_id -> set of member principal ids (users AND sub-groups)
    direct_members: dict[str, set[str]] = {}

    for g in all_groups:
        if not g.id:
            continue
        name = g.display_name or g.id
        group_name_by_id[g.id] = name
        group_id_by_name[name] = g.id
        direct_members[g.id] = {m.value for m in (g.members or []) if m.value}

    group_ids = set(group_name_by_id)

    def _ancestor_groups(principal_id: str) -> set[str]:
        """All groups that (transitively) contain principal_id."""
        ancestors: set[str] = set()
        # groups directly containing this principal
        frontier = [gid for gid, members in direct_members.items() if principal_id in members]
        while frontier:
            gid = frontier.pop()
            if gid in ancestors:
                continue          # cycle / diamond guard
            ancestors.add(gid)
            # a group can itself be a member of other (parent) groups
            frontier.extend(
                pid for pid, members in direct_members.items()
                if gid in members and pid not in ancestors
            )
        return ancestors

    # Every principal that appears as a member anywhere (users + groups) gets resolved.
    all_member_ids: set[str] = set()
    for members in direct_members.values():
        all_member_ids.update(members)

    transitive_groups = {pid: _ancestor_groups(pid) for pid in all_member_ids}

    # user id -> login name (for principals that are users, not groups)
    user_name_by_id: dict[str, str] = {}
    try:
        for u in w.users.list(attributes="id,userName", count=500):
            if u.id:
                user_name_by_id[u.id] = u.user_name or u.id
    except Exception as exc:
        log.warning("Failed to list users for membership graph: %s", exc)

    return MembershipGraph(
        group_name_by_id=group_name_by_id,
        group_id_by_name=group_id_by_name,
        transitive_groups=transitive_groups,
        user_name_by_id=user_name_by_id,
    )
