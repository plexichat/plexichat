# Disaster Recovery Guide

This guide covers disaster recovery procedures for Plexichat deployments, including backup strategies, failover procedures, and incident response.

## Overview

Disaster recovery (DR) planning ensures business continuity in the event of system failures, data corruption, security incidents, or natural disasters. This guide provides procedures for:

- Backup and restore operations
- Failover to secondary systems
- Data recovery testing
- Incident response
- Keyring and encryption key management

## Recovery Objectives

### RTO (Recovery Time Objective)

Target time to restore services after a disaster:

- **Critical services (API, Gateway)**: 4 hours
- **Non-critical services (Admin panel, Analytics)**: 24 hours
- **Full system recovery**: 8 hours

### RPO (Recovery Point Objective)

Maximum acceptable data loss:

- **Database**: 15 minutes (based on backup frequency)
- **Media files**: 1 hour (based on sync frequency)
- **Configuration**: 5 minutes (real-time backup)

## Backup Strategy

### Database Backups

#### PostgreSQL Backups

**Daily Full Backup:**

```bash
# Automated daily backup at 2 AM
0 2 * * * pg_dump -h localhost -U plexichat plexichat | gzip > /backups/plexichat-$(date +\%Y\%m\%d).sql.gz
```

**Hourly Incremental Backup:**

```bash
# Hourly incremental backup
0 * * * * pg_dump -h localhost -U plexichat plexichat --format=directory --file=/backups/incremental/$(date +\%Y\%m\%d-\%H)
```

**Manual Backup:**

```bash
pg_dump -h localhost -U plexichat plexichat > plexichat-backup.sql
```

**Restore from Backup:**

```bash
# Stop Plexichat service
sudo systemctl stop plexichat

# Restore database
gunzip < /backups/plexichat-20240101.sql.gz | psql -h localhost -U plexichat plexichat

# Start Plexichat service
sudo systemctl start plexichat
```

#### SQLite Backups

**Daily Backup:**

```bash
# Backup SQLite database
cp ~/.plexichat/data/plexichat.db /backups/plexichat-$(date +\%Y\%m\%d).db
```

**Restore from Backup:**

```bash
# Stop Plexichat service
sudo systemctl stop plexichat

# Restore database
cp /backups/plexichat-20240101.db ~/.plexichat/data/plexichat.db

# Start Plexichat service
sudo systemctl start plexichat
```

### Media Backups

#### Local Storage

**Daily Sync:**

```bash
# Sync media directory to backup location
rsync -av ~/.plexichat/media/ /backups/media/
```

**Restore:**

```bash
# Restore media directory
rsync -av /backups/media/ ~/.plexichat/media/
```

#### S3/MinIO Backups

**Enable Versioning:**

```bash
aws s3api put-bucket-versioning \
  --bucket plexichat-media \
  --versioning-configuration Status=Enabled
```

**Lifecycle Policy:**

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket plexichat-media \
  --lifecycle-configuration file://lifecycle.json
```

**lifecycle.json:**
```json
{
  "Rules": [
    {
      "Id": "DeleteOldVersions",
      "Status": "Enabled",
      "Prefix": "",
      "NoncurrentVersionExpiration": {
        "NoncurrentDays": 30
      }
    }
  ]
}
```

**Restore from S3:**

```bash
# List object versions
aws s3api list-object-versions --bucket plexichat-media

# Restore specific version
aws s3api get-object \
  --bucket plexichat-media \
  --key path/to/file.jpg \
  --version-id versionId \
  /restore/path/file.jpg
```

### Configuration Backups

**Daily Backup:**

```bash
# Backup configuration
cp ~/.plexichat/config/config.yaml /backups/config-$(date +\%Y\%m\%d).yaml
```

**Restore:**

```bash
# Restore configuration
cp /backups/config-20240101.yaml ~/.plexichat/config/config.yaml
```

### Keyring Backups

Keyrings contain encryption keys for sensitive data. Secure backup is critical.

**Export Keyring:**

```bash
# Backup keyring with KEK
python -m src.utils.encryption.keyring_manager \
  --export \
  --keyring ~/.plexichat/data/message_keyring.json \
  --kek-env PLEXICHAT_MESSAGE_KEY \
  --output /secure/backup/message_keyring.enc
```

**Restore Keyring:**

```bash
# Restore keyring with KEK
python -m src.utils.encryption.keyring_manager \
  --import \
  --keyring ~/.plexichat/data/message_keyring.json \
  --kek-env PLEXICHAT_MESSAGE_KEY \
  --input /secure/backup/message_keyring.enc
```

**KEK Backup:**

Store KEKs (Key Encryption Keys) securely:
- Use hardware security module (HSM) if available
- Store in password manager (1Password, LastPass)
- Use offline storage (air-gapped system)
- Never commit KEKs to version control

## Disaster Scenarios

### Scenario 1: Server Failure

**Symptoms:**
- Server unresponsive
- Cannot SSH to server
- Services not running

**Recovery Procedure:**

1. **Assess Impact:**
   - Check monitoring dashboard
   - Identify affected services
   - Determine user impact

2. **Attempt Recovery:**
   - Try remote reboot (if possible)
   - Check server status
   - Review system logs

3. **Failover (if recovery fails):**
   - Promote standby server
   - Update DNS to point to standby
   - Verify services are running

4. **Restore Failed Server:**
   - Reinstall operating system
   - Restore from backup
   - Reconfigure services
   - Return to primary when ready

### Scenario 2: Database Corruption

**Symptoms:**
- Database errors in logs
- Queries failing
- Data inconsistencies

**Recovery Procedure:**

1. **Stop Services:**
   ```bash
   sudo systemctl stop plexichat
   ```

2. **Identify Corruption:**
   ```bash
   # PostgreSQL
   psql -U plexichat -d plexichat -c "SELECT * FROM pg_stat_database WHERE datname = 'plexichat';"

   # SQLite
   sqlite3 ~/.plexichat/data/plexichat.db "PRAGMA integrity_check;"
   ```

3. **Restore from Backup:**
   ```bash
   # PostgreSQL
   gunzip < /backups/plexichat-latest.sql.gz | psql -U plexichat plexichat

   # SQLite
   cp /backups/plexichat-latest.db ~/.plexichat/data/plexichat.db
   ```

4. **Verify Integrity:**
   ```bash
   # Run integrity check
   psql -U plexichat -d plexichat -c "SELECT * FROM pg_stat_database WHERE datname = 'plexichat';"
   ```

5. **Start Services:**
   ```bash
   sudo systemctl start plexichat
   ```

### Scenario 3: Data Loss

**Symptoms:**
- Accidental deletion
- Malicious activity
- Application bug

**Recovery Procedure:**

1. **Identify Lost Data:**
   - Review audit logs
   - Identify affected tables/records
   - Determine time of loss

2. **Point-in-Time Recovery (PostgreSQL):**
   ```bash
   # Find appropriate backup
   ls -lt /backups/plexichat-*.sql.gz

   # Restore to point before data loss
   gunzip < /backups/plexichat-20240101-1200.sql.gz | psql -U plexichat plexichat
   ```

3. **Replay WAL Logs (PostgreSQL):**
   ```bash
   # Replay WAL logs to specific time
   pg_restore -U plexichat -d plexichat --recovery-target-time="2024-01-01 12:30:00" /backups/plexichat-backup
   ```

4. **Verify Data:**
   - Check lost data is restored
   - Verify data integrity
   - Test application functionality

### Scenario 4: Security Incident

**Symptoms:**
- Unauthorized access
- Data breach
- Malware infection

**Recovery Procedure:**

1. **Contain Incident:**
   - Isolate affected systems
   - Block malicious IPs
   - Disable compromised accounts
   - Change all credentials

2. **Assess Damage:**
   - Review access logs
   - Identify compromised data
   - Determine scope of breach
   - Document findings

3. **Eradicate Threat:**
   - Remove malware
   - Patch vulnerabilities
   - Close security gaps
   - Update security policies

4. **Recover Systems:**
   - Restore from clean backup
   - Rebuild compromised systems
   - Update configurations
   - Strengthen security

5. **Post-Incident Review:**
   - Document lessons learned
   - Update incident response plan
   - Implement additional safeguards
   - Train staff on security

## Failover Procedures

### Database Failover

**PostgreSQL Streaming Replication:**

**Setup Standby:**
```bash
# On standby server
sudo -u postgres pg_basebackup -h primary-server -D /var/lib/postgresql/data -P -U replicator -R
```

**Promote Standby:**
```bash
# On standby server
sudo -u postgres pg_ctl promote -D /var/lib/postgresql/data
```

**Update Application Config:**
```yaml
# config/config.yaml
database:
  host: standby-server-ip
  port: 5432
```

### Redis Failover

**Redis Sentinel:**

**Setup Sentinel:**
```bash
# sentinel.conf
sentinel monitor mymaster primary-server 6379 2
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 10000
sentinel parallel-syncs mymaster 1
```

**Automatic Failover:**
- Sentinel automatically promotes slave to master
- Application reconnects to new master
- No manual intervention required

### Application Failover

**Load Balancer Configuration:**

```nginx
upstream plexichat {
    server primary-server:8000 max_fails=3 fail_timeout=30s;
    server standby-server:8000 backup;
}

server {
    location / {
        proxy_pass http://plexichat;
    }
}
```

**Manual Failover:**
```bash
# Update DNS to point to standby
# Or update load balancer configuration
```

## Restoration Testing

### Backup Validation

**Automated Validation:**

```bash
#!/bin/bash
# validate-backup.sh

# Restore backup to test database
createdb plexichat_test
gunzip < /backups/plexichat-latest.sql.gz | psql -U plexichat plexichat_test

# Run integrity checks
psql -U plexichat -d plexichat_test -c "SELECT COUNT(*) FROM users;"
psql -U plexichat -d plexichat_test -c "SELECT COUNT(*) FROM servers;"

# Clean up
dropdb plexichat_test
```

**Schedule:**
- Daily automated validation
- Weekly full restoration test
- Monthly disaster recovery drill

### DR Drill

**Quarterly DR Drill:**

1. **Plan Drill:**
   - Define scenario (server failure, data loss, etc.)
   - Set objectives
   - Assign roles
   - Schedule drill

2. **Execute Drill:**
   - Simulate disaster
   - Execute recovery procedures
   - Document issues
   - Measure recovery time

3. **Review Results:**
   - Compare RTO/RPO to objectives
   - Identify gaps
   - Update procedures
   - Train staff

## Incident Response

### Incident Response Team

**Roles:**
- **Incident Commander**: Coordinates response
- **Technical Lead**: Manages technical recovery
- **Communications Lead**: Manages communications
- **Security Lead**: Handles security aspects

### Incident Response Process

**1. Detection:**
- Monitor alerts
- Identify incident
- Classify severity

**2. Containment:**
- Isolate affected systems
- Prevent spread
- Preserve evidence

**3. Eradication:**
- Remove threat
- Patch vulnerabilities
- Secure systems

**4. Recovery:**
- Restore from backup
- Verify systems
- Resume operations

**5. Post-Incident:**
- Document incident
- Analyze root cause
- Implement improvements

### Severity Levels

**P1 - Critical:**
- Complete system outage
- Data breach
- Immediate response required
- RTO: 4 hours

**P2 - High:**
- Partial system outage
- Significant degradation
- Response within 1 hour
- RTO: 8 hours

**P3 - Medium:**
- Minor degradation
- Limited impact
- Response within 4 hours
- RTO: 24 hours

**P4 - Low:**
- Minimal impact
- Routine issue
- Response within 24 hours
- RTO: 48 hours

## KEK Migration

### When to Migrate KEKs

- KEK compromised
- Regular key rotation (recommended annually)
- Security policy change
- Compliance requirements

### Migration Procedure

**1. Generate New KEK:**

```bash
# Generate new KEK
export PLEXICHAT_MESSAGE_KEY_NEW=$(openssl rand -base64 32)
```

**2. Migrate Keyring:**

```bash
# Migrate keyring to new KEK
python -m src.utils.encryption.kek_migration \
  --keyring ~/.plexichat/data/message_keyring.json \
  --new-kek-env PLEXICHAT_MESSAGE_KEY_NEW \
  --dry-run
```

**3. Validate Migration:**

```bash
# Validate keyring with new KEK
python -m src.utils.encryption.kek_migration \
  --keyring ~/.plexichat/data/message_keyring.json \
  --new-kek-env PLEXICHAT_MESSAGE_KEY_NEW \
  --validate
```

**4. Perform Migration:**

```bash
# Perform actual migration
python -m src.utils.encryption.kek_migration \
  --keyring ~/.plexichat/data/message_keyring.json \
  --new-kek-env PLEXICHAT_MESSAGE_KEY_NEW
```

**5. Update Configuration:**

```bash
# Update environment variable
export PLEXICHAT_MESSAGE_KEY=$PLEXICHAT_MESSAGE_KEY_NEW
```

**6. Rollback (if needed):**

```bash
# Rollback migration
python -m src.utils.encryption.kek_migration \
  --rollback \
  --keyring ~/.plexichat/data/message_keyring.json
```

## Monitoring and Alerting

### Health Checks

**API Health Check:**

```bash
curl https://api.plexichat.com/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "version": "a.1.0-51"
}
```

### Monitoring Metrics

**Key Metrics:**
- API response time
- Database connection pool
- Redis connection pool
- WebSocket connections
- Error rates
- Disk usage
- Memory usage

### Alerting

**Alert Conditions:**
- API response time > 1s
- Error rate > 5%
- Database connection failures
- Redis connection failures
- Disk usage > 80%
- Memory usage > 90%

**Alert Channels:**
- Email
- Slack/Teams
- SMS (for critical alerts)
- PagerDuty (for on-call)

## Documentation

### Runbook Maintenance

**Update Runbook When:**
- System architecture changes
- New services added
- Procedures updated
- Lessons learned from incidents

**Review Schedule:**
- Quarterly review
- Annual major update
- Post-incident review

### Contact Information

**Emergency Contacts:**
- System Administrator
- Database Administrator
- Security Team
- Management

**Service Providers:**
- Cloud provider support
- Database support
- Network provider

## Best Practices

### Backup Best Practices

- **3-2-1 Rule**: 3 copies, 2 different media, 1 offsite
- **Encrypt Backups**: Encrypt sensitive backup data
- **Test Restores**: Regularly test backup restoration
- **Document Procedures**: Maintain clear recovery procedures
- **Monitor Backups**: Ensure backups complete successfully

### Security Best Practices

- **Least Privilege**: Use minimal required permissions
- **Multi-Factor Auth**: Require MFA for sensitive operations
- **Audit Logs**: Enable comprehensive logging
- **Regular Updates**: Keep systems patched
- **Security Training**: Train staff on security best practices

### Testing Best Practices

- **Regular Drills**: Conduct quarterly DR drills
- **Document Results**: Document drill outcomes
- **Continuous Improvement**: Update procedures based on results
- **Realistic Scenarios**: Test realistic disaster scenarios
- **Staff Training**: Train all staff on procedures

## Resources

- [Deployment Guide](index.md)
- [Configuration Guide](configuration.md)
- [Security Best Practices](security.md)
- [Performance Guide](performance.md)
- [Troubleshooting Guide](../troubleshooting/index.md)
