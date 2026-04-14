// ─── State ───────────────────────────────────────────────
let merchants = [];
let results = [];

// ─── Init: Check sheets + scheduler status on load ──────
window.addEventListener('load', () => {
    checkSheetsStatus();
    loadSchedulerStatus();
});

// ─── Tab Switching ───────────────────────────────────────
function switchTab(name) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

    document.getElementById('tab-' + name).classList.add('active');

    const tabNames = ['merchants', 'sanity', 'results', 'settings'];
    const idx = tabNames.indexOf(name);
    document.querySelectorAll('.tab')[idx]?.classList.add('active');
    document.querySelectorAll('.nav-item')[idx]?.classList.add('active');
}

// ─── Google Sheets Status ────────────────────────────────
async function checkSheetsStatus() {
    try {
        const res = await fetch('/api/sheets-status');
        const data = await res.json();
        const el = document.getElementById('sheetsStatus');
        const detailEl = document.getElementById('sheetsConnectionInfo');

        if (data.success) {
            const sheets = data.sheets;
            const allOk = Object.values(sheets).every(s => s.connected);
            el.innerHTML = `<span class="dot" style="background:${allOk ? '#10b981' : '#ef4444'};"></span>
                <span style="color:${allOk ? '#10b981' : '#ef4444'};">${allOk ? 'Sheets Connected' : 'Some Sheets Disconnected'}</span>`;

            if (detailEl) {
                let html = '';
                for (const [name, info] of Object.entries(sheets)) {
                    const icon = info.connected ? '&#10003;' : '&#10007;';
                    const color = info.connected ? '#10b981' : '#ef4444';
                    html += `<div style="padding:0.5rem; margin-bottom:0.5rem; border-radius:6px; background:#f8fafc; display:flex; justify-content:space-between;">
                        <span>${name}</span>
                        <span style="color:${color}; font-weight:600;">${icon} ${info.connected ? info.title : info.error}</span>
                    </div>`;
                }
                detailEl.innerHTML = html;
            }
        } else {
            el.innerHTML = `<span class="dot" style="background:#ef4444;"></span>
                <span style="color:#ef4444;">Not Connected</span>`;
            if (detailEl) {
                detailEl.innerHTML = `<div style="color:#ef4444; padding:1rem;">
                    <strong>Error:</strong> ${data.error}<br><br>
                    <strong>Setup needed:</strong><br>
                    1. Create Google Service Account<br>
                    2. Save JSON key as <code>config/service_account.json</code><br>
                    3. Share all 3 Google Sheets with the service account email
                </div>`;
            }
        }
    } catch (e) {
        document.getElementById('sheetsStatus').innerHTML =
            `<span class="dot" style="background:#ef4444;"></span> <span style="color:#ef4444;">Error</span>`;
    }
}

// ─── Scheduler Status ────────────────────────────────────
async function loadSchedulerStatus() {
    try {
        const res = await fetch('/api/scheduler-status');
        const data = await res.json();
        const el = document.getElementById('schedulerInfo');
        const detailEl = document.getElementById('schedulerDetails');

        if (data.running) {
            el.innerHTML = `Next run: <strong>${data.next_run || 'N/A'}</strong>` +
                (data.last_run ? ` | Last: ${data.last_run}` : '');
        } else {
            el.innerHTML = 'Scheduler not running — will start when Google Sheets is connected';
        }

        if (detailEl) {
            detailEl.innerHTML = `<div style="padding:0.8rem; background:#f8fafc; border-radius:8px;">
                <div>Status: <strong>${data.running ? 'Running' : 'Stopped'}</strong></div>
                <div>Next Run: <strong>${data.next_run || 'N/A'}</strong></div>
                <div>Last Run: <strong>${data.last_run || 'Never'}</strong></div>
                ${data.last_result_summary ? `<div>Last Result: ${data.last_result_summary.total} merchants, ${data.last_result_summary.passed} passed, ${data.last_result_summary.failed} failed</div>` : ''}
            </div>`;
        }
    } catch (e) {}
}

// ─── Load Merchants ──────────────────────────────────────
async function loadTodayMerchants() {
    showLoading('Loading merchants from Google Sheet...');
    try {
        const res = await fetch('/api/today-merchants');
        const data = await res.json();
        if (data.success) {
            merchants = data.merchants || [];
            document.getElementById('metricTotal').textContent = merchants.length;
            renderMerchantTable(merchants);
        } else {
            alert('Error: ' + (data.error || 'Failed to load'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
    hideLoading();
}

function renderMerchantTable(data) {
    if (!data.length) {
        document.getElementById('merchantTableContainer').innerHTML =
            '<p style="color:#94a3b8;text-align:center;padding:2rem;">No merchants found for today</p>';
        return;
    }

    const cols = ['Date', 'Merchant Name', 'Mid', 'Bank Accont ', 'Web hook - VPA', 'Settlement Report Triggered', 'Commercial Validation', 'POC'];
    let html = '<div style="overflow-x:auto;"><table><thead><tr>';
    cols.forEach(c => html += `<th>${c.trim()}</th>`);
    html += '</tr></thead><tbody>';

    data.forEach(row => {
        html += '<tr>';
        cols.forEach(c => {
            const val = row[c] || row[c.trim()] || '';
            const display = val === 'Yes' ? '<span style="color:#10b981;font-weight:600;">Yes</span>' :
                            val === 'No' ? '<span style="color:#ef4444;">No</span>' :
                            String(val).includes('wrong') ? '<span style="color:#ef4444;">' + val + '</span>' :
                            (val || '-');
            html += `<td>${display}</td>`;
        });
        html += '</tr>';
    });

    html += '</tbody></table></div>';
    document.getElementById('merchantTableContainer').innerHTML = html;
}

function filterMerchants() {
    const q = document.getElementById('searchInput').value.toLowerCase();
    if (!q) return renderMerchantTable(merchants);
    const filtered = merchants.filter(m =>
        Object.values(m).some(v => String(v).toLowerCase().includes(q))
    );
    renderMerchantTable(filtered);
}

// ─── Progress Polling ────────────────────────────────────
let progressInterval = null;

function startProgressPolling() {
    progressInterval = setInterval(async () => {
        try {
            const res = await fetch('/api/progress');
            const p = await res.json();
            updateStageUI(p.stage, p.merchant, p.merchant_idx, p.total);
        } catch (e) {}
    }, 2000);
}

function stopProgressPolling() {
    if (progressInterval) { clearInterval(progressInterval); progressInterval = null; }
}

function updateStageUI(stage, merchant, idx, total) {
    const stages = ['eb-login', 'settlement', 'mdr', 'account', 'gk-login', 'saltkey', 'vpa'];
    const stageOrder = {
        'starting': -1, 'eb-login': 0, 'settlement': 1, 'mdr': 2, 'account': 3,
        'gk-login': 4, 'saltkey': 5, 'vpa': 6
    };

    const currentIdx = stageOrder[stage] ?? -1;

    stages.forEach((s, i) => {
        const el = document.getElementById('stage-' + s);
        const icon = document.getElementById('icon-' + s);
        if (!el) return;
        el.classList.remove('active', 'done', 'error');
        if (i < currentIdx) {
            el.classList.add('done');
            icon.innerHTML = '&#10003;';
        } else if (i === currentIdx) {
            el.classList.add('active');
            icon.innerHTML = '';
        } else {
            icon.innerHTML = '&#9711;';
        }
    });

    // Show current merchant
    const merchantEl = document.getElementById('currentMerchant');
    if (merchant && merchantEl) {
        merchantEl.style.display = 'block';
        document.getElementById('currentMerchantName').textContent = merchant;
        document.getElementById('merchantProgress').textContent = total ? `(${idx}/${total})` : '';
    }

    const textEl = document.getElementById('loadingText');
    if (textEl) {
        const msgs = {
            'starting': 'Initializing...',
            'eb-login': 'Logging in to EB Partner Portal...',
            'settlement': 'Generating & downloading settlement report...',
            'mdr': `Checking MDR rates for ${merchant}...`,
            'account': `Verifying account number for ${merchant}...`,
            'gk-login': 'Logging in to GK Dashboard (auto OTP)...',
            'saltkey': `Checking SALT & KEY for ${merchant}...`,
            'vpa': `Checking VPA for ${merchant}...`
        };
        textEl.textContent = msgs[stage] || 'Processing...';
    }
}

// ─── Run Batch Check ─────────────────────────────────────
async function runBatchCheck() {
    const selectedDate = document.getElementById('checkDate')?.value || '';
    showLoading('Starting sanity checks...');
    startProgressPolling();

    try {
        const res = await fetch('/api/run-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: selectedDate })
        });
        const data = await res.json();

        if (data.success && data.result) {
            const batch = data.result;
            if (batch.error) {
                alert(batch.error);
                stopProgressPolling();
                hideLoading();
                return;
            }
            results = batch.results || [];
            updateMetrics();
            renderResults();
            switchTab('results');

            document.getElementById('checkStatus').innerHTML =
                `<div style="color:#10b981;font-weight:600;">Done: ${batch.passed} passed, ${batch.failed} failed, ${batch.warned} warnings out of ${batch.total_merchants} merchants</div>`;
        } else {
            alert('Error: ' + (data.error || data.result?.error || 'Unknown error'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
    stopProgressPolling();
    hideLoading();
}

// ─── Run Now (Scheduler) ────────────────────────────────
async function runNow() {
    try {
        const res = await fetch('/api/run-now', { method: 'POST' });
        const data = await res.json();
        alert(data.message || 'Check started in background');
        setTimeout(loadSchedulerStatus, 2000);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ─── Write Results to Sheet ─────────────────────────────
async function writeToSheet() {
    if (!results.length) return alert('No results to write');
    showLoading('Writing results to Sanity Tracker sheet...');
    try {
        const res = await fetch('/api/write-results', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ results })
        });
        const data = await res.json();
        if (data.success) {
            alert(`Results written to Google Sheet! ${data.updated_cells} cells updated.`);
        } else {
            alert('Error: ' + (data.error || 'Write failed'));
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
    hideLoading();
}

// ─── Results Rendering ──────────────────────────────────
function updateMetrics() {
    document.getElementById('metricChecked').textContent = results.length;
    document.getElementById('metricPassed').textContent = results.filter(r => r.overall_status === 'PASS').length;
    document.getElementById('metricFailed').textContent = results.filter(r => r.overall_status === 'FAIL').length;
}

function renderResults() {
    if (!results.length) {
        document.getElementById('resultsContainer').innerHTML =
            '<p style="color:#94a3b8;text-align:center;padding:2rem;">No results yet.</p>';
        return;
    }

    let html = '';
    results.forEach(r => {
        const icon = r.overall_status === 'PASS' ? '&#10003;' : r.overall_status === 'FAIL' ? '&#10007;' : '&#9888;';
        const color = r.overall_status === 'PASS' ? '#10b981' : r.overall_status === 'FAIL' ? '#ef4444' : '#f59e0b';

        html += `<div style="border:1px solid #e8ecf1; border-radius:10px; margin-bottom:1rem; overflow:hidden;">`;
        html += `<div style="padding:1rem 1.5rem; background:#f8fafc; border-bottom:1px solid #e8ecf1; display:flex; justify-content:space-between; align-items:center;">`;
        html += `<span style="font-weight:600;">${icon} ${r.merchant_name} &mdash; <span style="color:${color}">${r.overall_status}</span></span>`;
        html += `<span style="color:#94a3b8; font-size:0.8rem;">${r.pass_count}/${r.total_checks} passed | ${r.date}${r.poc ? ' | POC: ' + r.poc : ''}</span>`;
        html += `</div><div style="padding:1rem 1.5rem;">`;

        r.checks.forEach(c => {
            const badgeClass = 'badge-' + c.status.toLowerCase();
            html += `<div class="check-result">`;
            html += `<span class="check-name">${c.check_name}</span>`;
            html += `<span class="badge ${badgeClass}">${c.status}</span>`;
            html += `<div class="check-detail">${c.message}`;
            if (c.expected) html += `<div class="sub">Expected: ${c.expected}</div>`;
            if (c.actual) html += `<div class="sub">Actual: ${c.actual}</div>`;
            html += `</div></div>`;
        });

        html += `</div></div>`;
    });

    document.getElementById('resultsContainer').innerHTML = html;
}

function clearResults() {
    results = [];
    updateMetrics();
    renderResults();
}

// ─── Export Functions ────────────────────────────────────
function exportCSV() {
    if (!results.length) return alert('No results to export');
    let csv = 'Merchant,MID,Date,Check,Status,Message\n';
    results.forEach(r => {
        r.checks.forEach(c => {
            csv += `"${r.merchant_name}","${r.eb_mid || ''}","${r.date}","${c.check_name}","${c.status}","${c.message}"\n`;
        });
    });
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `sanity_report_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
}

async function exportPDF() {
    if (!results.length) return alert('No results to export');
    showLoading('Generating PDF report...');
    try {
        const res = await fetch('/api/download-report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                date: results[0]?.date || new Date().toISOString().slice(0,10),
                total_merchants: results.length,
                passed: results.filter(r => r.overall_status === 'PASS').length,
                failed: results.filter(r => r.overall_status === 'FAIL').length,
                warned: results.filter(r => !['PASS','FAIL'].includes(r.overall_status)).length,
                results: results,
            }),
        });
        if (res.ok) {
            const blob = await res.blob();
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = `sanity_report_${new Date().toISOString().slice(0,10)}.pdf`;
            a.click();
        } else {
            alert('Error generating PDF');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
    hideLoading();
}

async function generateEmail() {
    if (!results.length) return alert('No results');
    try {
        const res = await fetch('/api/generate-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ results }),
        });
        const data = await res.json();
        if (data.success && data.email) {
            const e = data.email;
            const w = window.open('', '_blank');
            w.document.write(`<html><head><title>Email Draft</title>
                <style>body{font-family:monospace;padding:2rem;max-width:800px;margin:auto;}
                pre{background:#f8fafc;padding:1.5rem;border-radius:8px;white-space:pre-wrap;border:1px solid #e2e8f0;}
                .btn{background:#3b82f6;color:white;border:none;padding:10px 24px;border-radius:6px;cursor:pointer;margin-top:1rem;}</style></head><body>
                <h2>Discrepancy Report</h2>
                <p><strong>To:</strong> ${e.to}</p>
                <p><strong>Subject:</strong> ${e.subject}</p>
                <pre>${e.body}</pre>
                <button class="btn" onclick="navigator.clipboard.writeText(document.querySelector('pre').textContent).then(()=>alert('Copied!'))">Copy</button>
                </body></html>`);
        } else {
            alert(data.message || 'No discrepancies found');
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

// ─── Helpers ─────────────────────────────────────────────
function showLoading(text) {
    document.getElementById('loadingText').textContent = text;
    document.getElementById('loadingOverlay').style.display = 'flex';
}
function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}
