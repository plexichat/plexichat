Chart.defaults.color = '#999';
Chart.defaults.borderColor = 'oklch(0.22 0.005 240)';
Chart.defaults.plugins.legend.display = true;
Chart.defaults.plugins.legend.position = 'top';
Chart.defaults.plugins.legend.align = 'end';
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.legend.labels.font = { size: 10 };
Chart.defaults.elements.line.borderWidth = 2;
Chart.defaults.elements.point.radius = 3;
Chart.defaults.elements.point.hoverRadius = 5;

const C = {
    blue: 'oklch(0.65 0.18 250)',
    green: 'oklch(0.7 0.2 165)',
    orange: 'oklch(0.7 0.2 45)',
    red: 'oklch(0.65 0.2 330)',
    purple: 'oklch(0.6 0.15 290)',
    yellow: 'oklch(0.75 0.18 75)',
    teal: 'oklch(0.75 0.18 165)',
    destructive: 'oklch(0.6 0.22 25)'
};

let currentTab = 'dashboard', charts = {}, metricData = [], historicalData = {};
let selectedUserId = null, selectedTicketId = null, selectedTicketStatus = null, selectedAccessTokenId = null, adminOtpSetupChallenge = null;
let adminUsersCache = [], currentMigration = null, migrations = [];

const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);

const api = async (p, o = {}) => {
    const t = sessionStorage.getItem('plexichat-admin-token');
    const r = await fetch(p, { ...o, headers: { 'Authorization': `Bearer ${t}`, 'Content-Type': 'application/json', ...o.headers } });
    if (r.status === 401) { sessionStorage.removeItem('plexichat-admin-token'); window.location.replace('/api/v1/admin/login'); return; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail?.error?.message || d.detail || 'API Error');
    return d;
};

const ts = t => t ? new Date(t).toLocaleString() : '-';

const pageTitles = {
    dashboard:'Dashboard', users:'User Management', admins:'Admin Management', tickets:'Support Tickets',
    moderation:'Content Moderation', security:'Security', telemetry:'Telemetry & Performance',
    logs:'System Logs', database:'Database Management', approvals:'Approval Workflows',
    audit:'Audit Log', automod:'AutoMod', bots:'Bots', migrations:'Database Migrations',
    plexijoin:'PlexiJoin Federation', license:'License', account:'My Account'
};

// === TAB SYSTEM ===
const showTab = n => {
    document.querySelectorAll('.sidebar-nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === n));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${n}`));
    currentTab = n;
    document.getElementById('page-title').textContent = pageTitles[n] || n;
    window.location.hash = n;

    const loads = {
        dashboard:'refreshMetrics', tickets:'loadTickets', users:'loadUsers', admins:'loadAdminUsers',
        moderation:'loadModeration', security:'loadSecurity', telemetry:'refreshTelemetryStats',
        logs:'loadLogs', database:'refreshDatabase', approvals:'loadApprovals', audit:'loadAuditLog',
        automod:'loadAutomodConfig', bots:'refreshAdminBots', migrations:'refreshMigrations',
        plexijoin:'refreshPlexiJoin', license:'loadLicense', account:'loadAccount'
    };
    if (loads[n]) { const fn = window[loads[n]]; if (fn) fn(); }
};

// === SIDEBAR ===
const toggleSidebar = () => {
    const s = document.getElementById('sidebar');
    s.classList.toggle('collapsed');
    const collapsed = s.classList.contains('collapsed');
    document.getElementById('sidebar-collapse-label').textContent = collapsed ? '' : 'Collapse';
    document.getElementById('sidebar-logo-text').style.display = collapsed ? 'none' : '';
    document.getElementById('sidebar-sys-header').style.display = collapsed ? 'none' : '';
    document.getElementById('sidebar-version').style.display = collapsed ? 'none' : '';
    document.querySelectorAll('.sidebar-section-header').forEach(h => h.style.display = collapsed ? 'none' : '');
};

// === NOTIFICATION DROPDOWN ===
document.addEventListener('click', e => {
    const dd = document.getElementById('notif-dropdown');
    const m = document.getElementById('notif-menu');
    if (dd && m) {
        if (e.target.closest('#notif-btn')) { m.classList.toggle('active'); }
        else if (!e.target.closest('.dropdown-menu')) { m.classList.remove('active'); }
    }
});

// === SIDEBAR TAB CLICKS ===
document.addEventListener('click', e => {
    const tabBtn = e.target.closest('[data-tab]');
    if (tabBtn) {
        e.preventDefault();
        showTab(tabBtn.dataset.tab);
    }
});

// === EVENT DELEGATION ===
const actionHandlers = new Map([
    ['addAccessTokenScope', addAccessTokenScope],
    ['addTicketNote', addTicketNote],
    ['addUserBadge', addUserBadge],
    ['approveRequest', approveRequest],
    ['beginAdminOtpSetup', beginAdminOtpSetup],
    ['blockHash', blockHash],
    ['blockUser', blockUser],
    ['botsRefresh', botsRefresh],
    ['cancelDeletion', cancelDeletion],
    ['changeAdminPassword', changeAdminPassword],
    ['changeOwnPassword', changeOwnPassword],
    ['clearAccessTokenExpiry', clearAccessTokenExpiry],
    ['closeAccessTokenDetail', closeAccessTokenDetail],
    ['closeAdminUserModal', closeAdminUserModal],
    ['closeBanUsernameModal', closeBanUsernameModal],
    ['closeBlockIPModal', closeBlockIPModal],
    ['closeCreateConnectionModal', closeCreateConnectionModal],
    ['closeCreateTokenModal', closeCreateTokenModal],
    ['closeExport', closeExport],
    ['closeMigrationModal', closeMigrationModal],
    ['closeRoleDetail', closeRoleDetail],
    ['closeTicketDetail', closeTicketDetail],
    ['closeUserDetail', closeUserDetail],
    ['confirmBanUsername', confirmBanUsername],
    ['confirmBlockIP', confirmBlockIP],
    ['confirmCreateConnection', confirmCreateConnection],
    ['confirmCreateToken', confirmCreateToken],
    ['confirmRunMigration', confirmRunMigration],
    ['deleteAdminUser', deleteAdminUser],
    ['deleteAutomodRule', deleteAutomodRule],
    ['deleteConnection', deleteConnection],
    ['disableAdminOtp', disableAdminOtp],
    ['editAdminUser', editAdminUser],
    ['editAutomodRule', editAutomodRule],
    ['editRole', editRole],
    ['exportAuditLog', exportAuditLog],
    ['forcePurge', forcePurge],
    ['forceUserRename', forceUserRename],
    ['generateEmergencyToken', generateEmergencyToken],
    ['globalSessionPurge', globalSessionPurge],
    ['killUserSessions', killUserSessions],
    ['loadAccessTokenDetail', loadAccessTokenDetail],
    ['loadAutomodRules', loadAutomodRules],
    ['logout', logout],
    ['manageUser', manageUser],
    ['openExport', openExport],
    ['refreshAccessTokenDetail', refreshAccessTokenDetail],
    ['refreshApprovals', refreshApprovals],
    ['refreshAuditLog', refreshAuditLog],
    ['refreshHashReports', refreshHashReports],
    ['refreshMessageReports', refreshMessageReports],
    ['refreshMigrations', refreshMigrations],
    ['refreshPlexiJoin', refreshPlexiJoin],
    ['refreshTelemetryHistory', refreshTelemetryHistory],
    ['refreshTelemetryStats', refreshTelemetryStats],
    ['refreshTickets', refreshTickets],
    ['refreshUserReports', refreshUserReports],
    ['regenerateAdminBackupCodes', regenerateAdminBackupCodes],
    ['rejectRequest', rejectRequest],
    ['reloadLicense', reloadLicense],
    ['removeAccessTokenScope', removeAccessTokenScope],
    ['removeBan', removeBan],
    ['removeUserBadge', removeUserBadge],
    ['resetAutomodRule', resetAutomodRule],
    ['resetTelemetry', resetTelemetry],
    ['reviewHashReport', reviewHashReport],
    ['reviewMessageReport', reviewMessageReport],
    ['reviewUserReport', reviewUserReport],
    ['revokeSelectedAccessToken', revokeSelectedAccessToken],
    ['rotateAccessToken', rotateAccessToken],
    ['saveAccessTokenSettings', saveAccessTokenSettings],
    ['saveAdminUser', saveAdminUser],
    ['saveAutomodConfig', saveAutomodConfig],
    ['saveAutomodRule', saveAutomodRule],
    ['saveUserNotes', saveUserNotes],
    ['scrollLogBottom', scrollLogBottom],
    ['scrollLogTop', scrollLogTop],
    ['showAccount', showAccount],
    ['showBanUsernameModal', showBanUsernameModal],
    ['showBlockIPModal', showBlockIPModal],
    ['showCreateAdminModal', showCreateAdminModal],
    ['showCreateConnectionModal', showCreateConnectionModal],
    ['showCreateTokenModal', showCreateTokenModal],
    ['showDryRunMigration', showDryRunMigration],
    ['showEmergencyModal', showEmergencyModal],
    ['showMigrationDetails', showMigrationDetails],
    ['showRunMigration', showRunMigration],
    ['suspendUser', suspendUser],
    ['testConnection', testConnection],
    ['toggleAdminUserStatus', toggleAdminUserStatus],
    ['toggleAutomodRule', toggleAutomodRule],
    ['toggleSidebar', toggleSidebar],
    ['triggerCopy', triggerCopy],
    ['triggerDownload', triggerDownload],
    ['unblockHash', unblockHash],
    ['unblockIP', unblockIP],
    ['unblockUser', unblockUser],
    ['unrevokeSelectedAccessToken', unrevokeSelectedAccessToken],
    ['updateTicketStatus', updateTicketStatus],
    ['updateUserTier', updateUserTier],
    ['verifyAdminOtpSetup', verifyAdminOtpSetup],
    ['viewTicket', viewTicket],
    ['automodPresetAlert', automodPresetAlert],
    ['automodPresetDelete', automodPresetDelete],
    ['automodPresetTimeout', automodPresetTimeout],
]);
document.addEventListener('click', e => {
    const btn = e.target.closest('[data-click]');
    if (!btn) return;
    e.preventDefault();
    const fn = actionHandlers.get(btn.dataset.click);
    if (fn) fn.call(btn, btn);
});

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
}

const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

function renderChart(canvasId, type, data, options) {
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
    try { await api('/api/v1/admin/telemetry/reset', {method:'POST'}); refreshMetrics(); } catch(e) { alert('Reset failed'); }
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
    } catch(e) { alert('Export failed'); }
    closeExport();
}

// === TICKETS ===
async function loadTickets() {
    try {
        const sf = document.getElementById('ticket-status-filter')?.value || '';
        const url = sf ? `/api/v1/admin/tickets?status_filter=${encodeURIComponent(sf)}` : '/api/v1/admin/tickets';
        const t = await api(url);
        document.getElementById('tickets-tbody').innerHTML = (t || []).map(x => `
            <tr><td class="font-mono">${esc(x.id)}</td><td>${esc(x.username)}</td><td>${esc(x.category)}</td><td><span class="badge badge-warning">${esc(x.status)}</span></td><td>${ts(x.created_at)}</td>
            <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="viewTicket" data-id="${esc(x.id)}">View</button></td></tr>
        `).join('');
    } catch(e) { console.error('Tickets load failed'); }
}
function refreshTickets() { loadTickets(); }

async function viewTicket(btn) {
    const id = btn.dataset ? btn.dataset.id : btn;
    try {
        const t = await api(`/api/v1/admin/tickets/${id}`);
        selectedTicketId = id; selectedTicketStatus = t.status;
        document.getElementById('view-ticket-title').textContent = `Ticket #${id} - ${t.username}`;
        document.getElementById('view-ticket-content').textContent = t.content;
        document.getElementById('ticket-status-badge').textContent = t.status;
        document.getElementById('ticket-created-at').textContent = ts(t.created_at);
        document.getElementById('ticket-resolved-at').textContent = ts(t.resolved_at);
        document.getElementById('ticket-resolved-by').textContent = t.resolved_by || '-';
        document.getElementById('ticket-status-select').value = t.status;
        document.getElementById('ticket-detail').classList.remove('hidden');
        loadTicketNotes(id);
    } catch(e) { alert('Failed to load ticket'); }
}

function closeTicketDetail() { document.getElementById('ticket-detail').classList.add('hidden'); selectedTicketId = null; }

async function loadTicketNotes(id) {
    try {
        const n = await api(`/api/v1/admin/tickets/${id}/notes`);
        document.getElementById('ticket-notes-list').innerHTML = (n || []).map(x => `
            <div style="border-left:2px solid var(--primary);padding:8px;margin-bottom:8px;background:var(--secondary);border-radius:4px;">
                <div class="text-xs text-muted">${esc(x.admin_username)} • ${ts(x.created_at)}</div>
                <div style="font-size:13px;">${esc(x.content)}</div>
            </div>
        `).join('') || '<div class="text-sm text-muted">No notes yet.</div>';
    } catch(e) { console.error('Load ticket notes failed', e); }
}

async function updateTicketStatus() {
    if (!selectedTicketId) return;
    const status = document.getElementById('ticket-status-select').value;
    if (status === selectedTicketStatus) return;
    try { await api(`/api/v1/admin/tickets/${selectedTicketId}/status`, {method:'PATCH', body:JSON.stringify({status})}); viewTicket(selectedTicketId); } catch(e) { alert('Update failed'); }
}

async function addTicketNote() {
    const content = document.getElementById('new-note-input').value;
    if (!content || !selectedTicketId) return;
    try { await api(`/api/v1/admin/tickets/${selectedTicketId}/notes`, {method:'POST', body:JSON.stringify({content})}); document.getElementById('new-note-input').value = ''; loadTicketNotes(selectedTicketId); } catch(e) { alert('Failed to add note'); }
}

// === USERS ===
async function loadUsers() {
    try {
        const q = document.getElementById('user-search-input')?.value || '';
        const url = q ? `/api/v1/admin/users/search?q=${encodeURIComponent(q)}` : '/api/v1/admin/users/search?q=';
        const d = await api(url);
        const users = d.users || [];
        document.getElementById('users-tbody').innerHTML = users.map(u => `
            <tr>
                <td><div style="display:flex;align-items:center;gap:8px;"><div style="display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:var(--secondary);font-size:12px;font-weight:600;">${esc((u.username||'?')[0].toUpperCase())}</div><div><span>${esc(u.username)}</span><div class="text-xs text-muted font-mono">${esc(u.id)}</div></div></div></td>
                <td class="text-muted">${esc(u.email||'')}</td>
                <td><span class="badge badge-primary">${esc(u.tier||'free')}</span></td>
                <td>${u.account_locked ? '<span class="badge badge-danger">Locked</span>' : '<span class="badge badge-success">Active</span>'}</td>
                <td class="text-muted">${ts(u.last_login)}</td>
                <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="manageUser" data-id="${esc(u.id)}">Manage</button></td>
            </tr>
        `).join('');
        document.getElementById('users-count').textContent = `Showing ${users.length} users`;
    } catch(e) { console.error('Users load failed:', e); }
}

async function manageUser(btn) {
    const id = btn.dataset ? btn.dataset.id : btn;
    try {
        const [u, notes] = await Promise.all([
            api(`/api/v1/admin/users/${id}`),
            api(`/api/v1/admin/users/${id}/notes`)
        ]);
        selectedUserId = id;
        const panel = document.getElementById('user-detail-panel');
        panel.innerHTML = `
            <div class="card">
                <div class="card-header"><span class="card-title">${esc(u.username)}</span><button class="btn btn-outline btn-sm" data-click="closeUserDetail">Close</button></div>
                <div class="card-content">
                    <div style="display:flex;gap:16px;flex-wrap:wrap;">
                        <img src="/api/v1/avatars/users/${esc(u.id)}" style="width:80px;height:80px;border-radius:6px;background:var(--secondary);object-fit:cover;" alt="Avatar" onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><rect fill=%22oklch(0.18 0.005 240)%22 width=%22100%22 height=%22100%22/><text x=%2250%22 y=%2265%22 text-anchor=%22middle%22 font-size=%2240%22 fill=%22%23999%22>${esc((u.username||'?')[0].toUpperCase())}</text></svg>'">
                        <div style="flex:1;">
                            <p class="font-mono text-sm text-muted">ID: ${esc(u.id)}</p>
                            <div class="grid-2" style="display:grid;gap:12px;margin-top:16px;">
                                <div><label>Tier</label><div style="display:flex;gap:8px;"><select id="user-tier-select" style="flex:1;"><option value="free">Free</option><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="premium">Premium</option><option value="staff">Staff</option></select><button class="btn btn-primary btn-sm" data-click="updateUserTier">Set</button></div></div>
                                <div><label>Badges</label><div id="user-badges-list" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;">${(u.badges||[]).map(b => `<span class="badge badge-primary" style="display:flex;align-items:center;gap:4px;">${esc(b)}<span style="cursor:pointer;" data-click="removeUserBadge" data-val="${esc(b)}">×</span></span>`).join('')}</div><div style="display:flex;gap:8px;"><input type="text" id="new-badge-input" list="available-badges-list" placeholder="Select badge..." style="flex:1;"><datalist id="available-badges-list"></datalist><button class="btn btn-secondary btn-sm" data-click="addUserBadge">Add</button></div></div>
                                <div><label>Actions</label><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:4px;"><button class="btn btn-secondary btn-sm" data-click="killUserSessions">Kill Sessions</button><button class="btn btn-danger btn-sm" id="suspend-btn" data-click="suspendUser">${u.account_locked ? 'Unlock' : 'Suspend'}</button><button class="btn btn-outline btn-sm" data-click="forceUserRename">Force Rename</button></div></div>
                                <div><label>Notes</label><textarea id="user-notes-input" rows="3" style="width:100%;resize:vertical;" placeholder="Internal notes...">${esc(notes.notes||'')}</textarea><button class="btn btn-primary btn-sm" style="margin-top:8px;" data-click="saveUserNotes">Save Notes</button></div>
                            </div>
                        </div>
                    </div>
                    <div class="grid-2" style="display:grid;gap:16px;margin-top:16px;">
                        <div><label style="font-weight:600;">Tier Catalog</label><div id="tiers-list" class="text-sm text-muted">Loading...</div></div>
                    </div>
                </div>
            </div>
        `;
        document.getElementById('user-tier-select').value = u.tier || 'free';
        loadTierCatalog();
    } catch(e) { alert('Failed to load user'); }
}

function closeUserDetail() { document.getElementById('user-detail-panel').innerHTML = ''; selectedUserId = null; }

async function loadTierCatalog() {
    try {
        const d = await api('/api/v1/admin/tiers');
        document.getElementById('tiers-list').innerHTML = d.tiers ? Object.entries(d.tiers).map(([k,v]) => `<div style="margin-bottom:4px;"><strong>${esc(k)}</strong>: ${esc(JSON.stringify(v))}</div>`).join('') : JSON.stringify(d, null, 2);
    } catch(e) { console.error('Load tier catalog failed', e); }
}

async function updateUserTier() {
    if (!selectedUserId) return;
    const tier = document.getElementById('user-tier-select').value;
    try { await api(`/api/v1/admin/users/${selectedUserId}/tier`, {method:'PUT', body:JSON.stringify({tier})}); alert('Tier updated'); } catch(e) { alert('Failed: '+e.message); }
}

async function addUserBadge() {
    const badge = document.getElementById('new-badge-input')?.value;
    if (!badge || !selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/badges/${encodeURIComponent(badge)}`, {method:'POST'}); manageUser(selectedUserId); } catch(e) { alert('Failed: '+e.message); }
}

async function removeUserBadge(btn) {
    const badge = btn.dataset.val;
    if (!badge || !selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/badges/${encodeURIComponent(badge)}`, {method:'DELETE'}); manageUser(selectedUserId); } catch(e) { alert('Failed: '+e.message); }
}

async function suspendUser() {
    if (!selectedUserId) return;
    try { await api(`/api/v1/admin/security/lock-user`, {method:'POST', body:JSON.stringify({user_id:selectedUserId})}); manageUser(selectedUserId); } catch(e) { alert('Failed'); }
}

async function killUserSessions() {
    if (!selectedUserId) return;
    try { await api('/api/v1/admin/security/force-logout', {method:'POST', body:JSON.stringify({user_id:selectedUserId})}); alert('Sessions killed'); } catch(e) { alert('Failed'); }
}

async function forceUserRename() {
    if (!selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/force-username-change`, {method:'POST'}); alert('Rename forced'); } catch(e) { alert('Failed'); }
}

async function saveUserNotes() {
    const notes = document.getElementById('user-notes-input')?.value;
    if (!selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/notes`, {method:'POST', body:JSON.stringify({notes})}); alert('Notes saved'); } catch(e) { alert('Failed'); }
}

// === INNER TABS ===
document.addEventListener('click', e => {
    const tab = e.target.closest('[data-inner-tab]');
    if (!tab) return;
    const parent = tab.closest('.tab-content.active') || document;
    const name = tab.dataset.innerTab;
    parent.querySelectorAll('.inner-tab').forEach(t => t.classList.toggle('active', t.dataset.innerTab === name));
    parent.querySelectorAll('.inner-tab-content').forEach(c => c.classList.toggle('active', c.id === `inner-${name}`));
});

// === DELETIONS ===
async function refreshDeletions() {
    try {
        const d = await api('/api/v1/admin/users/scheduled-deletions');
        const items = d.deletions || d.users || [];
        document.getElementById('deletions-tbody').innerHTML = items.map(u => `
            <tr>
                <td><span>${esc(u.username)}</span><div class="text-xs text-muted font-mono">${esc(u.user_id||u.id)}</div></td>
                <td class="text-muted">${esc(u.email||'')}</td>
                <td class="text-muted">${ts(u.scheduled_at || u.request_date)}</td>
                <td><span class="badge ${(u.days_left||u.days_remaining||0) <= 5 ? 'badge-danger' : 'badge-warning'}">${u.days_left || u.days_remaining || '?'} days</span></td>
                <td class="text-right"><button class="btn btn-outline btn-sm" data-click="cancelDeletion" data-id="${esc(u.user_id||u.id)}">Cancel</button> <button class="btn btn-danger btn-sm" data-click="forcePurge" data-id="${esc(u.user_id||u.id)}">Purge</button></td>
            </tr>
        `).join('');
    } catch(e) { document.getElementById('deletions-tbody').innerHTML = '<tr><td colspan="5" class="text-muted">No scheduled deletions</td></tr>'; }
}

async function cancelDeletion(btn) {
    if (!confirm('Cancel scheduled deletion?')) return;
    try { await api(`/api/v1/admin/users/${btn.dataset.id}/cancel-deletion`, {method:'POST'}); refreshDeletions(); } catch(e) { alert('Failed'); }
}

async function forcePurge(btn) {
    if (!confirm('IRREVERSIBLE: Permanently purge this user?')) return;
    try { await api(`/api/v1/admin/users/${btn.dataset.id}/force-purge`, {method:'POST'}); refreshDeletions(); } catch(e) { alert('Failed'); }
}

// === SECURITY ===
async function loadSecurity() {
    try {
        const [sec, ips, bans, tokens] = await Promise.all([
            api('/api/v1/admin/auth/security-status'),
            api('/api/v1/admin/security/blocked-ips'),
            api('/api/v1/admin/security/banned-usernames'),
            api('/api/v1/admin/security/access-tokens')
        ]);
        setText('admin-security-username', sec?.username || '-');
        setText('admin-security-last-login', ts(sec?.last_login));
        setText('admin-security-otp-status', sec?.otp_enabled ? 'Enabled' : (sec?.must_setup_otp ? 'Setup Required' : 'Disabled'));
        setText('admin-security-backup-count', String(sec?.backup_codes_remaining||0));

        document.getElementById('blocked-ips-tbody').innerHTML = (ips||[]).map(i => `<tr><td class="font-mono">${esc(i.ip_address)}</td><td class="text-muted">${esc(i.reason||'')}</td><td class="text-muted">${ts(i.blocked_at)}</td><td>${i.expires_at === 'Permanent' ? '<span class="badge badge-danger">Permanent</span>' : ts(i.expires_at)}</td><td class="text-muted">${esc(i.blocked_by||'')}</td><td class="text-right"><button class="btn btn-outline btn-sm" data-click="unblockIP" data-val="${esc(i.ip_address)}">Unblock</button></td></tr>`).join('');
        document.getElementById('banned-usernames-tbody').innerHTML = (bans||[]).map(b => `<tr><td class="font-mono">${esc(b.pattern)}</td><td><span class="badge badge-outline">${b.is_regex ? 'regex' : 'wildcard'}</span></td><td class="text-muted">${esc(b.reason||'')}</td><td class="text-muted">${esc(b.created_by||'')}</td><td class="text-right"><button class="btn btn-outline btn-sm" data-click="removeBan" data-id="${esc(b.id)}" ${b.created_by === 'system' ? 'disabled' : ''}>Remove</button></td></tr>`).join('');
        renderAccessTokens(tokens);
        if (selectedAccessTokenId) loadAccessTokenDetail(selectedAccessTokenId);
    } catch(e) { console.error('Security load failed', e); }
}

function renderAccessTokens(items) {
    const tokens = items?.tokens || items || [];
    document.getElementById('access-tokens-tbody').innerHTML = tokens.map(t => `
        <tr style="cursor:pointer;" data-click="loadAccessTokenDetail" data-id="${esc(t.id)}">
            <td class="font-medium">${esc(t.name||'Unnamed')}</td>
            <td class="font-mono text-sm">${esc((t.prefix||'').slice(0,12))}...</td>
            <td>${t.scope_mode === 'none' ? '<span class="badge badge-outline">Unscoped</span>' : `<span class="badge badge-info">${esc(t.scope_mode)}</span>`}</td>
            <td class="text-right">${(t.use_count_total||0).toLocaleString()}</td>
            <td class="text-right ${t.denied_count_total > 0 ? 'text-muted' : ''}">${t.denied_count_total || 0}</td>
            <td>${t.revoked ? '<span class="badge badge-danger">Revoked</span>' : '<span class="badge badge-success">Active</span>'}</td>
            <td class="text-muted">${ts(t.last_used_at)}</td>
            <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="loadAccessTokenDetail" data-id="${esc(t.id)}">View</button></td>
        </tr>
    `).join('');
}

async function loadAccessTokenDetail(btn) {
    const tokenId = btn.dataset ? btn.dataset.id : btn;
    selectedAccessTokenId = tokenId;
    try {
        const detail = await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(tokenId)}`);
        const token = detail.access_token || detail;
        const panel = document.getElementById('access-token-detail');
        panel.classList.remove('hidden');
        setText('access-token-detail-title', `Token: ${esc(token.name||token.id)}`);
        setText('access-token-stat-requests', (detail.total_events || token.use_count_total || 0).toLocaleString());
        setText('access-token-stat-ips', (detail.distinct_ip_count || token.distinct_ip_count || 0).toLocaleString());
        setText('access-token-stat-denied', (detail.denied_count_total || 0).toLocaleString());
        setText('access-token-stat-last-used', ts(token.last_used_at) || 'Never');
        document.getElementById('access-token-detail-name').value = token.name || '';
        document.getElementById('access-token-detail-description').value = token.description || '';
        document.getElementById('access-token-detail-expires').value = token.expires_at ? new Date(token.expires_at).toISOString().slice(0,16) : '';
        document.getElementById('access-token-detail-scope-mode').value = token.scope_mode || 'none';
        setText('access-token-detail-created', ts(token.created_at));
        setText('access-token-detail-first-used', ts(token.first_used_at));
        setText('access-token-detail-last-ip', token.last_used_ip_address || 'Unknown');
        setText('access-token-detail-last-path', token.last_used_path || 'Unknown');
        setText('access-token-detail-last-ua', token.last_used_user_agent || 'Unknown');
        document.getElementById('access-token-rotated').textContent = '';
        const unrevokeBtn = document.getElementById('btn-unrevoke-token');
        const revokeBtn = document.getElementById('btn-revoke-token');
        if (token.revoked) { unrevokeBtn?.classList.remove('hidden'); revokeBtn?.classList.add('hidden'); }
        else { unrevokeBtn?.classList.add('hidden'); revokeBtn?.classList.remove('hidden'); }

        const scopes = detail.scopes || [];
        document.getElementById('access-token-scopes-tbody').innerHTML = scopes.length === 0 ? '<tr><td colspan="3" class="text-muted">No scopes</td></tr>' : scopes.map(s => `<tr><td>${esc(s.scope_type)}</td><td class="font-mono">${esc(s.value)}</td><td class="text-right"><button class="btn btn-outline btn-sm" data-click="removeAccessTokenScope" data-id="${esc(s.id)}">Remove</button></td></tr>`).join('');

        const ips = detail.top_ips || [];
        document.getElementById('access-token-top-ips-tbody').innerHTML = ips.length === 0 ? '<tr><td colspan="4" class="text-muted">No IP activity</td></tr>' : ips.map(i => `<tr><td class="font-mono">${esc(i.ip_address)}</td><td class="text-right">${(i.request_count||0).toLocaleString()}</td><td class="text-right">${i.denied_count||0}</td><td>${ts(i.last_seen_at)}</td></tr>`).join('');

        const paths = detail.top_paths || [];
        document.getElementById('access-token-top-paths-tbody').innerHTML = paths.length === 0 ? '<tr><td colspan="4" class="text-muted">No routes</td></tr>' : paths.map(p => `<tr><td class="font-mono">${esc(p.path)}</td><td>${esc(p.method)}</td><td class="text-right">${(p.request_count||0).toLocaleString()}</td><td>${ts(p.last_seen_at)}</td></tr>`).join('');

        const events = detail.recent_events || [];
        document.getElementById('access-token-events-tbody').innerHTML = events.length === 0 ? '<tr><td colspan="4" class="text-muted">No events</td></tr>' : events.map(e => `<tr><td>${ts(e.used_at)}</td><td class="font-mono">${esc(e.ip_address)}</td><td class="font-mono">${esc(e.method)} ${esc(e.path)}</td><td>${e.allowed ? '<span class="badge badge-success">Allowed</span>' : `<span class="badge badge-danger">Denied${e.reject_reason ? ': '+esc(e.reject_reason) : ''}</span>`}</td></tr>`).join('');
    } catch(e) { console.error('Token detail failed'); }
}

function closeAccessTokenDetail() { selectedAccessTokenId = null; document.getElementById('access-token-detail').classList.add('hidden'); }

async function refreshAccessTokenDetail() { if (selectedAccessTokenId) loadAccessTokenDetail(selectedAccessTokenId); }

async function saveAccessTokenSettings() {
    if (!selectedAccessTokenId) return;
    const body = {
        name: document.getElementById('access-token-detail-name').value,
        description: document.getElementById('access-token-detail-description').value,
        expires_at: document.getElementById('access-token-detail-expires').value ? new Date(document.getElementById('access-token-detail-expires').value).toISOString() : null,
        scope_mode: document.getElementById('access-token-detail-scope-mode').value
    };
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}`, {method:'PATCH', body:JSON.stringify(body)}); alert('Saved'); loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { alert('Failed'); }
}

async function clearAccessTokenExpiry() {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}`, {method:'PATCH', body:JSON.stringify({expires_at:null})}); loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { alert('Failed'); }
}

async function rotateAccessToken() {
    if (!selectedAccessTokenId || !confirm('Rotate this token? Old token will stop working.')) return;
    try { const d = await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/rotate`, {method:'POST'}); document.getElementById('access-token-rotated').textContent = `New token: ${d.token || 'rotated'}`; } catch(e) { alert('Failed'); }
}

async function revokeSelectedAccessToken() {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/revoke`, {method:'POST'}); loadAccessTokenDetail(selectedAccessTokenId); loadSecurity(); } catch(e) { alert('Failed'); }
}

async function unrevokeSelectedAccessToken() {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/unrevoke`, {method:'POST'}); loadAccessTokenDetail(selectedAccessTokenId); loadSecurity(); } catch(e) { alert('Failed'); }
}

async function addAccessTokenScope() {
    if (!selectedAccessTokenId) return;
    const type = document.getElementById('access-token-scope-type').value;
    const value = document.getElementById('access-token-scope-value').value;
    if (!value) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/scopes`, {method:'POST', body:JSON.stringify({scope_type:type, value})}); document.getElementById('access-token-scope-value').value = ''; loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { alert('Failed'); }
}

async function removeAccessTokenScope(btn) {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/scopes/${btn.dataset.id}`, {method:'DELETE'}); loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { alert('Failed'); }
}

async function unblockIP(btn) {
    try { await api(`/api/v1/admin/security/unblock-ip/${encodeURIComponent(btn.dataset.val)}`, {method:'DELETE'}); loadSecurity(); } catch(e) { alert('Failed'); }
}

async function removeBan(btn) {
    try { await api(`/api/v1/admin/security/banned-usernames/${btn.dataset.id}`, {method:'DELETE'}); loadSecurity(); } catch(e) { alert('Failed'); }
}

async function changeAdminPassword() {
    const cur = document.getElementById('admin-current-password').value;
    const nw = document.getElementById('admin-new-password').value;
    if (!cur || !nw) return;
    try { await api('/api/v1/admin/auth/change-password', {method:'POST', body:JSON.stringify({current_password:cur, new_password:nw})}); alert('Password changed'); document.getElementById('admin-current-password').value = ''; document.getElementById('admin-new-password').value = ''; } catch(e) { alert('Failed: '+e.message); }
}

async function beginAdminOtpSetup() {
    const pw = document.getElementById('admin-2fa-password').value;
    if (!pw) { alert('Enter password first'); return; }
    try {
        const d = await api('/api/v1/admin/auth/2fa/begin-setup', {method:'POST', body:JSON.stringify({password:pw})});
        adminOtpSetupChallenge = d.challenge_token;
        const qr = document.getElementById('admin-otp-qr');
        qr.src = `/api/v1/qr?size=200x200&data=${encodeURIComponent(d.otp_qr_uri)}`;
        document.getElementById('admin-otp-secret').textContent = d.otp_secret;
        document.getElementById('admin-security-otp-setup').classList.remove('hidden');
    } catch(e) { alert('Failed: '+e.message); }
}

async function verifyAdminOtpSetup() {
    const code = document.getElementById('admin-otp-verify-code').value;
    if (!code || !adminOtpSetupChallenge) return;
    try {
        await api('/api/v1/admin/auth/2fa/begin-setup', {method:'POST', body:JSON.stringify({code, challenge_token:adminOtpSetupChallenge})});
        const bc = await api('/api/v1/admin/auth/2fa/regenerate-backup-codes', {method:'POST'});
        document.getElementById('admin-backup-codes-list').textContent = (bc.codes||[]).join('\n');
        document.getElementById('admin-backup-codes').classList.remove('hidden');
        document.getElementById('admin-security-otp-setup').classList.add('hidden');
        alert('2FA enabled! Backup codes shown below. Store them safely.');
    } catch(e) { alert('Failed: '+e.message); }
}

async function regenerateAdminBackupCodes() {
    try {
        const d = await api('/api/v1/admin/auth/2fa/regenerate-backup-codes', {method:'POST'});
        document.getElementById('admin-backup-codes-list').textContent = (d.codes||[]).join('\n');
        document.getElementById('admin-backup-codes').classList.remove('hidden');
    } catch(e) { alert('Failed'); }
}

async function disableAdminOtp() {
    const code = document.getElementById('admin-disable-otp-code').value;
    if (!code) return;
    try { await api('/api/v1/admin/auth/2fa/disable', {method:'POST', body:JSON.stringify({code})}); alert('2FA disabled'); loadSecurity(); } catch(e) { alert('Failed'); }
}

function showBlockIPModal() { document.getElementById('block-ip-modal').classList.add('active'); }
function closeBlockIPModal() { document.getElementById('block-ip-modal').classList.remove('active'); }
async function confirmBlockIP() {
    const ip = document.getElementById('block-ip-val').value;
    const reason = document.getElementById('block-ip-reason').value;
    const duration = document.getElementById('block-ip-duration').value;
    if (!ip) return;
    try { await api('/api/v1/admin/security/block-ip', {method:'POST', body:JSON.stringify({ip_address:ip, reason, duration})}); closeBlockIPModal(); loadSecurity(); } catch(e) { alert('Failed'); }
}

function showBanUsernameModal() { document.getElementById('ban-username-modal').classList.add('active'); }
function closeBanUsernameModal() { document.getElementById('ban-username-modal').classList.remove('active'); }
async function confirmBanUsername() {
    const pattern = document.getElementById('ban-username-pattern').value;
    const type = document.getElementById('ban-username-type').value;
    const reason = document.getElementById('ban-username-reason').value;
    if (!pattern) return;
    try { await api('/api/v1/admin/security/banned-usernames', {method:'POST', body:JSON.stringify({pattern, is_regex: type==='regex', reason})}); closeBanUsernameModal(); loadSecurity(); } catch(e) { alert('Failed'); }
}

async function globalSessionPurge() {
    if (!confirm('PURGE ALL USER SESSIONS? This will log out every user. Continue?')) return;
    if (!confirm('ARE YOU SURE? This cannot be undone easily.')) return;
    try { await api('/api/v1/admin/security/logout-all', {method:'POST'}); alert('All sessions purged'); } catch(e) { alert('Failed'); }
}

// === ADMIN USERS ===
async function loadAdminUsers() {
    try {
        const d = await api('/api/v1/admin/admin-users');
        adminUsersCache = d.users || [];
        document.getElementById('admin-users-tbody').innerHTML = adminUsersCache.map(u => `
            <tr>
                <td>${esc(u.username)}</td>
                <td class="text-muted">${esc(u.email||'')}</td>
                <td>${esc(u.role||'admin')}</td>
                <td class="text-muted">${ts(u.created_at)}</td>
                <td class="text-muted">${ts(u.last_login_at) || 'Never'}</td>
                <td><span class="badge ${u.is_active ? 'badge-success' : 'badge-danger'}">${u.is_active ? 'Active' : 'Inactive'}</span></td>
                <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="editAdminUser" data-id="${esc(u.id)}">Edit</button> <button class="btn btn-outline btn-sm" data-click="toggleAdminUserStatus" data-id="${esc(u.id)}">${u.is_active ? 'Disable' : 'Enable'}</button> <button class="btn btn-danger btn-sm" data-click="deleteAdminUser" data-id="${esc(u.id)}">Delete</button></td>
            </tr>
        `).join('');
    } catch(e) { console.error('Admin users load failed'); }
}

let selectedAdminUserId = null;
function showCreateAdminModal() {
    selectedAdminUserId = null;
    document.getElementById('admin-user-modal-title').textContent = 'Create Admin User';
    document.getElementById('admin-user-username').value = '';
    document.getElementById('admin-user-email').value = '';
    document.getElementById('admin-user-password').value = '';
    document.getElementById('admin-user-role').value = 'admin';
    document.getElementById('admin-user-modal').classList.add('active');
}

function closeAdminUserModal() { document.getElementById('admin-user-modal').classList.remove('active'); }

function editAdminUser(btn) {
    const id = btn.dataset.id;
    selectedAdminUserId = id;
    const u = adminUsersCache.find(x => x.id === id);
    if (!u) return;
    document.getElementById('admin-user-modal-title').textContent = 'Edit Admin User';
    document.getElementById('admin-user-username').value = u.username || '';
    document.getElementById('admin-user-email').value = u.email || '';
    document.getElementById('admin-user-password').value = '';
    document.getElementById('admin-user-role').value = u.role || 'admin';
    document.getElementById('admin-user-modal').classList.add('active');
}

async function saveAdminUser() {
    const username = document.getElementById('admin-user-username').value;
    const email = document.getElementById('admin-user-email').value;
    const password = document.getElementById('admin-user-password').value;
    const role = document.getElementById('admin-user-role').value;
    if (!username || !email) return;
    try {
        if (selectedAdminUserId) {
            await api(`/api/v1/admin/admin-users/${selectedAdminUserId}`, {method:'PUT', body:JSON.stringify({username, email, role})});
        } else {
            await api('/api/v1/admin/admin-users', {method:'POST', body:JSON.stringify({username, email, password, role})});
        }
        closeAdminUserModal(); loadAdminUsers();
    } catch(e) { alert('Failed: '+e.message); }
}

async function toggleAdminUserStatus(btn) {
    try { await api(`/api/v1/admin/admin-users/${btn.dataset.id}/toggle-status`, {method:'POST'}); loadAdminUsers(); } catch(e) { alert('Failed'); }
}

async function deleteAdminUser(btn) {
    if (!confirm('Delete this admin user?')) return;
    try { await api(`/api/v1/admin/admin-users/${btn.dataset.id}`, {method:'DELETE'}); loadAdminUsers(); } catch(e) { alert('Failed'); }
}

// === MODERATION ===
async function loadModeration() {
    try {
        const hsf = document.getElementById('hash-report-status')?.value || 'pending';
        const [hashReports, blockedHashes, blockedUsers] = await Promise.all([
            api(`/api/v1/admin/hash-reports?status_filter=${encodeURIComponent(hsf)}`),
            api('/api/v1/admin/blocked-hashes'),
            api('/api/v1/admin/blocked-users')
        ]);
        renderHashReports(hashReports);
        document.getElementById('blocked-hashes-tbody').innerHTML = (blockedHashes||[]).map(h => `<tr><td class="font-mono">${esc((h.hash_value||'').slice(0,8))}...${esc((h.hash_value||'').slice(-6))}</td><td>${esc(h.hash_type||'sha256')}</td><td class="text-muted">${esc(h.reason||'')}</td><td class="text-muted">${ts(h.blocked_at)}</td><td class="text-right"><button class="btn btn-outline btn-sm" data-click="unblockHash" data-val="${esc(h.hash_value)}">Unblock</button></td></tr>`).join('');
        document.getElementById('blocked-users-tbody').innerHTML = (blockedUsers||[]).map(u => `<tr><td class="font-mono">${esc(u.user_id)}</td><td>${esc(u.username||'')}</td><td class="text-muted">${esc(u.reason||'')}</td><td class="text-muted">${ts(u.blocked_at)}</td><td class="text-muted">${ts(u.expires_at)}</td><td class="text-right"><button class="btn btn-outline btn-sm" data-click="unblockUser" data-val="${esc(u.user_id)}">Unblock</button></td></tr>`).join('');
        loadMessageReports();
        loadUserReports();
    } catch(e) { console.error('Moderation load failed', e); }
}

function renderHashReports(reports) {
    const grid = document.getElementById('hash-reports-grid');
    if (!reports || reports.length === 0) { grid.innerHTML = '<div class="text-muted text-sm">No reports found.</div>'; return; }
    grid.innerHTML = reports.map(r => {
        const preview = r.attachment_url ? `<img src="${esc(r.attachment_url)}" alt="Report" crossorigin="anonymous">` : '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:var(--muted-foreground);font-size:12px;">No preview</div>';
        const statusCls = r.status === 'blocked' ? 'badge-danger' : r.status === 'cleared' ? 'badge-success' : 'badge-warning';
        return `<div class="media-report-card">
            <div class="media-report-thumb">${preview}<div class="media-report-overlay"><span class="media-report-flag">Flagged</span></div></div>
            <div class="media-report-meta">
                <div style="display:flex;justify-content:space-between;align-items:center;"><strong>${esc(r.reason)}</strong><span class="badge ${statusCls}">${esc(r.status)}</span></div>
                <div class="text-muted">Reporter: ${esc(r.reporter_username||r.reporter_id)}</div>
                <div class="text-muted">Hash: ${esc((r.hash_value||'').slice(0,8))}...${esc((r.hash_value||'').slice(-6))}</div>
                <div class="text-muted">${ts(r.reported_at)}</div>
                <div class="media-report-actions">
                    <button class="btn btn-danger btn-sm" data-click="reviewHashReport" data-id="${esc(r.id)}" data-action="block">Block</button>
                    <button class="btn btn-outline btn-sm" data-click="reviewHashReport" data-id="${esc(r.id)}" data-action="clear">Clear</button>
                    <button class="btn btn-outline btn-sm" data-click="reviewHashReport" data-id="${esc(r.id)}" data-action="dismiss">Dismiss</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

async function reviewHashReport(btn) {
    try { await api(`/api/v1/admin/hash-reports/${btn.dataset.id}/review`, {method:'POST', body:JSON.stringify({action:btn.dataset.action})}); loadModeration(); } catch(e) { alert('Failed'); }
}

function refreshHashReports() { loadModeration(); }

async function blockHash() {
    const hash = document.getElementById('block-hash-val')?.value;
    const reason = document.getElementById('block-hash-reason')?.value;
    if (!hash) return;
    try { await api('/api/v1/admin/blocked-hashes', {method:'POST', body:JSON.stringify({hash_value:hash, reason})}); document.getElementById('block-hash-val').value = ''; document.getElementById('block-hash-reason').value = ''; loadModeration(); } catch(e) { alert('Failed'); }
}

async function unblockHash(btn) {
    try { await api(`/api/v1/admin/blocked-hashes/${encodeURIComponent(btn.dataset.val)}`, {method:'DELETE'}); loadModeration(); } catch(e) { alert('Failed'); }
}

async function blockUser() {
    const id = document.getElementById('block-user-id')?.value;
    const reason = document.getElementById('block-user-reason')?.value;
    const duration = document.getElementById('block-user-duration')?.value;
    if (!id) return;
    try { await api('/api/v1/admin/blocked-users', {method:'POST', body:JSON.stringify({user_id:id, reason, duration_hours:duration ? parseInt(duration) : null})}); document.getElementById('block-user-id').value = ''; document.getElementById('block-user-reason').value = ''; document.getElementById('block-user-duration').value = ''; loadModeration(); } catch(e) { alert('Failed'); }
}

async function unblockUser(btn) {
    try { await api(`/api/v1/admin/blocked-users/${btn.dataset.val}`, {method:'DELETE'}); loadModeration(); } catch(e) { alert('Failed'); }
}

async function loadMessageReports() {
    const sf = document.getElementById('message-report-status')?.value || 'pending';
    try {
        const [reports, counts] = await Promise.all([
            api(`/api/v1/admin/message-reports?status_filter=${encodeURIComponent(sf)}`),
            api('/api/v1/admin/message-reports/counts')
        ]);
        renderModCounts('message-report-counts', counts);
        document.getElementById('message-reports-tbody').innerHTML = (reports||[]).map(r => {
            const summary = [r.message_content, r.details].filter(Boolean).join(' • ');
            return `<tr><td style="max-width:260px;">${esc(summary||r.message_id)}</td><td>${esc(r.reporter_id)}</td><td>${esc(r.reported_user_id)}</td><td><strong>${esc(r.reason)}</strong></td><td>${reportBadge(r.status)}</td><td class="text-muted">${ts(r.reported_at)}</td><td class="text-right"><button class="btn btn-danger btn-sm" data-click="reviewMessageReport" data-id="${esc(r.id)}" data-action="action">Action</button> <button class="btn btn-outline btn-sm" data-click="reviewMessageReport" data-id="${esc(r.id)}" data-action="dismiss">Dismiss</button></td></tr>`;
        }).join('');
    } catch(e) { console.error('Load message reports failed', e); }
}

function refreshMessageReports() { loadMessageReports(); }

async function reviewMessageReport(btn) {
    try { await api(`/api/v1/admin/message-reports/${btn.dataset.id}/review`, {method:'POST', body:JSON.stringify({action:btn.dataset.action})}); loadMessageReports(); } catch(e) { alert('Failed'); }
}

async function loadUserReports() {
    const sf = document.getElementById('user-report-status')?.value || 'pending';
    try {
        const [reports, counts] = await Promise.all([
            api(`/api/v1/admin/user-reports?status_filter=${encodeURIComponent(sf)}`),
            api('/api/v1/admin/user-reports/counts')
        ]);
        renderModCounts('user-report-counts', counts);
        document.getElementById('user-reports-tbody').innerHTML = (reports||[]).map(r => {
            const evCnt = Array.isArray(r.evidence_message_ids) ? r.evidence_message_ids.length : 0;
            return `<tr><td>${esc(r.reported_user_id)}</td><td>${esc(r.reporter_id)}</td><td><strong>${esc(r.reason)}</strong><div class="text-muted">${esc(r.category||'')}</div></td><td>${evCnt ? evCnt+' msgs' : 'None'}</td><td>${reportBadge(r.status)}</td><td class="text-muted">${ts(r.reported_at)}</td><td class="text-right"><button class="btn btn-danger btn-sm" data-click="reviewUserReport" data-id="${esc(r.id)}" data-action="action">Action</button> <button class="btn btn-outline btn-sm" data-click="reviewUserReport" data-id="${esc(r.id)}" data-action="dismiss">Dismiss</button></td></tr>`;
        }).join('');
    } catch(e) { console.error('Load user reports failed', e); }
}

function refreshUserReports() { loadUserReports(); }

async function reviewUserReport(btn) {
    try { await api(`/api/v1/admin/user-reports/${btn.dataset.id}/review`, {method:'POST', body:JSON.stringify({action:btn.dataset.action})}); loadUserReports(); } catch(e) { alert('Failed'); }
}

function renderModCounts(id, counts) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = [['Pending',counts?.pending||0,'warning'],['Reviewed',counts?.reviewed||0,'info'],['Actioned',counts?.actioned||0,'danger'],['Dismissed',counts?.dismissed||0,'outline'],['Total',counts?.total||0,'primary']].map(([l,v,c]) => `<div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">${l}</span></div><div class="stat-card-value" style="font-size:20px;">${(v||0).toLocaleString()}</div></div>`).join('');
}

function reportBadge(s) { const m = {pending:'warning',reviewed:'info',actioned:'danger',dismissed:'outline'}; return `<span class="badge badge-${m[s]||'outline'}">${esc(s||'unknown')}</span>`; }

// === AUTOMOD ===
async function loadAutomodConfig() {
    try {
        const cfg = await api('/api/v1/admin/automod/config');
        document.getElementById('automod-enabled').checked = cfg.enabled !== false;
        const o = cfg.ai?.openai||{}, p = cfg.ai?.perspective||{}, c = cfg.ai?.custom||{};
        document.getElementById('automod-openai-key').value = o.api_key||'';
        document.getElementById('automod-openai-model').value = o.model||'';
        document.getElementById('automod-openai-url').value = o.api_url||'';
        document.getElementById('automod-openai-threshold').value = o.threshold??'';
        document.getElementById('automod-perspective-key').value = p.api_key||'';
        document.getElementById('automod-perspective-threshold').value = p.threshold??'';
        document.getElementById('automod-perspective-attributes').value = (p.attributes||[]).join(', ');
        document.getElementById('automod-custom-endpoint').value = c.endpoint_url||'';
        document.getElementById('automod-custom-key').value = c.api_key||'';
        document.getElementById('automod-custom-auth-header').value = c.auth_header||'';
        document.getElementById('automod-custom-auth-prefix').value = c.auth_prefix||'';
        document.getElementById('automod-custom-timeout').value = c.timeout_seconds??'';
        document.getElementById('automod-custom-threshold').value = c.threshold??'';
        document.getElementById('automod-custom-headers').value = c.headers ? JSON.stringify(c.headers) : '';
    } catch(e) { console.error('Load automod config failed', e); }
}

async function saveAutomodConfig() {
    const cfg = {
        enabled: document.getElementById('automod-enabled').checked,
        ai: {
            openai: {
                api_key: document.getElementById('automod-openai-key').value || null,
                model: document.getElementById('automod-openai-model').value || null,
                api_url: document.getElementById('automod-openai-url').value || null,
                threshold: parseFloat(document.getElementById('automod-openai-threshold').value) || null
            },
            perspective: {
                api_key: document.getElementById('automod-perspective-key').value || null,
                threshold: parseFloat(document.getElementById('automod-perspective-threshold').value) || null,
                attributes: document.getElementById('automod-perspective-attributes').value.split(',').map(s=>s.trim()).filter(Boolean)
            },
            custom: {
                endpoint_url: document.getElementById('automod-custom-endpoint').value || null,
                api_key: document.getElementById('automod-custom-key').value || null,
                auth_header: document.getElementById('automod-custom-auth-header').value || null,
                auth_prefix: document.getElementById('automod-custom-auth-prefix').value || null,
                timeout_seconds: parseInt(document.getElementById('automod-custom-timeout').value) || null,
                threshold: parseFloat(document.getElementById('automod-custom-threshold').value) || null,
                headers: document.getElementById('automod-custom-headers').value ? JSON.parse(document.getElementById('automod-custom-headers').value) : null
            }
        }
    };
    try { await api('/api/v1/admin/automod/config', {method:'PUT', body:JSON.stringify(cfg)}); alert('Config saved'); } catch(e) { alert('Failed: '+e.message); }
}

async function loadAutomodRules() {
    const sid = document.getElementById('automod-server-id')?.value.trim();
    if (!sid) return;
    try {
        const rules = await api(`/api/v1/admin/automod/rules?server_id=${encodeURIComponent(sid)}`);
        document.getElementById('automod-rules-tbody').innerHTML = (rules||[]).map(r => `
            <tr><td>${esc(r.name)}</td><td>${esc(r.rule_type)}</td><td>${r.enabled ? '<span class="badge badge-success">On</span>' : '<span class="badge badge-danger">Off</span>'}</td><td>${esc(String(r.priority))}</td><td class="text-muted">${esc((r.actions||[]).map(a=>a.action_type).join(', '))}</td>
            <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="editAutomodRule" data-id="${esc(r.id)}">Edit</button> <button class="btn btn-outline btn-sm" data-click="toggleAutomodRule" data-id="${esc(r.id)}" data-enabled="${r.enabled ? 'false' : 'true'}">${r.enabled ? 'Disable' : 'Enable'}</button> <button class="btn btn-danger btn-sm" data-click="deleteAutomodRule" data-id="${esc(r.id)}">Delete</button></td></tr>
        `).join('');
    } catch(e) { alert('Failed'); }
}

async function saveAutomodRule() { alert('Rule save requires full implementation - use the API directly'); }
async function editAutomodRule(btn) { alert('Edit rule #'+btn.dataset.id); }
async function toggleAutomodRule(btn) { alert('Toggle rule #'+btn.dataset.id); }
async function deleteAutomodRule(btn) { if (!confirm('Delete this rule?')) return; try { await api(`/api/v1/admin/automod/rules/${btn.dataset.id}`, {method:'DELETE'}); loadAutomodRules(); } catch(e) { alert('Failed'); } }
function resetAutomodRule() {}
function automodPresetDelete() { document.getElementById('automod-actions-json').value = JSON.stringify([{action_type:'delete_message'}], null, 2); }
function automodPresetAlert() { document.getElementById('automod-actions-json').value = JSON.stringify([{action_type:'delete_message'},{action_type:'alert_moderators'}], null, 2); }
function automodPresetTimeout() { document.getElementById('automod-actions-json').value = JSON.stringify([{action_type:'timeout_member'},{action_type:'alert_moderators'}], null, 2); }

// === LOGS ===
async function loadLogs() {
    try {
        const logs = await api('/api/v1/admin/logs');
        const sel = document.getElementById('log-file-select');
        if (!sel) return;
        sel.innerHTML = logs.map(l => `<option value="${esc(l.filename)}">${esc(l.filename)} (${(l.size/1024).toFixed(1)}KB)</option>`).join('');
        sel.onchange = () => loadLogFile(sel.value);
        if (logs.length > 0) loadLogFile(logs[0].filename);
    } catch(e) { console.error('Load logs failed', e); }
}

async function loadLogFile(f) {
    try {
        const search = document.getElementById('log-search')?.value || '';
        const d = await api(`/api/v1/admin/logs/${f}?limit=300${search ? '&q='+encodeURIComponent(search) : ''}`);
        const viewer = document.getElementById('log-viewer');
        viewer.innerHTML = (d.lines||[]).map(l => `<div class="log-${(l.level||'info').toLowerCase()}">${esc(l.raw||'')}</div>`).join('');
        if (document.getElementById('log-auto-refresh')?.checked) viewer.scrollTop = viewer.scrollHeight;
    } catch(e) { document.getElementById('log-viewer').textContent = 'Error loading logs'; }
}

let logRefreshInterval = null;
document.addEventListener('change', e => {
    if (e.target.id === 'log-auto-refresh') {
        if (logRefreshInterval) clearInterval(logRefreshInterval);
        if (e.target.checked) logRefreshInterval = setInterval(() => { const s = document.getElementById('log-file-select'); if(s&&s.value) loadLogFile(s.value); }, 5000);
    }
});

function scrollLogTop() { const v = document.getElementById('log-viewer'); if(v) v.scrollTop = 0; }
function scrollLogBottom() { const v = document.getElementById('log-viewer'); if(v) v.scrollTop = v.scrollHeight; }

// === ROLES ===
async function loadRoles() {
    try {
        const d = await api('/api/v1/admin/roles');
        document.getElementById('roles-tbody').innerHTML = (d.roles||[]).map(r => `
            <tr><td>${esc(r.name)}</td><td class="text-muted">${esc(r.description||'')}</td><td class="font-mono text-sm">${Object.keys(r.permissions||{}).length} perms</td><td>${r.is_system ? '<span class="badge badge-warning">System</span>' : '<span class="badge badge-outline">Custom</span>'}</td>
            <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="editRole" data-id="${esc(r.id)}">Edit</button></td></tr>
        `).join('');
    } catch(e) { console.error('Roles load failed'); }
}

async function createRole() {
    const name = prompt('Enter role name:'); if (!name) return;
    const desc = prompt('Enter description:'); if (!desc) return;
    try { await api('/api/v1/admin/roles', {method:'POST', body:JSON.stringify({name, description, permissions:{}})}); loadRoles(); } catch(e) { alert('Failed: '+e.message); }
}

async function editRole(btn) {
    try {
        const r = await api(`/api/v1/admin/roles/${btn.dataset.id}`);
        document.getElementById('role-detail-title').textContent = `Edit: ${esc(r.name)}`;
        document.getElementById('role-description-input').value = r.description||'';
        document.getElementById('role-permissions-input').value = JSON.stringify(r.permissions, null, 2);
        document.getElementById('role-detail').classList.remove('hidden');
        document.getElementById('role-detail').dataset.roleId = r.id;
        document.getElementById('role-detail').dataset.isSystem = r.is_system;
    } catch(e) { alert('Failed: '+e.message); }
}

function closeRoleDetail() { document.getElementById('role-detail').classList.add('hidden'); }

async function updateRole() {
    const id = document.getElementById('role-detail').dataset.roleId;
    if (document.getElementById('role-detail').dataset.isSystem === 'true') { alert('Cannot modify system roles'); return; }
    let perms;
    try { perms = JSON.parse(document.getElementById('role-permissions-input').value); } catch(e) { alert('Invalid JSON'); return; }
    try { await api(`/api/v1/admin/roles/${id}`, {method:'PUT', body:JSON.stringify({description:document.getElementById('role-description-input').value, permissions:perms})}); closeRoleDetail(); loadRoles(); } catch(e) { alert('Failed: '+e.message); }
}

async function deleteRole() {
    const id = document.getElementById('role-detail').dataset.roleId;
    if (document.getElementById('role-detail').dataset.isSystem === 'true') { alert('Cannot delete system roles'); return; }
    if (!confirm('Delete this role?')) return;
    try { await api(`/api/v1/admin/roles/${id}`, {method:'DELETE'}); closeRoleDetail(); loadRoles(); } catch(e) { alert('Failed: '+e.message); }
}

// === APPROVALS ===
async function loadApprovals() {
    try {
        const status = document.getElementById('approval-status-filter')?.value || '';
        const url = status ? `/api/v1/admin/approvals?status=${status}` : '/api/v1/admin/approvals';
        const d = await api(url);
        document.getElementById('approvals-tbody').innerHTML = (d.approvals||[]).map(a => `
            <tr><td class="font-mono">${esc(a.id)}</td><td>${esc(a.action_type)}</td><td>${esc(a.requested_by)}</td>
            <td><span class="badge ${a.status==='approved'?'badge-success':a.status==='rejected'?'badge-danger':'badge-warning'}">${esc(a.status)}</span></td>
            <td>${a.current_approvals}/${a.required_approvals}</td><td class="text-muted">${ts(a.created_at)}</td>
            <td class="text-right">${a.status==='pending' ? `<button class="btn btn-primary btn-sm" data-click="approveRequest" data-id="${esc(a.id)}">Approve</button> <button class="btn btn-danger btn-sm" data-click="rejectRequest" data-id="${esc(a.id)}">Reject</button>` : '-'}</td></tr>
        `).join('');
    } catch(e) { console.error('Approvals load failed'); }
}

function refreshApprovals() { loadApprovals(); }

async function approveRequest(btn) {
    try { await api(`/api/v1/admin/approvals/${btn.dataset.id}/approve`, {method:'POST'}); loadApprovals(); } catch(e) { alert('Failed: '+e.message); }
}

async function rejectRequest(btn) {
    const reason = prompt('Rejection reason:'); if (!reason) return;
    try { await api(`/api/v1/admin/approvals/${btn.dataset.id}/reject`, {method:'POST', body:JSON.stringify({decision:'reject', reason})}); loadApprovals(); } catch(e) { alert('Failed: '+e.message); }
}

// === DATABASE ===
async function refreshDatabase() {
    try {
        const pool = await api('/api/v1/admin/database/pool-health').catch(() => ({}));
        const status = await api('/api/v1/admin/database/migrations/status').catch(() => ({}));
        setText('migration-applied-count', String(status?.applied_count||status?.applied||'-'));
        setText('migration-pending-count', String(status?.pending_count||status?.pending||'-'));
        setText('migration-failed-count', String(status?.failed_count||status?.failed||'-'));
        setText('dash-db-pool-active', String(pool?.active_connections||'?'));
        setText('dash-db-pool-idle', String(pool?.idle_connections||'?'));
        setText('dash-db-pool-max', String(pool?.max_connections||'?'));
    } catch(e) { console.error('Database load failed', e); }
}

// === MIGRATIONS ===
async function refreshMigrations() {
    try {
        const d = await api('/api/v1/admin/migrations');
        setText('migration-applied-count', String(d.applied_count||0));
        setText('migration-pending-count', String(d.pending_count||0));
        setText('migration-failed-count', String(d.failed_count||0));
        migrations = d.migrations || [];
        const tbody = document.getElementById('migrations-tbody') || document.getElementById('migrations-tbody-mig');
        if (!tbody) return;
        if (migrations.length === 0) { tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center;">No migrations</td></tr>'; return; }
        tbody.innerHTML = migrations.map(m => `
            <tr>
                <td class="font-mono">${esc(m.version)}</td>
                <td>${esc(m.name)}</td>
                <td><span class="badge ${m.status==='completed'?'badge-success':m.status==='failed'?'badge-danger':'badge-warning'}">${esc(m.status)}</span></td>
                <td>${m.is_irreversible ? '<span class="badge badge-danger">IRREVERSIBLE</span>' : '-'}</td>
                <td class="text-muted">${m.applied_at || '-'}</td>
                <td class="text-right">
                    ${m.status === 'pending' ? `<button class="btn btn-primary btn-sm" data-click="showRunMigration" data-version="${esc(m.version)}">Run</button> <button class="btn btn-outline btn-sm" data-click="showDryRunMigration" data-version="${esc(m.version)}">Dry Run</button>` : ''}
                    <button class="btn btn-outline btn-sm" data-click="showMigrationDetails" data-version="${esc(m.version)}">Details</button>
                </td>
            </tr>
        `).join('');
    } catch(e) { console.error('Migrations load failed'); }
}

function showRunMigration(btn) { showRunModal(btn.dataset.version, false); }
function showDryRunMigration(btn) { showRunModal(btn.dataset.version, true); }

function showRunModal(version, dryRun) {
    const m = migrations.find(x => x.version === version);
    if (!m) return;
    currentMigration = { ...m, dry_run: dryRun };
    const content = document.getElementById('migration-run-modal-content');
    content.innerHTML = '';
    if (m.is_irreversible && !dryRun) {
        content.innerHTML = `
            <div style="background:color-mix(in oklch, var(--destructive) 10%, transparent);border:1px solid var(--destructive);border-radius:8px;padding:16px;margin-bottom:16px;">
                <h3 style="color:var(--destructive);margin-bottom:8px;">IRREVERSIBLE MIGRATION</h3>
                <p class="text-sm">This migration cannot be rolled back.</p>
                ${!m.can_run ? `<p class="text-sm" style="color:var(--warning);margin-top:8px;"><strong>Blocked:</strong> ${esc(m.can_run_reason)}</p>` : ''}
            </div>
            <div style="margin-bottom:12px;"><label>Type "THE DATABASE IS BACKED UP" to confirm</label><input type="text" id="migration-confirmation-text" placeholder="THE DATABASE IS BACKED UP"></div>`;
        document.getElementById('migration-run-confirm-btn').disabled = true;
        document.getElementById('migration-confirmation-text')?.addEventListener('input', e => {
            document.getElementById('migration-run-confirm-btn').disabled = e.target.value !== 'THE DATABASE IS BACKED UP';
        });
    } else {
        content.innerHTML = `<p><strong>Version:</strong> ${esc(m.version)}</p><p><strong>Name:</strong> ${esc(m.name)}</p><p><strong>Dry Run:</strong> ${dryRun ? 'Yes' : 'No'}</p>${!m.can_run ? `<p style="color:var(--warning);margin-top:8px;"><strong>Blocked:</strong> ${esc(m.can_run_reason)}</p>` : ''}`;
        document.getElementById('migration-run-confirm-btn').disabled = !m.can_run;
    }
    document.getElementById('migration-run-modal').classList.add('active');
}

async function confirmRunMigration() {
    if (!currentMigration) return;
    const btn = document.getElementById('migration-run-confirm-btn');
    btn.disabled = true; btn.textContent = 'Running...';
    try {
        const d = await api(`/api/v1/admin/migrations/${currentMigration.version}/run`, {
            method:'POST',
            body: JSON.stringify({
                dry_run: currentMigration.dry_run,
                confirmation_text: currentMigration.is_irreversible && !currentMigration.dry_run ? document.getElementById('migration-confirmation-text')?.value : null
            })
        });
        alert(currentMigration.dry_run ? 'Dry run completed' : 'Migration completed');
        closeMigrationModal(); refreshMigrations();
    } catch(e) { alert('Failed: '+(e.message||'Unknown')); }
    btn.disabled = false; btn.textContent = 'Run Migration';
}

async function showMigrationDetails(btn) {
    try {
        const d = await api(`/api/v1/admin/migrations/${btn.dataset.version}`);
        const content = document.getElementById('migration-details-modal-content');
        content.innerHTML = `
            <p><strong>Version:</strong> ${esc(d.version)}</p>
            <p><strong>Name:</strong> ${esc(d.name)}</p>
            <p><strong>Status:</strong> <span class="badge ${d.status==='completed'?'badge-success':d.status==='failed'?'badge-danger':'badge-warning'}">${esc(d.status)}</span></p>
            <p><strong>Irreversible:</strong> ${d.is_irreversible ? 'Yes' : 'No'}</p>
            <p><strong>Applied:</strong> ${d.applied_at ? esc(d.applied_at) : 'N/A'}</p>
            <p><strong>Execution:</strong> ${d.execution_time_ms ? esc(d.execution_time_ms)+'ms' : 'N/A'}</p>
            ${d.depends_on?.length ? `<p><strong>Depends On:</strong> ${esc(d.depends_on.join(', '))}</p>` : ''}
            <h4 style="margin-top:16px;font-size:14px;font-weight:600;">Logs</h4>
            <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;max-height:300px;overflow-y:auto;font-family:monospace;font-size:12px;">
                ${(d.logs||[]).length === 0 ? '<p class="text-muted">No logs</p>' : d.logs.map(l => `<div style="padding:2px 0;color:${l.level==='ERROR'?'var(--destructive)':l.level==='WARNING'?'var(--warning)':'var(--muted-foreground)'};">[${l.timestamp}] ${l.level}: ${esc(l.message)}</div>`).join('')}
            </div>`;
        document.getElementById('migration-details-modal').classList.add('active');
    } catch(e) { alert('Failed to load details'); }
}

function showEmergencyModal() { document.getElementById('migration-emergency-modal').classList.add('active'); }

async function generateEmergencyToken() {
    const reason = document.getElementById('migration-emergency-reason').value;
    const expires = document.getElementById('migration-emergency-expires').value;
    if (!reason) { alert('Enter a reason'); return; }
    try {
        const d = await api('/api/v1/admin/migrations/emergency-override', {method:'POST', body:JSON.stringify({reason, expires_minutes:parseInt(expires)})});
        alert(`Emergency token:\n\n${d.token}\n\nExpires: ${d.expires_at}`);
        closeMigrationModal();
    } catch(e) { alert('Failed'); }
}

function closeMigrationModal() {
    document.getElementById('migration-run-modal')?.classList.remove('active');
    document.getElementById('migration-details-modal')?.classList.remove('active');
    document.getElementById('migration-emergency-modal')?.classList.remove('active');
    currentMigration = null;
}

// === BOTS ===
async function refreshAdminBots() {
    try {
        const [stats, apps, reqs] = await Promise.all([
            api('/api/v1/admin/bots/stats'),
            api('/api/v1/admin/bots/applications'),
            api('/api/v1/admin/bots/requests')
        ]);
        setText('bot-stat-total', String(stats?.total_bots||0));
        setText('bot-stat-approved', String(stats?.approved_installations||0));
        setText('bot-stat-pending', String(stats?.pending_requests||0));
        setText('bot-stat-servers', String(stats?.servers_with_bots||0));
        document.getElementById('bots-table-body').innerHTML = (apps||[]).length === 0 ? '<tr><td colspan="6" class="text-muted">No bots</td></tr>' : (apps||[]).map(a => `<tr><td>${esc(a.name||a.application_id)}</td><td class="font-mono">${esc(a.bot_id||'')}</td><td>${esc(a.owner||'')}</td><td class="text-right">${a.approved_servers||0}</td><td class="text-right">${a.pending_requests||0}</td><td class="text-muted">${ts(a.created_at)}</td></tr>`).join('');
        document.getElementById('bots-requests-body').innerHTML = (reqs||[]).length === 0 ? '<tr><td colspan="6" class="text-muted">No requests</td></tr>' : (reqs||[]).map(r => `<tr><td>${esc(r.application_name||r.application_id)}</td><td>${esc(r.server_name||r.server_id)}</td><td>${esc(r.requester||'')}</td><td>${esc(r.reason||'')}</td><td><span class="badge badge-warning">${esc(r.status||'pending')}</span></td><td class="text-muted">${ts(r.created_at)}</td></tr>`).join('');
    } catch(e) { console.error('Bots load failed'); }
}
function botsRefresh() { refreshAdminBots(); }

// === TELEMETRY ===
async function refreshTelemetryStats() {
    try {
        const hrs = document.getElementById('tel-hours')?.value || '24';
        const d = await api(`/api/v1/admin/telemetry/stats?hours=${hrs}`);
        document.getElementById('telemetry-stats-tbody').innerHTML = (d.stats||[]).map(s => `
            <tr><td class="font-mono">${esc(s.endpoint)}</td><td>${esc(s.method)}</td><td class="text-right">${(s.count||0).toLocaleString()}</td><td class="text-right">${Math.round(s.avg_ms||0)}</td><td class="text-right">${s.min_ms||'-'}</td><td class="text-right">${s.max_ms||'-'}</td><td class="text-right">${Math.round(s.p95_ms||0)}</td><td class="text-right">${Math.round(s.p99_ms||0)}</td><td class="text-right">${s.error_count||0}</td><td class="text-right"><span class="badge ${((s.error_count||0) > 0) ? 'badge-danger' : 'badge-success'}">${s.count ? ((s.error_count||0)/s.count*100).toFixed(2)+'%' : '0%'}</span></td></tr>
        `).join('');
    } catch(e) { console.error('Telemetry load failed'); }
}

// === AUDIT ===
async function loadAuditLog() {
    try {
        const action = document.getElementById('audit-action-filter')?.value || '';
        const status = document.getElementById('audit-status-filter')?.value || '';
        let url = '/api/v1/admin/audit/logs';
        const params = new URLSearchParams();
        if (action) params.set('action_family', action);
        if (status) params.set('status', status);
        const qs = params.toString();
        if (qs) url += '?' + qs;
        const d = await api(url);
        document.getElementById('audit-tbody').innerHTML = (d.logs||[]).length === 0 ? '<tr><td colspan="6" class="text-muted">No audit logs</td></tr>' : (d.logs||[]).map(l => `
            <tr><td class="text-muted">${ts(l.timestamp||l.created_at)}</td><td>${esc(l.admin_username||l.admin_id||'')}</td><td>${esc(l.action_type||l.action||'')}</td><td>${esc(l.target_type||'')} ${esc(l.target_id||'')}</td><td class="text-muted">${esc(l.details||'')}</td><td><span class="badge ${l.status==='success'?'badge-success':'badge-danger'}">${esc(l.status||'')}</span></td></tr>
        `).join('');
    } catch(e) { console.error('Audit load failed'); }
}
function refreshAuditLog() { loadAuditLog(); }
async function exportAuditLog() {
    try {
        const d = await api('/api/v1/admin/audit/logs/export?format=csv');
        const blob = new Blob([typeof d === 'string' ? d : JSON.stringify(d, null, 2)], {type:'text/csv'});
        const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'audit-log.csv'; a.click();
    } catch(e) { alert('Export failed'); }
}

// === PLEXIJOIN ===
async function refreshPlexiJoin() {
    try {
        const [status, connections, requests] = await Promise.all([
            api('/api/v1/admin/plexijoin/status').catch(() => ({})),
            api('/api/v1/admin/plexijoin/connections').catch(() => ([])),
            api('/api/v1/admin/plexijoin/requests').catch(() => ([]))
        ]);
        document.getElementById('plexijoin-status').innerHTML = `
            <div class="stats-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));">
                <div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">Connections</span></div><div class="stat-card-value">${Array.isArray(connections)?connections.length:0}</div></div>
                <div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">Inbound Requests</span></div><div class="stat-card-value">${Array.isArray(requests)?requests.length:0}</div></div>
                <div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">Status</span></div><div class="stat-card-value" style="font-size:16px;"><span class="badge ${status?.healthy ? 'badge-success' : 'badge-warning'}">${status?.healthy ? 'Healthy' : 'Unknown'}</span></div></div>
            </div>`;
        const conns = Array.isArray(connections) ? connections : [];
        document.getElementById('plexijoin-connections').innerHTML = conns.length === 0 ? '<div class="card"><div class="card-content text-muted">No connections configured.</div></div>' : conns.map(c => `
            <div class="card">
                <div class="card-header"><span class="card-title">${esc(c.name||c.instance_url)}</span>
                    <div style="display:flex;gap:8px;"><button class="btn btn-outline btn-sm" data-click="testConnection" data-id="${esc(c.id)}">Test</button><button class="btn btn-danger btn-sm" data-click="deleteConnection" data-id="${esc(c.id)}">Delete</button></div>
                </div>
                <div class="card-content">
                    <div class="grid-2" style="display:grid;gap:12px;">
                        <div><span class="text-xs text-muted">URL</span><p class="font-mono text-sm">${esc(c.instance_url)}</p></div>
                        <div><span class="text-xs text-muted">Status</span><p><span class="badge ${c.connected ? 'badge-success' : 'badge-warning'}">${c.connected ? 'Connected' : 'Disconnected'}</span></p></div>
                        <div><span class="text-xs text-muted">Created</span><p class="text-sm text-muted">${ts(c.created_at)}</p></div>
                        <div><span class="text-xs text-muted">Last Sync</span><p class="text-sm text-muted">${ts(c.last_sync_at) || 'Never'}</p></div>
                    </div>
                </div>
            </div>`).join('');
    } catch(e) { console.error('PlexiJoin load failed'); }
}

function showCreateConnectionModal() { document.getElementById('create-connection-modal').classList.add('active'); }
function closeCreateConnectionModal() { document.getElementById('create-connection-modal').classList.remove('active'); }
async function confirmCreateConnection() {
    const url = document.getElementById('create-conn-url').value;
    const name = document.getElementById('create-conn-name').value;
    const key = document.getElementById('create-conn-key').value;
    if (!url) return;
    try { await api('/api/v1/admin/plexijoin/connections', {method:'POST', body:JSON.stringify({instance_url:url, display_name:name, api_key:key})}); closeCreateConnectionModal(); refreshPlexiJoin(); } catch(e) { alert('Failed'); }
}

async function deleteConnection(btn) {
    if (!confirm('Delete this connection?')) return;
    try { await api(`/api/v1/admin/plexijoin/connections/${btn.dataset.id}`, {method:'DELETE'}); refreshPlexiJoin(); } catch(e) { alert('Failed'); }
}

async function testConnection(btn) {
    try { const d = await api(`/api/v1/admin/plexijoin/connections/${btn.dataset.id}/test`, {method:'POST'}); alert(d.message||'Connection test complete'); } catch(e) { alert('Test failed: '+e.message); }
}

// === LICENSE ===
async function loadLicense() {
    document.getElementById('license-loading').classList.remove('hidden');
    document.getElementById('license-info').classList.add('hidden');
    try {
        const [status, features] = await Promise.all([
            api('/api/v1/admin/license/status').catch(() => ({valid:false, tier:'none'})),
            api('/api/v1/admin/license/features').catch(() => ({}))
        ]);
        document.getElementById('license-loading').classList.add('hidden');
        const info = document.getElementById('license-info');
        info.classList.remove('hidden');
        const valid = status.valid || false;
        const fg = features.features || features;
        info.innerHTML = `
            <div class="stats-grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));">
                <div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">Status</span></div><div class="stat-card-value" style="font-size:16px;"><span class="badge ${valid ? 'badge-success' : 'badge-danger'}">${valid ? 'Valid' : 'Invalid'}</span></div></div>
                <div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">Tier</span></div><div class="stat-card-value" style="font-size:16px;">${esc(status.tier||'none')}</div></div>
                <div class="stat-card"><div class="stat-card-header"><span class="stat-card-label">Days Remaining</span></div><div class="stat-card-value" style="font-size:16px;">${status.days_remaining ?? 'N/A'}</div></div>
            </div>
            <div class="card" style="margin-top:16px;">
                <div class="card-header"><span class="card-title">Features</span></div>
                <div class="card-content">
                    <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));">
                        ${Object.entries(fg||{}).map(([k,v]) => {
                            const val = v?.enabled ?? v;
                            return '<div style="display:flex;align-items:center;justify-content:space-between;padding:8px;background:var(--secondary);border-radius:6px;"><span>'+esc(k)+'</span><span class="badge '+(val ? 'badge-success' : 'badge-danger')+'">'+(val ? 'Enabled' : 'Disabled')+'</span></div>';
                        }).join('')}
                    </div>
                </div>
            </div>
            <div class="card" style="margin-top:16px;">
                <div class="card-header"><span class="card-title">Apply New License</span></div>
                <div class="card-content">
                    <div style="margin-bottom:8px;">
                        <textarea id="license-apply-input" placeholder="Paste base64-encoded license key here..." style="width:100%;min-height:80px;font-family:monospace;font-size:13px;padding:8px;border:1px solid var(--border);border-radius:6px;background:var(--secondary);color:var(--text);resize:vertical;"></textarea>
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button class="btn btn-primary btn-sm" data-click="applyLicense">Apply License</button>
                        <button class="btn btn-outline btn-sm" data-click="reloadLicense">Reload from Disk</button>
                    </div>
                    <div id="license-diff" class="hidden" style="margin-top:12px;"></div>
                </div>
            </div>`;
    } catch(e) { document.getElementById('license-loading').textContent = 'Failed to load license info'; }
}

async function reloadLicense() {
    try { await api('/api/v1/admin/license/reload', {method:'POST'}); alert('License reloaded'); loadLicense(); } catch(e) { alert('Failed'); }
}

async function applyLicense() {
    const input = document.getElementById('license-apply-input');
    const key = input?.value?.trim();
    if (!key) { alert('Paste a license key first'); return; }
    const diffEl = document.getElementById('license-diff');
    try {
        const resp = await api('/api/v1/admin/license/apply', {
            method:'POST',
            body:JSON.stringify({license_key: key})
        });
        if (resp.applied && resp.before && resp.after) {
            let html = '<div class="card" style="border:1px solid var(--success);"><div class="card-header"><span class="card-title" style="color:var(--success);">License Applied — Changes</span></div><div class="card-content"><table style="width:100%;border-collapse:collapse;font-size:13px;">';
            html += '<tr style="border-bottom:1px solid var(--border);"><th style="padding:6px 8px;text-align:left;">Field</th><th style="padding:6px 8px;text-align:left;">Before</th><th style="padding:6px 8px;text-align:left;">After</th></tr>';
            const rows = [
                ['Instance ID', resp.before.instance_id || '(none)', resp.after.instance_id],
                ['Valid', String(resp.before.valid), String(resp.after.valid)],
                ['Free Tier', String(resp.before.free_tier), String(resp.after.free_tier)],
                ['Expiry', 'N/A', resp.after.expiry ? new Date(resp.after.expiry*1000).toISOString().slice(0,10) : 'Perpetual'],
            ];
            if (resp.after.features) {
                Object.entries(resp.after.features).forEach(([k,v]) => {
                    rows.push(['Feature: '+esc(k), '?', typeof v === 'object' ? JSON.stringify(v) : String(v)]);
                });
            }
            if (resp.after.limits) {
                Object.entries(resp.after.limits).forEach(([k,v]) => {
                    rows.push(['Limit: '+esc(k), 'N/A', String(v)]);
                });
            }
            rows.forEach(([label, beforeVal, afterVal]) => {
                html += '<tr style="border-bottom:1px solid var(--border);">';
                html += '<td style="padding:6px 8px;font-weight:600;">'+esc(label)+'</td>';
                html += '<td style="padding:6px 8px;">'+esc(beforeVal)+'</td>';
                const changed = afterVal !== beforeVal && !(beforeVal === '(none)' && afterVal);
                html += '<td style="padding:6px 8px;'+ (changed ? 'color:var(--success);font-weight:600;' : '') +'">'+esc(afterVal)+'</td>';
                html += '</tr>';
            });
            html += '</table></div></div>';
            diffEl.innerHTML = html;
            diffEl.classList.remove('hidden');
            input.value = '';
            loadLicense();
        } else {
            diffEl.innerHTML = '<div class="text-muted">'+esc(resp.message||'License not applied')+'</div>';
            diffEl.classList.remove('hidden');
        }
    } catch(e) {
        alert('Failed to apply license: '+(e.message||e));
    }
}


// === ACCOUNT ===
async function loadAccount() {
    try {
        const sec = await api('/api/v1/admin/auth/security-status');
        setText('account-username', sec?.username || '-');
        setText('account-role', 'Super Admin');
        setText('account-last-login', ts(sec?.last_login));
        setText('account-2fa-status', sec?.otp_enabled ? 'Enabled' : 'Disabled');
        setText('header-avatar-name', sec?.username || 'admin');
        document.getElementById('header-avatar-initials').textContent = (sec?.username||'A')[0].toUpperCase() + ((sec?.username||'A')[1]||'').toUpperCase();
    } catch(e) { console.error('Load account failed', e); }
}

function showAccount() { showTab('account'); }

async function changeOwnPassword() {
    const cur = document.getElementById('account-current-password')?.value;
    const nw = document.getElementById('account-new-password')?.value;
    if (!cur || !nw) return;
    try { await api('/api/v1/admin/auth/change-password', {method:'POST', body:JSON.stringify({current_password:cur, new_password:nw})}); alert('Password changed'); document.getElementById('account-current-password').value = ''; document.getElementById('account-new-password').value = ''; } catch(e) { alert('Failed: '+e.message); }
}

// === LOGOUT ===
async function logout() {
    try { await fetch('/api/v1/admin/logout', { method:'POST', headers: { 'Authorization': `Bearer ${sessionStorage.getItem('plexichat-admin-token')}` } }); } catch(e) {}
    sessionStorage.removeItem('plexichat-admin-token');
    window.location.replace('/api/v1/admin/login');
}

// === SIDEBAR TOGGLE INIT ===
document.addEventListener('DOMContentLoaded', () => {
    const hash = window.location.hash.slice(1);
    if (hash && pageTitles[hash]) showTab(hash);
    else showTab('dashboard');
    refreshMetrics();

    document.getElementById('metric-hours')?.addEventListener('change', refreshMetrics);
    document.getElementById('endpoint-filter')?.addEventListener('input', renderEndpointTable);
    document.getElementById('log-file-select')?.addEventListener('change', e => { if (e.target.value) loadLogFile(e.target.value); });
    document.getElementById('log-search')?.addEventListener('input', () => { const s = document.getElementById('log-file-select'); if (s && s.value) loadLogFile(s.value); });
    document.getElementById('user-search-input')?.addEventListener('input', loadUsers);
    document.getElementById('ticket-status-filter')?.addEventListener('change', loadTickets);
    document.getElementById('hash-report-status')?.addEventListener('change', loadModeration);
    document.getElementById('message-report-status')?.addEventListener('change', loadMessageReports);
    document.getElementById('user-report-status')?.addEventListener('change', loadUserReports);
    document.getElementById('approval-status-filter')?.addEventListener('change', loadApprovals);
    document.getElementById('dash-time-range')?.addEventListener('change', refreshMetrics);
});
