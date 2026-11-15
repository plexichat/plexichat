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
    try { await api(`/api/v1/admin/hash-reports/${btn.dataset.id}/review`, {method:'POST', body:JSON.stringify({action:btn.dataset.action})}); loadModeration(); } catch(e) { showToast('Failed', 'error'); }
}

function refreshHashReports() { loadModeration(); }

async function blockHash() {
    const hash = document.getElementById('block-hash-val')?.value;
    const reason = document.getElementById('block-hash-reason')?.value;
    if (!hash) return;
    try { await api('/api/v1/admin/blocked-hashes', {method:'POST', body:JSON.stringify({hash_value:hash, reason})}); document.getElementById('block-hash-val').value = ''; document.getElementById('block-hash-reason').value = ''; loadModeration(); } catch(e) { showToast('Failed', 'error'); }
}

async function unblockHash(btn) {
    try { await api(`/api/v1/admin/blocked-hashes/${encodeURIComponent(btn.dataset.val)}`, {method:'DELETE'}); loadModeration(); } catch(e) { showToast('Failed', 'error'); }
}

async function blockUser() {
    const id = document.getElementById('block-user-id')?.value;
    const reason = document.getElementById('block-user-reason')?.value;
    const duration = document.getElementById('block-user-duration')?.value;
    if (!id) return;
    try { await api('/api/v1/admin/blocked-users', {method:'POST', body:JSON.stringify({user_id:id, reason, duration_hours:duration ? parseInt(duration) : null})}); document.getElementById('block-user-id').value = ''; document.getElementById('block-user-reason').value = ''; document.getElementById('block-user-duration').value = ''; loadModeration(); } catch(e) { showToast('Failed', 'error'); }
}

async function unblockUser(btn) {
    try { await api(`/api/v1/admin/blocked-users/${btn.dataset.val}`, {method:'DELETE'}); loadModeration(); } catch(e) { showToast('Failed', 'error'); }
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
    try { await api(`/api/v1/admin/message-reports/${btn.dataset.id}/review`, {method:'POST', body:JSON.stringify({action:btn.dataset.action})}); loadMessageReports(); } catch(e) { showToast('Failed', 'error'); }
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
    try { await api(`/api/v1/admin/user-reports/${btn.dataset.id}/review`, {method:'POST', body:JSON.stringify({action:btn.dataset.action})}); loadUserReports(); } catch(e) { showToast('Failed', 'error'); }
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
        const o = cfg.ai?.openai||{}, p = cfg.ai?.perspective||{}, c = cfg.ai?.custom||{}, s = cfg.ai?.shieldstral||{};
        document.getElementById('automod-openai-key').value = o.api_key||'';
        document.getElementById('automod-openai-model').value = o.model||'';
        document.getElementById('automod-openai-url').value = o.api_url||'';
        document.getElementById('automod-openai-threshold').value = o.threshold??'';
        document.getElementById('automod-perspective-key').value = p.api_key||'';
        document.getElementById('automod-perspective-threshold').value = p.threshold??'';
        document.getElementById('automod-perspective-attributes').value = (p.attributes||[]).join(', ');
        document.getElementById('automod-shieldstral-endpoint').value = s.endpoint_url||'';
        document.getElementById('automod-shieldstral-threshold').value = s.threshold??'';
        document.getElementById('automod-shieldstral-policy').value = s.policy_query||'';
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
            shieldstral: {
                endpoint_url: document.getElementById('automod-shieldstral-endpoint').value || null,
                threshold: parseFloat(document.getElementById('automod-shieldstral-threshold').value) || null,
                policy_query: document.getElementById('automod-shieldstral-policy').value || null
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
    try { await api('/api/v1/admin/automod/config', {method:'PUT', body:JSON.stringify(cfg)}); showToast('Config saved', 'success'); } catch(e) { showToast('Failed: '+e.message, 'error'); }
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
    } catch(e) { showToast('Failed', 'error'); }
}

async function saveAutomodRule() { showToast('Rule save requires full implementation - use the API directly', 'info'); }
async function editAutomodRule(btn) { showToast('Edit rule #'+btn.dataset.id, 'info'); }
async function toggleAutomodRule(btn) { showToast('Toggle rule #'+btn.dataset.id, 'info'); }
async function deleteAutomodRule(btn) { if (!confirm('Delete this rule?')) return; try { await api(`/api/v1/admin/automod/rules/${btn.dataset.id}`, {method:'DELETE'}); loadAutomodRules(); } catch(e) { showToast('Failed', 'error'); } }
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
    try { await api('/api/v1/admin/roles', {method:'POST', body:JSON.stringify({name, description, permissions:{}})}); loadRoles(); } catch(e) { showToast('Failed: '+e.message, 'error'); }
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
    } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

function closeRoleDetail() { document.getElementById('role-detail').classList.add('hidden'); }

async function updateRole() {
    const id = document.getElementById('role-detail').dataset.roleId;
    if (document.getElementById('role-detail').dataset.isSystem === 'true') { showToast('Cannot modify system roles', 'error'); return; }
    let perms;
    try { perms = JSON.parse(document.getElementById('role-permissions-input').value); } catch(e) { showToast('Invalid JSON', 'error'); return; }
    try { await api(`/api/v1/admin/roles/${id}`, {method:'PUT', body:JSON.stringify({description:document.getElementById('role-description-input').value, permissions:perms})}); closeRoleDetail(); loadRoles(); } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function deleteRole() {
    const id = document.getElementById('role-detail').dataset.roleId;
    if (document.getElementById('role-detail').dataset.isSystem === 'true') { showToast('Cannot delete system roles', 'error'); return; }
    if (!confirm('Delete this role?')) return;
    try { await api(`/api/v1/admin/roles/${id}`, {method:'DELETE'}); closeRoleDetail(); loadRoles(); } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

// === APPROVALS ===
async function loadApprovals() {
    try {
        // Bypass the status filter when we have a specific scroll target
        // pending — the operator explicitly asked to see this approval.
        const pendingTarget = sessionStorage.getItem('plexichat-approval-scroll');
        const status = pendingTarget ? '' : (document.getElementById('approval-status-filter')?.value || '');
        const url = status ? `/api/v1/admin/approvals?status=${status}` : '/api/v1/admin/approvals';
        const d = await api(url);
        document.getElementById('approvals-tbody').innerHTML = (d.approvals||[]).map(a => `
            <tr id="approval-${esc(a.id)}"><td class="font-mono">${esc(a.id)}</td><td>${esc(a.action_type)}</td><td>${esc(a.requested_by)}</td>
            <td><span class="badge ${a.status==='approved'?'badge-success':a.status==='rejected'?'badge-danger':'badge-warning'}">${esc(a.status)}</span></td>
            <td>${a.current_approvals}/${a.required_approvals}</td><td class="text-muted">${ts(a.created_at)}</td>
            <td class="text-right">${a.status==='pending' ? `<button class="btn btn-primary btn-sm" data-click="approveRequest" data-id="${esc(a.id)}">Approve</button> <button class="btn btn-danger btn-sm" data-click="rejectRequest" data-id="${esc(a.id)}">Reject</button>` : '-'}</td></tr>
        `).join('');

        // Scroll to a specific approval row if navigated via #approval-{id}.
        const targetId = sessionStorage.getItem('plexichat-approval-scroll');
        if (targetId) {
            sessionStorage.removeItem('plexichat-approval-scroll');
            const el = document.getElementById(targetId);
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // Highlight the row with a 2-second yellow fade so the
                // operator can visually identify which row they landed on.
                el.style.transition = 'background-color 2s ease-out';
                el.style.backgroundColor = 'var(--warning, #fef3c7)';
                requestAnimationFrame(() => {
                    el.style.backgroundColor = 'transparent';
                    setTimeout(() => {
                        el.style.transition = '';
                        el.style.backgroundColor = '';
                    }, 2100);
                });
            }
        }
    } catch(e) { console.error('Approvals load failed'); }
}

function refreshApprovals() { loadApprovals(); }

async function approveRequest(btn) {
    try { await api(`/api/v1/admin/approvals/${btn.dataset.id}/approve`, {method:'POST'}); loadApprovals(); } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function rejectRequest(btn) {
    const reason = prompt('Rejection reason:'); if (!reason) return;
    try { await api(`/api/v1/admin/approvals/${btn.dataset.id}/reject`, {method:'POST', body:JSON.stringify({decision:'reject', reason})}); loadApprovals(); } catch(e) { showToast('Failed: '+e.message, 'error'); }
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
        showToast(currentMigration.dry_run ? 'Dry run completed' : 'Migration completed', 'error');
        closeMigrationModal(); refreshMigrations();
    } catch(e) { showToast('Failed: '+(e.message||'Unknown'), 'error'); }
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
    } catch(e) { showToast('Failed to load details', 'error'); }
}

function showEmergencyModal() { document.getElementById('migration-emergency-modal').classList.add('active'); }

async function generateEmergencyToken() {
    const reason = document.getElementById('migration-emergency-reason').value;
    const expires = document.getElementById('migration-emergency-expires').value;
    if (!reason) { showToast('Enter a reason', 'error'); return; }
    try {
        const d = await api('/api/v1/admin/migrations/emergency-override', {method:'POST', body:JSON.stringify({reason, expires_minutes:parseInt(expires)})});
        showToast(`Emergency token:\n\n${d.token}\n\nExpires: ${d.expires_at}`, 'info');
        closeMigrationModal();
    } catch(e) { showToast('Failed', 'error'); }
}

function closeMigrationModal() {
    document.getElementById('migration-run-modal')?.classList.remove('active');
    document.getElementById('migration-details-modal')?.classList.remove('active');
    document.getElementById('migration-emergency-modal')?.classList.remove('active');
    currentMigration = null;
}

