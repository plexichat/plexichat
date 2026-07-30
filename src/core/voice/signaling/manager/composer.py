"""SignalingManager composer class combining all mixins."""

from typing import Any

import utils.logger as logger

from .base import SignalingManagerBase
from .connection_mixin import ConnectionManagementMixin
from .sdp_mixin import SDPMixin
from .ice_mixin import ICEMixin
from .turn_mixin import TURNMixin
from .screenshare_mixin import ScreenShareMixin
from .quality_mixin import QualityMixin


class SignalingManager(
    SDPMixin,
    ConnectionManagementMixin,
    ICEMixin,
    TURNMixin,
    ScreenShareMixin,
    QualityMixin,
    SignalingManagerBase,
):
    """Core signaling manager handling all WebRTC operations."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        try:
            self.init_bandwidth_estimator()
        except Exception as exc:
            logger.debug(f"Bandwidth estimator init deferred: {exc}")
