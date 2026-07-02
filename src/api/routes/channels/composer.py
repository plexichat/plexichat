from fastapi import APIRouter

from .base import ChannelBase
from .crud import ChannelCRUDMixin
from .invites import ChannelInvitesMixin
from .webhooks import ChannelWebhooksMixin
from .attachments import ChannelAttachmentsMixin
from .ratchet import register_ratchet_routes


class ChannelsComposer(
    ChannelCRUDMixin,
    ChannelInvitesMixin,
    ChannelWebhooksMixin,
    ChannelAttachmentsMixin,
    ChannelBase,
):
    def __init__(self) -> None:
        self.router = APIRouter(tags=["Channels"])
        ChannelCRUDMixin._register_routes(self)
        ChannelInvitesMixin._register_routes(self)
        ChannelWebhooksMixin._register_routes(self)
        ChannelAttachmentsMixin._register_routes(self)
        register_ratchet_routes(self.router)


channels_router = ChannelsComposer().router
