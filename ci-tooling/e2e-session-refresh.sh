#!/usr/bin/env bash
# End-to-end check of the session-refresh bug against a live server.
#
# Reproduces the user-visible failure: log in with cookies, let the client call
# /auth/refresh (which rotates the session secret), then keep using the session.
# Before the fix the response carried TWO plexichat_token Set-Cookie headers —
# the fresh one from the route and the rolled-over copy of the token the request
# presented — and a browser keeps the last, so the jar held the dead token and
# every later request 401'd ("signed out", and sends that vanish on reload).
#
# Usage: API=https://api.plexichat.com/api/v1 ./e2e-session-refresh.sh
set -uo pipefail

API="${API:-https://api.plexichat.com/api/v1}"
MARK='X-Plexichat-Request: 1'
USER="e2e_$(date +%s)_$RANDOM"
PASS="TestPass123!"
DIR="$(mktemp -d)"
JAR="$DIR/jar.txt"
FAILED=0

pass() { echo "  PASS: $*"; }
fail() { echo "  FAIL: $*"; FAILED=1; }
step() { echo; echo "== $* =="; }

step "register $USER"
code=$(curl -s -o "$DIR/reg.json" -w '%{http_code}' \
  -X POST "$API/auth/register" -H 'Content-Type: application/json' -H "$MARK" \
  -d "{\"username\":\"$USER\",\"email\":\"$USER@example.com\",\"password\":\"$PASS\"}")
[ "$code" = "200" ] && pass "register -> 200" || { fail "register -> $code"; head -c 300 "$DIR/reg.json"; echo; }

step "login (cookie flow)"
code=$(curl -s -o "$DIR/login.json" -w '%{http_code}' -c "$JAR" \
  -X POST "$API/auth/login" -H 'Content-Type: application/json' -H "$MARK" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}")
[ "$code" = "200" ] && pass "login -> 200" || { fail "login -> $code"; head -c 300 "$DIR/login.json"; echo; }

cookie_before=$(grep -o 'plexichat_token[[:space:]].*' "$JAR" | awk '{print $NF}')
echo "  session cookie after login: ${cookie_before:0:16}…"

step "authenticated call before refresh"
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$API/users/@me")
[ "$code" = "200" ] && pass "GET /users/@me -> 200" || fail "GET /users/@me -> $code"

step "refresh (the line that used to poison the session)"
code=$(curl -s -o "$DIR/refresh.json" -D "$DIR/refresh.hdr" -w '%{http_code}' -b "$JAR" -c "$JAR" \
  -X POST "$API/auth/refresh" -H "$MARK")
[ "$code" = "200" ] && pass "refresh -> 200" || { fail "refresh -> $code"; head -c 300 "$DIR/refresh.json"; echo; }

# The regression: one Set-Cookie for the session cookie, not two.
issued=$(grep -ci '^set-cookie: *plexichat_token=' "$DIR/refresh.hdr" || true)
if [ "$issued" = "1" ]; then
  pass "refresh issued exactly 1 session cookie"
else
  fail "refresh issued $issued session cookies (expected 1) — the stale one wins in a browser"
  grep -i '^set-cookie' "$DIR/refresh.hdr" | sed 's/\(plexichat_token=\)[^;]*/\1<redacted>/' | sed 's/^/        /'
fi

cookie_after=$(grep -o 'plexichat_token[[:space:]].*' "$JAR" | awk '{print $NF}')
if [ -n "$cookie_after" ] && [ "$cookie_after" != "$cookie_before" ]; then
  pass "session rotated (cookie changed)"
else
  fail "cookie did not rotate"
fi

step "session still works after the rotation (this is what was broken)"
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$API/users/@me")
[ "$code" = "200" ] && pass "GET /users/@me -> 200 after refresh" || fail "GET /users/@me -> $code after refresh"

code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" "$API/users/@me/settings")
[ "$code" = "200" ] && pass "GET /users/@me/settings -> 200 after refresh" || fail "GET /users/@me/settings -> $code"

step "send a message (the 'not sending, gone on reload' path)"
srv=$(curl -s -b "$JAR" -X POST "$API/servers/" -H 'Content-Type: application/json' -H "$MARK" \
  -d '{"name":"e2e-check"}' | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')
[ -n "$srv" ] && pass "created server $srv" || fail "server create failed"

chan=""
if [ -n "$srv" ]; then
  chan=$(curl -s -b "$JAR" -X POST "$API/servers/$srv/channels" -H 'Content-Type: application/json' -H "$MARK" \
    -d '{"name":"general","type":"text"}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("id",""))')
  [ -n "$chan" ] && pass "created channel $chan" || fail "channel create failed"
fi

if [ -n "$chan" ]; then
  code=$(curl -s -o "$DIR/send.json" -w '%{http_code}' -b "$JAR" \
    -X POST "$API/channels/$chan/messages" -H 'Content-Type: application/json' -H "$MARK" \
    -d '{"content":"e2e session refresh check"}')
  [ "$code" = "200" ] && pass "POST message -> 200" || { fail "POST message -> $code"; head -c 200 "$DIR/send.json"; echo; }

  # "Deleted on reload": the message must come back from the server, not just
  # have been rendered optimistically.
  got=$(curl -s -b "$JAR" "$API/channels/$chan/messages" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get("messages",[]); print(sum(1 for m in items if "e2e session refresh check" in str(m.get("content",""))))')
  [ "${got:-0}" -ge 1 ] && pass "message persisted and is returned by GET ($got)" \
    || fail "message NOT returned by GET after sending (would vanish on reload)"
fi

step "cleanup"
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" -X POST "$API/auth/logout" -H "$MARK")
[ "$code" = "200" ] && pass "logout -> 200" || fail "logout -> $code"

echo
if [ "$FAILED" = "0" ]; then
  echo "ALL CHECKS PASSED (test account: $USER)"
else
  echo "SOME CHECKS FAILED (test account: $USER)"
fi
rm -rf "$DIR"
exit "$FAILED"
