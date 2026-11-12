"""PlexiSum workbook routes package.

Compatibility shim: the former monolithic ``src.api.routes.suite.sum`` module
is now a package. ``router`` and every handler are re-exported here, so route
registration (``src/api/routes/__init__.py``) and direct imports keep working.

The route modules are imported in the order the handlers were declared in the
monolith. FastAPI matches routes in registration order, so that order is part
of the behavior, not a style choice.
"""

from src.core.artifacts.capabilities import (
    get_capability as get_capability,
    is_usable as is_usable,
)
from .helpers import (
    _error as _error,
    _manager as _manager,
    _require_sum_capability as _require_sum_capability,
)
from .router import router

from . import workbooks as _workbooks
from . import operations as _operations
from . import snapshots as _snapshots
from . import sheets as _sheets
from . import files as _files
from . import grants as _grants
from . import presence as _presence
from . import validations as _validations
from . import comments as _comments
from . import xlsx as _xlsx
from . import features as _features

from .workbooks import (
    list_workbooks as list_workbooks,
    create_workbook as create_workbook,
    create_from_template as create_from_template,
    set_pinned as set_pinned,
    set_template as set_template,
    get_workbook as get_workbook,
    delete_workbook as delete_workbook,
    restore_workbook as restore_workbook,
)
from .operations import (
    read_viewport as read_viewport,
    apply_operation as apply_operation,
    sort_range as sort_range,
    replay_operations as replay_operations,
)
from .snapshots import (
    create_snapshot as create_snapshot,
    list_snapshots as list_snapshots,
    restore_snapshot as restore_snapshot,
)
from .sheets import (
    create_sheet as create_sheet,
    rename_sheet as rename_sheet,
    delete_sheet as delete_sheet,
)
from .files import (
    download_file as download_file,
    upload_file as upload_file,
    export_csv as export_csv,
    import_csv as import_csv,
)
from .grants import (
    list_grants as list_grants,
    grant_workbook as grant_workbook,
    revoke_grant as revoke_grant,
    list_range_permissions as list_range_permissions,
    set_range_permission as set_range_permission,
    remove_range_permission as remove_range_permission,
)
from .presence import (
    list_presence as list_presence,
    set_presence as set_presence,
    get_freeze as get_freeze,
    set_freeze as set_freeze,
)
from .validations import (
    list_validations as list_validations,
    set_validation as set_validation,
    delete_validation as delete_validation,
    list_conditional_formats as list_conditional_formats,
    set_conditional_format as set_conditional_format,
    delete_conditional_format as delete_conditional_format,
)
from .comments import (
    list_comments as list_comments,
    add_comment as add_comment,
    resolve_comment as resolve_comment,
    delete_comment as delete_comment,
)
from .xlsx import (
    export_xlsx as export_xlsx,
    import_xlsx as import_xlsx,
)
from .features import (
    list_features as list_features,
    create_feature as create_feature,
    update_feature as update_feature,
    delete_feature as delete_feature,
)

__all__ = ["router"]

# Importing each module registers its handlers on the shared router.
_ROUTE_MODULES = (
    _workbooks,
    _operations,
    _snapshots,
    _sheets,
    _files,
    _grants,
    _presence,
    _validations,
    _comments,
    _xlsx,
    _features,
)
