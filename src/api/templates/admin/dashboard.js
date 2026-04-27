// Admin Dashboard JavaScript - Modularized
// This file contains all the JavaScript logic for the admin dashboard

// Global Chart Defaults for High Visibility
Chart.defaults.color = '#ccc';
Chart.defaults.borderColor = 'rgba(255,255,255,0.1)';
Chart.defaults.plugins.legend.display = true;
Chart.defaults.plugins.legend.position = 'top';
Chart.defaults.plugins.legend.align = 'end';
Chart.defaults.plugins.legend.labels.boxWidth = 12;
Chart.defaults.plugins.legend.labels.font = { size: 10 };
Chart.defaults.elements.line.borderWidth = 2;
Chart.defaults.elements.point.radius = 3;
Chart.defaults.elements.point.hoverRadius = 5;

const COLORS = {
    blue: '#36a2eb',
    orange: '#ff9f40',
    green: '#4bc0c0',
    red: '#ff6384',
    purple: '#9966ff',
    yellow: '#ffcd56'
};

let currentTab = 'metrics', charts = {}, metricData = [], historicalData = {}, selectedUserId = null, selectedTicketId = null, selectedTicketStatus = null, selectedAccessTokenId = null, adminOtpSetupChallenge = null;

const escapeHtml = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

const api = async (p, o = {}) => {
    const token = sessionStorage.getItem('plexichat-admin-token');
    const r = await fetch(p, { ...o, headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json', ...o.headers } });
    if (r.status === 401) { sessionStorage.removeItem('plexichat-admin-token'); window.location.replace('/api/v1/admin/login'); return; }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail?.error?.message || d.detail || 'API Error');
    return d;
};

const showTab = (n) => {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === n));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('hidden', c.id !== `tab-${n}`));
    currentTab = n;
    window.location.hash = n;
    if (n === 'metrics') refreshMetrics();
    else if (n === 'tickets') loadTickets();
    else if (n === 'deletions') refreshDeletions();
    else if (n === 'security') loadSecurity();
    else if (n === 'migrations') refreshMigrations();
    else if (n === 'users') loadAdminUsers();
    else if (n === 'automod') {
        loadAutomodConfig();
        const serverId = document.getElementById('automod-server-id')?.value;
        if (serverId) loadAutomodRules();
    }
    else if (n === 'logs') loadLogs();
    else if (n === 'roles') loadRoles();
    else if (n === 'approvals') loadApprovals();
};

async function refreshMetrics() {
    try {
        const hrs = document.getElementById('metric-hours').value;
        const [dash, stats, ver] = await Promise.all([
            api('/api/v1/admin/dashboard'), 
            api(`/api/v1/admin/telemetry/stats?hours=${hrs}`),
            api('/api/v1/version')
        ]);
        metricData = stats.stats;
        if (ver && ver.version) {
            document.getElementById('server-version').textContent = ver.version.string;
        }
        updateOverview(dash, stats.stats);
        updateCharts(dash, stats.stats);
        renderEndpointTable();
        syncTelemetryHistoryEndpoints(stats.stats);
    } catch (e) { console.error('Refresh failed:', e); }
}

function updateOverview(d, s) {
    document.getElementById('stat-active-users').textContent = d.active_users.toLocaleString();
    document.getElementById('stat-total-users').textContent = d.total_users.toLocaleString();
    document.getElementById('stat-scheduled-deletions').textContent = d.scheduled_deletions.toLocaleString();

    if (d.server_version) {
        document.getElementById('server-version').textContent = d.server_version;
    }
    
    const total = s.reduce((a, b) => a + b.count, 0);
    const errors = s.reduce((a, b) => a + b.error_count, 0);
    const avgLat = total > 0 ? (s.reduce((a, b) => a + (b.avg_ms * b.count), 0) / total) : 0;
    
    document.getElementById('stat-avg-latency').textContent = Math.round(avgLat);
    document.getElementById('stat-error-rate').textContent = total > 0 ? (errors / total * 100).toFixed(1) : '0.0';
    const sysLoadEl = document.getElementById('stat-sys-load');
    if (sysLoadEl) sysLoadEl.textContent = d.system?.cpu_percent?.toFixed(1) || '0.0';
}

function renderChart(canvasId, type, data, options) {
    const ctx = document.getElementById(canvasId)?.getContext('2d');
    if (!ctx) return;
    if (charts[canvasId]) charts[canvasId].destroy();
    charts[canvasId] = new Chart(ctx, { type, data, options });
}

function updateHist(canvasId, items) {
    renderChart(canvasId, 'bar', {
        labels: items.map(i => i.label),
        datasets: [{ data: items.map(i => i.val), backgroundColor: items.map(i => i.col + 'cc') }]
    }, { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { grid: { color: 'rgba(255,255,255,0.1)' }, ticks: { color: '#aaa' } } } });
}

// Additional dashboard functions would be included here
// (Functions for tickets, security, automod, logs, etc.)

// Roles Management Functions
async function loadRoles() {
    try {
        const data = await api('/api/v1/admin/roles');
        renderRoles(data.roles);
    } catch (e) { console.error('Failed to load roles:', e); }
}

function renderRoles(roles) {
    const tbody = document.getElementById('roles-tbody');
    if (!roles || roles.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="color:#666;">No roles found</td></tr>';
        return;
    }
    tbody.innerHTML = roles.map(role => `
        <tr>
            <td>${escapeHtml(role.name)}</td>
            <td>${escapeHtml(role.description || '')}</td>
            <td style="font-family:monospace;font-size:0.75rem;">${Object.keys(role.permissions || {}).length} permissions</td>
            <td>${role.is_system ? '<span style="color:var(--warn);">System</span>' : 'Custom'}</td>
            <td>
                <button class="btn btn-secondary btn-sm" data-click="editRole" data-id="${escapeHtml(role.id)}">Edit</button>
            </td>
        </tr>
    `).join('');
}

async function createRole() {
    const name = prompt('Enter role name:');
    if (!name) return;
    const description = prompt('Enter role description:');
    if (!description) return;
    
    try {
        await api('/api/v1/admin/roles', {
            method: 'POST',
            body: JSON.stringify({
                name,
                description,
                permissions: {}
            })
        });
        loadRoles();
    } catch (e) { alert('Failed to create role: ' + e.message); }
}

async function editRole(id) {
    try {
        const role = await api(`/api/v1/admin/roles/${id}`);
        document.getElementById('role-detail-title').textContent = `Edit Role: ${escapeHtml(role.name)}`;
        document.getElementById('role-description-input').value = role.description || '';
        document.getElementById('role-permissions-input').value = JSON.stringify(role.permissions, null, 2);
        document.getElementById('role-detail').classList.remove('hidden');
        document.getElementById('role-detail').dataset.roleId = id;
        document.getElementById('role-detail').dataset.isSystem = role.is_system;
    } catch (e) { alert('Failed to load role: ' + e.message); }
}

function closeRoleDetail() {
    document.getElementById('role-detail').classList.add('hidden');
}

async function updateRole() {
    const roleId = document.getElementById('role-detail').dataset.roleId;
    const isSystem = document.getElementById('role-detail').dataset.isSystem === 'true';
    
    if (isSystem) {
        alert('Cannot modify system roles');
        return;
    }
    
    const description = document.getElementById('role-description-input').value;
    const permissionsText = document.getElementById('role-permissions-input').value;
    
    let permissions;
    try {
        permissions = JSON.parse(permissionsText);
    } catch (e) {
        alert('Invalid JSON in permissions');
        return;
    }
    
    try {
        await api(`/api/v1/admin/roles/${roleId}`, {
            method: 'PUT',
            body: JSON.stringify({ description, permissions })
        });
        closeRoleDetail();
        loadRoles();
    } catch (e) { alert('Failed to update role: ' + e.message); }
}

async function deleteRole() {
    const roleId = document.getElementById('role-detail').dataset.roleId;
    const isSystem = document.getElementById('role-detail').dataset.isSystem === 'true';
    
    if (isSystem) {
        alert('Cannot delete system roles');
        return;
    }
    
    if (!confirm('Are you sure you want to delete this role?')) return;
    
    try {
        await api(`/api/v1/admin/roles/${roleId}`, { method: 'DELETE' });
        closeRoleDetail();
        loadRoles();
    } catch (e) { alert('Failed to delete role: ' + e.message); }
}

// Approvals Management Functions
async function loadApprovals() {
    try {
        const status = document.getElementById('approval-status-filter').value;
        const url = status ? `/api/v1/admin/approvals?status=${status}` : '/api/v1/admin/approvals';
        const data = await api(url);
        renderApprovals(data.approvals);
    } catch (e) { console.error('Failed to load approvals:', e); }
}

function renderApprovals(approvals) {
    const tbody = document.getElementById('approvals-tbody');
    if (!approvals || approvals.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:#666;">No approval requests found</td></tr>';
        return;
    }
    tbody.innerHTML = approvals.map(approval => `
        <tr>
            <td style="font-family:monospace;">${escapeHtml(approval.id)}</td>
            <td>${escapeHtml(approval.action_type)}</td>
            <td>${escapeHtml(approval.requested_by)}</td>
            <td>
                <span class="badge" style="background:${
                    approval.status === 'approved' ? 'var(--good)' :
                    approval.status === 'rejected' ? 'var(--bad)' :
                    approval.status === 'pending' ? 'var(--warn)' : '#666'
                };">${escapeHtml(approval.status)}</span>
            </td>
            <td>${approval.current_approvals}/${approval.required_approvals}</td>
            <td>${formatTimestamp(approval.created_at)}</td>
            <td>
                ${approval.status === 'pending' ? `
                    <button class="btn btn-primary btn-sm" data-click="approveRequest" data-id="${escapeHtml(approval.id)}">Approve</button>
                    <button class="btn btn-danger btn-sm" data-click="rejectRequest" data-id="${escapeHtml(approval.id)}">Reject</button>
                ` : '-'}
            </td>
        </tr>
    `).join('');
}

async function approveRequest(id) {
    try {
        await api(`/api/v1/admin/approvals/${id}/approve`, { method: 'POST' });
        loadApprovals();
    } catch (e) { alert('Failed to approve: ' + e.message); }
}

async function rejectRequest(id) {
    const reason = prompt('Enter rejection reason:');
    if (!reason) return;
    
    try {
        await api(`/api/v1/admin/approvals/${id}/reject`, {
            method: 'POST',
            body: JSON.stringify({ decision: 'reject', reason })
        });
        loadApprovals();
    } catch (e) { alert('Failed to reject: ' + e.message); }
}

function refreshApprovals() {
    loadApprovals();
}

// Utility function for timestamps
function formatTimestamp(ts) {
    if (!ts) return '-';
    return new Date(ts).toLocaleString();
}

// Security Functions
async function loadSecurity() {
    try {
        const hashStatusFilter = document.getElementById('hash-report-status').value;
        const messageStatusFilter = document.getElementById('message-report-status').value;
        const userStatusFilter = document.getElementById('user-report-status').value;
        const hashUrl = hashStatusFilter ? `/api/v1/admin/hash-reports?status_filter=${encodeURIComponent(hashStatusFilter)}` : '/api/v1/admin/hash-reports';
        const messageUrl = messageStatusFilter ? `/api/v1/admin/message-reports?status_filter=${encodeURIComponent(messageStatusFilter)}` : '/api/v1/admin/message-reports';
        const userUrl = userStatusFilter ? `/api/v1/admin/user-reports?status_filter=${encodeURIComponent(userStatusFilter)}` : '/api/v1/admin/user-reports';
        const [adminSecurity, ips, bans, hashReports, hashes, blockedUsers, accessTokens, messageCounts, messageReports, userCounts, userReports] = await Promise.all([
            api('/api/v1/admin/auth/security-status'),
            api('/api/v1/admin/security/blocked-ips'),
            api('/api/v1/admin/security/banned-usernames'),
            api(hashUrl),
            api('/api/v1/admin/blocked-hashes'),
            api('/api/v1/admin/blocked-users'),
            api('/api/v1/admin/security/access-tokens'),
            api('/api/v1/admin/message-reports/counts'),
            api(messageUrl),
            api('/api/v1/admin/user-reports/counts'),
            api(userUrl),
        ]);
        renderAdminSecurityStatus(adminSecurity);
        document.getElementById('blocked-ips-tbody').innerHTML = ips.map(i => `<tr><td>${escapeHtml(i.ip_address)}</td><td>${escapeHtml(i.reason || '')}</td><td><button class="btn btn-outline btn-sm" data-click="unblockIP" data-val="${escapeHtml(i.ip_address)}">Unblock</button></td></tr>`).join('');
        document.getElementById('banned-usernames-tbody').innerHTML = bans.map(b => `<tr><td>${escapeHtml(b.pattern)}</td><td>${escapeHtml(b.reason || '')}</td><td><button class="btn btn-outline btn-sm" data-click="removeBan" data-id="${escapeHtml(b.id)}">Remove</button></td></tr>`).join('');
        renderHashReports(hashReports);
        renderModerationReportCounts('message-report-counts', messageCounts);
        renderMessageReports(messageReports);
        renderModerationReportCounts('user-report-counts', userCounts);
        renderUserReports(userReports);
        renderBlockedHashes(hashes);
        renderBlockedUsers(blockedUsers);
        renderAccessTokens(accessTokens);
        if (selectedAccessTokenId) {
            await loadAccessTokenDetail(selectedAccessTokenId);
        }
    } catch (e) {}
}

function renderAdminSecurityStatus(status) {
    document.getElementById('admin-security-username').textContent = status?.username || '-';
    document.getElementById('admin-security-last-login').textContent = formatTimestamp(status?.last_login) || 'Never';
    document.getElementById('admin-security-otp-status').textContent = status?.otp_enabled
        ? 'Enabled'
        : (status?.must_setup_otp ? 'Setup Required' : 'Disabled');
    document.getElementById('admin-security-backup-count').textContent = String(status?.backup_codes_remaining || 0);
}

// AutoMod Functions
async function loadAutomodConfig() {
    try {
        const cfg = await api('/api/v1/admin/automod/config');
        document.getElementById('automod-enabled').checked = cfg.enabled !== false;
        const openai = cfg.ai?.openai || {};
        const perspective = cfg.ai?.perspective || {};
        const custom = cfg.ai?.custom || {};
        document.getElementById('automod-openai-key').value = openai.api_key || '';
        document.getElementById('automod-openai-model').value = openai.model || '';
        document.getElementById('automod-openai-url').value = openai.api_url || '';
        document.getElementById('automod-openai-threshold').value = openai.threshold ?? '';
        document.getElementById('automod-perspective-key').value = perspective.api_key || '';
        document.getElementById('automod-perspective-threshold').value = perspective.threshold ?? '';
        document.getElementById('automod-perspective-attributes').value = (perspective.attributes || []).join(', ');
        document.getElementById('automod-custom-endpoint').value = custom.endpoint_url || '';
        document.getElementById('automod-custom-key').value = custom.api_key || '';
        document.getElementById('automod-custom-auth-header').value = custom.auth_header || '';
        document.getElementById('automod-custom-auth-prefix').value = custom.auth_prefix || '';
        document.getElementById('automod-custom-timeout').value = custom.timeout_seconds ?? '';
        document.getElementById('automod-custom-threshold').value = custom.threshold ?? '';
        document.getElementById('automod-custom-headers').value = custom.headers ? JSON.stringify(custom.headers) : '';
    } catch (e) {}
}

async function loadAutomodRules() {
    const serverId = document.getElementById('automod-server-id').value.trim();
    if (!serverId) return;
    try {
        const rules = await api(`/api/v1/admin/automod/rules?server_id=${encodeURIComponent(serverId)}`);
        renderAutomodRules(rules);
    } catch (e) { alert('Failed to load automod rules: ' + e.message); }
}

function renderAutomodRules(rules) {
    const tbody = document.getElementById('automod-rules-tbody');
    if (!tbody) return;
    if (!rules || rules.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="color:#666;">No rules found</td></tr>';
        return;
    }
    tbody.innerHTML = rules.map(r => `
        <tr>
            <td>${escapeHtml(r.name)}</td>
            <td>${escapeHtml(r.rule_type)}</td>
            <td>${r.enabled ? '<span class="badge" style="background:var(--good);color:#000;">On</span>' : '<span class="badge" style="background:var(--bad);color:#000;">Off</span>'}</td>
            <td>${escapeHtml(String(r.priority))}</td>
            <td>${escapeHtml((r.actions || []).map(a => a.action_type).join(', '))}</td>
            <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
                <button class="btn btn-secondary btn-sm" data-click="editAutomodRule" data-id="${escapeHtml(r.id)}">Edit</button>
                <button class="btn btn-outline btn-sm" data-click="toggleAutomodRule" data-id="${escapeHtml(r.id)}" data-enabled="${r.enabled ? 'false' : 'true'}">${r.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-danger btn-sm" data-click="deleteAutomodRule" data-id="${escapeHtml(r.id)}">Delete</button>
            </td>
        </tr>
    `).join('');
}

// Logs Functions
async function loadLogs() {
    try {
        const logs = await api('/api/v1/admin/logs');
        const select = document.getElementById('log-file-select');
        select.innerHTML = logs.map(l => `<option value="${escapeHtml(l.filename)}">${escapeHtml(l.filename)} (${(l.size/1024).toFixed(1)}KB)</option>`).join('');
        if (logs.length > 0) loadLogFile(logs[0].filename);
    } catch (e) {}
}

async function loadLogFile(f) {
    try {
        const search = document.getElementById('log-search').value;
        const d = await api(`/api/v1/admin/logs/${f}?limit=300${search ? '&q='+encodeURIComponent(search) : ''}`);
        const viewer = document.getElementById('log-viewer');
        viewer.innerHTML = d.lines.map(l => {
            const cls = l.level === 'ERROR' ? 'log-error' : (l.level === 'WARNING' ? 'log-warning' : (l.level === 'DEBUG' ? 'log-debug' : 'log-info'));
            return `<div class="${cls}">${escapeHtml(l.raw || '')}</div>`;
        }).join('');
        if (document.getElementById('log-auto-refresh').checked) viewer.scrollTop = viewer.scrollHeight;
    } catch (e) { document.getElementById('log-viewer').textContent = 'Error loading logs'; }
}

// Tickets Functions
async function loadTickets() {
    try {
        const statusFilter = document.getElementById('ticket-status-filter')?.value || '';
        const url = statusFilter ? `/api/v1/admin/tickets?status_filter=${encodeURIComponent(statusFilter)}` : '/api/v1/admin/tickets';
        const tickets = await api(url);
        document.getElementById('tickets-tbody').innerHTML = tickets.map(t => `
            <tr><td>${escapeHtml(t.id)}</td><td>${escapeHtml(t.username)}</td><td>${escapeHtml(t.category)}</td><td><span class="badge" style="background:#222;">${escapeHtml(t.status)}</span></td><td>${new Date(t.created_at).toLocaleString()}</td>
            <td><button class="btn btn-secondary btn-sm" data-click="viewTicket" data-id="${escapeHtml(t.id)}">View</button></td></tr>
        `).join('');
    } catch (e) { console.error('Tickets load failed'); }
}

async function viewTicket(id) {
    try {
        const t = await api(`/api/v1/admin/tickets/${id}`);
        selectedTicketId = id;
        selectedTicketStatus = t.status;
        document.getElementById('view-ticket-title').textContent = `Ticket #${id} - ${t.username}`;
        document.getElementById('view-ticket-content').textContent = t.content;
        const sb = document.getElementById('ticket-status-badge');
        if (sb) sb.textContent = t.status;
        const sc = document.getElementById('ticket-created-at');
        if (sc) sc.textContent = t.created_at ? new Date(t.created_at).toLocaleString() : '';
        const sr = document.getElementById('ticket-resolved-at');
        if (sr) sr.textContent = t.resolved_at ? new Date(t.resolved_at).toLocaleString() : '';
        const srb = document.getElementById('ticket-resolved-by');
        if (srb) srb.textContent = t.resolved_by || '';
        const statusSelect = document.getElementById('ticket-status-select');
        if (statusSelect) statusSelect.value = t.status;
        document.getElementById('ticket-detail').classList.remove('hidden');
        loadTicketNotes(id);
    } catch (e) { alert('Failed to load ticket'); }
}

async function loadTicketNotes(id) {
    try {
        const notes = await api(`/api/v1/admin/tickets/${id}/notes`);
        document.getElementById('ticket-notes-list').innerHTML = notes.map(n => `
            <div style="border-left:2px solid var(--accent);padding:0.5rem;margin-bottom:0.5rem;background:#1a1a1c;">
                <div style="font-size:0.7rem;color:#888;margin-bottom:0.2rem;">${escapeHtml(n.admin_username)} • ${new Date(n.created_at).toLocaleString()}</div>
                <div style="font-size:0.85rem;">${escapeHtml(n.content)}</div>
            </div>
        `).join('');
    } catch (e) {}
}

// User Management Functions
async function searchUsers() {
    const q = document.getElementById('user-search-input').value;
    if (!q) return;
    try {
        const r = await api(`/api/v1/admin/users/search?q=${encodeURIComponent(q)}`);
        document.getElementById('user-results').innerHTML = `
            <table style="margin-top:1rem;"><thead><tr><th>ID</th><th>Username</th><th>Tier</th><th>Badges</th><th>Action</th></tr></thead>
            <tbody>${r.users.map(u => `
                <tr><td>${escapeHtml(u.id)}</td><td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.tier)}</td><td>${escapeHtml((u.badges || []).join(', '))}</td>
                <td><button class="btn btn-secondary btn-sm" data-click="manageUser" data-id="${escapeHtml(u.id)}">Manage</button></td></tr>
            `).join('')}</tbody></table>
        `;
    } catch (e) { alert('Search failed: ' + e.message); }
}

async function manageUser(id) {
    try {
        const [u, notes] = await Promise.all([
            api(`/api/v1/admin/users/${id}`),
            api(`/api/v1/admin/users/${id}/notes`)
        ]);
        selectedUserId = id;
        document.getElementById('user-display-name').textContent = u.username;
        document.getElementById('user-display-id').textContent = `ID: ${u.id}`;
        document.getElementById('user-avatar-large').src = `/api/v1/avatars/users/${u.id}`;
        document.getElementById('user-tier-select').value = u.tier;
        document.getElementById('suspend-btn').textContent = u.account_locked ? 'Unlock' : 'Suspend';
        
        renderBadges(u.badges);
        
        document.getElementById('user-notes-input').value = notes.notes || '';
        
        document.getElementById('user-manage').classList.remove('hidden');
        document.getElementById('user-manage').scrollIntoView({ behavior: 'smooth' });
    } catch (e) { alert('Failed to load user data'); }
}

function renderBadges(badges) {
    const list = document.getElementById('user-badges-list');
    list.innerHTML = (badges || []).map(b => `
        <span class="badge" style="background:var(--accent); color:white; display:flex; align-items:center; gap:0.3rem; padding: 0.3rem 0.6rem;">
            ${escapeHtml(b)}
            <span style="cursor:pointer; font-size:1.1rem; line-height:1;" data-click="removeUserBadge" data-val="${escapeHtml(b)}">×</span>
        </span>
    `).join('');
}

// Helper functions for reports and security
function renderHashReports(reports) {
    const grid = document.getElementById('hash-reports-grid');
    if (!reports || reports.length === 0) {
        grid.innerHTML = '<div style="color:#666;font-size:0.8rem;">No reports found.</div>';
        return;
    }
    grid.innerHTML = reports.map(r => {
        const preview = r.attachment_url ? `<img src="${escapeHtml(r.attachment_url)}" alt="Reported image" crossorigin="anonymous">` : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#555;font-size:0.75rem;">No preview</div>`;
        const reporter = r.reporter_username ? `${escapeHtml(r.reporter_username)} (${escapeHtml(r.reporter_id)})` : escapeHtml(r.reporter_id);
        const statusBadge = `<span class="badge" style="background:${r.status === 'blocked' ? 'var(--bad)' : (r.status === 'cleared' ? 'var(--good)' : 'var(--warn)')}; color:#000;">${escapeHtml(r.status)}</span>`;
        return `
            <div class="media-report-card">
                <div class="media-report-thumb">
                    ${preview}
                    <div class="media-report-overlay">
                        <span class="media-report-flag">Flagged</span>
                    </div>
                </div>
                <div class="media-report-meta">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <strong style="font-size:0.8rem;">${escapeHtml(r.reason)}</strong>
                        ${statusBadge}
                    </div>
                    <div style="color:#777;">Reporter: ${reporter}</div>
                    <div style="color:#777;">Hash: ${escapeHtml(r.hash_value ? r.hash_value.slice(0, 8) + '…' + r.hash_value.slice(-6) : '')}</div>
                    <div style="color:#777;">Reported: ${formatTimestamp(r.reported_at)}</div>
                    <div class="media-report-actions">
                        <button class="btn btn-danger btn-sm" data-click="reviewHashReport" data-id="${escapeHtml(r.id)}" data-action="block">Block</button>
                        <button class="btn btn-outline btn-sm" data-click="reviewHashReport" data-id="${escapeHtml(r.id)}" data-action="clear">Clear</button>
                        <button class="btn btn-outline btn-sm" data-click="reviewHashReport" data-id="${escapeHtml(r.id)}" data-action="dismiss">Dismiss</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function reportStatusBadge(status) {
    const colors = {
        pending: 'var(--warn)',
        reviewed: '#7fb3ff',
        actioned: 'var(--bad)',
        dismissed: '#888'
    };
    return `<span class="badge" style="background:${colors[status] || '#666'}; color:#000;">${escapeHtml(status || 'unknown')}</span>`;
}

function renderModerationReportCounts(containerId, counts) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const items = [
        ['Pending', counts?.pending || 0, 'var(--warn)'],
        ['Reviewed', counts?.reviewed || 0, '#7fb3ff'],
        ['Actioned', counts?.actioned || 0, 'var(--bad)'],
        ['Dismissed', counts?.dismissed || 0, '#888'],
        ['Total', counts?.total || 0, 'var(--line-primary)']
    ];

    container.innerHTML = items.map(([label, value, color]) => `
        <div class="card" style="padding:0.75rem 0.9rem;border-left:4px solid ${escapeHtml(color)};">
            <div class="stat-label">${escapeHtml(label)}</div>
            <div class="stat-value" style="font-size:1.2rem;">${Number(value).toLocaleString()}</div>
        </div>
    `).join('');
}

function renderMessageReports(items) {
    const tbody = document.getElementById('message-reports-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:#666;">No message reports</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(r => {
        const summary = [r.message_content, r.details].filter(Boolean).join(' • ');
        const resolution = [r.action_taken, r.admin_notes].filter(Boolean).join(' • ');
        return `
            <tr>
                <td style="max-width:260px;">${escapeHtml(summary || r.message_id)}</td>
                <td>${escapeHtml(r.reporter_id)}</td>
                <td>${escapeHtml(r.reported_user_id)}</td>
                <td><strong>${escapeHtml(r.reason)}</strong><div style="color:#777;">${escapeHtml(r.category || '')}</div></td>
                <td>${reportStatusBadge(r.status)}${resolution ? `<div style="color:#777;margin-top:0.35rem;">${escapeHtml(resolution)}</div>` : ''}</td>
                <td>${formatTimestamp(r.reported_at)}</td>
                <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
                    <button class="btn btn-danger btn-sm" data-click="reviewMessageReport" data-id="${escapeHtml(r.id)}" data-action="action">Action</button>
                    <button class="btn btn-outline btn-sm" data-click="reviewMessageReport" data-id="${escapeHtml(r.id)}" data-action="review">Review</button>
                    <button class="btn btn-outline btn-sm" data-click="reviewMessageReport" data-id="${escapeHtml(r.id)}" data-action="dismiss">Dismiss</button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderUserReports(items) {
    const tbody = document.getElementById('user-reports-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:#666;">No user reports</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(r => {
        const resolution = [r.action_taken, r.admin_notes].filter(Boolean).join(' • ');
        const evidenceCount = Array.isArray(r.evidence_message_ids) ? r.evidence_message_ids.length : 0;
        return `
            <tr>
                <td>${escapeHtml(r.reported_user_id)}</td>
                <td>${escapeHtml(r.reporter_id)}</td>
                <td><strong>${escapeHtml(r.reason)}</strong><div style="color:#777;">${escapeHtml(r.category || '')}${r.details ? ` • ${escapeHtml(r.details)}` : ''}</div></td>
                <td>${evidenceCount ? `${evidenceCount} linked message${evidenceCount !== 1 ? 's' : ''}` : 'None'}</td>
                <td>${reportStatusBadge(r.status)}${resolution ? `<div style="color:#777;margin-top:0.35rem;">${escapeHtml(resolution)}</div>` : ''}</td>
                <td>${formatTimestamp(r.reported_at)}</td>
                <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
                    <button class="btn btn-danger btn-sm" data-click="reviewUserReport" data-id="${escapeHtml(r.id)}" data-action="action">Action</button>
                    <button class="btn btn-outline btn-sm" data-click="reviewUserReport" data-id="${escapeHtml(r.id)}" data-action="review">Review</button>
                    <button class="btn btn-outline btn-sm" data-click="reviewUserReport" data-id="${escapeHtml(r.id)}" data-action="dismiss">Dismiss</button>
                </td>
            </tr>
        `;
    }).join('');
}

function renderBlockedHashes(items) {
    const tbody = document.getElementById('blocked-hashes-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="color:#666;">No blocked hashes</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(h => `
        <tr>
            <td style="font-family:monospace;">${escapeHtml(h.hash_value ? h.hash_value.slice(0, 8) + '…' + h.hash_value.slice(-6) : '')}</td>
            <td>${escapeHtml(h.hash_type || 'sha256')}</td>
            <td>${escapeHtml(h.reason || '')}</td>
            <td>${formatTimestamp(h.blocked_at)}</td>
            <td><button class="btn btn-outline btn-sm" data-click="unblockHash" data-val="${escapeHtml(h.hash_value)}">Unblock</button></td>
        </tr>
    `).join('');
}

function renderBlockedUsers(items) {
    const tbody = document.getElementById('blocked-users-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="color:#666;">No blocked users</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(u => `
        <tr>
            <td>${escapeHtml(u.user_id)}</td>
            <td>${escapeHtml(u.username || '')}</td>
            <td>${escapeHtml(u.reason || '')}</td>
            <td>${formatTimestamp(u.blocked_at)}</td>
            <td>${formatTimestamp(u.expires_at)}</td>
            <td><button class="btn btn-outline btn-sm" data-click="unblockUser" data-val="${escapeHtml(u.user_id)}">Unblock</button></td>
        </tr>
    `).join('');
}

function renderAccessTokens(items) {
    const tbody = document.getElementById('access-tokens-tbody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="color:#666;">No access tokens</td></tr>';
        return;
    }
    tbody.innerHTML = items.map(t => `
        <tr>
            <td>${escapeHtml(t.name || '')}</td>
            <td>${formatTimestamp(t.expires_at) || 'Never'}</td>
            <td>${formatTimestamp(t.last_used_at)}</td>
            <td>${Number(t.use_count_total || 0).toLocaleString()}</td>
            <td>${Number(t.distinct_ip_count || 0).toLocaleString()}</td>
            <td>${t.revoked ? 'Revoked' : (t.expires_at && t.expires_at <= Date.now() ? 'Expired' : 'Active')}</td>
            <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
                <button class="btn btn-secondary btn-sm" data-click="viewAccessToken" data-id="${escapeHtml(t.id)}">View</button>
                ${t.revoked ? `<button class="btn btn-secondary btn-sm" data-click="unrevokeAccessToken" data-id="${escapeHtml(t.id)}">Unrevoke</button>` : `<button class="btn btn-outline btn-sm" data-click="revokeAccessToken" data-id="${escapeHtml(t.id)}">Revoke</button>`}
            </td>
        </tr>
    `).join('');
}

async function loadAccessTokenDetail(tokenId) {
    selectedAccessTokenId = tokenId;
    const detail = await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(tokenId)}`);
    renderAccessTokenDetail(detail);
}

function closeAccessTokenDetail() {
    selectedAccessTokenId = null;
    document.getElementById('access-token-detail').classList.add('hidden');
}

function renderAccessTokenDetail(detail) {
    const panel = document.getElementById('access-token-detail');
    if (!detail || !detail.access_token) {
        panel.classList.add('hidden');
        return;
    }
    const token = detail.access_token;
    panel.classList.remove('hidden');
    document.getElementById('access-token-detail-title').textContent = `Token Detail: ${escapeHtml(token.name || token.id)}`;
    document.getElementById('access-token-stat-requests').textContent = Number(detail.total_events || token.use_count_total || 0).toLocaleString();
    document.getElementById('access-token-stat-ips').textContent = Number(detail.distinct_ip_count || token.distinct_ip_count || 0).toLocaleString();
    document.getElementById('access-token-stat-denied').textContent = Number(detail.denied_count_total || token.denied_count_total || 0).toLocaleString();
    document.getElementById('access-token-stat-last-used').textContent = formatTimestamp(token.last_used_at) || 'Never';
    document.getElementById('access-token-detail-name').value = token.name || '';
    document.getElementById('access-token-detail-description').value = token.description || '';
    document.getElementById('access-token-detail-expires').value = token.expires_at ? new Date(token.expires_at).toISOString().slice(0, 16) : '';
    document.getElementById('access-token-detail-scope-mode').value = token.scope_mode || 'none';
    document.getElementById('access-token-detail-created').textContent = formatTimestamp(token.created_at) || 'Unknown';
    document.getElementById('access-token-detail-first-used').textContent = formatTimestamp(token.first_used_at) || 'Never';
    document.getElementById('access-token-detail-last-ip').textContent = token.last_used_ip_address || 'Unknown';
    document.getElementById('access-token-detail-last-path').textContent = token.last_used_path || 'Unknown';
    document.getElementById('access-token-detail-last-ua').textContent = token.last_used_user_agent || 'Unknown';
    document.getElementById('access-token-rotated').textContent = '';
    const unrevokeBtn = document.getElementById('btn-unrevoke-token');
    const revokeBtn = document.getElementById('btn-revoke-token');
    if (token.revoked) {
        if (unrevokeBtn) unrevokeBtn.style.display = '';
        if (revokeBtn) revokeBtn.style.display = 'none';
    } else {
        if (unrevokeBtn) unrevokeBtn.style.display = 'none';
        if (revokeBtn) revokeBtn.style.display = '';
    }

    const scopesTbody = document.getElementById('access-token-scopes-tbody');
    scopesTbody.innerHTML = (detail.scopes || []).length === 0
        ? '<tr><td colspan="3" style="color:#666;">No scopes configured</td></tr>'
        : detail.scopes.map(scope => `
            <tr>
                <td>${escapeHtml(scope.scope_type)}</td>
                <td>${escapeHtml(scope.value)}</td>
                <td><button class="btn btn-outline btn-sm" data-click="removeAccessTokenScope" data-id="${escapeHtml(scope.id)}">Remove</button></td>
            </tr>
        `).join('');

    const ipsTbody = document.getElementById('access-token-top-ips-tbody');
    ipsTbody.innerHTML = (detail.top_ips || []).length === 0
        ? '<tr><td colspan="4" style="color:#666;">No IP activity yet</td></tr>'
        : detail.top_ips.map(item => `
            <tr>
                <td>${escapeHtml(item.ip_address || 'UNKNOWN')}</td>
                <td>${Number(item.request_count || 0).toLocaleString()}</td>
                <td>${Number(item.denied_count || 0).toLocaleString()}</td>
                <td>${formatTimestamp(item.last_seen_at)}</td>
            </tr>
        `).join('');

    const pathsTbody = document.getElementById('access-token-top-paths-tbody');
    pathsTbody.innerHTML = (detail.top_paths || []).length === 0
        ? '<tr><td colspan="4" style="color:#666;">No route usage yet</td></tr>'
        : detail.top_paths.map(item => `
            <tr>
                <td>${escapeHtml(item.path || '')}</td>
                <td>${escapeHtml(item.method || '')}</td>
                <td>${Number(item.request_count || 0).toLocaleString()}</td>
                <td>${formatTimestamp(item.last_seen_at)}</td>
            </tr>
        `).join('');

    const eventsTbody = document.getElementById('access-token-events-tbody');
    eventsTbody.innerHTML = (detail.recent_events || []).length === 0
        ? '<tr><td colspan="4" style="color:#666;">No recent events</td></tr>'
        : detail.recent_events.map(item => `
            <tr>
                <td>${formatTimestamp(item.used_at)}</td>
                <td>${escapeHtml(item.ip_address || 'UNKNOWN')}</td>
                <td>${escapeHtml(item.method || '')} ${escapeHtml(item.path || '')}</td>
                <td>${item.allowed ? 'Allowed' : `Denied${item.reject_reason ? ` (${escapeHtml(item.reject_reason)})` : ''}`}</td>
            </tr>
        `).join('');
}

// Migration Functions
let currentMigration = null;
let migrations = [];

async function refreshMigrations() {
    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch('/api/v1/admin/migrations', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();

        document.getElementById('migration-applied-count').textContent = data.applied_count;
        document.getElementById('migration-pending-count').textContent = data.pending_count;
        document.getElementById('migration-failed-count').textContent = data.failed_count;

        migrations = data.migrations;
        renderMigrationsTable(migrations);
    } catch (error) {
        console.error('Failed to load migrations:', error);
    }
}

function renderMigrationsTable(migrations) {
    const tbody = document.getElementById('migrations-tbody');
    tbody.innerHTML = '';

    if (migrations.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#666;">No migrations found</td></tr>';
        return;
    }

    for (const m of migrations) {
        const tr = document.createElement('tr');

        // Version
        const tdVersion = document.createElement('td');
        const code = document.createElement('code');
        code.textContent = m.version;
        tdVersion.appendChild(code);
        tr.appendChild(tdVersion);

        // Name
        const tdName = document.createElement('td');
        tdName.textContent = m.name;
        tr.appendChild(tdName);

        // Status
        const tdStatus = document.createElement('td');
        const statusBadge = document.createElement('span');
        statusBadge.className = 'badge';
        statusBadge.style.background = m.status === 'completed' ? 'rgba(0,255,136,0.2)' : 
                                      m.status === 'pending' ? 'rgba(255,170,0,0.2)' : 
                                      m.status === 'failed' ? 'rgba(255,68,68,0.2)' : 
                                      'rgba(0,102,255,0.2)';
        statusBadge.style.color = m.status === 'completed' ? 'var(--good)' : 
                                 m.status === 'pending' ? 'var(--warn)' : 
                                 m.status === 'failed' ? 'var(--bad)' : 
                                 'var(--accent)';
        statusBadge.textContent = m.status;
        tdStatus.appendChild(statusBadge);
        tr.appendChild(tdStatus);

        // Irreversible
        const tdIrreversible = document.createElement('td');
        if (m.is_irreversible) {
            const badge = document.createElement('span');
            badge.className = 'badge';
            badge.style.background = 'rgba(255,68,68,0.2)';
            badge.style.color = 'var(--bad)';
            badge.textContent = 'IRREVERSIBLE';
            tdIrreversible.appendChild(badge);
        } else {
            tdIrreversible.textContent = '-';
        }
        tr.appendChild(tdIrreversible);

        // Applied At
        const tdApplied = document.createElement('td');
        tdApplied.textContent = m.applied_at || '-';
        tr.appendChild(tdApplied);

        // Actions
        const tdActions = document.createElement('td');
        const actionsDiv = document.createElement('div');
        actionsDiv.style.display = 'flex';
        actionsDiv.style.gap = '0.25rem';

        if (m.status === 'pending') {
            const runBtn = document.createElement('button');
            runBtn.className = 'btn btn-primary';
            runBtn.style.fontSize = '0.75rem';
            runBtn.textContent = 'Run';
            runBtn.disabled = !m.can_run;
            runBtn.onclick = () => showRunModal(m.version);
            actionsDiv.appendChild(runBtn);

            const dryRunBtn = document.createElement('button');
            dryRunBtn.className = 'btn btn-outline';
            dryRunBtn.style.fontSize = '0.75rem';
            dryRunBtn.textContent = 'Dry Run';
            dryRunBtn.onclick = () => showRunModal(m.version, true);
            actionsDiv.appendChild(dryRunBtn);
        }

        const detailsBtn = document.createElement('button');
        detailsBtn.className = 'btn btn-outline';
        detailsBtn.style.fontSize = '0.75rem';
        detailsBtn.textContent = 'Details';
        detailsBtn.onclick = () => showDetails(m.version);
        actionsDiv.appendChild(detailsBtn);

        tdActions.appendChild(actionsDiv);
        tr.appendChild(tdActions);

        tbody.appendChild(tr);
    }
}

function showRunModal(version, dryRun = false) {
    const migration = migrations.find(m => m.version === version);
    if (!migration) return;

    currentMigration = { ...migration, dry_run: dryRun };

    const content = document.getElementById('migration-run-modal-content');
    content.innerHTML = '';

    if (migration.is_irreversible && !dryRun) {
        // Warning banner
        const warningDiv = document.createElement('div');
        warningDiv.style.background = 'rgba(255,68,68,0.1)';
        warningDiv.style.border = '1px solid var(--bad)';
        warningDiv.style.borderRadius = '4px';
        warningDiv.style.padding = '1rem';
        warningDiv.style.marginBottom = '1rem';

        const warningTitle = document.createElement('h3');
        warningTitle.style.color = 'var(--bad)';
        warningTitle.style.marginBottom = '0.5rem';
        warningTitle.textContent = 'IRREVERSIBLE MIGRATION';
        warningDiv.appendChild(warningTitle);

        const warningP = document.createElement('p');
        warningP.textContent = 'This migration cannot be rolled back after it is applied.';
        warningDiv.appendChild(warningP);

        if (!migration.can_run) {
            const blockedP = document.createElement('p');
            blockedP.style.color = 'var(--warn)';
            blockedP.style.marginTop = '0.5rem';
            blockedP.innerHTML = '<strong>Blocked:</strong> ' + escapeHtml(migration.can_run_reason);
            warningDiv.appendChild(blockedP);
        }

        content.appendChild(warningDiv);

        // Form group
        const formGroup = document.createElement('div');
        formGroup.style.marginBottom = '1rem';

        const label = document.createElement('label');
        label.style.display = 'block';
        label.style.color = '#888';
        label.style.fontSize = '0.85rem';
        label.style.marginBottom = '0.5rem';
        label.textContent = 'Type "THE DATABASE IS BACKED UP" to confirm';
        formGroup.appendChild(label);

        const input = document.createElement('input');
        input.type = 'text';
        input.id = 'migration-confirmation-text';
        input.placeholder = 'THE DATABASE IS BACKED UP';
        input.style.width = '100%';
        input.style.padding = '0.75rem';
        input.style.background = '#1a1a1c';
        input.style.border = '1px solid var(--border)';
        input.style.borderRadius = '3px';
        input.style.color = '#fff';
        formGroup.appendChild(input);

        content.appendChild(formGroup);

        document.getElementById('migration-run-confirm-btn').disabled = true;
        input.addEventListener('input', checkMigrationConfirmation);
    } else {
        const p1 = document.createElement('p');
        p1.innerHTML = '<strong>Version:</strong> ' + escapeHtml(migration.version);
        content.appendChild(p1);

        const p2 = document.createElement('p');
        p2.innerHTML = '<strong>Name:</strong> ' + escapeHtml(migration.name);
        content.appendChild(p2);

        const p3 = document.createElement('p');
        p3.innerHTML = '<strong>Dry Run:</strong> ' + (dryRun ? 'Yes' : 'No');
        content.appendChild(p3);

        if (!migration.can_run) {
            const blockedP = document.createElement('p');
            blockedP.style.color = 'var(--warn)';
            blockedP.innerHTML = '<strong>Blocked:</strong> ' + escapeHtml(migration.can_run_reason);
            content.appendChild(blockedP);
        }

        document.getElementById('migration-run-confirm-btn').disabled = !migration.can_run;
    }

    document.getElementById('migration-run-modal').classList.remove('hidden');
}

function checkMigrationConfirmation() {
    const input = document.getElementById('migration-confirmation-text').value;
    const btn = document.getElementById('migration-run-confirm-btn');
    btn.disabled = input !== 'THE DATABASE IS BACKED UP';
}

async function confirmRunMigration() {
    if (!currentMigration) return;

    const btn = document.getElementById('migration-run-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Running...';

    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch(`/api/v1/admin/migrations/${currentMigration.version}/run`, {
            method: 'POST',
            headers: { 
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify({
                dry_run: currentMigration.dry_run,
                confirmation_text: currentMigration.is_irreversible && !currentMigration.dry_run 
                    ? document.getElementById('migration-confirmation-text').value 
                    : null
            })
        });

        const data = await response.json();

        if (response.ok) {
            alert(currentMigration.dry_run ? 'Dry run completed successfully' : 'Migration completed successfully');
            closeMigrationModal();
            refreshMigrations();
        } else {
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }

    btn.disabled = false;
    btn.textContent = 'Run Migration';
}

async function showDetails(version) {
    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch(`/api/v1/admin/migrations/${version}`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();

        const content = document.getElementById('migration-details-modal-content');
        content.innerHTML = '';

        // Info section
        const infoDiv = document.createElement('div');
        infoDiv.style.marginBottom = '1rem';

        function addInfoRow(label, value) {
            const p = document.createElement('p');
            p.innerHTML = '<strong>' + label + ':</strong> ' + value;
            infoDiv.appendChild(p);
        }

        addInfoRow('Version', escapeHtml(data.version));
        addInfoRow('Name', escapeHtml(data.name));

        const statusP = document.createElement('p');
        statusP.innerHTML = '<strong>Status:</strong> ';
        const statusBadge = document.createElement('span');
        statusBadge.className = 'badge';
        statusBadge.style.background = data.status === 'completed' ? 'rgba(0,255,136,0.2)' : 
                                      data.status === 'pending' ? 'rgba(255,170,0,0.2)' : 
                                      data.status === 'failed' ? 'rgba(255,68,68,0.2)' : 
                                      'rgba(0,102,255,0.2)';
        statusBadge.style.color = data.status === 'completed' ? 'var(--good)' : 
                                 data.status === 'pending' ? 'var(--warn)' : 
                                 data.status === 'failed' ? 'var(--bad)' : 
                                 'var(--accent)';
        statusBadge.textContent = data.status;
        statusP.appendChild(statusBadge);
        infoDiv.appendChild(statusP);

        addInfoRow('Irreversible', data.is_irreversible ? 'Yes' : 'No');
        addInfoRow('Can Run', data.can_run ? 'Yes' : 'No');

        if (data.can_run_reason) {
            addInfoRow('Reason', escapeHtml(data.can_run_reason));
        }

        addInfoRow('Applied At', data.applied_at ? escapeHtml(data.applied_at) : 'N/A');
        addInfoRow('Execution Time', data.execution_time_ms ? escapeHtml(data.execution_time_ms) + 'ms' : 'N/A');

        if (data.depends_on && data.depends_on.length > 0) {
            addInfoRow('Depends On', escapeHtml(data.depends_on.join(', ')));
        }

        content.appendChild(infoDiv);

        // Logs section
        const logsHeader = document.createElement('h4');
        logsHeader.textContent = 'Logs';
        content.appendChild(logsHeader);

        const logsContainer = document.createElement('div');
        logsContainer.style.background = '#0a0a0a';
        logsContainer.style.border = '1px solid var(--border)';
        logsContainer.style.borderRadius = '3px';
        logsContainer.style.padding = '1rem';
        logsContainer.style.maxHeight = '300px';
        logsContainer.style.overflowY = 'auto';
        logsContainer.style.fontFamily = "'Courier New', monospace";
        logsContainer.style.fontSize = '0.8rem';

        if (data.logs.length === 0) {
            const noLogs = document.createElement('p');
            noLogs.style.color = '#888';
            noLogs.textContent = 'No logs available';
            logsContainer.appendChild(noLogs);
        } else {
            for (const log of data.logs) {
                const logEntry = document.createElement('div');
                logEntry.style.padding = '0.25rem 0';
                logEntry.style.borderBottom = '1px solid #1a1a1c';
                logEntry.style.color = log.level === 'ERROR' ? 'var(--bad)' : 
                                      log.level === 'WARNING' ? 'var(--warn)' : 
                                      '#888';
                logEntry.textContent = '[' + log.timestamp + '] ' + log.level + ': ' + log.message;
                logsContainer.appendChild(logEntry);
            }
        }

        content.appendChild(logsContainer);

        document.getElementById('migration-details-modal').classList.remove('hidden');
    } catch (error) {
        alert('Failed to load migration details: ' + error.message);
    }
}

function showEmergencyModal() {
    document.getElementById('migration-emergency-modal').classList.remove('hidden');
}

async function generateEmergencyToken() {
    const reason = document.getElementById('migration-emergency-reason').value;
    const expires = document.getElementById('migration-emergency-expires').value;

    if (!reason) {
        alert('Please provide a reason');
        return;
    }

    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch('/api/v1/admin/migrations/emergency-override', {
            method: 'POST',
            headers: { 
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify({ reason, expires_minutes: parseInt(expires) })
        });

        const data = await response.json();

        if (response.ok) {
            alert('Emergency token generated:\n\n' + data.token + '\n\n' + data.message + '\n\nExpires: ' + data.expires_at);
            closeMigrationModal();
        } else {
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

function closeMigrationModal() {
    document.getElementById('migration-run-modal').classList.add('hidden');
    document.getElementById('migration-details-modal').classList.add('hidden');
    document.getElementById('migration-emergency-modal').classList.add('hidden');
    currentMigration = null;
}

// Admin User Management Functions
let selectedAdminUserId = null;
let adminUsersCache = [];

async function loadAdminUsers() {
    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch('/api/v1/admin/admin-users', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        const data = await response.json();
        adminUsersCache = data.users || [];
        renderAdminUsersTable(adminUsersCache);
    } catch (error) {
        console.error('Failed to load admin users:', error);
    }
}

function renderAdminUsersTable(users) {
    const tbody = document.getElementById('admin-users-tbody');
    tbody.innerHTML = '';

    if (users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#666;">No admin users found</td></tr>';
        return;
    }

    for (const user of users) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${escapeHtml(user.username)}</td>
            <td>${escapeHtml(user.email || '')}</td>
            <td>${escapeHtml(user.role || 'admin')}</td>
            <td>${formatTimestamp(user.created_at)}</td>
            <td>${formatTimestamp(user.last_login_at) || 'Never'}</td>
            <td><span class="badge" style="background:${user.is_active ? 'rgba(0,255,136,0.2)' : 'rgba(255,68,68,0.2)'};color:${user.is_active ? 'var(--good)' : 'var(--bad)'};">${user.is_active ? 'Active' : 'Inactive'}</span></td>
            <td style="display:flex;gap:0.35rem;flex-wrap:wrap;">
                <button class="btn btn-secondary btn-sm" data-click="editAdminUser" data-id="${escapeHtml(user.id)}">Edit</button>
                <button class="btn btn-outline btn-sm" data-click="toggleAdminUserStatus" data-id="${escapeHtml(user.id)}">${user.is_active ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-danger btn-sm" data-click="deleteAdminUser" data-id="${escapeHtml(user.id)}">Delete</button>
            </td>
        `;
        tbody.appendChild(tr);
    }
}

function showCreateAdminModal() {
    selectedAdminUserId = null;
    document.getElementById('admin-user-modal-title').textContent = 'Create Admin User';
    document.getElementById('admin-user-username').value = '';
    document.getElementById('admin-user-email').value = '';
    document.getElementById('admin-user-password').value = '';
    document.getElementById('admin-user-role').value = 'admin';
    document.getElementById('admin-user-modal').classList.remove('hidden');
}

async function editAdminUser(userId) {
    selectedAdminUserId = userId;
    try {
        // Use cached data first
        const user = adminUsersCache.find(u => u.id === userId);
        
        if (user) {
            document.getElementById('admin-user-modal-title').textContent = 'Edit Admin User';
            document.getElementById('admin-user-username').value = user.username || '';
            document.getElementById('admin-user-email').value = user.email || '';
            document.getElementById('admin-user-password').value = '';
            document.getElementById('admin-user-role').value = user.role || 'admin';
            document.getElementById('admin-user-modal').classList.remove('hidden');
        } else {
            alert('User not found in cache');
        }
    } catch (error) {
        console.error('Failed to load admin user:', error);
        alert('Error loading user data');
    }
}

async function saveAdminUser() {
    const username = document.getElementById('admin-user-username').value;
    const email = document.getElementById('admin-user-email').value;
    const password = document.getElementById('admin-user-password').value;
    const role = document.getElementById('admin-user-role').value;

    if (!username || !email) {
        alert('Username and email are required');
        return;
    }

    if (!selectedAdminUserId && !password) {
        alert('Password is required for new users');
        return;
    }

    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const url = selectedAdminUserId 
            ? `/api/v1/admin/admin-users/${selectedAdminUserId}`
            : '/api/v1/admin/admin-users';
        
        const method = selectedAdminUserId ? 'PUT' : 'POST';
        
        const response = await fetch(url, {
            method: method,
            headers: { 
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify({
                username,
                email,
                password: password || undefined,
                role
            })
        });

        if (response.ok) {
            alert(selectedAdminUserId ? 'Admin user updated successfully' : 'Admin user created successfully');
            closeAdminUserModal();
            loadAdminUsers();
        } else {
            const data = await response.json();
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

async function toggleAdminUserStatus(userId) {
    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch(`/api/v1/admin/admin-users/${userId}/toggle-status`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            loadAdminUsers();
        } else {
            const data = await response.json();
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

async function deleteAdminUser(userId) {
    if (!confirm('Are you sure you want to delete this admin user?')) {
        return;
    }

    try {
        const token = sessionStorage.getItem('plexichat-admin-token');
        const response = await fetch(`/api/v1/admin/admin-users/${userId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${token}` }
        });

        if (response.ok) {
            alert('Admin user deleted successfully');
            loadAdminUsers();
        } else {
            const data = await response.json();
            alert('Failed: ' + (data.detail || 'Unknown error'));
        }
    } catch (error) {
        alert('Error: ' + error.message);
    }
}

function closeAdminUserModal() {
    document.getElementById('admin-user-modal').classList.add('hidden');
    selectedAdminUserId = null;
}

// Event delegation for data-click handlers
document.addEventListener('click', (e) => {
    const target = e.target.closest('[data-click]');
    if (!target) return;
    
    const action = target.dataset.click;
    const data = target.dataset;
    
    switch (action) {
        case 'logout':
            sessionStorage.removeItem('plexichat-admin-token');
            window.location.replace('/api/v1/admin/login');
            break;
        case 'showCreateAdminModal':
            showCreateAdminModal();
            break;
        case 'closeAdminUserModal':
            closeAdminUserModal();
            break;
        case 'saveAdminUser':
            saveAdminUser();
            break;
        case 'editAdminUser':
            editAdminUser(data.id);
            break;
        case 'toggleAdminUserStatus':
            toggleAdminUserStatus(data.id);
            break;
        case 'deleteAdminUser':
            deleteAdminUser(data.id);
            break;
        case 'refreshMigrations':
            refreshMigrations();
            break;
        case 'showEmergencyModal':
            showEmergencyModal();
            break;
        case 'closeMigrationModal':
            closeMigrationModal();
            break;
        case 'confirmRunMigration':
            confirmRunMigration();
            break;
        case 'generateEmergencyToken':
            generateEmergencyToken();
            break;
        case 'editRole':
            editRole(data.id);
            break;
        case 'approveRequest':
            approveRequest(data.id);
            break;
        case 'rejectRequest':
            rejectRequest(data.id);
            break;
        case 'unblockIP':
            unblockIP(data.val);
            break;
        case 'removeBan':
            removeBan(data.id);
            break;
        case 'editAutomodRule':
            editAutomodRule(data.id);
            break;
        case 'toggleAutomodRule':
            toggleAutomodRule(data.id, data.enabled === 'true');
            break;
        case 'deleteAutomodRule':
            deleteAutomodRule(data.id);
            break;
        case 'viewTicket':
            viewTicket(data.id);
            break;
        case 'manageUser':
            manageUser(data.id);
            break;
        case 'removeUserBadge':
            removeUserBadge(data.val);
            break;
        case 'reviewHashReport':
            reviewHashReport(data.id, data.action);
            break;
        case 'reviewMessageReport':
            reviewMessageReport(data.id, data.action);
            break;
        case 'reviewUserReport':
            reviewUserReport(data.id, data.action);
            break;
        case 'unblockHash':
            unblockHash(data.val);
            break;
        case 'unblockUser':
            unblockUser(data.val);
            break;
        case 'viewAccessToken':
            viewAccessToken(data.id);
            break;
        case 'unrevokeAccessToken':
            unrevokeAccessToken(data.id);
            break;
        case 'revokeAccessToken':
            revokeAccessToken(data.id);
            break;
        case 'removeAccessTokenScope':
            removeAccessTokenScope(data.id);
            break;
        case 'closeTicketDetail':
            closeTicketDetail();
            break;
        case 'updateTicketStatus':
            updateTicketStatus();
            break;
        case 'addTicketNote':
            addTicketNote();
            break;
        case 'searchUsers':
            searchUsers();
            break;
        case 'refreshTierCatalog':
            refreshTierCatalog();
            break;
        case 'updateUserTier':
            updateUserTier();
            break;
        case 'addUserBadge':
            addUserBadge();
            break;
        case 'killUserSessions':
            killUserSessions();
            break;
        case 'suspendUser':
            suspendUser();
            break;
        case 'forceUserRename':
            forceUserRename();
            break;
        case 'saveUserNotes':
            saveUserNotes();
            break;
        case 'refreshDeletions':
            refreshDeletions();
            break;
        case 'changeAdminPassword':
            changeAdminPassword();
            break;
        case 'beginAdminOtpSetup':
            beginAdminOtpSetup();
            break;
        case 'regenerateAdminBackupCodes':
            regenerateAdminBackupCodes();
            break;
        case 'disableAdminOtp':
            disableAdminOtp();
            break;
        case 'verifyAdminOtpSetup':
            verifyAdminOtpSetup();
            break;
        case 'blockIP':
            blockIP();
            break;
        case 'addBannedUsername':
            addBannedUsername();
            break;
        case 'createAccessToken':
            createAccessToken();
            break;
        case 'openExport':
            openExport();
            break;
        case 'closeExport':
            closeExport();
            break;
        case 'triggerCopy':
            triggerCopy();
            break;
        case 'triggerDownload':
            triggerDownload();
            break;
        case 'resetTelemetry':
            resetTelemetry();
            break;
        case 'refreshTelemetryHistory':
            refreshTelemetryHistory();
            break;
        case 'saveAutomodConfig':
            saveAutomodConfig();
            break;
        case 'loadAutomodRules':
            loadAutomodRules();
            break;
        case 'saveAutomodRule':
            saveAutomodRule();
            break;
        case 'resetAutomodRule':
            resetAutomodRule();
            break;
        case 'automodPresetDelete':
            automodPresetDelete();
            break;
        case 'automodPresetAlert':
            automodPresetAlert();
            break;
        case 'automodPresetTimeout':
            automodPresetTimeout();
            break;
        case 'loadLogs':
            loadLogs();
            break;
        case 'scrollLogTop':
            scrollLogTop();
            break;
        case 'scrollLogBottom':
            scrollLogBottom();
            break;
        case 'createRole':
            createRole();
            break;
        case 'closeRoleDetail':
            closeRoleDetail();
            break;
        case 'updateRole':
            updateRole();
            break;
        case 'deleteRole':
            deleteRole();
            break;
        case 'refreshApprovals':
            refreshApprovals();
            break;
        case 'refreshAccessTokenDetail':
            refreshAccessTokenDetail();
            break;
        case 'closeAccessTokenDetail':
            closeAccessTokenDetail();
            break;
        case 'saveAccessTokenSettings':
            saveAccessTokenSettings();
            break;
        case 'clearAccessTokenExpiry':
            clearAccessTokenExpiry();
            break;
        case 'rotateAccessToken':
            rotateAccessToken();
            break;
        case 'unrevokeSelectedAccessToken':
            unrevokeSelectedAccessToken();
            break;
        case 'revokeSelectedAccessToken':
            revokeSelectedAccessToken();
            break;
        case 'addAccessTokenScope':
            addAccessTokenScope();
            break;
        case 'globalSessionPurge':
            globalSessionPurge();
            break;
        case 'blockHash':
            blockHash();
            break;
        case 'blockUser':
            blockUser();
            break;
    }
});