# Local Janus gateway (development only)

The Janus adapter's media path is exercised two ways:

1. **Always, without a gateway.** `src/tests/voice/test_sfu_janus_gateway.py`
   asserts the fail-closed property the whole fix rests on: against an
   endpoint that cannot answer, the adapter raises instead of returning a
   usable-looking room, and an offer fails closed instead of being answered
   with fabricated SDP.
2. **Opt-in, against a real gateway.** With `PLEXICHAT_JANUS_URL` set, three
   more tests drive a real offer through room creation, the publish/`jsep`
   handshake, a trickled candidate, a layer configure and teardown.

Without the environment variable the second group **skips, loudly** — a green
run never implies the live path was exercised. This directory exists so that
skipping is a choice rather than the only option.

## Run it

```bash
./docker/janus/run-janus-gateway.sh up     # start it, wait for VideoRoom
PLEXICHAT_JANUS_URL=http://127.0.0.1:8088/janus \
    python -m pytest src/tests/voice/test_sfu_janus_gateway.py -q
./docker/janus/run-janus-gateway.sh down   # tear down, drop container state
```

`up` is only successful once the gateway has answered an
`{"janus": "info"}` request **with `janus.plugin.videoroom` loaded**, so a
"ready" line cannot mean "the container started but the plugin is missing".
Set `PLEXICHAT_JANUS_API` to probe a different address and
`PLEXICHAT_JANUS_WAIT_S` to change the timeout (default 60s).

If the gateway is configured with an API secret, export it as
`PLEXICHAT_JANUS_SECRET` for the tests; the compose file here does not set one.

## Point the server at it

The adapter is only one half. To run the whole voice plane against this
gateway, in `config.yaml`:

```yaml
voice:
  enabled: true
  sfu_backend: janus
  janus_url: http://127.0.0.1:8088/janus
```

`http://localhost:8088/janus` is the default `janus_url` in
`src/server/initializer.py`, so the compose file publishes exactly that port.

## Shape of the thing

- `docker-compose.janus.yml` — `canyan/janus-gateway:0.10.7`, pinned to a
  released tag. Verify the digest out of band before trusting the image
  (`docker inspect --format '{{index .RepoDigests 0}}' <image>`), the same
  policy `docker-compose.yml` documents for its third-party images.
- `run-janus-gateway.sh` — start/stop plus the readiness probe.

## Limits, stated plainly

- **Development gateway.** The API is unauthenticated and published on the
  host; nothing here is hardened. Do not run it on a shared or production
  host.
- **Signaling, not RTP.** Only the API port is published, so ICE candidates
  are gathered inside the container. The three opt-in tests cover the
  signaling handshake (the part the adapter drives); real media between peers
  needs host networking or `nat_1_1_mapping` set to the host address in a
  mounted `janus.jcfg`.
- **Not exercised in this repository's CI.** These tests are opt-in by design
  because they need a container runtime; the tooling makes them runnable, it
  does not make them run everywhere.
