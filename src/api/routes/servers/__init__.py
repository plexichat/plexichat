"""
Server routes - Server/guild management endpoints.
"""

from fastapi import APIRouter

from . import (
    server_crud,
    channels,
    members,
    roles,
    bans,
    misc,
    audit_log,
    automod,
    webhooks,
    icon_upload,
)

router = APIRouter()
router.include_router(server_crud.router)
router.include_router(channels.router)
router.include_router(members.router)
router.include_router(roles.router)
router.include_router(bans.router)
router.include_router(misc.router)
router.include_router(audit_log.router)
router.include_router(automod.router)
router.include_router(webhooks.router)
router.include_router(icon_upload.router)
