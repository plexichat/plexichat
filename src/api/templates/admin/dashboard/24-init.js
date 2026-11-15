// === HASH-BASED NAVIGATION (back/forward buttons + bookmarks) ===
window.addEventListener('hashchange', () => {
    if (_handlingHashChange) return;
    const hash = window.location.hash.slice(1);
    if (!hash) return;
    _handlingHashChange = true;
    try {
        // showTab already handles compound hashes like 'approval-{id}'.
        showTab(hash);
    } finally {
        _handlingHashChange = false;
    }
});

// === DASHBOARD INIT ===
// Run after the page's load event.  Chart.js sizes its canvases from computed
// styles, and doing that while stylesheets/fonts are still loading forces a
// synchronous layout (Firefox: "Layout was forced before the page was fully
// loaded").  showTab() already loads the active tab, so calling refreshMetrics()
// again here duplicated every dashboard API request and rebuilt every chart.
const initDashboard = () => {
    const hash = window.location.hash.slice(1);
    const isKnownTab = hash && (pageTitles[hash] || hash.startsWith('approval-') || hash.startsWith('user-'));
    if (isKnownTab) showTab(hash);
    else showTab('dashboard');

    document.getElementById('metric-hours')?.addEventListener('change', refreshMetrics);
    document.getElementById('endpoint-filter')?.addEventListener('input', renderEndpointTable);
    document.getElementById('log-file-select')?.addEventListener('change', e => { if (e.target.value) loadLogFile(e.target.value); });
    document.getElementById('log-search')?.addEventListener('input', () => { const s = document.getElementById('log-file-select'); if (s && s.value) loadLogFile(s.value); });
    document.getElementById('user-search-input')?.addEventListener('input', loadUsers);
    document.getElementById('ticket-status-filter')?.addEventListener('change', loadTickets);
    document.getElementById('data-export-status-filter')?.addEventListener('change', loadDataExports);
    document.getElementById('hash-report-status')?.addEventListener('change', loadModeration);
    document.getElementById('message-report-status')?.addEventListener('change', loadMessageReports);
    document.getElementById('user-report-status')?.addEventListener('change', loadUserReports);
    document.getElementById('approval-status-filter')?.addEventListener('change', loadApprovals);
    document.getElementById('dash-time-range')?.addEventListener('change', refreshMetrics);

    // Mandatory-2FA guard: login already forces setup, but a session minted
    // before the requirement (or before the setup was completed) must not be
    // usable on the dashboard. Send it back through the login flow.
    api('/api/v1/admin/auth/security-status')
        .then(sec => { if (sec.must_setup_otp) window.location.replace('/api/v1/admin/login'); })
        .catch(() => {});
};

if (document.readyState === 'complete') initDashboard();
else window.addEventListener('load', initDashboard, { once: true });
