"""Shared approval-ceremony helper for the self-test suite.

Destructive endpoints (migrations, reindex, emergency override, artifact
force-delete) are approval-gated: they require a *consumed* APPROVED
request for their action. Approval workflows demand ``required_approvals``
(default 2) distinct approvers and forbid self-approval, so the ceremony
performs the real multi-admin flow:

    request (other admin) -> approve (main) -> approve (third) -> execute

Every step runs over HTTP against the live server, so the gates themselves
are exercised instead of bypassed. Returns True when an APPROVED request
for the action exists afterwards.
"""

import re as _re

import src.api as api
import utils.logger as logger


def ensure_user_session(ctx, session, username: str) -> bool:
    """Probe a session; re-login with the test password when dead."""
    if session is None or not username:
        return False
    try:
        probe = session.get(f"{ctx.base_url}/api/v1/users/@me", timeout=5)
        if probe.status_code == 200:
            return True
        logger.warning(
            f"Session for '{username}' stale (probe {probe.status_code}); re-logging in"
        )
    except Exception as e:
        logger.warning(f"Session probe exception for '{username}': {e}")
    try:
        resp = session.post(
            f"{ctx.base_url}/api/v1/auth/login",
            json={"username": username, "password": ctx._test_password},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(
                f"Re-login for '{username}' -> {resp.status_code}: {resp.text[:200]}"
            )
            return False
        token = resp.json().get("token") or resp.json().get("access_token")
        if not token:
            return False
        session.headers.update({"Authorization": f"Bearer {token}"})
        if session is ctx.session:
            ctx.token = token
        elif session is getattr(ctx, "other_session", None):
            ctx.other_token = token
        logger.info(f"Re-authenticated session for '{username}'")
        return True
    except Exception as e:
        logger.warning(f"Re-login exception for '{username}': {e}")
        return False


def third_user_session(ctx):
    """Log in as the third (support) admin in the user realm.

    Cached on first use; re-created when the cached token dies.
    """
    sess = getattr(ctx, "third_session", None)
    if sess is not None:
        try:
            probe = sess.get(f"{ctx.base_url}/api/v1/users/@me", timeout=5)
            if probe.status_code == 200:
                return sess
        except Exception:
            pass
        try:
            sess.close()
        except Exception:
            pass
        ctx.third_session = None
    username = getattr(ctx, "test_third_username", None)
    password = ctx._test_password
    if not username or not password:
        return None
    try:
        sess = ctx.make_session()
        try:
            _secret = api.get_internal_secret()
        except Exception:
            _secret = None
        if _secret:
            sess.headers.update({"X-Plexichat-Internal-Secret": _secret})
        resp = sess.post(
            f"{ctx.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"Third user login -> {resp.status_code}: {resp.text[:200]}")
            try:
                sess.close()
            except Exception:
                pass
            return None
        token = resp.json().get("token") or resp.json().get("access_token")
        if not token:
            try:
                sess.close()
            except Exception:
                pass
            return None
        sess.headers.update({"Authorization": f"Bearer {token}"})
        ctx.third_session = sess
        return sess
    except Exception as e:
        logger.warning(f"Third user login exception: {e}")
        return None


def run_ceremony(ctx, action_type: str) -> bool:
    """Run the full approval ceremony for *action_type*."""
    base = ctx.base_url
    main = ctx.session
    other = getattr(ctx, "other_session", None)
    main_user = ctx.config.get("test_user", {}).get("username", "selftest_admin")
    if not ensure_user_session(ctx, main, main_user):
        logger.warning(f"Approval ceremony skipped ({action_type}): main dead")
        return False
    if other is None or not ensure_user_session(
        ctx, other, getattr(ctx, "_setup_other_username", None) or ""
    ):
        logger.warning(f"Approval ceremony skipped ({action_type}): other dead")
        return False
    # 1. Request (other admin).
    try:
        req_resp = other.post(
            f"{base}/api/v1/admin/approvals/request",
            json={
                "action_type": action_type,
                "target_type": "system",
                "action_details": f"self-test ceremony for {action_type}",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Approval request exception ({action_type}): {e}")
        return False
    if req_resp.status_code not in (200, 201):
        logger.warning(
            f"Approval request ({action_type}) -> {req_resp.status_code}: "
            f"{req_resp.text[:200]}"
        )
        return False
    approval_id = None
    try:
        m = _re.search(r"(\d{10,})", req_resp.text)
        if m:
            approval_id = int(m.group(1))
    except Exception:
        pass
    if approval_id is None:
        try:
            _db = api.get_db()
            if _db:
                _row = _db.fetch_one(
                    "SELECT id FROM admin_approvals WHERE action_type = ? "
                    "AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                    (action_type,),
                )
                if _row:
                    approval_id = int(_row["id"] if isinstance(_row, dict) else _row[0])
        except Exception:
            pass
    if approval_id is None:
        logger.warning(f"Approval ceremony ({action_type}): no id resolved")
        return False
    # 2. Approve as main.
    try:
        a1 = main.post(
            f"{base}/api/v1/admin/approvals/{approval_id}/approve", timeout=10
        )
    except Exception as e:
        logger.warning(f"Approval approve#1 exception ({action_type}): {e}")
        return False
    if a1.status_code not in (200, 201):
        logger.warning(
            f"Approval approve#1 ({action_type}) -> {a1.status_code}: {a1.text[:200]}"
        )
        return False
    # 3. Approve as third (second distinct vote -> approved).
    third_sess = third_user_session(ctx)
    if third_sess is None:
        return False
    try:
        a2 = third_sess.post(
            f"{base}/api/v1/admin/approvals/{approval_id}/approve", timeout=10
        )
    except Exception as e:
        logger.warning(f"Approval approve#2 exception ({action_type}): {e}")
        return False
    if a2.status_code not in (200, 201):
        logger.warning(
            f"Approval approve#2 ({action_type}) -> {a2.status_code}: {a2.text[:200]}"
        )
        return False
    logger.info(f"Approval ceremony complete for {action_type} (id={approval_id})")
    return True


def execute_gated(
    ctx, label: str, method: str, path: str, body=None, params=None, timeout=60
):
    """Run one ceremony + execute, recording a labeled result. Returns bool."""
    from time import time as _now

    if not run_ceremony(ctx, _action_for(label, path)):
        ctx.results.append(
            {
                "method": method,
                "path": path,
                "status_code": 0,
                "duration_ms": 0,
                "success": False,
                "label": label,
                "error": "approval ceremony failed",
            }
        )
        return False
    start = _now()
    try:
        resp = ctx.session.request(
            method,
            f"{ctx.base_url}{path}",
            json=body,
            params=params,
            timeout=timeout,
        )
        ok = 200 <= resp.status_code < 300
        ctx.results.append(
            {
                "method": method,
                "path": path,
                "status_code": resp.status_code,
                "duration_ms": (_now() - start) * 1000,
                "success": ok,
                "label": label,
            }
        )
        if ok:
            logger.info(f"Approval-gated {label} PASSED -> {resp.status_code}")
        else:
            logger.warning(
                f"Approval-gated {label} -> {resp.status_code}: {resp.text[:200]}"
            )
        return ok
    except Exception as e:
        logger.warning(f"Approval-gated {label} exception: {e}")
        ctx.results.append(
            {
                "method": method,
                "path": path,
                "status_code": 0,
                "duration_ms": 0,
                "success": False,
                "label": label,
                "error": str(e)[:200],
            }
        )
        return False


def _action_for(label: str, path: str) -> str:
    """Map a gated test label/path back to its approval action string."""
    _ = label
    if "apply-all" in path or "apply_all" in label:
        return "migrations.apply_all"
    if "emergency" in path:
        return "migrations.emergency_override"
    if "reindex" in path:
        return "search.reindex"
    if "rollback" in path:
        return "migrations.rollback"
    if "force" in path or "artifact" in path:
        return "artifacts.force_delete"
    return "migrations.run"
