"""
Shared fixtures for webhook tests.

Uses the session-scoped fixtures from root conftest for efficiency.
"""

import ipaddress
import socket

import pytest
import uuid


@pytest.fixture(autouse=True)
def _stub_dns_resolution(monkeypatch):
    """Resolve hostnames without hitting the network's DNS.

    The webhook suite exercises webhook CRUD, not the resolver, and CI
    runners can have transient (or absent) DNS -- live name resolution
    turns these tests into flakes. Hostnames resolve to a public TEST-NET
    address so the SSRF allow-path stays exercised; IP literals pass
    through untouched and ``localhost`` maps to 127.0.0.1 (as /etc/hosts
    does), so loopback/private-IP rejections keep their behavior. The
    validator's DNS semantics themselves are covered with explicit stubs
    in src/tests/unit/test_url_validator_matrix.py.
    """

    def fake_getaddrinfo(host, port=None, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
            resolved = str(host)
        except ValueError:
            resolved = (
                "127.0.0.1" if str(host).lower() == "localhost" else "93.184.216.34"
            )
        family = socket.AF_INET6 if ":" in resolved else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (resolved, port or 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def db_and_modules(modules):
    """Legacy fixture for backward compatibility."""
    return (
        modules._db,
        modules.auth,
        modules.messaging,
        modules.servers,
        modules.embeds,
        modules.webhooks,
    )


@pytest.fixture
def base_server_setup(modules):
    """Create base server with channel for webhook tests."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"owner_{unique_id}",
        email=f"owner_{unique_id}@example.com",
        password="TestPass123!",
    )

    member = modules.auth.register(
        username=f"member_{unique_id}",
        email=f"member_{unique_id}@example.com",
        password="TestPass123!",
    )

    non_member = modules.auth.register(
        username=f"nonmember_{unique_id}",
        email=f"nonmember_{unique_id}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Test Server {unique_id}")
    modules.servers.add_member(server.id, member.id)

    channels = modules.servers.get_channels(owner.id, server.id)
    channel = channels[0] if channels else None

    return {
        "owner": owner,
        "member": member,
        "non_member": non_member,
        "server": server,
        "channel": channel,
        "auth": modules.auth,
        "messaging": modules.messaging,
        "servers": modules.servers,
        "embeds": modules.embeds,
        "webhooks": modules.webhooks,
        "db": modules._db,
    }


@pytest.fixture
def fresh_server(modules):
    """Create a fresh server for isolated tests."""
    unique_id = uuid.uuid4().hex[:8]

    owner = modules.auth.register(
        username=f"fresh_owner_{unique_id}",
        email=f"fresh_owner_{unique_id}@example.com",
        password="TestPass123!",
    )

    non_member = modules.auth.register(
        username=f"fresh_nonmember_{unique_id}",
        email=f"fresh_nonmember_{unique_id}@example.com",
        password="TestPass123!",
    )

    server = modules.servers.create_server(owner.id, f"Fresh Server {unique_id}")

    channels = modules.servers.get_channels(owner.id, server.id)
    channel = channels[0] if channels else None

    return {
        "owner": owner,
        "non_member": non_member,
        "server": server,
        "channel": channel,
        "servers": modules.servers,
        "webhooks": modules.webhooks,
        "embeds": modules.embeds,
        "db": modules._db,
    }


@pytest.fixture
def webhook_with_token(base_server_setup):
    """Create a webhook and return it with token."""
    setup = base_server_setup
    unique_id = uuid.uuid4().hex[:8]

    webhook = setup["webhooks"].create_webhook(
        user_id=setup["owner"].id,
        channel_id=setup["channel"].id,
        name=f"Test Webhook {unique_id}",
    )

    return {
        **setup,
        "webhook": webhook,
        "token": webhook.token,
    }
