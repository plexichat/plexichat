"""Guard tests: cached dataclass payloads must reconstruct as real objects.

The cache serializes to JSON tagged with ``__type__`` and reconstructs ONLY
types on the ``register_reconstructable_type`` allowlist. If a dataclass that
flows through an ``@cached`` function (Server, Channel, User, Member,
TokenInfo, ...) is missing from the allowlist, cache hits silently degrade to
plain dicts and crash attribute-consuming code paths.
"""

import json

from src.core.database.cache.serialization import (
    ensure_serializable,
    get_type_from_name,
    is_reconstructable,
    reconstruct_object,
    register_reconstructable_type,
)

from src.core.auth.models import AccountType, TokenInfo, User
from src.core.messaging.models import (
    Attachment,
    Message,
    MessageStatusType,
    MessageType,
)
from src.core.servers.models import Channel, ChannelType, Member, Server

# Idempotent safety net: these registrations also live at the definition
# sites; re-registering here keeps this guard independent of import order.
# Message/Attachment are deliberately NOT registered here: their guard test
# must observe the registration that ships in the model module, and a
# re-registration in this file would make it pass no matter what.
register_reconstructable_type(Server, Channel, User, Member, TokenInfo)


def _round_trip(obj):
    """Serialize exactly like the cache tier does, including a JSON hop
    simulating Valkey storage, then reconstruct."""
    data = ensure_serializable(obj)
    return reconstruct_object(json.loads(json.dumps(data)))


def _assert_same_type(original, expected_type):
    back = _round_trip(original)
    assert type(back) is expected_type, (
        f"cached {expected_type.__name__} degraded to {type(back).__name__}; "
        "is it registered via register_reconstructable_type?"
    )
    return back


def test_cached_messaging_models_are_registered_at_their_definition_site():
    """Message/Attachment are cached on every send, so they must reconstruct.

    Deliberately free of any registration of its own: this asserts the
    registration that ships in ``src/core/messaging/models.py``.
    """
    for cls in (Message, Attachment):
        name = f"{cls.__module__}.{cls.__qualname__}"
        assert is_reconstructable(name), f"{name} not allowlisted"
        assert get_type_from_name(name) is cls


def test_allowlist_contains_cached_models():
    for cls in (Server, Channel, User, Member, TokenInfo):
        name = f"{cls.__module__}.{cls.__qualname__}"
        assert is_reconstructable(name), f"{name} not allowlisted"
        assert get_type_from_name(name) is cls


def test_server_round_trips_as_dataclass():
    server = Server(
        id=123,
        name="Plexi",
        owner_id=7,
        created_at=1,
        updated_at=2,
        member_count=10,
        default_channel_id=555,
    )
    back = _assert_same_type(server, Server)
    assert back.id == 123
    assert back.name == "Plexi"
    assert back.owner_id == 7
    assert back.default_channel_id == 555
    assert back.member_count == 10


def test_channel_round_trips_with_enum_member():
    channel = Channel(
        id=42,
        server_id=123,
        name="general",
        channel_type=ChannelType.TEXT,
        created_at=1,
        updated_at=2,
    )
    back = _assert_same_type(channel, Channel)
    assert back.channel_type is ChannelType.TEXT
    assert back.name == "general"


def test_user_round_trips_with_account_type_enum():
    user = User(
        id=9,
        account_type=AccountType.USER,
        username="alice",
        permissions={"channels.view": True},
        created_at=1,
        updated_at=2,
    )
    back = _assert_same_type(user, User)
    assert back.account_type is AccountType.USER
    assert back.username == "alice"
    assert back.permissions == {"channels.view": True}


def test_member_round_trips_as_dataclass():
    member = Member(
        id=11,
        server_id=123,
        user_id=9,
        joined_at=1,
        updated_at=2,
        roles=[3, 4],
        timeout_until=None,
    )
    back = _assert_same_type(member, Member)
    assert back.user_id == 9
    assert back.roles == [3, 4]


def test_message_round_trips_with_nested_attachment():
    """Cached message payloads must rebuild as objects, not plain dicts.

    Every sent message is written to ``msg:obj:{id}`` and its id pushed onto
    the conversation's recent-messages list, so the NEXT page read serves the
    payload straight from the cache. An unregistered Message reconstructs as a
    plain dict and the first ``message.id`` read on that hit raises
    AttributeError, which the channel listing route reports as an empty
    channel — the message looks like it vanished on reload.
    """
    message = Message(
        id=101,
        conversation_id=7,
        author_id=9,
        content="hello",
        created_at=1,
        updated_at=2,
        message_type=MessageType.TEXT,
        status=MessageStatusType.SENT,
        attachments=[
            Attachment(
                id=55,
                message_id=101,
                filename="clover.png",
                content_type="image/png",
                size=12,
                url="/media/clover.png",
                created_at=3,
            )
        ],
    )

    back = _assert_same_type(message, Message)
    assert back.id == 101
    assert back.content == "hello"
    assert back.message_type is MessageType.TEXT
    assert back.status is MessageStatusType.SENT
    assert isinstance(back.attachments[0], Attachment), (
        "nested attachments must rebuild as Attachment instances; attribute "
        "reads on them are what the API response mapper performs"
    )
    assert back.attachments[0].filename == "clover.png"


def test_token_info_round_trips_as_dataclass():
    token = TokenInfo(
        valid=True,
        token_type="user",
        account_id=9,
        user_id=9,
        session_id=77,
        permissions={},
        rate_limit_tier="user",
        expires_at=None,
        username="alice",
        account_type=AccountType.USER,
    )
    back = _assert_same_type(token, TokenInfo)
    assert back.session_id == 77
    assert back.account_type is AccountType.USER
