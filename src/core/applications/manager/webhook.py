import hashlib
import hmac
import threading
import time

from src.core.database.cache import cache_get, cache_set
from src.core.security.hardening import bounded_int, reject_control_characters

from ..exceptions import WebhookSignatureError
from .protocol import ApplicationManagerProtocol

MAX_SIGNATURE_LENGTH = 128

# A captured, validly signed webhook must be single-use. The timestamp skew
# window alone does not stop a replay *inside* the window, so consume a
# signature-keyed marker on first acceptance and refuse it thereafter.
_REPLAY_MARKER = "1"
_HMAC_REPLAY_LOCK = threading.Lock()


def _claim_replay_key(key: str, ttl: int):
    """Atomically claim ``key`` in the shared cache tier when available."""
    try:
        from src.core.database.valkey_client import (
            ValkeyOperationError,
            get_client,
            is_available,
        )
    except Exception:
        return None
    try:
        client = get_client()
        if not client or not is_available():
            return None
        set_nx = getattr(client, "set_nx", None)
        if not callable(set_nx):
            return None
        return bool(set_nx(key, _REPLAY_MARKER, ttl=ttl))
    except ValkeyOperationError:
        return None
    except Exception:
        return None


class WebhookMixin(ApplicationManagerProtocol):
    def verify_webhook_signature(
        self, body: bytes, signature: str, timestamp: str
    ) -> bool:
        max_skew = bounded_int(
            self._config.get("webhook_signature_max_skew_seconds", 300),
            "webhook_signature_max_skew_seconds",
            minimum=0,
            maximum=86_400,
        )
        try:
            signature = reject_control_characters(signature, "signature")
        except ValueError as exc:
            raise WebhookSignatureError("Invalid webhook signature") from exc
        if not signature or len(signature) > MAX_SIGNATURE_LENGTH:
            raise WebhookSignatureError("Invalid webhook signature")
        try:
            ts = int(timestamp)
        except (ValueError, TypeError):
            raise WebhookSignatureError("Invalid webhook timestamp")
        if abs(int(time.time()) - ts) > max_skew:
            raise WebhookSignatureError("Timestamp outside allowed window")
        secret = self._config.get("webhook_signature_secret", "")
        if not secret:
            raise WebhookSignatureError("Webhook signing secret is not configured")
        message = timestamp.encode() + body
        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError("Invalid webhook signature")
        # Replay protection: only a signature that just verified reaches here.
        replay_key = (
            "webhook:hmac-seen:"
            + hashlib.sha256((secret + "\x00" + signature).encode("utf-8")).hexdigest()
        )
        replay_ttl = max(60, 2 * max_skew + 60)
        claimed = _claim_replay_key(replay_key, replay_ttl)
        if claimed is True:
            return True
        if claimed is False:
            raise WebhookSignatureError("Webhook signature replay detected")
        with _HMAC_REPLAY_LOCK:
            try:
                consumed = cache_get(replay_key)
                if consumed is not None:
                    raise WebhookSignatureError("Webhook signature replay detected")
            except WebhookSignatureError:
                raise
            except Exception as exc:
                raise WebhookSignatureError("Webhook replay cache unavailable") from exc
            try:
                cache_set(replay_key, _REPLAY_MARKER, ttl=replay_ttl)
            except Exception as exc:
                raise WebhookSignatureError("Webhook replay cache unavailable") from exc
        return True
