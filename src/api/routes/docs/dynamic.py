"""
Dynamic data extraction for documentation.

This module extracts runtime data for rate limits, intents, permissions, and OAuth scopes.
"""

from typing import Dict, Any


def get_api_rate_limits() -> Dict[str, Any]:
    """Get actual API rate limits from the rate limit configuration."""
    try:
        from src.core.ratelimit.config import (
            DEFAULT_ROUTE_LIMITS,
            get_bot_multiplier,
            get_global_limit,
            get_ip_limit,
            get_user_limit,
            get_webhook_multiplier,
            should_bypass_admin,
            should_bypass_internal,
        )

        global_limit = get_global_limit()
        user_limit = get_user_limit()
        ip_limit = get_ip_limit()

        limits = {
            "global": {
                "requests": global_limit.requests,
                "window_seconds": global_limit.window_seconds,
                "burst": global_limit.burst,
            },
            "user": {
                "requests": user_limit.requests,
                "window_seconds": user_limit.window_seconds,
                "burst": user_limit.burst,
            },
            "ip": {
                "requests": ip_limit.requests,
                "window_seconds": ip_limit.window_seconds,
                "burst": ip_limit.burst,
            },
            "bot_multiplier": get_bot_multiplier(),
            "webhook_multiplier": get_webhook_multiplier(),
            "admin_bypass": should_bypass_admin(),
            "internal_bypass": should_bypass_internal(),
            "routes": {},
        }

        for route, cfg in DEFAULT_ROUTE_LIMITS.items():
            limits["routes"][route] = {
                "requests": cfg.requests,
                "window_seconds": cfg.window_seconds,
                "burst": cfg.burst,
            }

        return limits
    except Exception:
        return {}


def _format_window_seconds(value: Any) -> str:
    """Format rate-limit window seconds for docs display."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:g}"


def _format_limit_summary(limit: Dict[str, Any]) -> str:
    """Format a human-readable rate limit summary."""
    window = _format_window_seconds(limit.get("window_seconds", 0))
    seconds_label = "second" if window == "1" else "seconds"
    return (
        f"{limit.get('requests', 0)} requests per {window} {seconds_label}, "
        f"burst {limit.get('burst', 0)}"
    )


def _build_rate_limit_defaults_rows(limits: Dict[str, Any]) -> str:
    """Build markdown table rows for global default rate limits."""
    rows = []
    labels = (("global", "global"), ("per-user", "user"), ("per-IP", "ip"))
    for label, key in labels:
        limit = limits.get(key)
        if not limit:
            continue
        rows.append(f"| {label} | {_format_limit_summary(limit)} |")
    return "\n".join(rows)


def _build_rate_limit_route_rows(limits: Dict[str, Any]) -> str:
    """Build markdown table rows for route-specific rate limits."""
    route_rows = []
    for route, cfg in sorted(limits.get("routes", {}).items()):
        route_rows.append(f"| `{route}` | {_format_limit_summary(cfg)} |")
    return "\n".join(route_rows)


def _build_rate_limit_policy_rows(limits: Dict[str, Any]) -> str:
    """Build markdown rows for rate-limit policy flags and multipliers."""

    def enabled(value: Any) -> str:
        return "enabled" if value else "disabled"

    return "\n".join(
        [
            f"| Bot multiplier | {limits.get('bot_multiplier', 1.0):g}x |",
            f"| Webhook multiplier | {limits.get('webhook_multiplier', 1.0):g}x |",
            f"| Admin bypass | {enabled(limits.get('admin_bypass', False))} |",
            f"| Internal bypass | {enabled(limits.get('internal_bypass', False))} |",
        ]
    )


def get_gateway_intents_docs_data() -> Dict[str, Any]:
    """Build dynamic docs data for gateway intents."""
    try:
        from src.api.websocket.intents import (
            ALL_INTENTS,
            DEFAULT_INTENTS,
            PRIVILEGED_INTENTS,
            get_intent_description,
        )
        from src.core.events.types import GatewayIntent

        rows = []
        for intent in GatewayIntent:
            value = int(intent)
            rows.append(
                {
                    "value": value,
                    "name": intent.name,
                    "default": bool(DEFAULT_INTENTS & value),
                    "privileged": bool(PRIVILEGED_INTENTS & value),
                    "description": get_intent_description(intent),
                }
            )

        return {
            "default_value": int(DEFAULT_INTENTS),
            "all_value": int(ALL_INTENTS),
            "privileged_value": int(PRIVILEGED_INTENTS),
            "rows": rows,
        }
    except Exception:
        return {
            "default_value": 0,
            "all_value": 0,
            "privileged_value": 0,
            "rows": [],
        }


def _build_gateway_intent_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for the gateway intents docs page."""
    rows = []
    for row in data.get("rows", []):
        rows.append(
            "| `{value}` | `{name}` | {default} | {privileged} | {description} |".format(
                value=row["value"],
                name=row["name"],
                default="Yes" if row.get("default") else "No",
                privileged="Yes" if row.get("privileged") else "No",
                description=row.get("description", "Unknown intent"),
            )
        )
    return "\n".join(rows)


def get_permissions_docs_data() -> Dict[str, Any]:
    """Build dynamic docs data for the permissions page."""
    try:
        from src.core.auth.permissions import (
            BOT_RESTRICTED_PERMISSIONS,
            DEFAULT_BOT_PERMISSIONS,
            DEFAULT_USER_PERMISSIONS,
            PERMISSIONS,
            get_permission_categories,
        )

        categories = get_permission_categories()
        category_rows = [
            {
                "name": category,
                "count": len(sorted(perms)),
                "permissions": sorted(perms),
            }
            for category, perms in sorted(categories.items())
        ]
        permission_rows = [
            {
                "name": name,
                "category": name.split(".", 1)[0],
                "description": description,
                "default_user": bool(DEFAULT_USER_PERMISSIONS.get(name, False)),
                "default_bot": bool(DEFAULT_BOT_PERMISSIONS.get(name, False)),
                "bot_restricted": name in BOT_RESTRICTED_PERMISSIONS,
            }
            for name, description in sorted(PERMISSIONS.items())
        ]

        return {
            "category_count": len(category_rows),
            "permission_count": len(permission_rows),
            "categories": category_rows,
            "permissions": permission_rows,
        }
    except Exception:
        return {
            "category_count": 0,
            "permission_count": 0,
            "categories": [],
            "permissions": [],
        }


def _build_permission_category_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for permission categories."""
    rows = []
    for category in data.get("categories", []):
        permission_list = ", ".join(
            f"`{perm}`" for perm in category.get("permissions", [])
        )
        rows.append(
            f"| `{category['name']}` | {category['count']} | {permission_list} |"
        )
    return "\n".join(rows)


def _build_permission_detail_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for individual permissions."""
    rows = []
    for permission in data.get("permissions", []):
        rows.append(
            "| `{name}` | `{category}` | {default_user} | {default_bot} | {bot_restricted} | {description} |".format(
                name=permission["name"],
                category=permission["category"],
                default_user="Yes" if permission.get("default_user") else "No",
                default_bot="Yes" if permission.get("default_bot") else "No",
                bot_restricted="Yes" if permission.get("bot_restricted") else "No",
                description=permission.get("description", ""),
            )
        )
    return "\n".join(rows)


def get_oauth_scopes_docs_data() -> Dict[str, Any]:
    """Build dynamic docs data for OAuth scopes."""
    try:
        from src.core.applications.oauth.scopes import (
            BOT_REQUIRED_SCOPES,
            PRIVILEGED_SCOPES,
            VALID_SCOPES,
            get_scope_description,
        )

        scopes = sorted(VALID_SCOPES)
        rows = [
            {
                "name": scope,
                "privileged": scope in PRIVILEGED_SCOPES,
                "bot_required": scope in BOT_REQUIRED_SCOPES,
                "description": get_scope_description(scope),
            }
            for scope in scopes
        ]

        return {
            "scope_count": len(rows),
            "privileged_count": sum(1 for row in rows if row["privileged"]),
            "bot_required_count": sum(1 for row in rows if row["bot_required"]),
            "rows": rows,
        }
    except Exception:
        return {
            "scope_count": 0,
            "privileged_count": 0,
            "bot_required_count": 0,
            "rows": [],
        }


def _build_oauth_scope_rows(data: Dict[str, Any]) -> str:
    """Build markdown rows for OAuth scope documentation."""
    rows = []
    for scope in data.get("rows", []):
        rows.append(
            "| `{name}` | {privileged} | {bot_required} | {description} |".format(
                name=scope["name"],
                privileged="Yes" if scope.get("privileged") else "No",
                bot_required="Yes" if scope.get("bot_required") else "No",
                description=scope.get("description", scope["name"]),
            )
        )
    return "\n".join(rows)


def get_app_config() -> Dict[str, Any]:
    """Get application configuration for documentation."""
    try:
        import utils.version as version

        return {
            "name": "Plexichat",
            "version": version.current_string(),
        }
    except Exception:
        return {"name": "Plexichat", "version": "unknown"}
