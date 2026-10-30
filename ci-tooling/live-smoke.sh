#!/usr/bin/env bash
#
# Live smoke suite for a running PlexiChat deployment — curl + jq only.
#
# Drives the real HTTP surface the client uses: public endpoints, auth,
# profiles, relationships, servers/channels, messaging, bookmarks (including
# bookmarking a server-channel message by CHANNEL id, which the client sends),
# threads, DMs, search and the admin denial path for a normal account.
#
# Usage:
#   PLEXICHAT_BASE_URL=https://api.plexichat.com/api/v1 ./ci-tooling/live-smoke.sh
#
#   PLEXICHAT_BASE_URL   API root including /api/v1 (that is the default)
#   SMOKE_KEEP=1         keep the throwaway accounts for debugging
#   SMOKE_PACE=0.5       seconds between requests (default 0.4) to dodge limits
#   SMOKE_USER_A/B/C     pre-provisioned accounts to log in with instead of
#                        registering (+ SMOKE_PASSWORD, default SmokeTest123!)
#   SMOKE_TOKEN_A/B/C    pre-issued session tokens; skips login entirely (use
#                        when /auth/login is rate limited by your CI's IP)
#
# Needs no configuration from the runner. Registration is limited per client IP
# (3/minute, 10/hour, 20/day), and a CI runner shares its egress address, so the
# suite keeps two `smoke_gate_{a,b}` accounts on the deployment: it creates them
# on first use and logs in on every later run, which costs one login instead of
# two registrations. Everything those accounts create in a run is removed again.
# The third account is still created per run and deleted, so the account
# deletion and session-revocation path stays covered; it is skipped (not failed)
# when the IP's registration budget for the window is spent. Exits non-zero if
# any check fails. Requires: curl, jq.
#
set -uo pipefail

BASE="${PLEXICHAT_BASE_URL:-https://api.plexichat.com/api/v1}"
BASE="${BASE%/}"
PACE="${SMOKE_PACE:-0.4}"
PASSWORD="${SMOKE_PASSWORD:-SmokeTest123!}"
RUN="$(date +%s)$RANDOM"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
SKIP=0
STATUS=000
BODY=""

ok()   { PASS=$((PASS + 1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  FAIL  %s   %s\n' "$1" "${2:-}"; }
skip() { SKIP=$((SKIP + 1)); printf '  SKIP  %s   (%s)\n' "$1" "${2:-}"; }
sec()  { printf '\n=== %s ===\n' "$1"; }
step() { sleep "$PACE"; }

jget() { # jget dotted.path -> stdout value or empty
    jq -r ".${1} // empty" "$TMP/body.json" 2>/dev/null || true
}

expect() { # expect LABEL ACCEPTED_CODES_CSV
    case ",$2," in
        *",$STATUS,"*) ok "$1" ;;
        *) bad "$1" "-> $STATUS ${BODY:0:200}" ;;
    esac
}

field_is() { # field_is LABEL PATH EXPECTED
    local got
    got="$(jget "$2")"
    if [ "$got" = "$3" ]; then ok "$1"; else bad "$1" "-> '$got' (want '$3')"; fi
}

# req METHOD PATH [JAR] [JSON]
req() {
    local method=$1 path=$2 jar=${3:-} body=${4:-} attempt
    local args=(-sS -m 30 -o "$TMP/body.json" -w '%{http_code}'
        -X "$method" "$BASE$path" -H 'X-Plexichat-Request: 1')
    [ -n "$jar" ] && args+=(-b "$jar" -c "$jar")
    if [ -n "$jar" ] && [ -n "${JAR_TOKEN[$jar]:-}" ]; then
        args+=(-H "Authorization: Bearer ${JAR_TOKEN[$jar]}")
    fi
    [ -n "$body" ] && args+=(-H 'Content-Type: application/json' -d "$body")
    # A deployment reached through a CDN drops the occasional connection, and
    # curl then reports no status at all. Three attempts two seconds apart
    # gave up inside such a blip: the request never reached the app (no access
    # log line), and because the caller got no id back, every later check that
    # used it failed too -- a dozen FAILs from one dropped POST. Back off
    # further so a blip cannot fail the gate, and keep curl's own message in
    # the trace so a real outage stays distinguishable from a retried blip.
    local err out
    for attempt in 1 2 3 4 5 6; do
        step
        # curl's http_code goes to stdout and its diagnostics to stderr; both
        # are kept, and the status comes from the file so a retry cannot leave
        # a stale 000 behind.
        out="$(curl "${args[@]}" 2>"$TMP/curl.err")" || true
        STATUS="${out:-000}"
        [ "$STATUS" != "000" ] && break
        err="$(cat "$TMP/curl.err" 2>/dev/null)"
        [ "$attempt" -lt 6 ] || break
        printf '        (request did not complete: %s; retry %s/5)\n' \
            "${err:-no response}" "$attempt"
        sleep "$((attempt * attempt))"
    done
    BODY="$(cat "$TMP/body.json" 2>/dev/null || true)"
}

JAR_A="$TMP/jar_a"
JAR_B="$TMP/jar_b"
JAR_C="$TMP/jar_c"
JAR_ANON="$TMP/jar_anon"
declare -A JAR_TOKEN=()

# Wait out a per-IP limit, or report that the window is too long to sit
# through. Returns 0 when another attempt is worth making.
wait_for_reset() { # wait_for_reset MAX_SECONDS_TO_WAIT
    local wait
    wait="$(jq -r '.retry_after // empty' "$TMP/body.json" 2>/dev/null || true)"
    case "$wait" in ''|null|*[!0-9.]*) wait=2 ;; esac
    # A limit measured in the hour or the day is not something a gate should
    # wait for: the caller reports it instead.
    awk -v w="$wait" -v max="$1" 'BEGIN { exit !(w > max) }' && return 1
    awk -v w="$wait" 'BEGIN { exit !(w < 1) }' && wait=1
    printf '        (rate limited; waiting %ss)\n' "$wait"
    sleep "$wait"
    return 0
}

# Bring up one of the deployment's long-lived gate accounts.
#
# Log in first and register only when the account is not there yet: on a shared
# egress address a fresh pair per run exhausts the registration limit after a
# handful of runs, and the gate then fails for a reason that has nothing to do
# with the deployment. The first run creates the pair; every later run costs one
# login, which is limited far more loosely (5/minute, 20/hour per IP).
gate_account() { # gate_account LABEL USERNAME JAR
    local attempt
    for attempt in 1 2 3; do
        req POST /auth/login "$3" \
            "{\"username\":\"$2\",\"password\":\"$PASSWORD\"}"
        case "$STATUS" in
            200) ok "login $1 (gate account $2)"; return 0 ;;
            429) wait_for_reset 75 || break; continue ;;
            401|403|404) : ;;  # not created yet (or its password was rotated)
            *) bad "login $1" "-> $STATUS ${BODY:0:200}"; return 1 ;;
        esac
        # Registered against the account's own jar: the route's auto-login puts
        # the session cookie there, so the run is authenticated from here on.
        req POST /auth/register "$3" \
            "{\"username\":\"$2\",\"email\":\"$2@example.com\",\"password\":\"$PASSWORD\"}"
        case "$STATUS" in
            200|201) ok "register $1 ($2)"; return 0 ;;
            409) continue ;;  # a parallel run created it: log in on the next pass
            429) wait_for_reset 75 || break; continue ;;
            *) bad "register $1" "-> $STATUS ${BODY:0:200}"; return 1 ;;
        esac
    done
    bad "account $1" "could not log in or register $2 (per-IP limits)"
    return 1
}

# A throwaway account, so the run still covers deletion and session revocation.
# Best effort: when the IP's registration budget is spent this skips.
ephemeral_account() { # ephemeral_account LABEL USERNAME -> 0 when created
    local attempt
    for attempt in 1 2 3; do
        req POST /auth/register "$JAR_ANON" \
            "{\"username\":\"$2\",\"email\":\"$2@example.com\",\"password\":\"$PASSWORD\"}"
        case "$STATUS" in
            200|201) ok "register $1 ($2)"; return 0 ;;
            409) return 1 ;;  # the name is taken: this is not a fresh account
            429) wait_for_reset 75 || return 1; continue ;;
            *) bad "register $1" "-> $STATUS ${BODY:0:200}"; return 1 ;;
        esac
    done
    return 1
}

login() { # login LABEL USERNAME JAR
    req POST /auth/login "$3" "{\"username\":\"$2\",\"password\":\"$PASSWORD\"}"
    expect "login $1" 200
}

printf '== PlexiChat live smoke: %s ==\n' "$BASE"

sec "public surface"
req GET /version "";      expect "GET /version" 200
req GET /health "";       expect "GET /health" 200
req GET /ready "";        expect "GET /ready" 200
req GET /status "";       expect "GET /status" 200
req GET /capabilities ""; expect "GET /capabilities" 200
req GET /users/@me "";    expect "unauthenticated /users/@me refused" 401

sec "accounts and sessions"
# Registration and login are both rate limited per client IP. The suite keeps
# its own gate accounts so it needs nothing from the runner, and SMOKE_USER_* /
# SMOKE_TOKEN_* stay available as overrides for a host that cannot register.
HAVE_A=0; HAVE_B=0; HAVE_C=0; NEED_LOGIN=1
if [ -n "${SMOKE_TOKEN_A:-}" ] && [ -n "${SMOKE_TOKEN_B:-}" ]; then
    NEED_LOGIN=0
    JAR_TOKEN["$JAR_A"]="$SMOKE_TOKEN_A"
    JAR_TOKEN["$JAR_B"]="$SMOKE_TOKEN_B"
    HAVE_A=1; HAVE_B=1
    if [ -n "${SMOKE_TOKEN_C:-}" ]; then
        JAR_TOKEN["$JAR_C"]="$SMOKE_TOKEN_C"; HAVE_C=1
    fi
    USER_A="${SMOKE_USER_A:-preissued_A}"
    USER_B="${SMOKE_USER_B:-preissued_B}"
    USER_C="${SMOKE_USER_C:-preissued_C}"
    ok "using pre-issued session tokens for A/B$([ "$HAVE_C" = 1 ] && printf '/C')"
    skip "login A/B${USER_C:+/C}" "session tokens supplied"
elif [ -n "${SMOKE_USER_A:-}" ] && [ -n "${SMOKE_USER_B:-}" ]; then
    USER_A="$SMOKE_USER_A"; USER_B="$SMOKE_USER_B"; USER_C="${SMOKE_USER_C:-}"
    HAVE_A=1; HAVE_B=1
    [ -n "$USER_C" ] && HAVE_C=1
    ok "using pre-provisioned accounts A/B${USER_C:+/C}"
else
    NEED_LOGIN=0  # gate_account logs in as it goes
    USER_A="${SMOKE_USER_A:-smoke_gate_a}"
    USER_B="${SMOKE_USER_B:-smoke_gate_b}"
    USER_C="smoke_${RUN}_c"
    gate_account A "$USER_A" "$JAR_A" && HAVE_A=1
    gate_account B "$USER_B" "$JAR_B" && HAVE_B=1
    if ephemeral_account C "$USER_C"; then
        login C "$USER_C" "$JAR_C" && HAVE_C=1
    else
        skip "login C" "no third account (per-IP registration budget spent)"
    fi
fi
if [ "$NEED_LOGIN" = 1 ]; then
    login A "$USER_A" "$JAR_A"
    login B "$USER_B" "$JAR_B"
    if [ "$HAVE_C" = 1 ]; then
        login C "$USER_C" "$JAR_C"
    else
        skip "login C" "no third account available"
    fi
fi
if [ "$HAVE_A" != 1 ] || [ "$HAVE_B" != 1 ]; then
    printf '\nAborting: accounts A and B are required but could not be set up.\n'
    printf 'Registration is limited per client IP, so this host cannot create them:\n'
    printf 'seed the pair once on the deployment with\n'
    printf '  ci-tooling/seed-smoke-accounts.sh\n'
    printf 'or pass SMOKE_USER_A/B (+SMOKE_PASSWORD) / SMOKE_TOKEN_A/B.\n'
    exit 1
fi

req POST /auth/login "$JAR_ANON" "{\"username\":\"$USER_A\",\"password\":\"WrongPass123!\"}"
expect "wrong password refused" "401,429"

req GET /users/@me "$JAR_A"; expect "GET /users/@me" 200; ID_A="$(jget id)"
req GET /users/@me "$JAR_B"; ID_B="$(jget id)"
req GET /users/@me "$JAR_C"; ID_C="$(jget id)"
[ "$HAVE_C" = 1 ] || ID_C=""
# C is optional: it carries the stranger checks, and its registration is the
# one thing the per-IP budget can refuse.
if [ -n "$ID_A" ] && [ -n "$ID_B" ] && { [ "$HAVE_C" != 1 ] || [ -n "$ID_C" ]; }; then
    ok "identities resolve ($ID_A / $ID_B${ID_C:+ / $ID_C})"
else
    bad "identities resolve" "A=$ID_A B=$ID_B C=$ID_C"
fi
req GET /auth/sessions "$JAR_A"; expect "GET /auth/sessions" 200
req POST /auth/ws-ticket "$JAR_A"; expect "websocket ticket" 200

sec "profile and presence"
req PATCH /users/@me "$JAR_A" '{"bio":"live smoke suite","timezone":"UTC"}'
expect "PATCH /users/@me" 200
req GET "/features/users/$ID_A/profile" "$JAR_A"
field_is "profile bio persisted" profile.bio "live smoke suite"
req PUT /users/@me/presence "$JAR_A" '{"status":"dnd","custom_status":"smoke"}'
expect "PUT presence" 200
req GET "/users/$ID_A/presence" "$JAR_A"
field_is "presence reads back dnd" status "dnd"

sec "relationships"
req POST /relationships "$JAR_A" "{\"user_id\":\"$ID_B\"}"
expect "A sends a friend request to B" "200,201,409"
req PUT "/relationships/$ID_A/accept" "$JAR_B"
expect "B accepts the request" "200,204,404,409"
if [ "$HAVE_C" = 1 ]; then
    req POST /relationships "$JAR_C" "{\"user_id\":\"$ID_A\"}"
expect "C sends a request to A" "200,201,409"
req DELETE "/relationships/$ID_C" "$JAR_A"; expect "A declines C" "200,204,404"
fi

sec "servers and channels"
req POST /servers/ "$JAR_A" '{"name":"Live Smoke Server","description":"smoke suite"}'
expect "A creates a server" "200,201"
SERVER_ID="$(jget id)"
req POST "/servers/$SERVER_ID/channels" "$JAR_A" \
    '{"name":"general","channel_type":"text","topic":"smoke"}'
expect "create a text channel" "200,201"
CHANNEL_ID="$(jget id)"
req POST "/channels/$CHANNEL_ID/invites" "$JAR_A" '{"max_age":3600,"max_uses":5}'
expect "create an invite" "200,201"
INVITE="$(jget code)"
req POST "/channels/invites/$INVITE" "$JAR_B"
expect "B joins with the invite" "200,201,204"
if [ "$HAVE_C" = 1 ]; then
    req GET "/channels/$CHANNEL_ID/messages" "$JAR_C"
    expect "outsider cannot read channel history" "403,404"
fi

sec "messaging"
req POST "/channels/$CHANNEL_ID/messages" "$JAR_A" '{"content":"hello from smoke"}'
expect "A sends a message" "200,201"
MSG_ID="$(jget id)"
req GET "/channels/$CHANNEL_ID/messages" "$JAR_B"; expect "B reads history" 200
req PATCH "/channels/$CHANNEL_ID/messages/$MSG_ID" "$JAR_B" '{"content":"hijacked"}'
expect "B cannot edit A's message" 403
req PUT "/channels/$CHANNEL_ID/messages/$MSG_ID/reactions/%F0%9F%91%8D" "$JAR_B"
expect "B reacts" "200,201,204"
req PUT "/channels/$CHANNEL_ID/pins/$MSG_ID" "$JAR_A"; expect "pin the message" "200,204"
req DELETE "/channels/$CHANNEL_ID/pins/$MSG_ID" "$JAR_A"; expect "unpin the message" "200,204"

sec "bookmarks (server-channel id regression)"
# The client only holds the channel id; msg_messages stores the channel's own
# conversation id. Sending the channel id must work for any server channel.
req POST /features/bookmarks "$JAR_B" \
    "{\"message_id\":\"$MSG_ID\",\"conversation_id\":\"$CHANNEL_ID\",\"label\":\"smoke\"}"
expect "bookmark a channel message by channel id" "200,201"
req GET /features/bookmarks "$JAR_B"; expect "list bookmarks" 200
if jq -e --arg m "$MSG_ID" \
    '[.bookmarks[]? | select((.message_id|tostring) == $m)] | length > 0' \
    "$TMP/body.json" >/dev/null 2>&1; then
    ok "bookmark appears in the listing"
else
    bad "bookmark appears in the listing" "message $MSG_ID missing"
fi
req DELETE "/features/bookmarks/$MSG_ID" "$JAR_B"; expect "delete the bookmark" "200,204"

sec "threads"
req POST "/channels/$CHANNEL_ID/threads" "$JAR_A" \
    "{\"name\":\"smoke thread\",\"parent_message_id\":\"$MSG_ID\"}"
expect "create a thread from a message" "200,201"
THREAD_ID="$(jget id)"
if [ -n "$THREAD_ID" ]; then
    req POST "/threads/$THREAD_ID/messages" "$JAR_A" '{"content":"thread reply"}'
    expect "post in the thread" "200,201"
    req GET "/threads/$THREAD_ID" "$JAR_B"; expect "B reads the thread" 200
    req POST "/threads/$THREAD_ID/lock" "$JAR_A"; expect "lock the thread" "200,204"
    req POST "/threads/$THREAD_ID/unlock" "$JAR_A"; expect "unlock the thread" "200,204"
else
    skip "thread follow-ups" "no thread id"
fi

sec "direct messages"
req POST /users/@me/channels "$JAR_A" "{\"recipient_id\":\"$ID_B\"}"
expect "open a DM with B" "200,201"
DM_ID="$(jget id)"
if [ -n "$DM_ID" ]; then
    req POST "/channels/$DM_ID/messages" "$JAR_A" '{"content":"dm from smoke"}'
    expect "send a DM" "200,201"
    req GET "/channels/$DM_ID/messages" "$JAR_B"; expect "B reads the DM" 200
    if [ "$HAVE_C" = 1 ]; then
        req GET "/channels/$DM_ID/messages" "$JAR_C"
        expect "C cannot read the DM" "403,404"
    else
        skip "C cannot read the DM" "no third account"
    fi
else
    skip "DM follow-ups" "no DM channel id"
fi

sec "search"
req GET "/search/users?query=smoke" "$JAR_A"; expect "search users" 200
req GET "/search/messages?query=hello" "$JAR_A"; expect "search messages" 200

sec "admin denial for a normal account"
req GET /admin/dashboard "$JAR_A"; expect "normal account refused the admin dashboard" "401,403"
req GET /admin/users "$JAR_A"; expect "normal account refused admin users" "401,403,404"

if [ "${SMOKE_KEEP:-0}" = "1" ]; then
    skip "delete smoke accounts" "SMOKE_KEEP=1"
else
    sec "cleanup"
    # A and B stay: they are the gate's identity on this deployment, and keeping
    # them is what lets a rerun skip registration. Everything they created in
    # this run goes, and logging out has to take the session with it.
    if [ -n "$SERVER_ID" ]; then
        req DELETE "/servers/$SERVER_ID" "$JAR_A"
        expect "A deletes the smoke server" "200,204,404"
    fi
    req POST /auth/logout "$JAR_A"; expect "A logs out" "200,204"
    req GET /users/@me "$JAR_A"; expect "A's session is dead" "401,403"
    req POST /auth/logout "$JAR_B"; expect "B logs out" "200,204"
    req GET /users/@me "$JAR_B"; expect "B's session is dead" "401,403"
    if [ "$HAVE_C" = 1 ]; then
        req DELETE /users/@me "$JAR_C" "{\"password\":\"$PASSWORD\"}"
        expect "C deletes their account" "200,202,204"
        req GET /users/@me "$JAR_C"; expect "C's session is dead" "401,403"
    fi
fi

printf '\n%s passed, %s failed, %s skipped\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ]
