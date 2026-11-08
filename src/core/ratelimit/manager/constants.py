"""Shared limits for rate-limit manager internals.

These caps are defence-in-depth: middleware normalizes routes before they
reach the manager, but a caller can still hand us an arbitrarily long
route/IP/resource string. Every component that becomes part of a storage
key must be bounded so a single request cannot create an oversized key or
an unbounded keyspace.
"""

# Maximum length of the route component inside a bucket key.
MAX_ROUTE_KEY_LENGTH = 256

# Maximum length of any other textual key component (IP, user, resource...).
MAX_COMPONENT_KEY_LENGTH = 64

# Hard cap for the fully-assembled key. Kept as route + fixed slack so the
# existing ``MAX_ROUTE_KEY_LENGTH + 32`` contract remains true.
MAX_TOTAL_KEY_LENGTH = MAX_ROUTE_KEY_LENGTH + 32
