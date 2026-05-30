"""EndpointTester composition class.

Combines all mixins into a single EndpointTester class used by SelfTestRunner.
"""

from .base import EndpointTesterBase
from .core import CoreMixin
from .auth import AuthMixin
from .admin import AdminMixin
from .resources import ResourceMixin
from .media import MediaMixin
from .polls import PollMixin
from .bots import BotMixin


class EndpointTester(
    CoreMixin,
    AuthMixin,
    AdminMixin,
    ResourceMixin,
    MediaMixin,
    PollMixin,
    BotMixin,
    EndpointTesterBase,
):
    """Endpoint test execution service for SelfTestRunner.

    Executes individual API endpoint tests, DELETE resource tests,
    and specialised bot-server integration flows.
    """
