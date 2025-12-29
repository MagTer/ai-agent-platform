# ruff: noqa: E501
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from core.core.config import Settings, get_settings
from core.diagnostics.service import DiagnosticsService, TestResult, TraceGroup

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def get_diagnostics_service(
    settings: Settings = Depends(get_settings),
) -> DiagnosticsService:
    return DiagnosticsService(settings)


@router.get("/traces", response_model=list[TraceGroup])
async def get_traces(
    limit: int = 1000, service: DiagnosticsService = Depends(get_diagnostics_service)
) -> list[TraceGroup]:
    return service.get_recent_traces(limit)


@router.get("/metrics")
async def get_metrics(
    window: int = 60, service: DiagnosticsService = Depends(get_diagnostics_service)
) -> dict:
    return service.get_system_health_metrics(window=window)


@router.post("/run", response_model=list[TestResult])
async def run_diagnostics(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> list[TestResult]:
    return await service.run_diagnostics()


@router.get("/", response_class=HTMLResponse)
async def diagnostics_dashboard(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> str:
    # Professional Split-Pane Dashboard
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Diagnostics Dashboard</title>
    <style>
        :root { --sidebar-w: 350px; --primary: #2563eb; --bg: #f3f4f6; --white: #fff; --border: #e5e7eb; --text: #1f2937; --text-muted: #6b7280; --success: #10b981; --error: #ef4444; }
        body { font-family: 'Inter', system-ui, -apple-system, sans-serif; margin: 0; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        
        /* Header */
        .header { background: #fff; border-bottom: 1px solid var(--border); padding: 0 20px; height: 56px; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; z-index: 10; }
        .brand { font-weight: 600; font-size: 16px; display: flex; align-items: center; gap: 8px; }
        
        /* Layout */
        .layout { display: flex; flex: 1; overflow: hidden; flex-direction: row; }
        
        /* Sidebar */
        .sidebar { width: var(--sidebar-w); background: #fff; border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
        .sidebar-header { padding: 12px; border-bottom: 1px solid var(--border); background: #f9fafb; font-size: 12px; font-weight: 600; color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; }
        .request-list { flex: 1; overflow-y: auto; }
        
        .req-card { padding: 16px; border-bottom: 1px solid var(--border); cursor: pointer; transition: all 0.1s; border-left: 3px solid transparent; }
        .req-card:hover { background: #f9fafb; }
        .req-card.active { background: #eff6ff; border-left-color: var(--primary); }
        .req-card.error { border-left-color: var(--error); background: #fef2f2; }
        
        .req-top { display: flex; justify-content: space-between; margin-bottom: 6px; }
        .req-status { width: 8px; height: 8px; border-radius: 50%; background: var(--text-muted); display: inline-block; }
        .req-status.ok { background: var(--success); }
        .req-status.err { background: var(--error); }
        
        .req-time { font-size: 11px; color: var(--text-muted); font-weight: 500; }
        .req-query { font-size: 13px; font-weight: 500; margin-bottom: 8px; line-height: 1.4; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
        
        .req-meta { display: flex; gap: 12px; font-size: 11px; color: var(--text-muted); }
        .badge { background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-weight: 500; }

        /* Main View */
        .main { flex: 1; display: flex; flex-direction: column; background: #fff; overflow: hidden; position: relative; }
        .empty-state { flex: 1; display: flex; align-items: center; justify-content: center; color: var(--text-muted); flex-direction: column; }
        
        .trace-detail { display: flex; flex-direction: column; height: 100%; }
        .detail-header { padding: 20px; border-bottom: 1px solid var(--border); background: #fff; }
        .dh-title { font-size: 18px; font-weight: 600; margin-bottom: 8px; }
        .dh-meta { display: flex; gap: 20px; font-size: 12px; color: var(--text-muted); font-family: monospace; }
        
        .waterfall-scroll { flex: 1; overflow-y: auto; padding: 20px; position: relative; background: #fafafa; }
        .waterfall-canvas { position: relative; min-height: 200px; }
        
        .span-row { position: relative; height: 32px; margin-bottom: 4px; }
        .span-bar { position: absolute; height: 24px; border-radius: 4px; font-size: 11px; color: white; display: flex; align-items: center; padding: 0 8px; overflow: hidden; white-space: nowrap; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,0.05); transition: opacity 0.2s; }
        .span-bar:hover { opacity: 0.9; z-index: 10; }
        
        .bg-ai { background: #3b82f6; }
        .bg-tool { background: #14b8a6; }
        .bg-db { background: #f59e0b; }
        .bg-err { background: #ef4444; } /* RED for Errors */
        .bg-def { background: #9ca3af; }

        /* Tabs */
        .screen { display: none; height: 100%; width: 100%; overflow-y: auto; box-sizing: border-box; }
        .tab-nav { display: flex; gap: 24px; font-size: 13px; font-weight: 500; height: 100%; }
        .nav-item { display: flex; align-items: center; cursor: pointer; border-bottom: 2px solid transparent; color: var(--text-muted); transition: all 0.2s; padding: 0 4px; }
        .nav-item:hover { color: var(--primary); }
        .nav-item.active { border-bottom-color: var(--primary); color: var(--primary); }

        /* Health & Metrics Screen */
        .health-screen { padding: 40px; background: #fafafa; }
        .metric-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }
        .m-card { background: white; padding: 24px; border-radius: 8px; border: 1px solid var(--border); box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
        .m-title { color: var(--text-muted); font-size: 13px; font-weight: 500; margin-bottom: 8px; text-transform: uppercase; }
        .m-value { font-size: 28px; font-weight: 700; color: var(--text); }
        .m-card.bad .m-value { color: var(--error); }
        
        .section-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; color: var(--text); }
        
        table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); font-size: 13px; }
        th { text-align: left; padding: 12px 16px; background: #f9fafb; font-weight: 600; color: var(--text-muted); border-bottom: 1px solid var(--border); }
        td { padding: 12px 16px; border-bottom: 1px solid var(--border); vertical-align: top; }
        tr:last-child td { border-bottom: none; }
        
        .reason-tag { display: inline-block; background: #fef2f2; color: #b91c1c; padding: 2px 8px; border-radius: 12px; font-size: 11px; margin-right: 4px; border: 1px solid #fecaca; margin-bottom: 4px; }

        /* Health Grid */
        .health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
        .health-card { background: white; padding: 20px; border-radius: 8px; border: 1px solid var(--border); border-top-width: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .health-card.ok { border-top-color: var(--success); }
        .health-card.fail { border-top-color: var(--error); }
        
        /* Drawer */
        .drawer { position: fixed; right: -450px; top: 57px; bottom: 0; width: 450px; background: white; border-left: 1px solid var(--border); box-shadow: -4px 0 15px rgba(0,0,0,0.05); transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1); z-index: 100; display: flex; flex-direction: column; }
        .drawer.open { right: 0; }
        .drawer-header { padding: 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #fff; }
        .drawer-content { flex: 1; overflow-y: auto; padding: 20px; background: #f9fafb; }
        #drawerPre { background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 6px; font-size: 11px; overflow-x: auto; font-family: 'Menlo', monospace; }
        .close-drawer { cursor: pointer; font-size: 20px; color: var(--text-muted); width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border-radius: 4px; }
        
        .hidden { display: none !important; }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">🚀 Agent Flight Recorder</div>
        
        <div class="tab-nav">
            <div id="tab-traces" class="nav-item active" onclick="switchTab('traces')">Trace Waterfall</div>
            <div id="tab-metrics" class="nav-item" onclick="switchTab('metrics')">Metrics & Insights</div>
            <div id="tab-health" class="nav-item" onclick="switchTab('health')">System Health</div>
        </div>

        <div style="display:flex; gap:10px">
            <button onclick="refreshCurrent()" style="padding:6px 12px; border:1px solid #d1d5db; border-radius:6px; background:white; font-size:13px; cursor:pointer">Refresh</button>
        </div>
    </div>

    <!-- Trace Screen -->
    <div class="layout screen" id="view-traces" style="display:flex">
        <div class="sidebar">
            <div class="sidebar-header">
                <span>RECENT REQUESTS</span>
                <span id="trace-count">0</span>
            </div>
            <div class="request-list" id="reqList"></div>
        </div>

        <div class="main">
            <div id="emptyState" class="empty-state">
                <div style="font-size:40px; margin-bottom:10px">👋</div>
                <div>Select a request to view details</div>
            </div>

            <div id="detailView" class="trace-detail hidden">
                <div class="detail-header">
                    <div class="dh-title" id="dTitle">Query...</div>
                    <div class="dh-meta">
                        <span id="dId">ID</span>
                        <span id="dTime">Time</span>
                        <span id="dDur">Duration</span>
                    </div>
                </div>
                
                <div class="waterfall-scroll">
                    <div class="waterfall-canvas" id="waterfall"></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Health Screen -->
    <div class="screen health-screen" id="view-health">
        <div style="max-width: 1000px; margin: 0 auto;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:24px">
                <div>
                    <h2 class="section-title" style="margin:0">Component Health Status</h2>
                    <div style="color:var(--text-muted); font-size:13px; margin-top:4px">Real-time integration tests</div>
                </div>
                <button onclick="runHealthChecks()" style="background:var(--primary); color:white; border:none; padding:8px 16px; border-radius:6px; font-weight:500; cursor:pointer">Run Integration Tests</button>
            </div>
            
            <div class="health-grid" id="healthGrid">
                <div style="grid-column: 1/-1; padding:40px; text-align:center; color:var(--text-muted); border: 2px dashed var(--border); border-radius:8px;">
                    Click "Run Integration Tests" to start probing.
                </div>
            </div>
        </div>
    </div>

    <!-- Metrics Screen -->
    <div class="screen health-screen" id="view-metrics">
        <div style="max-width: 1000px; margin: 0 auto;">
            <h2 class="section-title">System Metrics (Last 60 Traces)</h2>
            <div class="metric-cards">
                <div class="m-card">
                    <div class="m-title">Total Requests</div>
                    <div class="m-value" id="mTotal">-</div>
                </div>
                <div class="m-card">
                    <div class="m-title">Error Rate</div>
                    <div class="m-value" id="mRate">-</div>
                </div>
                <div class="m-card">
                    <div class="m-title">Failed Requests</div>
                    <div class="m-value" id="mCount">-</div>
                </div>
            </div>

            <h2 class="section-title">Insights: Failing Components</h2>
            <table id="hotspotsTable">
                <thead>
                    <tr>
                        <th style="width:200px">Component / Tool</th>
                        <th style="width:100px">Failures</th>
                        <th>Top Error Reasons</th>
                    </tr>
                </thead>
                <tbody id="hotspotsBody">
                    <tr><td colspan="3" style="text-align:center; color:#999">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- Drawer -->
    <div class="drawer" id="attrDrawer">
        <div class="drawer-header">
            <h3 style="margin:0; font-size:14px;">Span Details</h3>
            <div class="close-drawer" onclick="closeDrawer()">&times;</div>
        </div>
        <div class="drawer-content" id="drawerContent"></div>
    </div>

    <script>
        let currentTab = 'traces';
        let traceGroups = [];

        // Initialization
        window.switchTab = switchTab;
        window.refreshCurrent = refreshCurrent;
        window.runHealthChecks = runHealthChecks;
        window.closeDrawer = closeDrawer;
        
        loadTraces();

        function switchTab(tab) {
            currentTab = tab;
            // Nav
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.getElementById(`tab-${tab}`).classList.add('active');
            
            // Screens
            document.querySelectorAll('.screen').forEach(el => el.style.display = 'none');
            const view = document.getElementById(`view-${tab}`);
            
            if (tab === 'traces') {
                view.style.display = 'flex';
                loadTraces();
            } else {
                view.style.display = 'block';
                if (tab === 'metrics') loadMetrics();
            }
        }

        function refreshCurrent() {
            if (currentTab === 'traces') loadTraces();
            else if (currentTab === 'metrics') loadMetrics();
            else runHealthChecks();
        }

        // --- Metrics & Insights ---
        async function loadMetrics() {
            try {
                const res = await fetch('/diagnostics/metrics?window=60');
                const data = await res.json();
                
                // Big Numbers
                document.getElementById('mTotal').innerText = data.metrics.total_requests;
                document.getElementById('mCount').innerText = data.metrics.error_count;
                
                const rateEl = document.getElementById('mRate');
                const rate = (data.metrics.error_rate * 100).toFixed(1) + '%';
                rateEl.innerText = rate;
                if(data.metrics.error_rate > 0.1) rateEl.parentElement.classList.add('bad');
                else rateEl.parentElement.classList.remove('bad');

                // Insights Table
                const tbody = document.getElementById('hotspotsBody');
                tbody.innerHTML = '';
                
                if (data.metrics.error_count === 0) {
                    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; padding:30px; color:#999">✅ No errors detected in the last window.</td></tr>';
                    return;
                }

                if (data.insights && data.insights.hotspots) {
                    data.insights.hotspots.forEach(h => {
                        const tr = document.createElement('tr');
                        
                        let reasonsHtml = '';
                        h.top_reasons.forEach(r => {
                            reasonsHtml += `<span class="reason-tag">${escapeHtml(r)}</span>`;
                        });

                        tr.innerHTML = `
                            <td style="font-weight:600">${escapeHtml(h.name)}</td>
                            <td>${h.count}</td>
                            <td>${reasonsHtml}</td>
                        `;
                        tbody.appendChild(tr);
                    });
                }
            } catch (e) {
                console.error(e);
            }
        }

        // --- Health ---
        async function runHealthChecks() {
            const grid = document.getElementById('healthGrid');
            grid.innerHTML = '<div style="padding:20px; text-align:center">Running integration tests...</div>';
            
            try {
                const res = await fetch('/diagnostics/run', {method: 'POST'});
                const results = await res.json();
                grid.innerHTML = '';
                
                results.forEach(r => {
                    const isOk = r.status === 'ok';
                    const cls = isOk ? 'ok' : 'fail';
                    const icon = isOk ? 'Active' : 'Failed';
                    
                    const el = document.createElement('div');
                    el.className = `health-card ${cls}`;
                    el.innerHTML = `
                        <div style="display:flex; justify-content:space-between; margin-bottom:10px">
                            <span style="font-weight:600; font-size:14px">${escapeHtml(r.component)}</span>
                            <span style="font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px" 
                                  class="${isOk ? 'bg-tool' : 'bg-err'}" style="color:white; opacity:0.8">${icon}</span>
                        </div>
                        <div style="font-size:24px; font-weight:700; margin-bottom:4px">${r.latency_ms.toFixed(0)}<span style="font-size:12px; font-weight:400; color:#999; margin-left:4px">ms</span></div>
                        ${!isOk && r.message ? `<div style="font-size:12px; color:var(--error); margin-top:8px">${escapeHtml(r.message)}</div>` : ''}
                    `;
                    grid.appendChild(el);
                });
            } catch (e) {
                grid.innerHTML = `<div style="color:red">Failed to run checks: ${e}</div>`;
            }
        }

        // --- Traces ---
        async function loadTraces() {
            const list = document.getElementById('reqList');
            try {
                const res = await fetch('/diagnostics/traces?limit=100');
                if(!res.ok) throw new Error("API " + res.status);
                traceGroups = await res.json();
                renderTraceList(list);
            } catch (e) {
                list.innerHTML = `<div style="padding:20px; color:red">Error: ${e}</div>`;
            }
        }

        function renderTraceList(list) {
            list.innerHTML = '';
            document.getElementById('trace-count').innerText = traceGroups.length;
            
            traceGroups.forEach((g, idx) => {
                const el = document.createElement('div');
                el.className = `req-card ${g.status === 'ERR' ? 'error' : ''}`;
                el.onclick = () => selectTrace(idx);
                
                const time = new Date(g.start_time).toLocaleTimeString();
                const intent = extractUserIntent(g.root);
                
                el.innerHTML = `
                    <div class="req-top">
                        <span class="req-status ${g.status === 'ERR' ? 'err' : 'ok'}"></span>
                        <span class="req-time">${time}</span>
                    </div>
                    <div class="req-query">${escapeHtml(intent)}</div>
                    <div class="req-meta">
                         <span class="badge">${(g.total_duration_ms/1000).toFixed(1)}s</span>
                         <span class="badge">${g.spans.length} spans</span>
                    </div>
                `;
                list.appendChild(el);
            });
        }

        function selectTrace(idx) {
             const g = traceGroups[idx];
             if(!g) return;
             
             document.getElementById('emptyState').classList.add('hidden');
             document.getElementById('detailView').classList.remove('hidden');
             
             document.getElementById('dTitle').innerText = extractUserIntent(g.root);
             document.getElementById('dId').innerText = g.trace_id;
             document.getElementById('dDur').innerText = `${g.total_duration_ms.toFixed(0)} ms`;
             document.getElementById('dTime').innerText = new Date(g.start_time).toLocaleString();

             renderWaterfall(g);
        }

        function renderWaterfall(g) {
            const container = document.getElementById('waterfall');
            container.innerHTML = '';
            
            const totalDur = Math.max(g.total_duration_ms, 1);
            const baseTime = new Date(g.start_time).getTime();

            g.spans.forEach(span => {
                const start = new Date(span.start_time).getTime();
                const offset = start - baseTime;
                
                // Colors
                let bg = 'bg-def';
                const name = (span.name || '').toLowerCase();
                const type = (span.attributes?.type);

                if (span.status === 'ERROR' || span.status === 'fail') bg = 'bg-err'; // Explicit Error Logic
                else if (name.includes('completion') || type === 'ai') bg = 'bg-ai';
                else if (name.includes('tool') || span.attributes?.['tool.name']) bg = 'bg-tool';
                else if (name.includes('postgres') || name.includes('db')) bg = 'bg-db';

                const left = (offset / totalDur) * 100;
                const width = Math.max((span.duration_ms / totalDur) * 100, 0.5);
                
                const row = document.createElement('div');
                row.className = 'span-row';
                
                const bar = document.createElement('div');
                bar.className = `span-bar ${bg}`;
                bar.style.left = `${left}%`;
                bar.style.width = `${width}%`;
                bar.title = `${span.name} (Status: ${span.status})`;
                
                // Formatted name
                let label = span.name;
                if(label.startsWith('executor.step_run')) label = `Step ${span.attributes?.step || '?'}`;
                if(label.includes('tool.call.')) label = `Tool: ${span.attributes?.['tool.name'] || label}`;

                bar.innerText = label;
                bar.onclick = () => showDetails(span);
                
                row.appendChild(bar);
                container.appendChild(row);
            });
        }

        function showDetails(span) {
             const drawer = document.getElementById('attrDrawer');
             const content = document.getElementById('drawerContent');
             
             content.innerHTML = `
                <div style="font-weight:600; font-size:16px; margin-bottom:8px">${escapeHtml(span.name)}</div>
                <div style="margin-bottom:16px">
                    <span class="badge ${span.status==='ERROR' ? 'bg-err' : 'bg-ai'}" style="color:white">${span.status}</span>
                    <span class="badge">${span.duration_ms.toFixed(1)} ms</span>
                </div>
                <pre id="drawerPre">${JSON.stringify(span, null, 2)}</pre>
             `;
             
             drawer.classList.add('open');
        }

        function closeDrawer() {
             document.getElementById('attrDrawer').classList.remove('open');
        }

        function extractUserIntent(span) {
            // ... (Same reuse logic as before) ...
            if (!span || !span.attributes) return "System Action";
            const body = span.attributes['http.request.body'] || span.attributes['body'];
            if (body) {
                try {
                     const obj = typeof body === 'string' ? JSON.parse(body) : body;
                     if(obj.messages) {
                         const user = obj.messages.find(m => m.role==='user');
                         if(user) return user.content;
                     }
                     if(obj.prompt) return obj.prompt;
                } catch(e) { if(typeof body==='string' && body.length>4) return body; }
            }
            return span.name;
        }

        function escapeHtml(str) {
            if(!str) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
    </script>
</body>
</html>
"""
    return html_content
