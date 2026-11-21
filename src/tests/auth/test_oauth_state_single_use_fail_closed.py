"""OAuth state consumption must fail closed when single-use cannot be proven.

``OAuthStateManager.verify_state`` reads ``used = 0`` as a fast-fail and then
relies on a conditional ``UPDATE ... WHERE used = 0`` plus its rowcount to make
consumption single-use under concurrency. The rowcount check previously used
``hasattr(result, "rowcount") and result.rowcount == 0``, which silently
treated a driver that reports no rowcount (or the driver's ``None`` result) as
a *successful* consume. Two concurrent callbacks could then both be accepted
for the same state token — an OAuth state replay / login-CSRF primitive.

The applications OAuth code-exchange and refresh paths already fail closed in
exactly this situation. This test pins the same contract for login states: if
the atomic consume cannot be proven, verification is denied.
"""

from types import SimpleNamespace

from src.core.auth.oauth import OAuthStateManager

PROVIDER = "google"
REDIRECT = "https://example.com/callback"


class _CursorWithoutRowcount:
    """A DB execute result that does not expose ``rowcount`` at all."""

    __slots__ = ()


class _UnusableDb:
    """Minimal DB double: state lookup says 'unused', consume reports nothing.

    Returning ``used = 0`` on every read models two concurrent callbacks that
    both pass the fast-fail before either write lands. The consume result has
    no usable rowcount, so the single-use guarantee cannot be proven.
    """

    def __init__(self, row, consume_result):
        self._row = row
        self._consume_result = consume_result

    def fetch_one(self, query, params=None):
        if "SELECT" in query.upper():
            return dict(self._row)
        return None

    def execute(self, query, params=None):
        if "UPDATE auth_oauth_states SET used = 1" in query:
            return self._consume_result
        # The cleanup_expired DELETE (and any other statement) sees a normal
        # cursor so it does not interfere with the scenario under test.
        return SimpleNamespace(rowcount=0)


def _state_row(state):
    return {
        "id": state.id,
        "state_hash": state.state_hash,
        "provider": PROVIDER,
        "redirect_uri": REDIRECT,
        "created_at": state.created_at,
        "expires_at": state.expires_at,
        "used": 0,
        "nonce_hash": state.nonce,
        "pkce_challenge": state.pkce_challenge,
        "ip_address": state.ip_address,
        "cookie_issued": 1 if state.cookie_issued else 0,
    }


def _make_manager_and_state(db):
    manager = OAuthStateManager(db)
    state = manager.create_state(provider=PROVIDER, redirect_uri=REDIRECT)
    assert state is not None and state.state_token is not None
    return manager, state


def _verify_with(manager, state, consume_result):
    manager._db = _UnusableDb(_state_row(state), consume_result)
    return manager.verify_state(
        state.state_token,
        provider=PROVIDER,
        redirect_uri=REDIRECT,
    )


def test_missing_rowcount_denies_verification(db):
    """A consume result without ``rowcount`` must never count as consumed."""
    manager, state = _make_manager_and_state(db)

    valid, record, error = _verify_with(manager, state, _CursorWithoutRowcount())

    assert valid is False
    assert record is None
    assert error == "OAuth state could not be atomically consumed"


def test_none_rowcount_denies_verification(db):
    """A driver reporting ``rowcount = None`` must also fail closed."""
    manager, state = _make_manager_and_state(db)

    valid, record, error = _verify_with(manager, state, SimpleNamespace(rowcount=None))

    assert valid is False
    assert record is None
    assert error == "OAuth state could not be atomically consumed"


def test_rowcount_zero_still_reports_already_used(db):
    """A genuine lost race keeps the existing 'already used' contract."""
    manager, state = _make_manager_and_state(db)

    valid, record, error = _verify_with(manager, state, SimpleNamespace(rowcount=0))

    assert valid is False
    assert record is None
    assert error == "State token already used"


def test_proven_single_consume_still_succeeds(db):
    """A successful atomic consume (rowcount 1) is still accepted."""
    manager, state = _make_manager_and_state(db)

    valid, record, error = _verify_with(manager, state, SimpleNamespace(rowcount=1))

    assert valid is True
    assert record is not None
    assert error is None
