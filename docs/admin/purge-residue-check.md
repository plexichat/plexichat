# Account-purge residue check

Verify that erasing an account really erases it: not only the `auth_users` row, but the servers, channels, conversations, messages and stored uploads the account owned. The check purges a throwaway account through the admin force-purge endpoint and then reads the database and the storage backend back.

An account purge touches several tables and the object store, so a regression here is silent by default: the account is gone, the API says the purge succeeded, and the account's data stays behind owned by a user id that no longer exists. Nothing can administer it, and nothing can erase it. The check exists to make that failure loud.

## Before you begin

You need:

* To run the command on the same host as the server's database and media storage. The check starts its own server on `127.0.0.1`, so it reads the database it just wrote to, not a remote deployment's.
* A writable data directory (`~/.plexichat` by default, or the path set by `--config`).
* Nothing else. The check creates its own admin identity, its own fixtures, and removes them afterwards.

## Run the check

```bash
python main.py self-test --purge-residue
```

The flag is opt-in and is never part of the default suite, because the check deletes an account. `python main.py self-test` does not run it.

Use a different port when another self-test may be running on the same host:

```bash
python main.py --port 8100 self-test --purge-residue
```

The command exits `0` when every assertion passes and `1` when any fails.

## What it checks

1. **Seeds a throwaway account.** Registers `selftest_purge_<tag>` in-process (the reserved `selftest` prefix and the per-IP registration throttle do not apply), then logs in over HTTP like a client.
2. **Gives it a footprint.** The account creates a server, a channel in that server, a message, a text upload and a DM with the test admin. The upload is seeded as a precondition: if the object is not in storage before the purge, its removal cannot be proved, and the check fails rather than passing on an empty assertion.
3. **Purges it** through `POST /api/v1/admin/users/{user_id}/force-purge`, the same endpoint and code path an operator uses, with the account's own server and conversations in place.
4. **Reads the state back.** Every one of these must hold:

| Assertion | Fails when |
|---|---|
| `purge_residue_seed` | The account could not be registered, logged in, or given its server, channel, upload and DM. |
| `purge_residue_storage_object_present` | The upload's object is missing from storage before the purge — the later removal check would prove nothing. |
| `purge_residue_force_purge` | The admin endpoint did not answer `2xx`. |
| `purge_residue_left_no_rows` | Any of `auth_users`, `auth_sessions`, `srv_servers`, `srv_channels`, `srv_members`, `msg_participants` or `media_files` still has a row for the account, or a conversation it was alone in still exists without a participant. |
| `purge_residue_left_no_stored_objects` | The upload's object is still in the storage backend after its `media_files` row was deleted. |
| `purge_residue_spares_shared_dm` | The DM with the surviving user was erased too — a purge that over-deletes is a failure as well. |

The failure detail is printed with the run, for example:

```
purge_residue_left_no_rows
  Error: {"srv_servers": 374236208856961024, "srv_channels": 374236208894709760, "media_files": 1, "orphaned_conversations": [1565405123121778688]}
```

## How it fits with cleanup

The self-test's pre-run cleanup sweeps residue left by purges that predate the cascade, and it only touches rows whose owning account is already gone. That sweep is why this check needs to assert rather than rely on leftover rows being visible later: without it, the residue would be swept up and the next run would still be green. See `src/core/erasure.py` for the cascade the purge uses, and [User management](user-management.md) for the operator-facing purge.

## Run it in CI

`.gitlab-ci.yml` defines `test:purge_residue`, which runs `python main.py --port 8100 self-test --purge-residue` in the `test` stage on branch pipelines, in parallel with `test:pytest` and `test:self_test`. It has its own port so it can share a runner host with the default self-test, and it is a separate job so no ordinary run of the suite can purge an account.

## Related documentation

* [User management](user-management.md) — account deletion, grace periods and force purge.
* [Data export (DSAR)](data-export.md) — the other half of the erasure story: what an account can take with it.
* [CLI overview](../cli/overview.md) — `self-test` and its flags.
