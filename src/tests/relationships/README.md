# Relationship tests

Tests for friend and block functionality, including request workflows, edge cases, and
module integration.

## Test files

- `test_blocking.py` — Verifies block/unblock operations, friendship removal on block,
  pending-request handling, and bidirectional block checks.
- `test_edge_cases.py` — Covers self-relationship errors, invalid requests, double
  actions (accept, decline, cancel, block, unfriend), wrong-user operations, empty
  lists, and timestamp validation.
- `test_friend_requests.py` — Tests sending, accepting, declining, and cancelling friend
  requests, including duplicate detection, blocked-user rejection, and reverse-request
  auto-acceptance.
- `test_friends.py` — Validates friend-list retrieval, bidirectional friendship, removal
  behavior, mutual friends, mutual servers, and `get_mutual_info`.
- `test_integration.py` — Covers end-to-end workflows (friend-request lifecycle,
  block-after-friendship, unblock-and-refriend), integration with the auth and servers
  modules, anti-spam cooldown enforcement, and concurrent-relationship scenarios.
