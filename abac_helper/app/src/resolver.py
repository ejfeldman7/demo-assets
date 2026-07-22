"""
resolver.py — Direct vs. inherited grant resolution logic.

Given a raw ACL plus a UserInfo and their list of GroupInfo memberships, this
module determines whether a permission applies:
  - Directly to the user (Grant Type = "Direct")
  - Through one or more groups (Grant Type = "Inherited", Inherited Via = group names)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.scim import GroupInfo, UserInfo


@dataclass
class ResolvedPermission:
    object_type: str
    object_name: str
    permission_level: str
    grant_type: str           # "Direct" or "Inherited"
    inherited_via: str        # comma-sep group display names, or ""


def resolve_acl(
    object_type: str,
    object_name: str,
    acl_entries: list[dict],
    user: UserInfo,
    user_groups: list[GroupInfo],
) -> list[ResolvedPermission]:
    """Resolve an ACL to a list of ResolvedPermission records for the given user."""
    group_id_to_name: dict[str, str] = {g.scim_id: g.display_name for g in user_groups}
    group_name_to_id: dict[str, str] = {g.display_name: g.scim_id for g in user_groups}

    user_identifiers: set[str] = {
        user.scim_id,
        user.user_name,
        *user.emails,
    }
    group_identifiers: dict[str, str] = {}
    for g in user_groups:
        group_identifiers[g.scim_id] = g.display_name
        group_identifiers[g.display_name] = g.display_name

    seen_permissions: dict[str, ResolvedPermission] = {}

    for entry in acl_entries:
        principal = entry.get("principal", "")
        permission = entry.get("permission", "")
        if not principal or not permission:
            continue

        if principal in user_identifiers:
            key = permission
            if key not in seen_permissions or seen_permissions[key].grant_type == "Inherited":
                seen_permissions[key] = ResolvedPermission(
                    object_type=object_type,
                    object_name=object_name,
                    permission_level=permission,
                    grant_type="Direct",
                    inherited_via="",
                )
        elif principal in group_identifiers:
            key = permission
            group_display = group_identifiers[principal]
            if key not in seen_permissions:
                seen_permissions[key] = ResolvedPermission(
                    object_type=object_type,
                    object_name=object_name,
                    permission_level=permission,
                    grant_type="Inherited",
                    inherited_via=group_display,
                )
            elif seen_permissions[key].grant_type == "Inherited":
                existing = seen_permissions[key].inherited_via
                names = set(existing.split(", ")) if existing else set()
                names.add(group_display)
                seen_permissions[key].inherited_via = ", ".join(sorted(names))

    return list(seen_permissions.values())
