import os
from enum import Enum
from typing import List, Dict, Set, Optional
from fastapi import Header, HTTPException, Depends, status
from backend.shared.utils.logger import get_logger
from backend.shared.utils.route_utils import is_production

logger = get_logger(__name__)

class UserRole(str, Enum):
    ADMIN = "ADMIN"
    LEGAL_REVIEWER = "LEGAL_REVIEWER"
    AUDITOR = "AUDITOR"
    VIEWER = "VIEWER"

class Permission(str, Enum):
    UPLOAD = "UPLOAD"
    DELETE = "DELETE"
    ANALYZE = "ANALYZE"
    VIEW_REPORTS = "VIEW_REPORTS"
    MANAGE_POLICIES = "MANAGE_POLICIES"
    VIEW_AUDIT = "VIEW_AUDIT"
    # Ruling on a redline changes what goes into a contract. It is deliberately
    # not covered by ANALYZE, which VIEWER holds — being able to run an analysis
    # is not the same as being able to accept its output.
    APPROVE_REDLINE = "APPROVE_REDLINE"

class RBACManager:
    """
    Manager for Role-Based Access Control.
    Maps roles to permissions and provides validation logic.
    """
    
    # Role-Permission Mapping (Static for now, could be loaded from DB/Config)
    ROLE_PERMISSIONS: Dict[UserRole, Set[Permission]] = {
        UserRole.ADMIN: set(Permission),  # Admins have all permissions
        UserRole.LEGAL_REVIEWER: {
            Permission.ANALYZE,
            Permission.UPLOAD,
            Permission.VIEW_REPORTS,
            Permission.APPROVE_REDLINE
        },
        UserRole.AUDITOR: {
            Permission.VIEW_REPORTS,
            Permission.VIEW_AUDIT
        },
        UserRole.VIEWER: {
            Permission.ANALYZE  # Can query/analyze but not upload/delete
        }
    }

    @classmethod
    def has_permission(cls, role: UserRole, permission: Permission) -> bool:
        """Check if a role has a specific permission"""
        allowed_permissions = cls.ROLE_PERMISSIONS.get(role, set())
        return permission in allowed_permissions

async def get_current_user_role(x_user_role: Optional[str] = Header(None)) -> UserRole:
    """
    FastAPI dependency to extract user role from header.
    Mock implementation - in production this would validate a JWT token.
    """
    if not x_user_role:
        # Fail closed in production: an unauthenticated caller gets the
        # least-privileged role, never ADMIN. Defaulting high made every
        # endpoint effectively unguarded, since the header is trivially absent.
        #
        # There is no real authentication yet, so in development we fall back to
        # a configurable role (DEV_DEFAULT_ROLE) — otherwise the UI, which sends
        # no header, cannot upload. Replace this whole branch with real token
        # validation when auth lands.
        if is_production():
            logger.warning("Access attempted without user role header; denying beyond VIEWER")
            return UserRole.VIEWER

        configured = os.getenv("DEV_DEFAULT_ROLE", UserRole.LEGAL_REVIEWER.value)
        try:
            role = UserRole(configured.upper())
        except ValueError:
            logger.error(f"Invalid DEV_DEFAULT_ROLE '{configured}'; falling back to VIEWER")
            return UserRole.VIEWER
        logger.debug(f"No user role header; using DEV_DEFAULT_ROLE={role.value}")
        return role
        
    try:
        role = UserRole(x_user_role.upper())
        return role
    except ValueError:
        logger.error(f"Invalid user role provided: {x_user_role}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid user role: {x_user_role}"
        )

def requires_permission(permission: Permission):
    """
    FastAPI dependency factory for RBAC.
    Usage: @app.get("/...", dependencies=[Depends(requires_permission(Permission.UPLOAD))])
    """
    async def permission_dependency(role: UserRole = Depends(get_current_user_role)):
        if not RBACManager.has_permission(role, permission):
            logger.error(f"RBAC Denied: Role '{role}' attempted action requiring '{permission}'")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Resource requires '{permission}' permission which is not assigned to role '{role}'"
            )
        logger.info(f"RBAC Allowed: Role '{role}' authorized for '{permission}'")
        return True
        
    return permission_dependency


async def get_current_tenant(
    x_tenant_id: Optional[str] = Header(None),
    role: UserRole = Depends(get_current_user_role),
) -> str:
    """Resolve the tenant whose data this request may touch.

    **This is not tenant isolation, and must not be described as such.** The
    header is supplied by the caller and nothing validates it, so anyone who can
    reach the API can name any tenant. Moving it off the query string removed the
    most casual form of the problem — a URL you could edit and share — but the
    guarantee is only as good as the header, and the header is not trusted.

    Real isolation needs the tenant to come from a claim in a validated token,
    i.e. it is blocked on authentication existing at all. Until then treat every
    tenant-scoped endpoint as open, and do not deploy this to real client data.

    Development falls back to DEV_DEFAULT_TENANT so the UI works; production
    requires the header, which at least makes the caller state an intent that
    lands in the audit log.
    """
    if x_tenant_id:
        return x_tenant_id

    if is_production():
        logger.error("Tenant-scoped request with no X-Tenant-ID header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Tenant-ID header is required",
        )

    return os.getenv("DEV_DEFAULT_TENANT", "default-tenant")
