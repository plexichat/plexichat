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
    } catch(e) { showToast('Export failed', 'error'); }
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
    try { await api('/api/v1/admin/plexijoin/connections', {method:'POST', body:JSON.stringify({instance_url:url, display_name:name, api_key:key})}); closeCreateConnectionModal(); refreshPlexiJoin(); } catch(e) { showToast('Failed', 'error'); }
}

async function deleteConnection(btn) {
    if (!confirm('Delete this connection?')) return;
    try { await api(`/api/v1/admin/plexijoin/connections/${btn.dataset.id}`, {method:'DELETE'}); refreshPlexiJoin(); } catch(e) { showToast('Failed', 'error'); }
}

async function testConnection(btn) {
    try { const d = await api(`/api/v1/admin/plexijoin/connections/${btn.dataset.id}/test`, {method:'POST'}); showToast(d.message||'Connection test complete', 'error'); } catch(e) { showToast('Test failed: '+e.message, 'error'); }
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
    try { await api('/api/v1/admin/license/reload', {method:'POST'}); showToast('License reloaded', 'error'); loadLicense(); } catch(e) { showToast('Failed', 'error'); }
}

async function applyLicense() {
    const input = document.getElementById('license-apply-input');
    const key = input?.value?.trim();
    if (!key) { showToast('Paste a license key first', 'error'); return; }
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
        showToast('Failed to apply license: '+(e.message||e), 'error');
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
    try { await api('/api/v1/admin/auth/change-password', {method:'POST', body:JSON.stringify({current_password:cur, new_password:nw})}); showToast('Password changed', 'success'); document.getElementById('account-current-password').value = ''; document.getElementById('account-new-password').value = ''; } catch(e) { showToast('Failed: '+e.message, 'error'); }
}

// === LOGOUT ===
async function logout() {
    try { await fetch('/api/v1/admin/logout', { method:'POST', headers: { 'Authorization': `Bearer ${sessionStorage.getItem('plexichat-admin-token')}` } }); } catch(e) {}
    sessionStorage.removeItem('plexichat-admin-token');
    window.location.replace('/api/v1/admin/login');
}

// === ARTIFACTS ADMIN ===
function artifactsStateClass(state) {
    switch (state) {
        case 'available': return 'banner-available';
        case 'disabled_by_config': return 'banner-disabled-config';
        case 'disabled_by_license': return 'banner-disabled-license';
        case 'dependency_missing': return 'banner-dependency-missing';
        case 'misconfigured': return 'banner-misconfigured';
        default: return 'banner-disabled-config';
    }
}

function artifactsStateLabel(state) {
    switch (state) {
        case 'available': return 'Available';
        case 'disabled_by_config': return 'Disabled by config';
        case 'disabled_by_license': return 'Disabled by license';
        case 'dependency_missing': return 'Dependency missing';
        case 'misconfigured': return 'Misconfigured';
        default: return 'Unknown';
    }
}

async function renderArtifacts() {
    await Promise.all([renderArtifactCapabilities(), renderArtifactList()]);
    renderFeatureSettings();
}

async function renderArtifactCapabilities() {
    try {
        const d = await api('/api/v1/admin/capabilities');
        const container = document.getElementById('artifacts-capabilities');
        if (!container) return;
        const caps = d.capabilities || d;
        const features = ['artifacts', 'plexiscribe', 'plexiscript', 'plexiboard', 'voice_transcription', 'voice_recording'];
        const rows = features.filter(f => caps[f]).map(f => {
            const info = caps[f];
            const detail = info.details ? ` — ${esc(info.details)}` : '';
            return `
                <div class="artifacts-banner ${artifactsStateClass(info.state)}">
                    <div class="artifacts-banner-head">
                        <span class="artifacts-banner-title">${esc(f)}</span>
                        <span class="artifacts-banner-state">${esc(artifactsStateLabel(info.state))}</span>
                    </div>
                    <div class="artifacts-banner-msg">${esc(info.message || '')}${detail}</div>
                </div>`;
        });
        container.innerHTML = rows.length ? rows.join('') : '<p class="text-muted">No capability data.</p>';
    } catch (e) {
        const container = document.getElementById('artifacts-capabilities');
        if (container) container.innerHTML = `<div class="artifacts-banner banner-misconfigured"><div class="artifacts-banner-msg">Failed to load capabilities: ${esc(e.message || 'unknown error')}</div></div>`;
    }
}

async function renderFeatureSettings() {
  const container = document.getElementById('artifact-feature-settings');
  if (!container) return;

  const features = [
    { key: 'plexiscribe', label: 'Plexiscribe', icon: 'ph-file-text', defaults: { max_participants: '50', max_document_size_mb: '100' } },
    { key: 'plexiscript', label: 'Plexiscript', icon: 'ph-code-block', defaults: { max_participants: '50', max_file_size_kb: '2048' } },
    { key: 'plexiboard', label: 'Plexiboard (Whiteboard)', icon: 'ph-chalkboard', defaults: { max_participants: '50' } },
  ];

  let html = '<h3>Per-Server Feature Overrides</h3><p>Set per-server overrides for artifact features. Leave blank to use defaults.</p>';
  features.forEach(f => {
    html += `
      <div class="feature-settings-card">
        <h4><i class="ph ${f.icon}"></i> ${f.label}</h4>
        <form class="feature-settings-form" data-feature="${f.key}">
          <div class="feature-setting-row">
            <label>Server ID</label>
            <input type="number" class="feature-server-id" placeholder="Server ID (e.g. 1001)" required>
          </div>
          <div class="feature-setting-row">
            <label>Enabled (true/false)</label>
            <input type="text" class="feature-enabled" placeholder="e.g. true" value="true">
          </div>
          ${Object.entries(f.defaults).map(([k, v]) => `
            <div class="feature-setting-row">
              <label>${k.replace(/_/g, ' ')}</label>
              <input type="text" class="feature-setting-${k}" data-setting-key="${k}" placeholder="${v}" value="${v}">
            </div>
          `).join('')}
          <button type="submit" class="btn btn-primary">Save Settings</button>
          <div class="feature-settings-result"></div>
        </form>
        <div class="feature-current-settings">
          <h5>Current Settings</h5>
          <div class="feature-current-settings-data" data-feature="${f.key}">
            <em>Enter a Server ID and click "Load" to see current settings.</em>
          </div>
          <button class="btn btn-secondary btn-load-settings" data-feature="${f.key}">Load Settings</button>
        </div>
      </div>
    `;
  });
  container.innerHTML = html;

  // Wire form submissions
  container.querySelectorAll('.feature-settings-form').forEach(form => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const feature = form.dataset.feature;
      const serverId = form.querySelector('.feature-server-id').value;
      const settings = {};
      form.querySelectorAll('input[type="text"]').forEach(input => {
        if (!input.classList.contains('feature-server-id')) {
          const key = input.dataset.settingKey;
          settings[key] = input.value || null;
        }
      });
      try {
        const result = await api('/api/v1/admin/artifacts/features/' + feature, {
          method: 'POST',
          // Snowflake id as a string: Number() would round a 64-bit id onto
          // a different server.
          body: JSON.stringify({ server_id: String(serverId).trim(), settings }),
        });
        const resultEl = form.querySelector('.feature-settings-result');
        if (resultEl) {
          resultEl.innerHTML = '<span class="text-success">Settings saved successfully.</span>';
          resultEl.style.display = 'block';
        }
      } catch (err) {
        console.error('Save error:', err);
        const resultEl = form.querySelector('.feature-settings-result');
        if (resultEl) {
          resultEl.innerHTML = '<span class="text-error">Error saving settings: ' + (err.message || 'Unknown error') + '</span>';
          resultEl.style.display = 'block';
        }
      }
    });
  });

  // Wire load buttons
  container.querySelectorAll('.btn-load-settings').forEach(btn => {
    btn.addEventListener('click', async () => {
      const feature = btn.dataset.feature;
      const form = document.querySelector(`.feature-settings-form[data-feature="${feature}"]`);
      const serverId = form ? form.querySelector('.feature-server-id').value : '';
      if (!serverId) { showToast('Enter a Server ID first.', 'error'); return; }
      try {
        const result = await api('/api/v1/admin/artifacts/features/' + feature + '?server_id=' + serverId);
        const dataEl = document.querySelector(`.feature-current-settings-data[data-feature="${feature}"]`);
        if (dataEl && result.settings) {
          dataEl.innerHTML = Object.entries(result.settings).map(([k, v]) =>
            `<div class="setting-item"><strong>${k}:</strong> ${v || '<em>default</em>'}</div>`
          ).join('') || '<em>No overrides (using defaults).</em>';
        }
      } catch (err) {
        console.error('Save error:', err);
        showToast('Failed to load settings: ' + (err.message || 'Unknown error'), 'error');
      }
    });
  });
}

function formatExpires(artifact) {
    if (artifact.expires_at) return ts(artifact.expires_at);
    if (artifact.retention_policy && artifact.retention_policy.days) {
        return `in ${esc(artifact.retention_policy.days)}d`;
    }
    return 'never';
}

async function renderArtifactList() {
    try {
        const d = await api('/api/v1/admin/artifacts');
        const tbody = document.getElementById('artifacts-tbody');
        if (!tbody) return;
        const items = d.items || [];
        setText('artifacts-count', String(d.total ?? items.length));
        if (items.length === 0) {
            tbody.innerHTML = '<tr><td colspan="9" class="text-muted" style="text-align:center;">No artifacts</td></tr>';
            return;
        }
        tbody.innerHTML = items.map(a => `
            <tr>
                <td class="font-mono">${esc(a.id)}</td>
                <td><span class="badge badge-outline">${esc(a.artifact_type)}</span></td>
                <td>${esc(a.title)}</td>
                <td class="font-mono">${esc(a.author_id)}</td>
                <td class="font-mono">${a.server_id != null ? esc(a.server_id) : '<span class="text-muted">-</span>'}</td>
                <td class="text-muted">${ts(a.created_at)}</td>
                <td>${a.retention_policy && a.retention_policy.days != null ? esc(a.retention_policy.days) + 'd' : '<span class="text-muted">-</span>'}</td>
                <td class="text-muted">${formatExpires(a)}</td>
                <td class="text-right">
                    <button class="btn btn-danger btn-xs" data-click="forceDeleteArtifact" data-id="${esc(a.id)}">Force delete</button>
                </td>
            </tr>
        `).join('');
    } catch (e) {
        const tbody = document.getElementById('artifacts-tbody');
        if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="text-muted" style="text-align:center;">Failed to load: ${esc(e.message || 'unknown error')}</td></tr>`;
    }
}

async function refreshArtifacts() {
    try {
        await renderArtifacts();
        showToast('Artifacts refreshed', 'error');
    } catch (e) {
        showToast('Refresh failed: ' + (e.message || 'unknown error'), 'error');
    }
}

async function forceDeleteArtifact(btn) {
    const id = btn.dataset.id;
    if (!confirm(`IRREVERSIBLE: Permanently delete artifact ${id}?`)) return;
    try {
        await api(`/api/v1/admin/artifacts/${encodeURIComponent(id)}`, { method: 'DELETE' });
        showToast('Artifact deleted', 'error');
        await renderArtifactList();
    } catch (e) {
        showToast('Failed: ' + (e.message || 'unknown error'), 'error');
    }
}

async function runRetentionPurge() {
    if (!confirm('Purge all artifacts whose retention window has expired?')) return;
    try {
        const d = await api('/api/v1/admin/artifacts/retention/purge', { method: 'POST' });
        showToast(`Retention purge complete — ${d.purged ?? 0} artifact(s) removed`, 'success');
        await renderArtifactList();
    } catch (e) {
        showToast('Purge failed: ' + (e.message || 'unknown error'), 'error');
    }
}

async function saveServerRetention(e) {
    if (e) e.preventDefault();
    const idEl = document.getElementById('server-retention-id');
    const daysEl = document.getElementById('server-retention-days');
    const resultEl = document.getElementById('server-retention-result');
    if (!idEl || !daysEl) return;
    const serverId = idEl.value.trim();
    const daysRaw = daysEl.value.trim();
    if (serverId === '') { showToast('Server ID is required', 'error'); return; }
    // Keep the snowflake as a string: Number() would round a 64-bit id.
    const body = { server_id: String(serverId).trim(), retention_days: daysRaw === '' ? null : Number(daysRaw) };
    if (!/^\d+$/.test(body.server_id)) { showToast('Server ID must be a number', 'error'); return; }
    if (daysRaw !== '' && Number.isNaN(body.retention_days)) { showToast('Retention days must be a number', 'error'); return; }
    try {
        const d = await api('/api/v1/admin/artifacts/retention/server', { method: 'POST', body: JSON.stringify(body) });
        const effective = d.retention_days == null ? 'cleared (use global default)' : `${esc(d.retention_days)} days`;
        if (resultEl) resultEl.innerHTML = `<span style="color:var(--success);">Server ${esc(d.server_id)} override ${effective}.</span>`;
        showToast('Server retention override saved', 'error');
        await renderArtifactList();
    } catch (err) {
        if (resultEl) resultEl.innerHTML = `<span style="color:var(--destructive);">Failed: ${esc(err.message || 'unknown error')}</span>`;
        showToast('Failed: ' + (err.message || 'unknown error', 'error'));
    }
}

