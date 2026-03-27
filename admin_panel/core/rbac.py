"""
Role-Based Access Control (RBAC) for admin panel.

Roles:
  - owner:  Full access (manage admins, settings, all CRUD)
  - editor: Lead/pro management, schedule editing
  - viewer: Read-only dashboard access
"""

from enum import Enum


class AdminRole(str, Enum):
    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


# Permission matrix
_PERMISSIONS = {
    AdminRole.OWNER: {
        "view_dashboard", "view_leads", "view_pros", "view_schedule", "view_settings",
        "edit_leads", "edit_pros", "edit_schedule", "edit_settings",
        "manage_admins", "view_audit_log", "delete_leads", "delete_pros",
        "export_data", "delete_user_data",
    },
    AdminRole.EDITOR: {
        "view_dashboard", "view_leads", "view_pros", "view_schedule", "view_settings",
        "edit_leads", "edit_pros", "edit_schedule",
        "export_data",
    },
    AdminRole.VIEWER: {
        "view_dashboard", "view_leads", "view_pros", "view_schedule",
    },
}


def has_permission(role: str, permission: str) -> bool:
    """Check if a role has a specific permission."""
    try:
        admin_role = AdminRole(role)
    except ValueError:
        return False
    return permission in _PERMISSIONS.get(admin_role, set())


def can_edit(role: str) -> bool:
    """Check if role can edit any resources."""
    return has_permission(role, "edit_leads")


def can_manage_admins(role: str) -> bool:
    """Check if role can manage admin users."""
    return has_permission(role, "manage_admins")


def can_edit_settings(role: str) -> bool:
    """Check if role can edit system settings."""
    return has_permission(role, "edit_settings")


def can_view_audit_log(role: str) -> bool:
    """Check if role can view the audit log."""
    return has_permission(role, "view_audit_log")
