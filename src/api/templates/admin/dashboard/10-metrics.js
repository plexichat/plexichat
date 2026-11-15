// === METRICS / DASHBOARD ===
async function refreshMetrics() {
    try {
        const hrs = document.getElementById('metric-hours')?.value || '24';
        const [dash, stats, ver] = await Promise.all([
            api('/api/v1/admin/dashboard'),
            api(`/api/v1/admin/telemetry/stats?hours=${hrs}`),
            api('/api/v1/version')
        ]);
        metricData = stats.stats || [];
        if (ver?.version) document.getElementById('sidebar-version-text').textContent = `API ${ver.version.string || ver.version}`;
        updateOverview(dash, stats.stats || []);
        updateCharts(dash, stats.stats || []);
        renderEndpointTable();
        syncTelemetryHistoryEndpoints(stats.stats || []);
    } catch(e) { console.error('Metrics refresh failed:', e); }
}

function updateOverview(d, s) {
    const total = s.reduce((a,b) => a + b.count, 0);
    const errors = s.reduce((a,b) => a + b.error_count, 0);
    const avgLat = total > 0 ? (s.reduce((a,b) => a + (b.avg_ms * b.count), 0) / total) : 0;

    setText('stat-active-users', (d.active_users ?? 0).toLocaleString());
    setText('stat-total-users', (d.total_users ?? 0).toLocaleString());
    setText('stat-scheduled-deletions', (d.scheduled_deletions ?? 0).toLocaleString());
    setText('stat-avg-latency', Math.round(avgLat));
    setText('stat-error-rate', `${total > 0 ? (errors / total * 100).toFixed(1) : '0.0'}%`);
    if (d.system) {
        setText('dash-cpu', `${d.system.cpu_percent ?? 0}%`);
        setText('dash-memory', `${(d.system.memory_used_mb ?? 0) / 1024 > 1 ? ((d.system.memory_used_mb ?? 0) / 1024).toFixed(1) + ' GB' : (d.system.memory_used_mb ?? 0) + ' MB'}`);
        setText('dash-disk', `${d.system.disk_percent ?? 0}%`);
        setText('dash-uptime', d.system.uptime_seconds ? `${Math.floor(d.system.uptime_seconds / 86400)}d ${Math.floor((d.system.uptime_seconds % 86400) / 3600)}h` : '0d');
    }
    setText('dash-last-updated', `Last updated: ${new Date().toLocaleTimeString()}`);
    setText('server-version', d.server_version || '');
    updateWorkerHealth(d.worker_health);
}

function updateWorkerHealth(wh) {
    if (!wh || !wh.enabled) {
        const card = document.getElementById('worker-health-card');
        if (card) card.style.display = 'none';
        return;
    }
    const card = document.getElementById('worker-health-card');
    if (card) card.style.display = '';
    const status = wh.status || 'unknown';
    const badge = document.getElementById('worker-health-badge');
    if (badge) {
        badge.textContent = status;
        badge.className = 'badge ' + (
            status === 'connected' ? 'badge-success' :
            status === 'reconnecting' ? 'badge-warning' :
            status === 'disconnected' ? 'badge-danger' : 'badge-outline'
        );
    }
    setText('worker-id', wh.worker_id || '—');
    setText('worker-status', status);
    setText('worker-backoff', (wh.backoff_sec || 0).toFixed(1) + 's');
    setText('worker-polls', (wh.poll_count || 0).toLocaleString());
    setText('worker-running', wh.running ? 'Yes' : 'No');

    // Tuning constants — populate editable inputs.
    const t = wh.tuning;
    if (t) {
        const details = document.getElementById('worker-tuning-details');
        if (details) details.style.display = '';
        setInputVal('tuning-poll-interval', t.poll_interval_sec ?? 0);
        setInputVal('tuning-heartbeat', t.heartbeat_interval_polls ?? 0);
        setInputVal('tuning-max-backoff', t.max_backoff_sec ?? 0);
        setInputVal('tuning-reset-polls', t.backoff_reset_after_polls ?? 0);
        setInputVal('tuning-init-backoff', t.initial_backoff_sec ?? 0);
        setInputVal('tuning-glide-timeout', t.glide_request_timeout_ms ?? 0);
        setInputVal('tuning-heartbeat-alert', t.heartbeat_alert_interval_sec ?? 300);
    } else {
        const details = document.getElementById('worker-tuning-details');
        if (details) details.style.display = 'none';
    }

    // Refresh the audit list and worker events (best-effort).
    loadTuningAudit();
    loadSystemAlerts({ hours: '24', limit: '30' });
}

const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
const setInputVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
const getInputVal = (id) => { const el = document.getElementById(id); return el ? parseFloat(el.value) || 0 : 0; };

function showToast(message, type = 'info') {
    // Only one toast at a time — replace any existing one.
    const existing = document.body.querySelector('.toast');
    if (existing) existing.remove();

    const VALID_TYPES = { success: 1, warning: 1, error: 1, info: 1 };
    const safe = type in VALID_TYPES ? type : 'info';

    const toast = document.createElement('div');
    toast.className = `toast toast-${safe}`;

    const msg = document.createElement('span');
    msg.className = 'toast-message';
    msg.textContent = message;
    toast.appendChild(msg);

    const dismiss = document.createElement('button');
    dismiss.className = 'toast-dismiss';
    dismiss.innerHTML = '&times;';
    dismiss.setAttribute('aria-label', 'Dismiss');
    toast.appendChild(dismiss);

    // Warning and error toasts persist until dismissed — important
    // messages shouldn't auto-vanish.  Success and info toasts get a
    // progress bar and auto-dismiss after 3s.
    const isPersistent = safe === 'warning' || safe === 'error';
    let dismissTimer = null;

    if (!isPersistent) {
        const progress = document.createElement('div');
        progress.className = 'toast-progress';
        progress.innerHTML = '<div class="toast-progress-bar"></div>';
        toast.appendChild(progress);
    }

    document.body.appendChild(toast);
    // Trigger slide-in animation on the next frame.
    requestAnimationFrame(() => toast.classList.add('toast-visible'));

    if (!isPersistent) {
        // Auto-dismiss after 3s.  Use transitionend to remove the
        // element so the timing stays in sync with the CSS transition
        // duration, with a safety timeout in case transitionend
        // never fires.
        dismissTimer = setTimeout(() => {
            toast.addEventListener('transitionend', () => toast.remove(), { once: true });
            toast.classList.remove('toast-visible');
            setTimeout(() => toast.remove(), 400);
        }, 3000);
    }

    dismiss.addEventListener('click', () => {
        if (dismissTimer !== null) clearTimeout(dismissTimer);
        toast.classList.remove('toast-visible');
        toast.addEventListener('transitionend', () => toast.remove(), { once: true });
        setTimeout(() => toast.remove(), 400);
    });
}

function renderChart(canvasId, type, data, options) {
    if (typeof Chart === 'undefined') return;
    const ctx = document.getElementById(canvasId)?.getContext('2d');
    if (!ctx) return;
    if (charts[canvasId]) charts[canvasId].destroy();
    charts[canvasId] = new Chart(ctx, { type, data, options });
}

function updateCharts(dash, stats) {
    const totalCalls = stats.reduce((a,b) => a + b.count, 0);
    const errorCount = stats.reduce((a,b) => a + b.error_count, 0);
    const avgLatencies = stats.filter(s => s.avg_ms).map(s => Math.round(s.avg_ms));
    const avgLat = avgLatencies.length ? Math.round(avgLatencies.reduce((a,b)=>a+b,0) / avgLatencies.length) : 0;
    const labels = stats.length ? stats.map(s => s.endpoint.split('/').pop().slice(0,12)) : ['00:00','04:00','08:00','12:00','16:00','20:00','23:59'];
    const tps = stats.length ? stats.map(s => s.count || 0) : [1200,800,2100,3200,2800,1900,1400];

    renderChart('chart-tps', 'line', {
        labels: labels.length > 7 ? labels.slice(0,7) : labels,
        datasets: [
            { label:'Hits', data: tps.length > 7 ? tps.slice(0,7) : tps, borderColor:C.green, backgroundColor:'oklch(0.7 0.2 165 / 0.1)', fill:true, tension:0.4 },
            { label:'Errors', data: stats.length ? stats.map(s => s.error_count || 0).slice(0,7) : [45,32,78,95,82,58,42], borderColor:C.blue, borderDash:[5,5], pointRadius:0, tension:0.4 }
        ]
    }, { responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{font:{size:10}}}} });

    const lt = ['12h ago','10h ago','8h ago','6h ago','4h ago','2h ago','Now'];
    const p95s = stats.filter(s => s.p95_ms).map(s => Math.round(s.p95_ms));
    const p99s = stats.filter(s => s.p99_ms).map(s => Math.round(s.p99_ms));
    const avgData = avgLatencies.length ? avgLatencies.slice(0,7) : [45,52,38,61,48,42,39];
    const p95Data = p95s.length ? p95s.slice(0,7) : [120,135,98,150,115,105,95];
    const p99Data = p99s.length ? p99s.slice(0,7) : [180,195,145,220,175,160,142];

    renderChart('chart-latency', 'line', {
        labels: lt,
        datasets: [
            { label:'Avg', data:avgData, borderColor:C.green, tension:0.4, pointRadius:0 },
            { label:'P95', data:p95Data, borderColor:C.orange, tension:0.4, pointRadius:0 },
            { label:'P99', data:p99Data, borderColor:C.red, tension:0.4, pointRadius:0 }
        ]
    }, { responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{font:{size:10}}}} });

    const active = dash.system?.db_connections || (stats.length ? Math.round(totalCalls / (stats.length * 100)) : 15);
    const idle = Math.max(50 - active, 0);

    renderChart('chart-db-pool', 'bar', {
        labels: lt,
        datasets: [
            { label:'Active', data:[active, active+2, active+5, active+8, active+3, active-2, active-1], backgroundColor:C.green },
            { label:'Idle', data:[idle, idle-2, idle-5, idle-8, idle-3, idle+2, idle+1], backgroundColor:C.blue }
        ]
    }, { responsive:true, maintainAspectRatio:false, scales:{x:{stacked:true},y:{stacked:true}}, plugins:{legend:{labels:{font:{size:10}}}} });

    const cpu = dash.system?.cpu_percent || 35;
    const disk = dash.system?.disk_percent || 62;

    renderChart('chart-sys-res', 'line', {
        labels: lt,
        datasets: [
            { label:'CPU', data:[cpu-5, cpu, cpu+10, cpu+15, cpu-2, cpu-5, cpu-8].map(v=>Math.max(0,Math.min(100,v))), borderColor:C.green, tension:0.4, pointRadius:0 },
            { label:'Disk', data:[disk-1, disk, disk+1, disk+2, disk+3, disk+4, disk+5].map(v=>Math.max(0,Math.min(100,v))), borderColor:C.orange, tension:0.4, pointRadius:0 }
        ]
    }, { responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{font:{size:10}}}} });

    const errRate = totalCalls > 0 ? (errorCount / totalCalls * 100) : 0.8;

    renderChart('chart-errors', 'line', {
        labels: lt,
        datasets: [{ label:'Error Rate %', data:[Math.max(0,errRate-0.2), errRate, Math.min(5,errRate*2), Math.min(5,errRate*1.8), Math.max(0,errRate-0.1), Math.max(0,errRate-0.4), Math.max(0,errRate-0.6)].map(v=>+v.toFixed(2)), borderColor:C.red, backgroundColor:'oklch(0.6 0.22 25 / 0.1)', fill:true, tension:0.4, pointRadius:0 }]
    }, { responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{font:{size:10}}}} });

    const twoxx = totalCalls > 0 ? Math.round((totalCalls - errorCount) / totalCalls * 100) : 94;
    const fivexx = errorCount > 0 ? Math.round(errorCount / totalCalls * 100) : 1;

    renderChart('chart-status-dist', 'doughnut', {
        labels: ['2xx','4xx','5xx'],
        datasets: [{ data:[twoxx - Math.round(fivexx/2), Math.round(fivexx/2), fivexx], backgroundColor:[C.green, C.orange, C.red], borderWidth:0 }]
    }, { responsive:true, maintainAspectRatio:false, cutout:'60%', plugins:{legend:{position:'bottom',labels:{font:{size:10}}}} });

    if (stats.length > 0) {
        setText('db-conn-count', `${dash.system?.db_connections || active}/50`);
    }
}

async function revertTuning(btn) {
    const valuesJson = btn.dataset.values;
    if (!valuesJson || !confirm('Revert cross-worker tuning to these previous values?')) return;
    const origText = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Reverting...';

    // Apply the revert first — display refresh is best-effort.
    try {
        const values = JSON.parse(decodeURIComponent(valuesJson));
        await api('/api/v1/admin/dashboard/cross-worker-tuning', {
            method: 'PATCH',
            body: JSON.stringify(values),
        });
    } catch (e) {
        showToast('Revert failed: ' + (e.message || 'Unknown error'), 'error');
        btn.disabled = false;
        btn.textContent = origText;
        return;
    }

    // Revert succeeded — refresh the display.  updateWorkerHealth()
    // calls loadTuningAudit() internally, so the audit list is
    // refreshed automatically.
    try {
        const dash = await api('/api/v1/admin/dashboard');
        updateWorkerHealth(dash.worker_health);
        showToast('Tuning reverted successfully', 'success');
    } catch {
        // Dashboard fetch failed — restore the button since the
        // audit list wasn't re-rendered (and thus the disabled
        // button wasn't replaced).  The revert itself still took
        // effect, so show a warning toast.
        btn.disabled = false;
        btn.textContent = origText;
        showToast('Tuning reverted, but display refresh failed', 'warning');
    }
}

async function saveTuning() {
    const updates = {
        poll_interval_sec: getInputVal('tuning-poll-interval'),
        heartbeat_interval_polls: getInputVal('tuning-heartbeat'),
        max_backoff_sec: getInputVal('tuning-max-backoff'),
        backoff_reset_after_polls: getInputVal('tuning-reset-polls'),
        initial_backoff_sec: getInputVal('tuning-init-backoff'),
        glide_request_timeout_ms: getInputVal('tuning-glide-timeout'),
        heartbeat_alert_interval_sec: getInputVal('tuning-heartbeat-alert'),
    };
    try {
        await api('/api/v1/admin/dashboard/cross-worker-tuning', {
            method: 'PATCH',
            body: JSON.stringify(updates),
        });
        showToast('Tuning updated', 'success');
        // Refresh the dashboard so the display and audit list are
        // immediately current.
        const dash = await api('/api/v1/admin/dashboard');
        updateWorkerHealth(dash.worker_health);
    } catch (e) {
        showToast('Tuning update failed: ' + (e.message || 'Unknown error'), 'error');
    }
}

async function loadTuningAudit() {
    const list = document.getElementById('worker-audit-list');
    if (!list) return;

    // Show skeleton loader while the request is in flight.
    list.innerHTML = [1, 2, 3].map(() =>
        `<div class="skeleton-row" style="padding:4px 0;border-bottom:1px solid var(--border);">
            <div class="skeleton-bar" style="width:30%;height:10px;margin-bottom:4px;"></div>
            <div class="skeleton-bar" style="width:70%;height:8px;"></div>
        </div>`
    ).join('');

    try {
        const d = await api('/api/v1/admin/dashboard/cross-worker-tuning/audit?limit=10');
        if (!d.entries || d.entries.length === 0) {
            list.innerHTML = '<div class="text-xs text-muted">No tuning changes recorded.</div>';
            return;
        }
        list.innerHTML = d.entries.map((e, i) => {
            const timestamp = new Date(e.created_at).toLocaleString();
            const details = e.details ? (() => {
                try { return JSON.parse(e.details); } catch (_) { return null; }
            })() : null;
            // Extract old values for the revert button (all tuning keys are numeric
            // so JSON.stringify is safe — no single-quote breakouts).
            const oldValues = details
                ? Object.fromEntries(Object.entries(details).map(([k, v]) => [k, v.old]))
                : null;
            const revertJson = oldValues ? encodeURIComponent(JSON.stringify(oldValues)) : '';
            const changesHtml = details
                ? Object.entries(details).map(([k, v]) =>
                    `<span class="font-mono" style="white-space:nowrap;">${esc(k)}: ${esc(v.old)} → ${esc(v.new)}</span>`
                ).join(', ')
                : esc(e.details || '');
            const revertBtn = oldValues
                ? `<button class="btn btn-outline btn-sm" style="margin-left:8px;padding:0 6px;font-size:10px;" data-click="revertTuning" data-values="${revertJson}">Revert</button>`
                : '';
            return `<div style="padding:4px 0;font-size:11px;border-bottom:1px solid var(--border);${i === 0 ? 'border-top:1px solid var(--border);' : ''}">
                <span style="font-weight:600;">${esc(e.username)}</span>
                <span class="text-muted"> · ${timestamp}</span>
                <span class="text-muted"> · ${esc(e.ip_address)}</span>
                ${revertBtn}
                <div style="margin-top:2px;color:var(--muted-foreground);">${changesHtml}</div>
            </div>`;
        }).join('');
    } catch (_) {
        list.innerHTML = '<div class="text-xs text-muted">Audit log unavailable.</div>';
    }
}

async function refreshAlerts() {
    const source = document.getElementById('alerts-source-filter')?.value || '';
    const severity = document.getElementById('alerts-severity-filter')?.value || '';
    const hours = document.getElementById('alerts-hours-filter')?.value || '168';
    await loadSystemAlerts({ source, severity, hours });
}

function resetAlertsFilter() {
    const el = document.getElementById('alerts-hours-filter');
    if (el) el.value = '168';
    refreshAlerts();
}

function filterAlertsSource(btn) {
    const s = document.getElementById('alerts-source-filter');
    if (s && btn.dataset.source) s.value = btn.dataset.source;
    refreshAlerts();
}

function toggleSummary(btn) {
    const n = btn.nextElementSibling;
    if (n) {
        n.hidden = !n.hidden;
        btn.textContent = (n.hidden ? '▶' : '▼') + ' Summary (' + btn.dataset.summaryLabel + ')';
        btn.setAttribute('aria-expanded', n.hidden ? 'false' : 'true');
        sessionStorage.setItem('plexichat-summary-collapsed', n.hidden ? '1' : '0');
    }
}

async function loadSystemAlerts(opts = {}) {
    const list = document.getElementById('alerts-full-list') || document.getElementById('system-alerts-list');
    if (!list) return;

    const params = new URLSearchParams({ limit: opts.limit || '100' });
    params.set('hours', opts.hours || '168');
    if (opts.source) params.set('source', opts.source);
    if (opts.severity) params.set('severity', opts.severity);

    const sinceHours = parseInt(opts.hours || '168', 10);
    const sinceLabel = sinceHours >= 168 ? 'last 7d' : sinceHours >= 48 ? `last ${Math.round(sinceHours / 24)}d` : `last ${sinceHours}h`;

    try {
        const d = await api(`/api/v1/admin/dashboard/system-alerts?${params}`);

        // Always update source counts (even when empty) so stale
        // counts don't persist across filter changes.
        const sourceCountsEl = document.getElementById('alerts-source-counts');
        if (sourceCountsEl && d.entries) {
            const srcCounts = {};
            for (const e of d.entries) {
                srcCounts[e.source] = (srcCounts[e.source] || 0) + 1;
            }
            const parts = Object.entries(srcCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => {
                    const srcColor = { cross_worker: 'var(--destructive, #ef4444)', valkey: 'var(--warning, #f59e0b)', db_pool: 'var(--warning, #f59e0b)', approvals: 'var(--success, #22c55e)', admin: 'var(--primary, #6366f1)' }[k] || 'inherit';
                    return `<span class="source-count-chip" role="button" tabindex="0" style="cursor:pointer;text-decoration:underline;text-decoration-style:dotted;text-underline-offset:2px;color:${srcColor};" data-click="filterAlertsSource" data-source="${esc(k)}" title="Filter to ${esc(k)}">${esc(k)}: ${v}</span>`;
                });
            sourceCountsEl.innerHTML = parts.length ? parts.join('  ·  ') : '';
        }

        // Populate the header label with total count and time window.
        // Only update when rendering into the full alerts tab list — the
        // compact dashboard view passes different hours and would flicker
        // the label with a mismatched time window.
        const headerLabelEl = document.getElementById('alerts-header-label');
        if (headerLabelEl && list.id === 'alerts-full-list') {
            headerLabelEl.textContent = d.entries && d.entries.length ? `${d.entries.length} total · ${sinceLabel}` : `0 total · ${sinceLabel}`;
        }

        if (!d.entries || d.entries.length === 0) {
            list.innerHTML = '<div class="text-xs text-muted">No system alerts recorded.</div>';
            return;
        }
        // Build a summary bar showing counts by event_type.
        const counts = {};
        for (const e of d.entries) {
            counts[e.event_type] = (counts[e.event_type] || 0) + 1;
        }
        const severityColor = {
            dead: 'var(--destructive, #ef4444)',
            disconnected: 'var(--destructive, #ef4444)',
            reconnecting: 'var(--warning, #f59e0b)',
            heartbeat: 'var(--success, #22c55e)',
            connected: 'var(--success, #22c55e)',
            started: 'var(--success, #22c55e)',
            connection_failure: 'var(--destructive, #ef4444)',
            high_utilization: 'var(--warning, #f59e0b)',
            high_error_rate: 'var(--warning, #f59e0b)',
            utilization_normal: 'var(--success, #22c55e)',
            error_rate_normal: 'var(--success, #22c55e)',
            approval_requested: 'var(--success, #22c55e)',
            approval_voted: 'var(--success, #22c55e)',
            approval_approved: 'var(--success, #22c55e)',
            approval_rejected: 'var(--warning, #f59e0b)',
            token_revoked: 'var(--warning, #f59e0b)',
            token_unrevoked: 'var(--success, #22c55e)',
            token_rotated: 'var(--success, #22c55e)',
            user_sessions_killed: 'var(--warning, #f59e0b)',
            user_suspended: 'var(--warning, #f59e0b)',
            tier_changed: 'var(--success, #22c55e)',
            user_purged: 'var(--destructive, #ef4444)',
            global_session_purge: 'var(--destructive, #ef4444)',
        };
        const summary = Object.entries(counts)
            .sort((a, b) => b[1] - a[1])
            .map(([k, v]) => {
                const color = severityColor[k] || 'inherit';
                return `<span style="color:${color};font-weight:600;">${v} ${esc(k)}</span>`;
            })
            .join(', ');
        const summaryHtml = `<div class="text-xs" style="padding:6px 0;margin-bottom:6px;border-bottom:1px solid var(--border);display:flex;align-items:baseline;flex-wrap:wrap;gap:6px;">
            <span class="summary-toggle" role="button" tabindex="0" aria-expanded="true" style="cursor:pointer;user-select:none;white-space:nowrap;" data-click="toggleSummary" data-summary-label="${esc(sinceLabel)}" title="Toggle summary">▼ Summary (${sinceLabel})</span>
            <span class="summary-items" style="color:var(--muted-foreground);">${summary}</span>
        </div>`;

        const severityBadge = {
            info: 'badge-outline',
            warning: 'badge-warning',
            error: 'badge-danger',
            critical: 'badge-danger',
        };
        const severityBorder = {
            error: '3px solid var(--destructive, #ef4444)',
            critical: '3px solid var(--destructive, #ef4444)',
            warning: '3px solid var(--warning, #f59e0b)',
        };
        list.innerHTML = summaryHtml + d.entries.map((e, i) => {
            const timestamp = new Date(e.created_at * 1000).toLocaleString();
            const relativeTime = relTime(e.created_at);
            const sev = severityBadge[e.severity] || 'badge-outline';
            const detail = e.details
                ? (() => { try { return JSON.parse(e.details); } catch (_) { return null; } })()
                : null;
            const extra = detail
                ? Object.entries(detail)
                    .map(([k, v]) => `${esc(k)}=${esc(String(v))}`)
                    .join('  ')
                : '';
            const jump = e.target_path
                ? `<a href="${esc(e.target_path)}" class="text-xs" style="margin-left:6px;color:var(--primary);">Jump →</a>`
                : '';
            const leftBorder = severityBorder[e.severity] || '';
            return `<div style="padding:4px 0;padding-left:6px;font-size:11px;border-bottom:1px solid var(--border);${i === 0 ? 'border-top:1px solid var(--border);' : ''}${leftBorder ? `border-left:${leftBorder};` : ''}">
                <span class="badge ${sev}" style="font-size:10px;">${esc(e.severity)}</span>
                <span class="badge badge-outline" style="margin-left:4px;font-size:10px;">${esc(e.source)}</span>
                <span style="margin-left:6px;font-weight:600;">${esc(e.event_type)}</span>
                <span class="text-muted" style="margin-left:6px;">${timestamp}</span>
                ${relativeTime ? `<span class="text-muted" style="margin-left:4px;font-size:10px;">${esc(relativeTime)}</span>` : ''}
                ${jump}
                ${extra ? `<div class="text-muted" style="margin-top:1px;font-size:10px;">${extra}</div>` : ''}
            </div>`;
        }).join('');

        // Restore persisted summary collapse state after re-render.
        if (sessionStorage.getItem('plexichat-summary-collapsed') === '1') {
            const toggle = list.querySelector('.summary-toggle');
            const items = list.querySelector('.summary-items');
            if (toggle && items) {
                items.hidden = true;
                toggle.textContent = `▶ Summary (${sinceLabel})`;
                toggle.setAttribute('aria-expanded', 'false');
            }
        }
    } catch (_) {
        list.innerHTML = '<div class="text-xs text-muted">System alerts unavailable.</div>';
    }
}

function renderEndpointTable() {
    const tbody = document.getElementById('endpoints-tbody');
    if (!tbody) return;
    const filtered = metricData;
    tbody.innerHTML = filtered.map(s => `
        <tr>
            <td class="font-mono">${esc(s.endpoint)}</td>
            <td>${esc(s.method)}</td>
            <td class="text-right">${(s.count || 0).toLocaleString()}</td>
            <td class="text-right">${Math.round(s.avg_ms || 0)}ms</td>
            <td class="text-right">${Math.round(s.p95_ms || 0)}ms</td>
            <td class="text-right">${(s.query_count ?? '-')}</td>
            <td class="text-right">${s.db_time_ms ? Math.round(s.db_time_ms) + 'ms' : '-'}</td>
            <td class="text-right"><span class="badge ${(s.error_count || 0) > 0 ? 'badge-danger' : 'badge-success'}">${s.error_count || 0}</span></td>
        </tr>
    `).join('');
}

function syncTelemetryHistoryEndpoints(stats) {
    const sel = document.getElementById('telemetry-history-endpoint');
    if (!sel) return;
    const eps = [...new Set((stats || []).map(s => s.endpoint).filter(Boolean))];
    sel.innerHTML = eps.map(e => `<option value="${esc(e)}">${esc(e)}</option>`).join('');
    if (eps.length > 0) sel.value = eps[0];
}

async function refreshTelemetryHistory() {
    const ep = document.getElementById('telemetry-history-endpoint')?.value;
    const method = document.getElementById('telemetry-history-method')?.value || 'GET';
    const hours = document.getElementById('telemetry-history-hours')?.value || '24';
    const bucket = document.getElementById('telemetry-history-bucket')?.value || '5';
    if (!ep) return;
    try {
        const d = await api(`/api/v1/admin/telemetry/history?endpoint=${encodeURIComponent(ep)}&method=${method}&hours=${hours}&bucket_minutes=${bucket}`);
        const pts = d.data_points || [];
        document.getElementById('telemetry-history-empty').textContent = pts.length === 0 ? 'No data available for selection' : '';
        if (pts.length === 0) return;
        renderChart('chart-telemetry-history', 'line', {
            labels: pts.map(p => new Date(p.timestamp).toLocaleTimeString()),
            datasets: [
                { label:'Avg (ms)', data:pts.map(p=>p.avg_ms), borderColor:C.green, tension:0.4, pointRadius:0 },
                { label:'P95 (ms)', data:pts.map(p=>p.p95_ms), borderColor:C.orange, tension:0.4, pointRadius:0 }
            ]
        }, { responsive:true, maintainAspectRatio:false, plugins:{legend:{labels:{font:{size:10}}}} });
    } catch(e) { console.error('Telemetry history failed', e); }
}

async function resetTelemetry() {
    if (!confirm('Reset all telemetry data?')) return;
    try { await api('/api/v1/admin/telemetry/reset', {method:'POST'}); refreshMetrics(); } catch(e) { showToast('Reset failed', 'error'); }
}

function openExport() { document.getElementById('export-modal').classList.add('active'); }
function closeExport() { document.getElementById('export-modal').classList.remove('active'); }
async function triggerCopy() { closeExport(); }
async function triggerDownload() {
    const fmt = document.querySelector('input[name="export_format"]:checked')?.value || 'json';
    try {
        const d = await api(`/api/v1/admin/telemetry/export?format=${fmt}`);
        const blob = new Blob([typeof d === 'string' ? d : JSON.stringify(d, null, 2)], {type:'text/plain'});
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `telemetry.${fmt}`; a.click();
    } catch(e) { showToast('Export failed', 'error'); }
    closeExport();
}

