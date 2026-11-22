"""The all-permissions vocabulary is built once, not once per resolved member.

Bulk member-list reads resolve permissions for every member of a page in a
single request (``PermissionOpsMixin.get_channel_view_user_ids`` and the
reaction audience fan-out both loop over members). For an owner or an
administrator, ``calculate_base_permissions`` and ``apply_channel_overrides``
used to materialise ``{perm: True for perm in SERVER_PERMISSIONS}`` with a
dict comprehension *each time*: the same 43-key invariant was rebuilt once per
member, and again when channel overrides were applied.

This test counts the ``MAP_ADD`` opcodes executed inside those two helpers
(the bytecode a dict comprehension emits for every inserted key). Before the
fix a burst of owner/administrator resolutions performs ``N * len(vocab)``
``MAP_ADD`` operations; after hoisting the vocabulary to one module-level
computation the helpers perform none -- they copy a shared template. The
output-equality and mutation-isolation assertions keep the authz semantics and
the per-result mutability identical (timeouts/overrides mutate their own dict).
"""

import dis
import sys

from src.core.servers import permission_utils as pu
from src.core.servers.models import SERVER_PERMISSIONS

_MAP_ADD = dis.opmap["MAP_ADD"]


def _count_map_adds(calls):
    """Count MAP_ADD opcodes executed by the permission vocabulary helpers."""
    fn1 = pu.calculate_base_permissions
    fn2 = pu.apply_channel_overrides
    targets = {fn1.__code__, fn2.__code__}
    total = 0

    def tracer(frame, event, arg):
        nonlocal total
        if frame.f_code in targets:
            if event == "call":
                frame.f_trace_opcodes = True
            elif event == "opcode" and frame.f_code.co_code[frame.f_lasti] == _MAP_ADD:
                total += 1
        return tracer

    sys.settrace(tracer)
    try:
        for _ in range(calls):
            # Owner resolution: the vocabulary is the full grant set.
            base = pu.calculate_base_permissions([], is_owner=True)
            # Administrator resolution: same vocabulary, via a role and via an
            # override, both reached on the bulk member-list read path.
            pu.apply_channel_overrides(base, [], None)
            pu.calculate_base_permissions(
                [{"permissions": {"administrator": True}}], is_owner=False
            )
            pu.apply_channel_overrides({"administrator": True}, [], None)
    finally:
        sys.settrace(None)
    return total


def test_permission_vocabulary_not_rebuilt_per_member():
    """A burst of owner/admin resolutions inserts no vocabulary keys in-loop."""
    # Warm up so opcode tracing is active on every measured call.
    _count_map_adds(4)
    assert _count_map_adds(50) == 0


def test_hoisted_vocabulary_matches_catalog_exactly():
    """The shared template is the same all-True mapping the catalog defines."""
    assert pu._ALL_PERMISSIONS == {perm: True for perm in SERVER_PERMISSIONS}


def test_each_resolution_gets_its_own_mutable_dict():
    """A caller mutating one result cannot poison a later member's decision."""
    first = pu.calculate_base_permissions([], is_owner=True)
    first["messages.send"] = False
    second = pu.calculate_base_permissions([], is_owner=True)
    assert second["messages.send"] is True
    assert all(value is True for value in second.values())

    admin_base = {perm: True for perm in SERVER_PERMISSIONS}
    with_override = pu.apply_channel_overrides(dict(admin_base), [], None)
    with_override["messages.send"] = False
    again = pu.apply_channel_overrides(dict(admin_base), [], None)
    assert again["messages.send"] is True
    assert admin_base["messages.send"] is True
