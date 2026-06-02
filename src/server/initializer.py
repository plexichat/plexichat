import os
import secrets
import threading
import time
from typing import Any, Dict, Tuple

import utils.logger as logger
import utils.config as config


def initialize_modules(
    modules_store: dict, worker_id: str
) -> Tuple[Any, Tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]]:
    from src.core.database import Database, setup_redis
    from src.core import (
        auth,
        messaging,
        polls,
        servers,
        relationships,
        presence,
        reactions,
        stickers,
        embeds,
        webhooks,
        settings,
        media,
        events,
        automod,
        search,
    )
    from src.core import voice, notifications, threads
    from src.core.voice import signaling
    import src.api as api

    internal_secret = secrets.token_hex(32)
    api.set_internal_secret(internal_secret)
    logger.info("Internal security secret generated for self-test")

    app_config = config.get("applications", {})
    if not app_config.get("webhook_signature_secret"):
        app_config["webhook_signature_secret"] = secrets.token_hex(32)
        config.set("applications", app_config)
        logger.info("Generated and persisted webhook_signature_secret")

    rl_config = config.get("rate_limiting", {})
    if not rl_config.get("bypass_secret"):
        rl_config["bypass_secret"] = secrets.token_hex(32)
        config.set("rate_limiting", rl_config)
        logger.info("Generated and persisted rate-limit bypass_secret")

    failed_modules = []

    logger.info("Initializing database...")
    db = Database()

    from src.core.migrations import run_migrations

    try:
        migration_result = run_migrations(db)
        if migration_result["success"]:
            logger.info(
                f"Migrations applied successfully: "
                f"{migration_result['applied_count']} applied, "
                f"{migration_result['failed_count']} failed "
                f"in {migration_result.get('total_elapsed_ms', 0)}ms"
            )
        else:
            failed = next(
                (
                    m
                    for m in migration_result.get("migrations", [])
                    if not m.get("success")
                ),
                None,
            )
            detail = ""
            if failed:
                detail = f" (version={failed.get('version', '?')}, error={failed.get('error', '?')})"
            logger.error(
                f"Migration process had failures: "
                f"{migration_result['applied_count']} applied, "
                f"{migration_result['failed_count']} failed{detail}"
            )
            raise RuntimeError(
                f"Database migrations failed{detail}. "
                "Server cannot start with an inconsistent schema."
            )
    except Exception as e:
        logger.error(f"Critical error during migrations: {e}")
        raise

    db.connect()

    redis_config = config.get("redis") or {}
    if redis_config.get("enabled", False):
        logger.info("Initializing Redis...")
        redis_client = setup_redis()
        if redis_client and redis_client.ping():
            redis_client.set_worker_id(worker_id)
            logger.info(
                f"Connected to Redis at {redis_config.get('host', 'localhost')}:{redis_config.get('port', 6379)} (Worker ID: {worker_id})"
            )
        else:
            logger.warning(
                "Redis is enabled but connection failed - continuing without Redis"
            )
    else:
        logger.info("Redis is disabled in configuration")

    try:
        from src.utils import encryption

        if encryption.rotate_keys():
            logger.info("Encryption keys rotated successfully")
    except Exception as e:
        logger.error(f"Failed to check encryption key rotation: {e}")

    startup_times: Dict[str, float] = {}

    def timed_init(name: str, init_func):
        start = time.perf_counter()
        logger.info(f"Initializing {name} module...")
        try:
            result = init_func()
            elapsed = (time.perf_counter() - start) * 1000
            startup_times[name] = elapsed
            logger.info(f"  -> {name} initialized in {elapsed:.1f}ms")
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            startup_times[name] = elapsed
            logger.error(f"  -> {name} FAILED after {elapsed:.1f}ms: {e}")
            raise

    def init_independent():
        threads_list = []

        def init_servers():
            timed_init("servers", lambda: servers.setup(db, auth, messaging))
            modules_store["servers"] = servers

        threads_list.append(threading.Thread(target=init_servers, name="InitServers"))

        def init_rel():
            timed_init("relationships", lambda: relationships.setup(db, auth, servers))
            modules_store["relationships"] = relationships

        threads_list.append(threading.Thread(target=init_rel, name="InitRel"))

        def init_media():
            timed_init("media", lambda: media.setup(db, messaging))
            modules_store["media"] = media

        threads_list.append(threading.Thread(target=init_media, name="InitMedia"))

        def init_settings():
            timed_init("settings", lambda: settings.setup(db))
            modules_store["settings"] = settings

        threads_list.append(threading.Thread(target=init_settings, name="InitSettings"))

        for t in threads_list:
            t.start()
        for t in threads_list:
            t.join()

    email_config = config.get("email", {})
    email_sender = None
    smtp_password = os.getenv("PLEXICHAT_SMTP_PASSWORD")
    if email_config.get("smtp_host") and smtp_password:
        from src.utils.email import SMTPEmailSender

        email_sender = SMTPEmailSender(
            host=email_config["smtp_host"],
            port=email_config.get("smtp_port", 587),
            user=email_config.get("smtp_user", ""),
            password=smtp_password,
            from_email=email_config.get("from_email", "noreply@plexichat.internal"),
            use_tls=email_config.get("use_tls", True),
        )
        logger.info(f"Email sender initialized via SMTP ({email_config['smtp_host']})")
    else:
        logger.info(
            "Email sender not initialized (SMTP host or PLEXICHAT_SMTP_PASSWORD missing)"
        )

    def init_auth_and_messaging_parallel():
        """Initialize auth and messaging concurrently.

        ``messaging.setup`` only stores the auth reference on its manager
        (via ``BaseManager.__init__``) and never calls into auth at
        construction time, so we can build both managers in parallel and
        attach the real auth reference to messaging once auth has
        finished initializing.
        """
        threads_list = []

        def init_auth():
            timed_init("auth", lambda: auth.setup(db, email_sender=email_sender))
            modules_store["auth"] = auth

        threads_list.append(threading.Thread(target=init_auth, name="InitAuth"))

        def init_messaging():
            timed_init("messaging", lambda: messaging.setup(db, None))
            modules_store["messaging"] = messaging

        threads_list.append(
            threading.Thread(target=init_messaging, name="InitMessaging")
        )

        for t in threads_list:
            t.start()
        for t in threads_list:
            t.join()

        # Re-attach the real auth module to the messaging manager. This
        # is the only post-init step required because auth was deferred
        # while messaging was being constructed in parallel.
        if (
            modules_store.get("messaging") is not None
            and modules_store["messaging"]._manager is not None
        ):
            modules_store["messaging"]._manager._auth = auth

    init_auth_and_messaging_parallel()

    init_independent()

    timed_init("search", lambda: search.setup(db, auth, messaging, servers))
    modules_store["search"] = search

    def init_dependent():
        threads_list = []

        try:
            from src.core.auth.reaper import AccountReaper

            reaper = AccountReaper(db, auth)
            reaper.start()
            modules_store["reaper"] = reaper
        except Exception as e:
            logger.error(f"Failed to start Account Reaper: {e}")

        def init_presence():
            timed_init(
                "presence",
                lambda: presence.setup(db, auth, relationships, servers),
            )
            modules_store["presence"] = presence

        threads_list.append(threading.Thread(target=init_presence, name="InitPresence"))

        def init_reactions():
            timed_init(
                "reactions",
                lambda: reactions.setup(db, messaging, servers, relationships),
            )
            modules_store["reactions"] = reactions

        threads_list.append(
            threading.Thread(target=init_reactions, name="InitReactions")
        )

        def init_embeds():
            timed_init("embeds", lambda: embeds.setup(db, messaging, servers))
            modules_store["embeds"] = embeds

        threads_list.append(threading.Thread(target=init_embeds, name="InitEmbeds"))

        def init_polls():
            timed_init("polls", lambda: polls.setup(db, messaging))
            modules_store["polls"] = polls

        threads_list.append(threading.Thread(target=init_polls, name="InitPolls"))

        def init_notifications():
            try:
                timed_init(
                    "notifications",
                    lambda: notifications.setup(
                        db,
                        auth_module=auth,
                        messaging_module=messaging,
                        servers_module=servers,
                        relationships_module=relationships,
                        presence_module=presence,
                    ),
                )
                modules_store["notifications"] = notifications
            except Exception as e:
                logger.warning(f"Failed to initialize notifications module: {e}")

        threads_list.append(
            threading.Thread(target=init_notifications, name="InitNotifications")
        )

        def init_stickers():
            timed_init(
                "stickers",
                lambda: stickers.setup(
                    db,
                    messaging_module=messaging,
                    servers_module=servers,
                    media_module=media,
                ),
            )
            modules_store["stickers"] = stickers

        threads_list.append(threading.Thread(target=init_stickers, name="InitStickers"))

        for t in threads_list:
            t.start()
        for t in threads_list:
            t.join()

    init_dependent()

    timed_init(
        "automod",
        lambda: automod.setup(
            db,
            servers_module=servers,
            messaging_module=messaging,
            notifications_module=modules_store.get("notifications"),
        ),
    )
    modules_store["automod"] = automod

    try:
        all_servers = db.fetch_all(
            "SELECT id, owner_id FROM srv_servers WHERE deleted = 0"
        )
        server_count = 0
        for srv in all_servers:
            automod.ensure_default_rules(srv["id"], srv["owner_id"])
            server_count += 1
        if server_count:
            logger.info(
                "Verified automod rules across %d server(s)",
                server_count,
            )
    except Exception as e:
        logger.warning(f"Failed to ensure default rules for servers: {e}")

    timed_init(
        "events",
        lambda: events.setup(
            relationships_module=relationships,
            servers_module=servers,
            messaging_module=messaging,
        ),
    )
    modules_store["events"] = events

    timed_init(
        "webhooks",
        lambda: webhooks.setup(db, auth, messaging, servers, embeds),
    )
    modules_store["webhooks"] = webhooks

    try:
        timed_init("threads", lambda: threads.setup(db, auth, messaging, servers))
        modules_store["threads"] = threads
    except Exception as e:
        logger.warning(f"Failed to initialize threads module: {e}")
        failed_modules.append("threads")

    features = None
    try:
        from src.core import features

        timed_init("features", lambda: features.setup(db))
        modules_store["features"] = features
    except Exception as e:
        logger.warning(f"Failed to initialize user features module: {e}")
        failed_modules.append("features")

    try:
        from src.core import avatars

        timed_init("avatars", lambda: avatars.setup(db))
        modules_store["avatars"] = avatars
    except Exception as e:
        logger.warning(f"Failed to initialize avatars module: {e}")
        failed_modules.append("avatars")

    try:
        timed_init(
            "voice",
            lambda: voice.setup(db, auth, servers, relationships, presence),
        )
        modules_store["voice"] = voice
    except Exception as e:
        logger.warning(f"Failed to initialize voice module: {e}")
        failed_modules.append("voice")

    voice_config = config.get("voice") or {}
    if voice_config.get("enabled", False):
        try:
            sfu_backend = voice_config.get("sfu_backend", "aiortc")

            def init_signaling():
                signaling.setup(
                    voice_module=voice,
                    events_module=events,
                    sfu_backend=sfu_backend,
                    mediasoup_url=voice_config.get(
                        "mediasoup_url", "https://localhost:4443"
                    ),
                    mediasoup_origin=voice_config.get(
                        "mediasoup_origin",
                        "https://localhost:4443",
                    ),
                    janus_url=voice_config.get(
                        "janus_url", "http://localhost:8088/janus"
                    ),
                    stun_urls=voice_config.get(
                        "stun_urls", ["stun:stun.l.google.com:19302"]
                    ),
                    turn_urls=voice_config.get("turn_urls", []),
                    turn_secret=voice_config.get("turn_secret", ""),
                    turn_ttl=voice_config.get("turn_ttl", 86400),
                    turn_username=voice_config.get("turn_username", ""),
                    turn_credential=voice_config.get("turn_credential", ""),
                )

            timed_init("signaling", init_signaling)
            modules_store["signaling"] = signaling
            sfu_url = (
                voice_config.get("mediasoup_url")
                if sfu_backend in ("mediasoup", "mediasoup-ws")
                else voice_config.get("janus_url")
            )
            logger.info(
                f"Voice signaling initialized with {sfu_backend} backend at {sfu_url}"
            )
            if voice_config.get("turn_urls"):
                logger.info(
                    f"TURN servers configured: {len(voice_config.get('turn_urls', []))} servers"
                )
            if voice_config.get("log_connections", False):
                logger.info("Voice connection logging enabled")
        except Exception as e:
            logger.warning(f"Failed to initialize voice signaling module: {e}")
            failed_modules.append("signaling")
    else:
        logger.info("Voice module disabled in configuration")

    rate_limit_settings = config.get("rate_limiting", {})
    if rate_limit_settings.get("enabled", True):
        try:
            from src.core import ratelimit
            from src.core.ratelimit.storage import (
                RedisStorage,
                MemoryStorage,
                DatabaseStorage,
            )
            from src.core.database.redis_client import (
                is_available as redis_is_available,
            )

            def init_ratelimit():
                storage = None
                if redis_is_available():
                    storage = RedisStorage()
                    logger.info("Using Redis storage for rate limiting")
                elif db:
                    storage = DatabaseStorage(db)
                    db_type = db.type.capitalize()
                    logger.info(
                        f"Using {db_type} storage for rate limiting (shared across workers)"
                    )
                else:
                    storage = MemoryStorage()
                    logger.warning("=" * 60)
                    logger.warning(
                        "SECURITY WARNING: Using in-memory storage for rate limiting!"
                    )
                    logger.warning(
                        "In a multi-worker environment (Uvicorn), this will result in"
                    )
                    logger.warning(
                        "independent counters per worker, allowing rate-limit bypass."
                    )
                    logger.warning(
                        "To fix: Enable Redis or configure a shared database."
                    )
                    logger.warning("=" * 60)

                ratelimit.setup(
                    storage_backend=storage,
                    bot_multiplier=rate_limit_settings.get("bot_multiplier", 1.5),
                    webhook_multiplier=rate_limit_settings.get(
                        "webhook_multiplier", 1.0
                    ),
                    enable_global_limit=True,
                )

            timed_init("ratelimit", init_ratelimit)
            modules_store["ratelimit"] = ratelimit
        except Exception as e:
            logger.warning(f"Failed to initialize rate limit module: {e}")
            failed_modules.append("ratelimit")

    telemetry_config = config.get("telemetry") or {}
    if telemetry_config.get("enabled", True):
        try:
            from src.core import telemetry

            timed_init("telemetry", lambda: telemetry.setup(db))
            modules_store["telemetry"] = telemetry
        except Exception as e:
            logger.warning(f"Failed to initialize telemetry module: {e}")
            failed_modules.append("telemetry")

    feedback_config = config.get("feedback") or {}
    if feedback_config.get("enabled", True):
        try:
            from src.core import feedback

            timed_init("feedback", lambda: feedback.setup(db))
            modules_store["feedback"] = feedback
        except Exception as e:
            logger.warning(f"Failed to initialize feedback module: {e}")
            failed_modules.append("feedback")

    reports_config = config.get("reports") or {}
    if reports_config.get("enabled", True):
        try:
            from src.core import reports

            timed_init("reports", lambda: reports.setup(db, messaging))
            modules_store["reports"] = reports
        except Exception as e:
            logger.warning(f"Failed to initialize reports module: {e}")
            failed_modules.append("reports")

    admin_config = config.get("admin_ui") or {}
    if admin_config.get("enabled", True):
        try:
            from src.core import admin

            timed_init("admin", lambda: admin.setup(db, auth, features))
            modules_store["admin"] = admin
            logger.info(
                f"Admin module initialized (path: {admin_config.get('path', '/admin')})"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize admin module: {e}")
            failed_modules.append("admin")

    total_time = sum(startup_times.values())
    logger.info("=" * 60)
    logger.info("MODULE INITIALIZATION SUMMARY")
    logger.info("=" * 60)
    for module_name, elapsed in sorted(startup_times.items(), key=lambda x: -x[1]):
        logger.info(f"  {module_name:20s} {elapsed:8.1f}ms")
    logger.info("-" * 60)
    logger.info(f"  {'TOTAL':20s} {total_time:8.1f}ms")
    logger.info("=" * 60)

    if failed_modules:
        logger.error(
            f"Module initialization incomplete. Failed modules: {', '.join(failed_modules)}"
        )
    else:
        logger.info("All modules initialized successfully")

    return db, (
        auth,
        messaging,
        servers,
        relationships,
        presence,
        reactions,
        embeds,
        webhooks,
        settings,
        media,
        search,
    )
