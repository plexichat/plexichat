# Operations guide

This guide describes how to operate a Plexichat deployment, including daily, weekly, and monthly tasks, incident response, backups, and monitoring.

## Before you begin

- Ensure you have an admin account with the appropriate role assignments.
- Familiarize yourself with the [RBAC guide](rbac.md) and the [security guide](security.md).
- Keep the [versioning and updates guide](../deployment/versioning.md) handy for upgrades and rollbacks.

## Daily operations

### Check system health

Complete this checklist each morning:

- Verify that all services are running.
- Check CPU, memory, and disk usage.
- Review error logs for critical issues.
- Verify database connectivity.

### Review security signals

- Review failed login attempts from the last 24 hours.
- Check for new admin accounts or permission changes.
- Verify that two-factor authentication (2FA) compliance is intact.

### Check performance

- Review API response times and database query times.
- Monitor memory usage and disk space.
- Verify that background jobs and the database connection pool are healthy.

### Monitor key metrics

Track these metrics daily:

| Metric | What to look for |
|--------|------------------|
| Active users | Unexpected spikes or drops |
| Message volume | Throughput trends |
| API response time | p95 under 500 ms |
| Error rate | Under 1% |
| Database connection pool | No exhaustion or long wait times |
| System resources | CPU and memory under 80%, disk under 70% |

### Respond to alerts

**Critical alerts — respond immediately:**

- A service is down.
- Database connectivity has failed.
- A security breach is detected.
- Disk usage is critical (less than 10% free).
- Memory is exhausted.

**Warning alerts — respond within 4 hours:**

- Elevated error rates.
- Performance degradation.
- Unusual activity patterns.
- Resource limits are approaching.

## Weekly operations

### Audit access and configuration

- Review the admin account list and remove unused accounts.
- Verify role assignments and recent permission changes.
- Review audit logs for sensitive actions and unusual patterns.
- Verify that approval-workflow and audit-logging settings are correct.
- Verify RBAC settings under `admin_ui.rbac`.

### Review performance

- Review response-time trends and slow queries.
- Check cache hit rates and database index performance.
- Review growth trends and plan capacity upgrades.

### Perform maintenance

- Verify that log rotation is working and check log file sizes.
- Archive old logs if needed and verify log backups completed.
- Check database size, index performance, and table bloat.

## Monthly operations

### Assess security and compliance

- Run vulnerability scans and review dependency updates and security advisories.
- Certify admin access: review all admin accounts, remove unnecessary access, and document decisions.
- Verify audit-trail completeness and policy compliance.

### Maintain the system

- Review available updates, test them in staging, and schedule production updates.
- Verify that backups completed and test restoring a backup.
- Review retention settings and update backup procedures.
- Identify and implement performance optimizations and document changes.

### Report status

Include the following in your monthly report:

- System availability
- Performance metrics
- Security incidents
- Capacity utilization
- Summary of changes

## Respond to incidents

### Classify the incident

| Severity | Description | Example |
|----------|-------------|---------|
| P1 - Critical | System is down, data is lost, or a breach occurred | Outage, data loss |
| P2 - High | Major functionality is degraded | Significant performance impact |
| P3 - Medium | Minor functionality is affected | Moderate performance impact |
| P4 - Low | Cosmetic issue | Minimal user impact |

### Follow the incident process

1.  **Detect and identify.** Monitor alerts, determine severity and affected systems, and estimate impact.
2.  **Contain.** Isolate affected systems, apply temporary workarounds, and protect unaffected systems.
3.  **Eradicate.** Identify the root cause, remove the threat or fix the issue, and verify resolution.
4.  **Recover.** Restore affected systems, verify functionality, and monitor for recurrence.
5.  **Learn.** Document details, run a post-mortem, implement preventive measures, and update procedures.

### Communicate

**Internal communication:**

- Notify the technical team immediately.
- Provide regular status updates and escalate to management when needed.
- Document all communication.

**External communication (if required):**

- Prepare user notifications with status and estimated resolution time.
- Follow up after the incident is resolved.

## Back up and restore

The Docker Compose stack includes a `backup` service that uses restic and runs on a schedule (default: daily at 02:00). For full configuration options, see [`docker/README.md`](../../docker/README.md).

### Schedule and retention

| Frequency | Retention | Environment variable |
|-----------|-----------|----------------------|
| Daily (02:00 by default) | 7 daily snapshots | `BACKUP_KEEP_DAILY` |
| Weekly | 4 weekly snapshots | `BACKUP_KEEP_WEEKLY` |
| Monthly | 6 monthly snapshots | `BACKUP_KEEP_MONTHLY` |

Snapshots use block-level deduplication, so retaining weeks or months of history uses minimal extra storage.

### What is backed up

Each snapshot contains:

- `tmp/backup/db/plexichat.sql.gz` — full PostgreSQL dump
- `plexichat-home/` — server configuration, `.machine_key`, and keyrings
- `rustfs-data/` — uploaded media and attachments

### Restore

```bash
# List available snapshots
docker exec plexichat-backup-1 /scripts/backup.sh list

# Restore the database from the latest snapshot
docker exec plexichat-backup-1 /scripts/backup.sh restore-db latest

# Restore files to /tmp/restore
docker exec plexichat-backup-1 /scripts/backup.sh restore latest /tmp/restore
```

### Verify backups

Verify snapshot integrity periodically:

```bash
docker exec plexichat-backup-1 /scripts/backup.sh check
```

Test a full restore in a staging environment before you rely on production backups, and document test results in your incident-response runbook.

## Monitor webhook delivery retries

Outbound webhook deliveries use a persistent retry queue with exponential backoff. The system does not use fire-and-forget delivery.

### How retries work

- When the first delivery attempt fails (non-`2xx` response or transport error), the system enqueues the delivery in `webhook_retry_queue`.
- The `WebhookDeliveryJob` worker drains due records every `webhooks.delivery_interval_seconds` (default `30`; set to `<= 0` to disable). The server initializer starts the worker automatically after webhook setup.
- The system retries with exponential backoff and jitter (`1s`, `2s`, `4s`, ... capped at `60s`). After `webhooks.max_retries` (default `5`) failures, the delivery moves to the dead-letter queue (`status = 'dead_letter'`) and is no longer retried automatically.
- Successful deliveries are recorded as `status = 'delivered'` with the HTTP status.
- You can retry dead-letter entries manually through the retry-queue API. The system cleans up delivered and dead-letter records after the retention window.

### Monitor the queue

The admin dashboard exposes these metrics:

| Metric | Meaning |
|--------|---------|
| `webhook_retries_pending` | Deliveries that are due or not yet attempted |
| `webhook_retries_retrying` | Deliveries in the backoff window |
| `webhook_retries_dead_letter` | Deliveries that failed permanently |

A growing `webhook_retries_dead_letter` count indicates that a downstream endpoint is persistently rejecting deliveries (HTTP `4xx`/`5xx` or unreachable) and needs attention.

### Operational tasks

- **Investigate dead letters.** List dead-letter entries, inspect `last_error` and `response_status`, fix the downstream endpoint, then trigger a manual retry.
- **Tune delivery cadence.** Lower `delivery_interval_seconds` for faster retries on critical webhooks, or raise `max_retries` for long outages. Restart the server to apply changes.

## Monitor performance

### Track key indicators

**System metrics:**

- CPU utilization (under 80%)
- Memory utilization (under 80%)
- Disk utilization (under 70%)
- Network throughput and I/O

**Application metrics:**

- Response time (p95 under 500 ms)
- Error rate (under 1%)
- Throughput (requests per second)
- Database query time (p95 under 100 ms)
- Cache hit rate (over 90%)

**Business metrics:**

- Active users
- Message volume
- Server count
- User growth rate

### Use monitoring tools

- **System monitoring:** CPU, memory, disk, network, processes, service health, and log aggregation.
- **Application monitoring:** Application performance monitoring (APM), database performance, API response times, and error rates.
- **Business monitoring:** User activity, feature usage, growth trends, and engagement.

### Tune performance

- **Database:** Optimize indexes and queries, tune the connection pool, and review caching.
- **Application:** Optimize code, improve caching, configure load balancing, and allocate resources.
- **Infrastructure:** Right-size servers, optimize networking and storage, and configure your CDN.

## Perform security operations

### Daily

- Review failed login attempts.
- Check for new admin accounts and permission changes.
- Monitor for unusual activity and verify security controls.

### Weekly

- Review security logs.
- Run vulnerability scans.
- Review access and verify compliance.

### Monthly

- Run a security audit.
- Run penetration tests.
- Provide security training.
- Review policies and run an incident-response drill.

### Respond to security incidents

1.  **Prepare.** Designate an incident-response team, define communication channels, and document escalation paths.
2.  **Detect.** Monitor alerts, analyze logs, investigate anomalies, and assess severity.
3.  **Respond.** Contain the incident, eradicate the threat, recover systems, and document lessons learned.

## Upgrade

For versioning, upgrade steps, and rollback strategies, see the [Versioning and updates guide](../deployment/versioning.md).

### Quick reference

**Before you upgrade:**

- Review upgrade notes and breaking changes.
- Test the upgrade in staging.
- Back up the database, configuration, and media.
- Prepare a rollback plan.
- Schedule a maintenance window and notify stakeholders.

**Basic upgrade steps:**

1.  Stop services.
2.  Apply updates (`git pull`, `docker pull`, or manual install).
3.  Run database migrations.
4.  Start services.
5.  Verify functionality.

**When to roll back:**

- Critical functionality is broken.
- Data corruption is detected.
- Performance is severely degraded.
- A security issue is identified.

**How to roll back:**

1.  Stop services.
2.  Restore from backup.
3.  Verify system state.
4.  Restart services.
5.  Validate functionality and document the rollback.

## Recover from disaster

### Plan

**Scope:** System failures, data corruption, security breaches, natural disasters, and human errors.

**Objectives:**

- Recovery time objective (RTO): 4 hours (1 hour for critical systems)
- Recovery point objective (RPO): 1 hour (15 minutes for critical systems)

### Recover

**System recovery:**

1.  Assess damage.
2.  Activate the recovery team.
3.  Restore from backups.
4.  Verify system integrity.
5.  Resume operations and monitor for issues.

**Data recovery:**

1.  Identify data loss.
2.  Select the appropriate backup.
3.  Restore data and verify integrity.
4.  Update applications and validate functionality.

### Test the plan

- **Quarterly:** Test backup restoration, verify recovery procedures, update documentation, and train the recovery team.
- **Annually:** Run a full disaster-recovery drill, including an offsite recovery test and complete system restoration, then update the plan.

## Maintain documentation

Update this guide when procedures change, new systems are added, contact information changes, you learn lessons from an incident, or regulatory requirements change.

Review the guide quarterly and perform a major revision annually. After any incident, update the guide based on lessons learned.

## Get help

**Emergency contacts:**

- Primary sysadmin: [contact]
- Secondary sysadmin: [contact]
- Management: [contact]
- Security team: [contact]

**Vendor contacts:**

- Hosting provider: [contact]
- Database support: [contact]
- Security vendor: [contact]
