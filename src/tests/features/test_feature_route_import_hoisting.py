"""Feature route handlers bind their service classes once at import time.

The hot endpoints resolve a service class on every request via a local
``from ... import``. Since these imports never vary at runtime, bind them at
module scope so the request path does not re-run import machinery.
"""

import inspect

import pytest

from src.api.routes.features import (
    bookmarks,
    forwarding,
    last_chat,
    profiles,
    scheduled_messages,
)

HOT_HANDLERS = [
    bookmarks.list_bookmarks,
    forwarding.forward_message,
    last_chat.save_last_chat,
    profiles.update_own_profile,
    scheduled_messages.create_scheduled_message,
]

MODULE_SERVICES = [
    (bookmarks, "BookmarkService"),
    (forwarding, "ForwardingService"),
    (last_chat, "LastChatService"),
    (profiles, "ProfileManager"),
    (scheduled_messages, "ScheduledMessageService"),
]


@pytest.mark.parametrize("handler", HOT_HANDLERS, ids=lambda h: h.__name__)
def test_hot_handler_has_no_local_core_import(handler):
    src = inspect.getsource(handler)
    assert "from src.core." not in src
    assert "import src.core" not in src


@pytest.mark.parametrize("module,name", MODULE_SERVICES)
def test_service_bound_at_module_scope(module, name):
    assert isinstance(getattr(module, name), type)
