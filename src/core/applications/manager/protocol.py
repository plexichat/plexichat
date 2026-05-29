from typing import Any, Dict, Optional

from ..models import Application, ApplicationInstallation


class ApplicationManagerProtocol:
    _db: Any = None
    _auth: Any = None
    _servers: Any = None
    _events: Any = None
    _config: Dict[str, Any] = {}
    _oauth: Any = None
    _commands: Any = None
    _interactions: Any = None
    _rate_limits: Dict[int, Dict[str, int]] = {}

    def _get_timestamp(self) -> int: ...
    def _generate_id(self) -> int: ...

    def get_application(
        self, application_id: int, user_id: Optional[int] = None
    ) -> Optional[Application]: ...

    def install_application(
        self,
        application_id: int,
        server_id: int,
        installer_id: int,
        permissions: str = "0",
        scopes: Optional[list[str]] = None,
    ) -> ApplicationInstallation: ...

    def uninstall_application(
        self, application_id: int, server_id: int, user_id: int
    ) -> bool: ...
