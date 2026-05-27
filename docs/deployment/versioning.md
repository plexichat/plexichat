# Versioning and Updates

This guide explains Plexichat's versioning scheme and how to update your deployment to new versions.

## Versioning Scheme

Plexichat uses a stage-based versioning scheme:

```
[stage].[major].[minor]-[build]
```

### Components

- `stage` (a, b, c, r): Alpha, Beta, Candidate, Release
- `major` (1+): Major version (breaking changes)
- `minor` (0+): Minor version (new features)
- `build` (1+): Build number

### Stage Meanings

- **a (Alpha)**: Early development, unstable, may have breaking changes
- **b (Beta)**: Feature complete, testing phase, relatively stable
- **c (Candidate)**: Release candidate, stable, ready for production testing
- **r (Release)**: Stable release, recommended for production

### Examples

- `a.1.0-1` - Alpha 1.0, build 1
- `b.2.3-15` - Beta 2.3, build 15
- `r.1.0-1` - Release 1.0, build 1

### Version Comparison

Versions are compared by: stage -> major -> minor -> build

```
a.1.0-1 < a.1.0-2 < a.1.1-1 < a.2.0-1 < b.1.0-1 < r.1.0-1
```

Stage priority: a < b < c < r

## Checking Your Version

You can check your current Plexichat version via:

### API Endpoint

```bash
curl https://your-server.com/api/v1/version
```

Response:
```json
{
  "version": "a.1.0-51",
  "minimum_client_version": "a.1.0-0"
}
```

### Admin Panel

The version is displayed in the admin panel dashboard and footer.

### Server Logs

The version is logged on server startup.

## Update Strategy

### When to Update

- **Alpha/Beta**: Only for testing environments, not production
- **Candidate**: Good for staging environments
- **Release**: Safe for production deployments

### Update Channels

Choose a channel based on your stability requirements:

- **Stable**: Only release versions (r.x.x-x)
- **Testing**: Release candidates and releases (c.x.x-x, r.x.x-x)
- **Development**: All versions including alpha/beta

## Update Procedure

### Pre-Update Checklist

Before updating, complete these steps:

- [ ] Review the release notes for the target version
- [ ] Check for breaking changes in the version changelog
- [ ] Test the update in a staging environment first
- [ ] Create a full database backup
- [ ] Backup your configuration files
- [ ] Backup media files (if using local storage)
- [ ] Document your current version
- [ ] Schedule a maintenance window if needed

### Backup Procedure

```bash
# Database backup (PostgreSQL)
pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-pre-update-$(date +%Y%m%d).sql.gz

# Database backup (SQLite)
cp data/plexichat.db /backups/plexichat-pre-update-$(date +%Y%m%d).db

# Configuration backup
cp config/config.yaml /backups/config-pre-update-$(date +%Y%m%d).yaml

# Media backup (if using local storage)
rsync -av ~/.plexichat/media/ /backups/media-pre-update-$(date +%Y%m%d)/
```

### Update Methods

#### Method 1: Git Update (Recommended for Development)

```bash
# Stop the service
sudo systemctl stop plexichat

# Navigate to installation directory
cd /opt/plexichat

# Stash any local changes
git stash

# Pull latest code
git pull origin main

# Update dependencies
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Run database migrations
python -m src.core.migrations.cli apply_migrations

# Start the service
sudo systemctl start plexichat

# Verify the update
curl https://your-server.com/api/v1/version
```

#### Method 2: Docker Update

```bash
# Pull new image
docker pull plexichat:latest

# Stop containers
docker compose down

# Update compose file if needed
# (check for new environment variables or configuration changes)

# Start containers
docker compose up -d

# Run migrations
docker compose exec plexichat python -m src.core.migrations.cli apply_migrations

# Verify the update
docker compose exec plexichat curl http://localhost:8000/api/v1/version
```

#### Method 3: Manual Update

```bash
# Download new release
wget https://releases.plexichat.com/plexichat-r.1.0-1.tar.gz

# Extract
tar -xzf plexichat-r.1.0-1.tar.gz
cd plexichat-r.1.0-1

# Copy configuration from old installation
cp /opt/plexichat/config/config.yaml config/

# Copy database (if using SQLite)
cp /opt/plexichat/data/plexichat.db data/

# Install dependencies
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run migrations
python -m src.core.migrations.cli apply_migrations

# Update service configuration
sudo systemctl edit plexichat
# Update ExecStart path to new installation

# Start service
sudo systemctl start plexichat
```

### Post-Update Verification

After updating, verify:

1. **Version Check**
   ```bash
   curl https://your-server.com/api/v1/version
   ```

2. **Health Check**
   ```bash
   curl https://your-server.com/health
   ```

3. **Database Connectivity**
   ```bash
   curl https://your-server.com/api/v1/status
   ```

4. **Admin Panel Access**
   - Log in to admin panel
   - Check dashboard for errors
   - Verify user counts and metrics

5. **Functionality Tests**
   - Test user login
   - Test message sending
   - Test file uploads
   - Test WebSocket connection

## Rollback Procedure

If the update causes issues, rollback to the previous version:

### Git Rollback

```bash
# Stop service
sudo systemctl stop plexichat

# Rollback to previous commit
git log --oneline -10
git checkout <previous-commit-hash>

# Restore database if needed
cp /backups/plexichat-pre-update-YYYYMMDD.db data/plexichat.db

# Start service
sudo systemctl start plexichat
```

### Docker Rollback

```bash
# Stop containers
docker compose down

# Use previous image tag
# Edit compose file to use previous tag
# e.g., image: plexichat:r.1.0-50

# Start containers
docker compose up -d

# Restore database if needed
docker compose exec postgres psql -U plexichat plexichat < /backups/plexichat-pre-update-YYYYMMDD.sql
```

## Database Migrations

### Automatic Migrations

Plexichat automatically runs pending database migrations on startup:

- Migrations are applied in version order
- Migration status is tracked in the database
- Failed migrations prevent server startup

### Manual Migration Control

For more control over migrations:

```bash
# Check pending migrations
python -m src.core.migrations.cli list_migrations

# Apply pending migrations (applies all unapplied migrations in order)
python -m src.core.migrations.cli apply_migrations

# Rollback migration (if reversible)
python -m src.core.migrations.cli rollback_migration <revision>

# Dry-run migration
python -m src.core.migrations.cli apply_migrations --dry-run
```

### Irreversible Migrations

Some migrations are irreversible (drop columns/tables):

- These require a confirmation phrase: "THE DATABASE IS BACKED UP"
- They have a configurable delay period (default 7 days)
- Use the admin panel to run these migrations
- See [Migrations Guide](../migrations.md) for details

## Configuration Changes

### Checking for New Configuration Options

After an update, check for new configuration options:

1. Review the [Default Configuration Reference](../default-config.md)
2. Compare with your current `config/config.yaml`
3. Add new options with appropriate values
4. Remove deprecated options

### Environment Variables

New versions may introduce new environment variables:

- Check release notes for new variables
- Update your `.env` file or systemd service
- Restart the service after changes

## Breaking Changes

### Identifying Breaking Changes

Breaking changes are documented in:

- Release notes
- Migration documentation
- API changelog

### Common Breaking Changes

- Database schema changes
- API endpoint changes
- Configuration structure changes
- Dependency version changes

### Handling Breaking Changes

1. Review breaking change documentation
2. Update your configuration accordingly
3. Update any custom integrations
4. Test thoroughly in staging
5. Plan for potential downtime

## Update Best Practices

### Regular Updates

- Stay within 2-3 versions of the latest release
- Test updates in staging before production
- Keep backups before every update
- Monitor release notes for important changes

### Production Updates

- Only use release versions (r.x.x-x)
- Schedule updates during low-traffic periods
- Have a rollback plan ready
- Notify users of scheduled maintenance

### Testing Updates

- Use a staging environment that mirrors production
- Test all critical functionality
- Verify integrations work correctly
- Check performance metrics

## Monitoring After Update

After updating, monitor:

- Error rates in logs
- Database performance
- API response times
- WebSocket connection stability
- User-reported issues

## Troubleshooting

### Update Fails

**Symptoms**: Update process fails partway through

**Solutions**:
- Check error logs for specific failure
- Verify network connectivity
- Ensure sufficient disk space
- Check database connectivity
- Try the update again

### Migration Fails

**Symptoms**: Database migration fails

**Solutions**:
- Check migration logs in admin panel
- Verify database backup is accessible
- Ensure database user has required permissions
- Check for schema conflicts
- Contact support if needed

### Service Won't Start After Update

**Symptoms**: Service fails to start after update

**Solutions**:
- Check service logs: `journalctl -u plexichat -n 100`
- Verify configuration syntax
- Check for missing environment variables
- Verify database connectivity
- Rollback if necessary

### Performance Degradation After Update

**Symptoms**: Slower performance after update

**Solutions**:
- Check database query performance
- Review new configuration options
- Check for new background processes
- Monitor resource utilization
- Report performance issues

## Support

For update-related issues:

1. Check the troubleshooting section above
2. Review release notes and migration documentation
3. Search existing issues in the project tracker
4. Contact support with version details and error logs

## Related Documentation

- [Deployment Guide](./getting-started.md) - Initial deployment setup
- [Migrations Guide](../migrations.md) - Database migration details
- [Migration Reference](../migration-reference.md) - Detailed migration reference
- [Security Best Practices](../security.md) - Security considerations for updates
- [Performance Tuning](../performance.md) - Performance optimization
