"""PlexiSum workbook routes package.

Compatibility shim: the former monolithic ``src.api.routes.suite.sum`` module
is now a package. ``router`` and every handler are re-exported here, so route
registration (``src/api/routes/__init__.py``) and direct imports keep working.

The route modules are imported in the order the handlers were declared in the
monolith. FastAPI matches routes in registration order, so that order is part
of the behavior, not a style choice.
"""

from src.core.artifacts.capabilities import (  # noqa: F401
    get_capability,
    is_usable,
)
from .helpers import _error, _manager, _require_sum_capability  # noqa: F401
from .router import router

from . import workbooks as _workbooks  # noqa: F401  (registers routes)
from . import operations as _operations  # noqa: F401  (registers routes)
from . import snapshots as _snapshots  # noqa: F401  (registers routes)
from . import sheets as _sheets  # noqa: F401  (registers routes)
from . import files as _files  # noqa: F401  (registers routes)
from . import grants as _grants  # noqa: F401  (registers routes)
from . import presence as _presence  # noqa: F401  (registers routes)
from . import validations as _validations  # noqa: F401  (registers routes)
from . import comments as _comments  # noqa: F401  (registers routes)
from . import xlsx as _xlsx  # noqa: F401  (registers routes)
from . import features as _features  # noqa: F401  (registers routes)

from .workbooks import (  # noqa: F401
    list_workbooks,
    create_workbook,
    create_from_template,
    set_pinned,
    set_template,
    get_workbook,
    delete_workbook,
    restore_workbook,
)
from .operations import (  # noqa: F401
    read_viewport,
    apply_operation,
    sort_range,
    replay_operations,
)
from .snapshots import (  # noqa: F401
    create_snapshot,
    list_snapshots,
    restore_snapshot,
)
from .sheets import (  # noqa: F401
    create_sheet,
    rename_sheet,
    delete_sheet,
)
from .files import (  # noqa: F401
    download_file,
    upload_file,
    export_csv,
    import_csv,
)
from .grants import (  # noqa: F401
    list_grants,
    grant_workbook,
    revoke_grant,
    list_range_permissions,
    set_range_permission,
    remove_range_permission,
)
from .presence import (  # noqa: F401
    list_presence,
    set_presence,
    get_freeze,
    set_freeze,
)
from .validations import (  # noqa: F401
    list_validations,
    set_validation,
    delete_validation,
    list_conditional_formats,
    set_conditional_format,
    delete_conditional_format,
)
from .comments import (  # noqa: F401
    list_comments,
    add_comment,
    resolve_comment,
    delete_comment,
)
from .xlsx import (  # noqa: F401
    export_xlsx,
    import_xlsx,
)
from .features import (  # noqa: F401
    list_features,
    create_feature,
    update_feature,
    delete_feature,
)

__all__ = ["router"]
