"""Process-global module graphs must not outlive the test that installed them.

``src.api.setup`` stores the whole module graph (including ``_auth``) in module
globals, and the suite domain ``setup`` helpers keep one process-wide manager
each. The autouse ``_isolate_process_globals`` fixture resets them after every
test; these tests pin that isolation. Without it, a later test inherits a
manager over the previous test's deleted temp database — the order-dependent
"Auth module not available" and "no such table: artifacts" failures.

The two tests MUST run in definition order: the first installs the globals the
second asserts are gone.
"""

import src.api as api
from src.core.suite import stash as stash_domain
from src.core.suite import sum as sum_domain


def test_first_test_installs_the_process_globals(db):
    # The standalone-app pattern that pinned ``api._auth`` to None (it calls
    # ``api.setup(db=db)`` with no auth module) and then made every later bare
    # app respond 500 "Auth module not available".
    api.setup(db=db)
    assert api._setup_complete is True
    assert api.get_auth() is None  # setup with no auth module -> unavailable

    # The suite routes' lazy fallback installs these process-wide managers.
    sum_domain._manager = object()
    stash_domain._manager = object()


def test_second_test_starts_from_the_un_setup_state():
    assert api._setup_complete is False
    # The lazy-import fallback runs again instead of returning the pinned None.
    assert api.get_auth() is not None

    assert sum_domain._manager is None
    assert stash_domain._manager is None
