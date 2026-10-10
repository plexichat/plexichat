# Versioning and updates

This guide describes the versioning scheme for Plexichat and how to update a deployment.

## Version scheme

Plexichat uses a stage-based version string with the format:

```
[stage].[major].[minor]-[build]
```

Valid stages are `a`, `b`, `c`, and `r`. The regular expression enforced by the server is `^([abcr])\.(\d+)\.(\d+)-(\d+)$`. Strings are lowercased and trimmed before validation, and strings that contain `\r`, `\n`, or `\x00` are rejected.

### Components

- `stage` — Release stage: `a` (alpha), `b` (beta), `c` (candidate), `r` (release).
- `major` — Major version, minimum 1. Incremented for breaking changes.
- `minor` — Minor version, minimum 0. Incremented for new features.
- `build` — Build number, minimum 1. Resets to 1 when `minor` is incremented or when the stage changes.

### Stage order

The server compares versions in the order `stage` > `major` > `minor` > `build`. Stage priority is `a` < `b` < `c` < `r`. A version with a higher stage is always newer than a version with a lower stage, regardless of numeric components.

For example:

```
a.1.0-1 < a.1.0-2 < a.1.1-1 < b.1.0-1 < c.1.0-1 < r.1.0-1
```

An `r.1.0-1` server is newer than `a.99.99-999`.

### Examples

- `a.1.0-1` — Alpha 1.0, build 1
- `b.2.3-15` — Beta 2.3, build 15
- `r.1.0-1` — Release 1.0, build 1

## Check your version

The API prefix is `/api/v1` by default.

### Get server version

```bash
curl https://your-server.com/api/v1/version
```

Response (`ServerVersionResponse` in `src/api/schemas/version.py:34`):

```json
{
  "version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 51,
    "string": "a.1.0-51"
  },
  "min_supported_version": {
    "stage": "a",
    "major": 1,
    "minor": 0,
    "build": 1,
    "string": "a.1.0-1"
  },
  "api_version": "v1"
}
```

- `version` is a `VersionInfo` object with `stage`, `major`, `minor`, `build`, and `string` (`src/api/schemas/version.py:19`).
- `min_supported_version` is `null` if no minimum is configured.
- `api_version` is the API prefix version, currently `v1`.

Other endpoints:

- `POST /api/v1/version/negotiate` — Check whether a client version is compatible. Send `{"client_version": "a.1.0-1"}`. Returns `compatible`, `update_required`, and `update_recommended`, or HTTP 400 for an invalid format and HTTP 426 when an update is required (`src/api/routes/version.py:94`).
- `GET /api/v1/status` — Get server state, uptime, and maintenance information (`src/api/routes/version.py:230`).

You can also view the version in the admin panel and in the server logs at startup.

## When to update

- Use `a` and `b` builds only in test environments.
- Use `c` (candidate) in staging.
- Use `r` (release) in production.

Choose an update channel based on your stability needs:

- **Stable** — `r.x.x-x` only.
- **Testing** — `c.x.x-x` and `r.x.x-x`.
- **Development** — All stages.

## Update

### Before you update

1. Review the release notes for the target version.
2. Review the changelog for breaking changes.
3. Test the update in a staging environment that mirrors production.
4. Create a database backup and back up `config/config.yaml`.
5. Document your current version string.

### Update methods

#### Git update

```bash
sudo systemctl stop plexichat
cd /opt/plexichat
git stash
git pull origin main
source .venv/bin/activate
pip install --upgrade pip
pip install --require-hashes -r requirements.txt
python -m src.core.migrations.cli apply_migrations
sudo systemctl start plexichat
curl https://your-server.com/api/v1/version
```

#### Docker update

```bash
docker pull plexichat:latest
docker compose down
# Update your compose file if the release notes list new variables.
docker compose up -d
docker compose exec plexichat python -m src.core.migrations.cli apply_migrations
docker compose exec plexichat curl http://localhost:8000/api/v1/version
```

### After you update

Verify the update:

1. Check the version endpoint.
2. Check `GET /health` and `GET /api/v1/status`.
3. Log in to the admin panel and check for errors.
4. Send a test message and verify file uploads and WebSocket connectivity.

## Roll back

If an update fails, restore the previous code and, if needed, the database backup you created before the update.

```bash
# Git example
sudo systemctl stop plexichat
git log --oneline -10
git checkout <previous-commit-hash>
cp /backups/plexichat-pre-update-YYYYMMDD.db data/plexichat.db
sudo systemctl start plexichat
```

For Docker, set the image tag in your compose file to the previous version and run `docker compose up -d`. Restore the database from your backup if required.

## Database migrations

Plexichat applies pending migrations on startup in version order. Failed migrations prevent startup.

To manage migrations manually:

```bash
# List pending migrations
python -m src.core.migrations.cli list_migrations

# Apply pending migrations
python -m src.core.migrations.cli apply_migrations

# Preview without applying
python -m src.core.migrations.cli apply_migrations --dry-run

# Roll back a reversible migration
python -m src.core.migrations.cli rollback_migration <revision>
```

Some migrations are irreversible. They require the confirmation phrase `THE DATABASE IS BACKED UP` and respect the `irreversible_migration_delay_days` setting (default 7 days).

## Configuration changes

After you update, compare your `config/config.yaml` with the default configuration reference. Add new options and remove deprecated options. Check the release notes for new environment variables and restart the service after you change them.

## Troubleshooting

### Update fails

Check the error logs, verify network connectivity and disk space, and verify database connectivity.

### Migration fails

Check migration logs, verify that the database user has the required permissions, and verify that your backup is accessible.

### Service does not start after update

Check `journalctl -u plexichat -n 100`, validate `config.yaml` syntax, verify that required environment variables are set, and verify database connectivity. Roll back if you cannot resolve the issue.

## Related documentation

- [Deployment guide](./getting-started.md)
- [Migrations guide](../migrations.md)
- [Security best practices](../security.md)
