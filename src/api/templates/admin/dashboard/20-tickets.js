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

// === DATA EXPORTS (DSAR) ===
const DSAR_STATUS_TONES = {
    pending: 'warning', approved: 'info', generating: 'info', ready: 'success',
    failed: 'danger', denied: 'danger', expired: 'muted', cancelled: 'muted'
};

const dsarTs = t => {
    if (!t) return '-';
    const n = Number(t);
    return new Date(n < 1e12 ? n * 1000 : n).toLocaleString();
};

const formatBytes = n => {
    const value = Number(n);
    if (!Number.isFinite(value) || value <= 0) return '-';
    if (value < 1024) return `${value} B`;
    if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1073741824) return `${(value / 1048576).toFixed(1)} MB`;
    return `${(value / 1073741824).toFixed(1)} GB`;
};

async function loadDataExports() {
    const tbody = document.getElementById('data-exports-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="8" class="text-muted">Loading...</td></tr>';
    try {
        const status = document.getElementById('data-export-status-filter')?.value || '';
        const query = status ? `?status=${encodeURIComponent(status)}&limit=100` : '?limit=100';
        const data = await api(`/api/v1/admin/data-export${query}`);
        const items = data.items || [];
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="8" class="text-muted">No data export requests.</td></tr>';
            return;
        }
        tbody.innerHTML = items.map(x => {
            const tone = DSAR_STATUS_TONES[x.status] || 'muted';
            const actions = [];
            if (x.status === 'pending') {
                actions.push(`<button class="btn btn-primary btn-sm" data-click="approveDataExport" data-id="${esc(x.id)}">Approve</button>`);
                actions.push(`<button class="btn btn-danger btn-sm" data-click="denyDataExport" data-id="${esc(x.id)}">Deny</button>`);
            }
            if (x.status === 'pending' || x.status === 'approved') {
                actions.push(`<button class="btn btn-secondary btn-sm" data-click="generateDataExport" data-id="${esc(x.id)}">Generate</button>`);
            }
            const reason = x.denial_reason || x.error_message || '';
            return `<tr>
                <td class="font-mono">${esc(x.id)}</td>
                <td class="font-mono">${esc(x.user_id)}</td>
                <td><span class="badge badge-${tone}" title="${esc(reason)}">${esc(x.status)}</span></td>
                <td class="text-muted">${dsarTs(x.requested_at)}</td>
                <td>${esc((x.format || 'json').toUpperCase())}</td>
                <td>${formatBytes(x.file_size_bytes)}</td>
                <td class="text-muted">${dsarTs(x.expires_at)}</td>
                <td class="text-right"><div style="display:flex;gap:6px;justify-content:flex-end;flex-wrap:wrap;">${actions.join('') || '<span class="text-muted text-xs">-</span>'}</div></td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-muted">Could not load data exports.</td></tr>';
        showToast('Could not load data exports', 'error');
    }
}

function refreshDataExports() { loadDataExports(); }

async function approveDataExport(btn) {
    const id = btn?.dataset?.id;
    if (!id || !confirm('Approve this data export request?')) return;
    try {
        await api(`/api/v1/admin/data-export/${id}/approve`, { method: 'POST' });
        showToast('Data export approved', 'success');
        loadDataExports();
    } catch (e) { showToast(e.message || 'Could not approve request', 'error'); }
}

function denyDataExport(btn) {
    const id = btn?.dataset?.id;
    if (!id) return;
    selectedDataExportId = id;
    const summary = document.getElementById('data-export-deny-summary');
    if (summary) summary.textContent = `Request ${id} will be denied. The user will not receive an export.`;
    const reason = document.getElementById('data-export-deny-reason');
    if (reason) reason.value = '';
    document.getElementById('data-export-deny-modal')?.classList.add('active');
}

function closeDenyDataExport() {
    document.getElementById('data-export-deny-modal')?.classList.remove('active');
    selectedDataExportId = null;
}

async function confirmDenyDataExport() {
    const reason = document.getElementById('data-export-deny-reason')?.value?.trim() || '';
    if (!selectedDataExportId) return;
    if (!reason) { showToast('A denial reason is required', 'error'); return; }
    try {
        await api(`/api/v1/admin/data-export/${selectedDataExportId}/deny`, {
            method: 'POST', body: JSON.stringify({ reason })
        });
        showToast('Data export denied', 'success');
        closeDenyDataExport();
        loadDataExports();
    } catch (e) { showToast(e.message || 'Could not deny request', 'error'); }
}

async function generateDataExport(btn) {
    const id = btn?.dataset?.id;
    if (!id || !confirm('Generate this data export now? This may take a while.')) return;
    try {
        await api(`/api/v1/admin/data-export/${id}/generate`, { method: 'POST' });
        showToast('Data export generated', 'success');
        loadDataExports();
    } catch (e) { showToast(e.message || 'Could not generate export', 'error'); }
}

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
    } catch(e) { showToast('Failed to load ticket', 'error'); }
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
    try { await api(`/api/v1/admin/tickets/${selectedTicketId}/status`, {method:'PATCH', body:JSON.stringify({status})}); viewTicket(selectedTicketId); } catch(e) { showToast('Update failed', 'error'); }
}

async function addTicketNote() {
    const content = document.getElementById('new-note-input').value;
    if (!content || !selectedTicketId) return;
    try { await api(`/api/v1/admin/tickets/${selectedTicketId}/notes`, {method:'POST', body:JSON.stringify({content})}); document.getElementById('new-note-input').value = ''; loadTicketNotes(selectedTicketId); } catch(e) { showToast('Failed to add note', 'error'); }
}

// === USERS ===
async function loadUsers() {
    try {
        // Bypass search filter when a deep-linked user target is pending —
        // same pattern as approval status-filter bypass.
        const userTarget = sessionStorage.getItem('plexichat-user-scroll');
        const searchInput = document.getElementById('user-search-input');
        const q = userTarget ? '' : (searchInput?.value || '');
        if (userTarget && searchInput) searchInput.value = '';
        const url = q ? `/api/v1/admin/users/search?q=${encodeURIComponent(q)}` : '/api/v1/admin/users/search?q=';
        const d = await api(url);
        const users = d.users || [];
        document.getElementById('users-tbody').innerHTML = users.map(u => `
            <tr id="user-${esc(u.id)}">
                <td><div style="display:flex;align-items:center;gap:8px;"><div style="display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:var(--secondary);font-size:12px;font-weight:600;">${esc((u.username||'?')[0].toUpperCase())}</div><div><span>${esc(u.username)}</span><div class="text-xs text-muted font-mono">${esc(u.id)}</div></div></div></td>
                <td class="text-muted">${esc(u.email||'')}</td>
                <td><span class="badge badge-primary">${esc(u.tier||'free')}</span></td>
                <td>${u.account_locked ? '<span class="badge badge-danger">Locked</span>' : '<span class="badge badge-success">Active</span>'}</td>
                <td class="text-muted">${ts(u.last_login)}</td>
                <td class="text-right"><button class="btn btn-secondary btn-sm" data-click="manageUser" data-id="${esc(u.id)}">Manage</button></td>
            </tr>
        `).join('');
        document.getElementById('users-count').textContent = `Showing ${users.length} users`;

        // Scroll to a specific user row if navigated via #user-{id}.
        if (userTarget) {
            sessionStorage.removeItem('plexichat-user-scroll');
            const el = document.getElementById(userTarget);
            if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
                        <img id="admin-user-avatar" src="/api/v1/avatars/users/${esc(u.id)}" style="width:80px;height:80px;border-radius:6px;background:var(--secondary);object-fit:cover;" alt="Avatar">
                        <div style="flex:1;">
                            <p class="font-mono text-sm text-muted">ID: ${esc(u.id)}</p>
                            <div class="grid-2" style="display:grid;gap:12px;margin-top:16px;">
                                <div><label>Tier</label><div style="display:flex;gap:8px;"><select id="user-tier-select" style="flex:1;"><option value="free">Free</option><option value="alpha">Alpha</option><option value="beta">Beta</option><option value="premium">Premium</option><option value="staff">Staff</option></select><button class="btn btn-primary btn-sm" data-click="updateUserTier">Set</button></div></div>
                                <div><label>Storage quota</label><div id="user-storage-panel" class="text-sm text-muted">Loading...</div></div>
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
        const avatar = document.getElementById('admin-user-avatar');
        if (avatar) {
            avatar.addEventListener('error', () => {
                const initial = String(u.username || '?').replace(/[^a-z0-9]/gi, '').charAt(0).toUpperCase() || '?';
                const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect fill="#1c1c24" width="100" height="100"/><text x="50" y="65" text-anchor="middle" font-size="40" fill="#999">${initial}</text></svg>`;
                avatar.src = `data:image/svg+xml,${encodeURIComponent(svg)}`;
            }, { once: true });
        }
        loadTierCatalog();
        loadUserStorage();
    } catch(e) { showToast('Failed to load user', 'error'); }
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
    try {
        await api(`/api/v1/admin/users/${selectedUserId}/tier`, {method:'PUT', body:JSON.stringify({tier})});
        showToast('Tier updated', 'success');
        loadUserStorage();
    } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

function formatQuotaBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return 'Unlimited';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

async function loadUserStorage() {
    if (!selectedUserId) return;
    const panel = document.getElementById('user-storage-panel');
    if (!panel) return;
    try {
        const status = await api(`/api/v1/admin/users/${selectedUserId}/storage`);
        const limit = status.unlimited ? 'Unlimited' : formatQuotaBytes(status.limit_bytes);
        const used = formatQuotaBytes(status.used_bytes);
        const tierDefault = status.tier_limit_bytes == null ? 'Unlimited' : formatQuotaBytes(status.tier_limit_bytes);
        const globalDefault = status.default_limit_bytes == null ? 'Unlimited' : formatQuotaBytes(status.default_limit_bytes);
        const source = status.source === 'override' ? 'Admin override' : (status.source === 'tier' ? `Tier default (${esc(status.tier || 'standard')})` : 'Global default');
        panel.innerHTML = `
            <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;"><span>Used <strong>${esc(used)}</strong> of <strong>${esc(limit)}</strong></span><span class="text-xs text-muted">${esc(source)}</span></div>
            <div class="text-xs text-muted" style="margin-top:2px;">Tier ${esc(status.tier || 'standard')}: ${esc(tierDefault)} · Global default: ${esc(globalDefault)}</div>
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:8px;">
                <input id="user-storage-mb" type="number" min="0" step="1" style="width:110px;" placeholder="Limit (MB)" value="${status.unlimited ? '' : Math.round(Number(status.limit_bytes) / 1048576)}" ${status.unlimited ? 'disabled' : ''}>
                <span class="text-xs text-muted">MB</span>
                <label style="display:flex;align-items:center;gap:4px;font-size:12px;font-weight:normal;"><input id="user-storage-unlimited" type="checkbox" ${status.unlimited ? 'checked' : ''}> Unlimited</label>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">
                <button class="btn btn-primary btn-sm" data-click="saveUserStorage">Save override</button>
                <button class="btn btn-outline btn-sm" data-click="clearUserStorage" ${status.source === 'override' ? '' : 'disabled'}>Use tier default</button>
            </div>
        `;
        const unlimited = document.getElementById('user-storage-unlimited');
        const mbInput = document.getElementById('user-storage-mb');
        unlimited?.addEventListener('change', () => { if (mbInput) mbInput.disabled = unlimited.checked; });
    } catch (e) {
        panel.innerHTML = '<span class="text-xs text-danger">Storage quota unavailable</span>';
    }
}

async function saveUserStorage() {
    if (!selectedUserId) return;
    const unlimited = Boolean(document.getElementById('user-storage-unlimited')?.checked);
    const raw = document.getElementById('user-storage-mb')?.value;
    const mb = Number(raw);
    if (!unlimited && (!raw || !Number.isFinite(mb) || mb < 0)) {
        showToast('Enter a non-negative limit in MB, or choose Unlimited', 'warning');
        return;
    }
    const body = unlimited ? {unlimited: true} : {limit_bytes: Math.round(mb * 1048576)};
    try {
        await api(`/api/v1/admin/users/${selectedUserId}/storage`, {method:'PUT', body:JSON.stringify(body)});
        showToast('Storage quota updated', 'success');
        loadUserStorage();
    } catch (e) { showToast('Failed: '+e.message, 'error'); }
}

async function clearUserStorage() {
    if (!selectedUserId) return;
    try {
        await api(`/api/v1/admin/users/${selectedUserId}/storage`, {method:'PUT', body:JSON.stringify({clear:true})});
        showToast('Storage override cleared', 'success');
        loadUserStorage();
    } catch (e) { showToast('Failed: '+e.message, 'error'); }
}

async function addUserBadge() {
    const badge = document.getElementById('new-badge-input')?.value;
    if (!badge || !selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/badges/${encodeURIComponent(badge)}`, {method:'POST'}); manageUser(selectedUserId); } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function removeUserBadge(btn) {
    const badge = btn.dataset.val;
    if (!badge || !selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/badges/${encodeURIComponent(badge)}`, {method:'DELETE'}); manageUser(selectedUserId); } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function suspendUser() {
    if (!selectedUserId) return;
    try { await api(`/api/v1/admin/security/lock-user`, {method:'POST', body:JSON.stringify({user_id:selectedUserId})}); manageUser(selectedUserId); } catch(e) { showToast('Failed', 'error'); }
}

async function killUserSessions() {
    if (!selectedUserId) return;
    try { await api('/api/v1/admin/security/force-logout', {method:'POST', body:JSON.stringify({user_id:selectedUserId})}); showToast('Sessions killed', 'success'); } catch(e) { showToast('Failed', 'error'); }
}

async function forceUserRename() {
    if (!selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/force-username-change`, {method:'POST'}); showToast('Rename forced', 'success'); } catch(e) { showToast('Failed', 'error'); }
}

async function saveUserNotes() {
    const notes = document.getElementById('user-notes-input')?.value;
    if (!selectedUserId) return;
    try { await api(`/api/v1/admin/users/${selectedUserId}/notes`, {method:'POST', body:JSON.stringify({notes})}); showToast('Notes saved', 'success'); } catch(e) { showToast('Failed', 'error'); }
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
    try { await api(`/api/v1/admin/users/${btn.dataset.id}/cancel-deletion`, {method:'POST'}); refreshDeletions(); } catch(e) { showToast('Failed', 'error'); }
}

async function forcePurge(btn) {
    if (!confirm('IRREVERSIBLE: Permanently purge this user?')) return;
    try { await api(`/api/v1/admin/users/${btn.dataset.id}/force-purge`, {method:'POST'}); refreshDeletions(); } catch(e) { showToast('Failed', 'error'); }
}

