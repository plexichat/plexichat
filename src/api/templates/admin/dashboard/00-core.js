// Chart.js is vendored and served locally. Guard every use so a missing or
// blocked chart bundle degrades to an empty panel instead of throwing at the
// top of this script and taking the rest of the dashboard down with it.
if (typeof Chart !== 'undefined') {
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
}

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
let selectedUserId = null, selectedTicketId = null, selectedTicketStatus = null, selectedAccessTokenId = null, adminOtpSetupChallenge = null, selectedDataExportId = null;
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

const relTime = unixSec => {
    if (!unixSec) return '';
    const diff = Math.abs(Date.now() / 1000 - unixSec);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
    return '';
};

const pageTitles = {
    dashboard:'Dashboard', users:'User Management', admins:'Admin Management', tickets:'Support Tickets',
    moderation:'Content Moderation', security:'Security', telemetry:'Telemetry & Performance',
    logs:'System Logs', database:'Database Management', approvals:'Approval Workflows',
    audit:'Audit Log', automod:'AutoMod', bots:'Bots', artifacts:'Artifacts', migrations:'Database Migrations',
    alerts:'System Alerts', 'data-exports':'Data Exports',
    plexijoin:'PlexiJoin Federation', license:'License', account:'My Account'
};

// === TAB SYSTEM ===
let _handlingHashChange = false;
const showTab = n => {
    // Handle compound hashes like 'approval-{id}' and 'user-{id}' —
    // navigate to the parent tab and schedule a scroll-to after data loads.
    let subTarget = null;
    if (n.startsWith('approval-')) {
        subTarget = n;
        n = 'approvals';
    } else if (n.startsWith('user-')) {
        subTarget = n;
        n = 'users';
    }

    document.querySelectorAll('.sidebar-nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === n));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${n}`));
    currentTab = n;
    document.getElementById('page-title').textContent = pageTitles[n] || n;
    _handlingHashChange = true;
    try {
        window.location.hash = subTarget || n;
    } finally {
        _handlingHashChange = false;
    }
    if (subTarget) {
        const key = subTarget.startsWith('approval-') ? 'plexichat-approval-scroll' : 'plexichat-user-scroll';
        sessionStorage.setItem(key, subTarget);
    }

    const loads = {
        dashboard:'refreshMetrics', tickets:'loadTickets', 'data-exports':'loadDataExports', users:'loadUsers', admins:'loadAdminUsers',
        moderation:'loadModeration', security:'loadSecurity', telemetry:'refreshTelemetryStats',
        logs:'loadLogs', database:'refreshDatabase', approvals:'loadApprovals', audit:'loadAuditLog',
        automod:'loadAutomodConfig', bots:'refreshAdminBots', artifacts:'renderArtifacts', migrations:'refreshMigrations',
        plexijoin:'refreshPlexiJoin', license:'loadLicense', account:'loadAccount',
        alerts:'refreshAlerts'
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

// === KEYBOARD SHORTCUTS ===
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        const toast = document.body.querySelector('.toast');
        if (toast) toast.querySelector('.toast-dismiss')?.click();
    }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.closest('[data-click]')) {
        e.preventDefault();
        e.target.closest('[data-click]').click();
    }
});

// === ALERTS FILTER CHANGE ===
document.addEventListener('change', e => {
    const target = e.target.closest('#alerts-source-filter, #alerts-severity-filter, #alerts-hours-filter');
    if (target) refreshAlerts();
});
