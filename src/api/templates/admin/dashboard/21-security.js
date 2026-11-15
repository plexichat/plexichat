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

        document.getElementById('blocked-ips-tbody').innerHTML = (ips||[]).map(i => `<tr><td class="font-mono">${esc(i.ip_address)}</td><td class="text-muted">${esc(i.reason||'')}</td><td class="text-muted">${ts(i.blocked_at)}</td><td>${i.expires_at == null ? '<span class="badge badge-danger">Permanent</span>' : ts(i.expires_at)}</td><td class="text-muted">${esc(i.blocked_by||'')}</td><td class="text-right"><button class="btn btn-outline btn-sm" data-click="unblockIP" data-val="${esc(i.ip_address)}">Unblock</button></td></tr>`).join('');
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

function showCreateTokenModal() {
    document.getElementById('create-token-name').value = '';
    document.getElementById('create-token-description').value = '';
    document.getElementById('create-token-scope-mode').value = 'none';
    document.getElementById('create-token-expires').value = '30d';
    document.getElementById('create-token-scopes').value = '';
    const modal = document.getElementById('create-token-modal');
    modal.classList.remove('hidden');
    modal.classList.add('active');
    document.getElementById('create-token-name').focus();
}

function closeCreateTokenModal() {
    const modal = document.getElementById('create-token-modal');
    modal.classList.remove('active');
    modal.classList.add('hidden');
}

async function confirmCreateToken() {
    const nameInput = document.getElementById('create-token-name');
    const name = nameInput.value.trim();
    if (!name) {
        showToast('Token name is required', 'warning');
        nameInput.focus();
        return;
    }

    const expiry = document.getElementById('create-token-expires').value;
    const expirySeconds = {'30d': 30 * 86400, '90d': 90 * 86400, '1y': 365 * 86400}[expiry];
    const body = {
        name,
        description: document.getElementById('create-token-description').value.trim(),
        scope_mode: document.getElementById('create-token-scope-mode').value,
    };
    if (expirySeconds) body.expires_at = Math.floor(Date.now() / 1000) + expirySeconds;

    try {
        const created = await api('/api/v1/admin/security/access-tokens', {method:'POST', body:JSON.stringify(body)});
        const tokenId = created.access_token?.id;
        const scopes = document.getElementById('create-token-scopes').value
            .split(',').map(v => v.trim()).filter(Boolean);
        const failedScopes = [];
        if (tokenId) {
            for (const value of scopes) {
                try {
                    await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(tokenId)}/scopes`, {
                        method:'POST',
                        body:JSON.stringify({scope_type: value.includes('/') ? 'cidr' : 'ip', value}),
                    });
                } catch (e) {
                    failedScopes.push(value);
                }
            }
        }
        closeCreateTokenModal();
        loadSecurity();
        const warning = failedScopes.length
            ? ` (scopes failed: ${failedScopes.join(', ')})`
            : '';
        showToast(`Token created. Copy it now: ${created.token}${warning}`, failedScopes.length ? 'warning' : 'success');
    } catch (e) {
        showToast('Failed to create token: ' + e.message, 'error');
    }
}

async function refreshAccessTokenDetail() { if (selectedAccessTokenId) loadAccessTokenDetail(selectedAccessTokenId); }

async function saveAccessTokenSettings() {
    if (!selectedAccessTokenId) return;
    const body = {
        name: document.getElementById('access-token-detail-name').value,
        description: document.getElementById('access-token-detail-description').value,
        expires_at: document.getElementById('access-token-detail-expires').value ? new Date(document.getElementById('access-token-detail-expires').value).toISOString() : null,
        scope_mode: document.getElementById('access-token-detail-scope-mode').value
    };
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}`, {method:'PATCH', body:JSON.stringify(body)}); showToast('Saved', 'success'); loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { showToast('Failed', 'error'); }
}

async function clearAccessTokenExpiry() {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}`, {method:'PATCH', body:JSON.stringify({expires_at:null})}); loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { showToast('Failed', 'error'); }
}

async function rotateAccessToken() {
    if (!selectedAccessTokenId || !confirm('Rotate this token? Old token will stop working.')) return;
    try { const d = await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/rotate`, {method:'POST'}); document.getElementById('access-token-rotated').textContent = `New token: ${d.token || 'rotated'}`; } catch(e) { showToast('Failed', 'error'); }
}

async function revokeSelectedAccessToken() {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/revoke`, {method:'POST'}); loadAccessTokenDetail(selectedAccessTokenId); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

async function unrevokeSelectedAccessToken() {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/unrevoke`, {method:'POST'}); loadAccessTokenDetail(selectedAccessTokenId); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

async function addAccessTokenScope() {
    if (!selectedAccessTokenId) return;
    const type = document.getElementById('access-token-scope-type').value;
    const value = document.getElementById('access-token-scope-value').value;
    if (!value) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/scopes`, {method:'POST', body:JSON.stringify({scope_type:type, value})}); document.getElementById('access-token-scope-value').value = ''; loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { showToast('Failed', 'error'); }
}

async function removeAccessTokenScope(btn) {
    if (!selectedAccessTokenId) return;
    try { await api(`/api/v1/admin/security/access-tokens/${encodeURIComponent(selectedAccessTokenId)}/scopes/${btn.dataset.id}`, {method:'DELETE'}); loadAccessTokenDetail(selectedAccessTokenId); } catch(e) { showToast('Failed', 'error'); }
}

async function unblockIP(btn) {
    try { await api(`/api/v1/admin/security/unblock-ip/${encodeURIComponent(btn.dataset.val)}`, {method:'DELETE'}); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

async function removeBan(btn) {
    try { await api(`/api/v1/admin/security/banned-usernames/${btn.dataset.id}`, {method:'DELETE'}); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

async function changeAdminPassword() {
    const cur = document.getElementById('admin-current-password').value;
    const nw = document.getElementById('admin-new-password').value;
    if (!cur || !nw) return;
    try { await api('/api/v1/admin/auth/change-password', {method:'POST', body:JSON.stringify({current_password:cur, new_password:nw})}); showToast('Password changed', 'success'); document.getElementById('admin-current-password').value = ''; document.getElementById('admin-new-password').value = ''; } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function beginAdminOtpSetup() {
    const pw = document.getElementById('admin-2fa-password').value;
    if (!pw) { showToast('Enter password first', 'warning'); return; }
    try {
        const d = await api('/api/v1/admin/auth/2fa/begin-setup', {method:'POST', body:JSON.stringify({password:pw})});
        adminOtpSetupChallenge = d.challenge_token;
        const qr = document.getElementById('admin-otp-qr');
        qr.src = `/api/v1/qr?size=200x200&data=${encodeURIComponent(d.otp_qr_uri)}`;
        document.getElementById('admin-otp-secret').textContent = d.otp_secret;
        document.getElementById('admin-security-otp-setup').classList.remove('hidden');
    } catch(e) { showToast('Failed: '+e.message, 'error'); }
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
        showToast('2FA enabled', 'success');
    } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function regenerateAdminBackupCodes() {
    try {
        const d = await api('/api/v1/admin/auth/2fa/regenerate-backup-codes', {method:'POST'});
        document.getElementById('admin-backup-codes-list').textContent = (d.codes||[]).join('\n');
        document.getElementById('admin-backup-codes').classList.remove('hidden');
    } catch(e) { showToast('Failed', 'error'); }
}

async function disableAdminOtp() {
    const code = document.getElementById('admin-disable-otp-code').value;
    if (!code) return;
    try { await api('/api/v1/admin/auth/2fa/disable', {method:'POST', body:JSON.stringify({code})}); showToast('2FA disabled', 'success'); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

function showBlockIPModal() { document.getElementById('block-ip-modal').classList.add('active'); }
function closeBlockIPModal() { document.getElementById('block-ip-modal').classList.remove('active'); }
async function confirmBlockIP() {
    const ip = document.getElementById('block-ip-val').value;
    const reason = document.getElementById('block-ip-reason').value;
    const duration = document.getElementById('block-ip-duration').value;
    if (!ip) return;
    try { await api('/api/v1/admin/security/block-ip', {method:'POST', body:JSON.stringify({ip_address:ip, reason, duration})}); closeBlockIPModal(); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

function showBanUsernameModal() { document.getElementById('ban-username-modal').classList.add('active'); }
function closeBanUsernameModal() { document.getElementById('ban-username-modal').classList.remove('active'); }
async function confirmBanUsername() {
    const pattern = document.getElementById('ban-username-pattern').value;
    const type = document.getElementById('ban-username-type').value;
    const reason = document.getElementById('ban-username-reason').value;
    if (!pattern) return;
    try { await api('/api/v1/admin/security/banned-usernames', {method:'POST', body:JSON.stringify({pattern, is_regex: type==='regex', reason})}); closeBanUsernameModal(); loadSecurity(); } catch(e) { showToast('Failed', 'error'); }
}

async function globalSessionPurge() {
    if (!confirm('PURGE ALL USER SESSIONS? This will log out every user. Continue?')) return;
    if (!confirm('ARE YOU SURE? This cannot be undone easily.')) return;
    try { await api('/api/v1/admin/security/logout-all', {method:'POST'}); showToast('All sessions purged', 'success'); } catch(e) { showToast('Failed', 'error'); }
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
    } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

async function toggleAdminUserStatus(btn) {
    try { await api(`/api/v1/admin/admin-users/${btn.dataset.id}/toggle-status`, {method:'POST'}); loadAdminUsers(); } catch(e) { showToast('Failed', 'error'); }
}

async function deleteAdminUser(btn) {
    if (!confirm('Delete this admin user?')) return;
    try { await api(`/api/v1/admin/admin-users/${btn.dataset.id}`, {method:'DELETE'}); loadAdminUsers(); } catch(e) { showToast('Failed', 'error'); }
}

