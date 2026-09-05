"""
Sadhaka — Access Hierarchy
===========================
Three tiers, enforced entirely server-side via a request header. This is
explicitly NOT the "enter password 1234, hide it from inspect element"
pattern — that pattern cannot work for a static frontend (anything client-
side is visible in dev tools by construction) and pretending otherwise would
be a worse signal than having no gate at all.

The real distinction a client-side check cannot make but a server-side check
can: whether the REQUEST carries a credential the server independently
verifies, not whether the requester clicked past a UI screen.

TIERS
-----
  viewer    (default, no header needed) — read-only reporting endpoints.
            Anyone with the API's URL can see reconciliation results, which
            is appropriate for a demo/reviewer audience.

  operator  (X-Sadhaka-Role: operator + valid key) — can additionally trigger
            pipeline runs and the verification harness. These are not
            destructive, but they consume compute and could be used to spam
            a public deployment, so they are gated.

  admin     (X-Sadhaka-Role: admin + valid key) — can additionally read the
            raw audit trail without the reporting layer's filtering, and
            adjust rate limits. No admin action is a money-moving action;
            this codebase never lets any API call change what the engine
            decided about a transaction, at any tier.

KEYS ARE ENVIRONMENT VARIABLES, NEVER HARDCODED
------------------------------------------------
SADHAKA_OPERATOR_KEY and SADHAKA_ADMIN_KEY are read from the environment. If
unset, that tier is simply unreachable (every request is rejected) rather
than falling back to a default value — a missing admin key must fail closed,
not open.
"""

import os
import hmac
import logging
from enum import IntEnum
from typing import Optional

from fastapi import Header, HTTPException, Request

logger = logging.getLogger("sadhaka.security")


class Role(IntEnum):
    VIEWER = 0
    OPERATOR = 1
    ADMIN = 2


def _get_key(env_var: str) -> Optional[str]:
    val = os.environ.get(env_var, "").strip()
    return val or None


def _constant_time_eq(a: str, b: str) -> bool:
    """Regular == leaks timing information proportional to how many leading
    characters match, which is a real (if narrow) attack surface for
    guessing a secret one character at a time. hmac.compare_digest is
    constant-time regardless of where the strings first differ."""
    return hmac.compare_digest(a.encode(), b.encode())


def resolve_role(role_header: Optional[str], key_header: Optional[str]) -> Role:
    """Determine the caller's role from headers, verifying the key
    server-side. Never trusts the role header alone — a request claiming
    'admin' with no key, or the wrong key, is downgraded to viewer rather
    than rejected outright, so read-only access still works for a caller who
    mistyped a header they didn't need.
    """
    if not role_header or role_header.lower() == "viewer":
        return Role.VIEWER

    requested = role_header.lower()

    if requested == "operator":
        real_key = _get_key("SADHAKA_OPERATOR_KEY")
        if real_key and key_header and _constant_time_eq(key_header, real_key):
            return Role.OPERATOR
        logger.warning("qa: operator role requested with invalid or missing key")
        return Role.VIEWER

    if requested == "admin":
        real_key = _get_key("SADHAKA_ADMIN_KEY")
        if real_key and key_header and _constant_time_eq(key_header, real_key):
            return Role.ADMIN
        logger.warning("qa: admin role requested with invalid or missing key")
        return Role.VIEWER

    return Role.VIEWER


async def get_role(
    x_sadhaka_role: Optional[str] = Header(None),
    x_sadhaka_key: Optional[str] = Header(None),
) -> Role:
    """FastAPI dependency. Use as: role: Role = Depends(get_role)"""
    return resolve_role(x_sadhaka_role, x_sadhaka_key)


def require_role(minimum: Role):
    """FastAPI dependency factory: require_role(Role.OPERATOR) as a route
    dependency rejects the request with 403 before the route body runs,
    rather than relying on the route itself to remember to check."""
    async def _checker(
        x_sadhaka_role: Optional[str] = Header(None),
        x_sadhaka_key: Optional[str] = Header(None),
    ) -> Role:
        role = resolve_role(x_sadhaka_role, x_sadhaka_key)
        if role < minimum:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This endpoint requires '{minimum.name.lower()}' role or "
                    f"higher. Provide X-Sadhaka-Role and X-Sadhaka-Key headers "
                    f"with a valid key. Current effective role: "
                    f"'{role.name.lower()}'."
                ),
            )
        return role
    return _checker


def client_identity(request: Request) -> str:
    """Best-effort caller identity for rate limiting. Falls back to a
    constant if no client host is available (e.g. under some test clients),
    which means all such callers share one bucket rather than the limiter
    crashing — acceptable degradation for a demo-scale deployment."""
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
