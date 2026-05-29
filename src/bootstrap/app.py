import time
from typing import Any

import utils.logger as logger
import utils.config as config


def create_application(
    server,
    auth: Any,
    messaging: Any,
    servers: Any,
    relationships: Any,
    presence: Any,
    reactions: Any,
    embeds: Any,
    webhooks: Any,
    settings: Any,
    media: Any,
    search: Any,
) -> Any:
    from src.api import setup as api_setup, create_app
    from src.core import applications

    start = time.perf_counter()
    logger.info("Initializing API module...")
    api_setup(
        db=server.db,
        auth_module=auth,
        messaging_module=messaging,
        polls_module=server._modules.get("polls"),
        servers_module=servers,
        relationships_module=relationships,
        presence_module=presence,
        reactions_module=reactions,
        stickers_module=server._modules.get("stickers"),
        embeds_module=embeds,
        webhooks_module=webhooks,
        settings_module=settings,
        media_module=media,
        search_module=search,
        features_module=server._modules.get("features"),
        avatars_module=server._modules.get("avatars"),
        reports_module=server._modules.get("reports"),
        feedback_module=server._modules.get("feedback"),
        admin_module=server._modules.get("admin"),
        events_module=server._modules.get("events"),
        notifications_module=server._modules.get("notifications"),
        threads_module=server._modules.get("threads"),
        telemetry_module=server._modules.get("telemetry"),
    )
    applications.setup(
        server.db,
        auth_module=auth,
        servers_module=servers,
        events_module=server._modules.get("events"),
    )

    rate_limit_config = config.get("rate_limiting", {})
    docs_config = config.get("docs", {})
    enable_rate_limiting = rate_limit_config.get("enabled", True)
    enable_docs = docs_config.get("enabled", True)

    app = create_app(enable_rate_limiting=enable_rate_limiting, enable_docs=enable_docs)
    elapsed = (time.perf_counter() - start) * 1000
    logger.info(f"  -> API initialized in {elapsed:.1f}ms")
    return app
