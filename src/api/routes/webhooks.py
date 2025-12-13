"""
Webhook routes - Webhook management and execution endpoints.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends

import src.api as api
from src.api.middleware.authentication import get_current_user, get_optional_user, TokenInfo
from src.api.schemas.common import SnowflakeID
from src.api.schemas.webhooks import (
    WebhookCreateRequest,
    WebhookResponse,
    WebhookExecuteRequest,
    WebhookMessageResponse,
)

router = APIRouter()


def _webhook_to_response(webhook, include_token: bool = False) -> WebhookResponse:
    """Convert webhook object to response model."""
    return WebhookResponse(
        id=SnowflakeID(webhook.id),
        channel_id=SnowflakeID(webhook.channel_id),
        server_id=SnowflakeID(webhook.server_id),
        creator_id=SnowflakeID(getattr(webhook, "creator_id", 0)),
        name=webhook.name,
        avatar_url=webhook.avatar_url,
        token=webhook.token if include_token and webhook.token else None,
        url=webhook.url if include_token and webhook.token else None,
        created_at=webhook.created_at,
    )


@router.post("", response_model=WebhookResponse)
async def create_webhook(
    body: WebhookCreateRequest,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Create a new webhook.
    
    Creates a webhook for the specified channel. Returns the token only on creation.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        cid = int(body.channel_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid channel ID"}})

    try:
        webhook = webhooks.create_webhook(
            user_id=current_user.user_id,
            channel_id=cid,
            name=body.name,
            avatar_url=body.avatar_url
        )
        return _webhook_to_response(webhook, include_token=True)
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Channel not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        elif "Limit" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        elif "Name" in exc_name or "Avatar" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(webhook_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get webhook by ID.
    
    Returns webhook information without the token.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        wid = int(webhook_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid webhook ID"}})

    try:
        webhook = webhooks.get_webhook(wid, current_user.user_id)
        if not webhook:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Webhook not found"}})
        return _webhook_to_response(webhook)
    except HTTPException:
        raise
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Webhook not found"}})
        elif "Access" in exc_name or "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": "Access denied"}})
        raise


@router.delete("/{webhook_id}")
async def delete_webhook(webhook_id: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Delete a webhook.
    
    Permanently deletes the webhook.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        wid = int(webhook_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid webhook ID"}})

    try:
        webhooks.delete_webhook(current_user.user_id, wid)
        return {"success": True}
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Webhook not found"}})
        elif "Permission" in exc_name:
            raise HTTPException(status_code=403, detail={"error": {"code": 403, "message": str(e)}})
        raise


@router.post("/{webhook_id}/{token}", response_model=Optional[WebhookMessageResponse])
async def execute_webhook(
    webhook_id: str,
    token: str,
    body: WebhookExecuteRequest,
    wait: bool = False,
    current_user: Optional[TokenInfo] = Depends(get_optional_user)
):
    """
    Execute a webhook.
    
    Sends a message via the webhook. No authentication required if token is valid.
    """
    webhooks = api.get_webhooks()
    if not webhooks:
        raise HTTPException(status_code=500, detail={"error": {"code": 500, "message": "Webhooks module not available"}})

    try:
        wid = int(webhook_id)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": "Invalid webhook ID"}})

    if not body.content and not body.embeds:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Message must have content or embeds"}}
        )

    thread_id = int(body.thread_id) if body.thread_id else None

    try:
        result = webhooks.execute_webhook(
            webhook_id=wid,
            token=token,
            content=body.content,
            username=body.username,
            avatar_url=body.avatar_url,
            embeds=body.embeds,
            thread_id=thread_id,
            wait=wait
        )

        if wait and result:
            return WebhookMessageResponse(
                id=SnowflakeID(result.id),
                webhook_id=SnowflakeID(wid),
                channel_id=SnowflakeID(result.channel_id),
                content=result.content,
                username=result.username,
                avatar_url=result.avatar_url,
                created_at=result.created_at,
            )

        return None
    except Exception as e:
        exc_name = type(e).__name__
        if "NotFound" in exc_name:
            raise HTTPException(status_code=404, detail={"error": {"code": 404, "message": "Webhook not found"}})
        elif "Token" in exc_name:
            raise HTTPException(status_code=401, detail={"error": {"code": 401, "message": "Invalid webhook token"}})
        elif "Content" in exc_name or "Embed" in exc_name:
            raise HTTPException(status_code=400, detail={"error": {"code": 400, "message": str(e)}})
        raise
