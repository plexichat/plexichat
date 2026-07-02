from fastapi import APIRouter

from .helpers import _channel_to_response


class ChannelBase:
    router: APIRouter = APIRouter(tags=["Channels"])

    def _channel_to_response(self, channel, current_user_id=None):
        return _channel_to_response(channel, current_user_id)
