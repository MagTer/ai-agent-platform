# ruff: noqa: E501
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from core.core.config import Settings, get_settings
from core.diagnostics.service import DiagnosticsService, TestResult, TraceSpan

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def get_diagnostics_service(
    settings: Settings = Depends(get_settings),
) -> DiagnosticsService:
    return DiagnosticsService(settings)


@router.get("/traces", response_model=list[TraceSpan])
async def get_traces(
    limit: int = 500, service: DiagnosticsService = Depends(get_diagnostics_service)
) -> list[TraceSpan]:
    return service.get_recent_traces(limit)


@router.post("/run", response_model=list[TestResult])
async def run_diagnostics(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> list[TestResult]:
    return await service.run_diagnostics()


@router.get("/", response_class=HTMLResponse)
async def diagnostics_dashboard(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> str:
    # MVP Dashboard HTML
    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agent Diagnostics</title>
    <style>
        :root { --primary: #3498db; --bg: #f4f6f8; --white: #fff; --border: #e1e4e8; --text: #24292e; --text-muted: #586069; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        
        .header { background: #24292e; color: white; padding: 0 20px; height: 50px; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }
        .header h1 { font-size: 16px; margin: 0; font-weight: 600; display: flex; align-items: center; gap: 8px; }
        
        .main-layout { display: flex; flex: 1; overflow: hidden; }
        
        /* Master View: Request List */
        .master-view { width: 400px; border-right: 1px solid var(--border); background: white; display: flex; flex-direction: column; flex-shrink: 0; }
        .master-header { padding: 10px; border-bottom: 1px solid var(--border); background: #f6f8fa; font-size: 12px; font-weight: 600; color: var(--text-muted); display: flex; justify-content: space-between; }
        .request-list { flex: 1; overflow-y: auto; }
        
        .request-item { padding: 12px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.1s; border-left: 3px solid transparent; }
        .request-item:hover { background: #f6f8fa; }
        .request-item.active { background: #e6f7ff; border-left-color: var(--primary); }
        
        .req-meta { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-bottom: 4px; }
        .req-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .req-info { font-size: 11px; color: var(--text-muted); display: flex; gap: 8px; }
        
        /* Detail View: Waterfall */
        .detail-view { flex: 1; display: flex; flex-direction: column; background: white; overflow: hidden; }
        .detail-header { padding: 15px; border-bottom: 1px solid var(--border); background: #fff; flex-shrink: 0; }
        .trace-id { font-family: monospace; font-size: 12px; color: var(--text-muted); background: #f6f8fa; padding: 2px 6px; border-radius: 4px; }
        
        .waterfall-container { flex: 1; overflow-y: auto; padding: 20px; position: relative; }
        .timeline-ruler { height: 20px; border-bottom: 1px solid var(--border); margin-bottom: 10px; position: sticky; top: 0; background: white; z-index: 10; display: flex; }
        .tick { position: absolute; font-size: 10px; color: var(--text-muted); padding-left: 2px; border-left: 1px solid #eee; height: 100%; top: 0; }
        
        .span-row { position: relative; height: 28px; margin-bottom: 4px; display: flex; align-items: center; }
        .span-bar { position: absolute; height: 20px; border-radius: 3px; min-width: 2px; display: flex; align-items: center; padding: 0 8px; font-size: 11px; color: white; white-space: nowrap; overflow: hidden; cursor: pointer; transition: opacity 0.2s; }
        .span-bar:hover { opacity: 0.9; box-shadow: 0 2px 4px rgba(0,0,0,0.1); z-index: 5; }
        
        /* Colors */
        .bg-llm { background: #3498db; } /* Blue */
        .bg-tool { background: #2ecc71; } /* Green */
        .bg-db { background: #f1c40f; color: black; } /* Yellow */
        .bg-other { background: #95a5a6; } /* Grey */
        .bg-error { background: #e74c3c; } /* Red */

        /* Panel Logic (Tabs) */
        .tab-nav { display: flex; gap: 20px; }
        .nav-item { cursor: pointer; opacity: 0.7; font-size: 13px; font-weight: 500; }
        .nav-item:hover { opacity: 1; }
        .nav-item.active { opacity: 1; border-bottom: 2px solid white; padding-bottom: 14px; }
        
        .screen { display: none; height: 100%; }
        .screen.active { display: flex; }

        /* Health Grid Styles */
        .health-screen { padding: 20px; overflow-y: auto; width: 100%; box-sizing: border-box; }
        .health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 20px; }
        .health-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-top: 4px solid #ddd; }
        .health-card.ok { border-top-color: #2ecc71; }
        .health-card.fail { border-top-color: #e74c3c; }
        .health-stat { font-size: 24px; font-weight: bold; margin: 10px 0; }
        .btn-primary { background: var(--primary); color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
        .btn-primary:hover { opacity: 0.9; }

        /* Drawer for Details */
        .drawer { position: fixed; right: -400px; top: 50px; bottom: 0; width: 400px; background: white; border-left: 1px solid var(--border); box-shadow: -2px 0 10px rgba(0,0,0,0.05); transition: right 0.3s; z-index: 100; overflow-y: auto; padding: 20px; box-sizing: border-box; }
        .drawer.open { right: 0; }
        .drawer pre { background: #f6f8fa; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 11px; font-family: monospace; }
        .close-drawer { float: right; cursor: pointer; font-size: 20px; color: var(--text-muted); }
    </style>
</head>
<body>
    <div class="header">
        <h1>🛠️ Agent Flight Recorder</h1>
        <div class="tab-nav">
            <div class="nav-item active" onclick="switchScreen('traces')">Trace Waterfall</div>
            <div class="nav-item" onclick="switchScreen('health')">System Health</div>
        </div>
        <button class="btn-primary" style="font-size:12px;" onclick="loadData()">Refresh</button>
    </div>

    <!-- Trace Screen -->
    <div id="traces" class="screen main-layout active">
        <!-- Sidebar -->
        <div class="master-view">
            <div class="master-header">
                <span>REQUESTS</span>
                <span id="req-count">0 traces</span>
            </div>
            <div class="request-list" id="requestList">
                <!-- Items injected here -->
                <div style="padding:20px; text-align:center; color:#999">Loading traces...</div>
            </div>
        </div>

        <!-- Main Content -->
        <div class="detail-view">
            <div class="detail-header" id="detailHeader" style="display:none">
                <h2 style="margin:0 0 5px 0; font-size:18px;" id="traceName">Trace Name</h2>
                <div style="display:flex; gap:10px; align-items:center;">
                    <span class="trace-id" id="traceId">ID</span>
                    <span style="font-size:12px; color:#586069" id="traceDuration">0ms</span>
                    <span style="font-size:12px; color:#586069" id="traceTime">Timestamp</span>
                </div>
            </div>
            
            <div class="waterfall-container" id="waterfallDetails">
                <div style="text-align:center; margin-top:50px; color:#999">Select a request to view details</div>
            </div>
        </div>
    </div>

    <!-- Health Screen -->
    <div id="health" class="screen health-screen">
        <div style="margin-bottom:20px; display:flex; justify-content:space-between;">
            <h2>System Health</h2>
            <button class="btn-primary" onclick="runHealthChecks()" id="btnHealh">Run Checks</button>
        </div>
        <div class="health-grid" id="healthGrid">
            <div style="color:#777">Click "Run Checks" to probe system status.</div>
        </div>
    </div>

    <!-- Drawer -->
    <div class="drawer" id="attrDrawer">
        <span class="close-drawer" onclick="closeDrawer()">&times;</span>
        <h3 id="drawerTitle">Span Details</h3>
        <div id="drawerContent"></div>
    </div>

    <script>
        let allSpans = [];
        let groupedTraces = [];

        // --- Init ---
        loadData();

        function switchScreen(id) {
            document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.getElementById(id).classList.add('active');
            event.target.classList.add('active');
        }

        async function loadData() {
            try {
                const res = await fetch('/diagnostics/traces?limit=500');
                allSpans = await res.json();
                processTraces();
                renderRequestList();
            } catch (e) {
                console.error("Failed to load", e);
            }
        }

        // --- Logic: Grouping ---
        function processTraces() {
            const groups = {};
            
            // 1. Group by trace_id
            allSpans.forEach(span => {
                const tid = span.trace_id || 'unknown';
                if (!groups[tid]) groups[tid] = [];
                groups[tid].push(span);
            });

            // 2. Identify Root & Sort
            groupedTraces = Object.values(groups).map(spans => {
                // Determine root: explicit parent=None OR earliest start time
                spans.sort((a, b) => new Date(a.start_time) - new Date(b.start_time));
                const root = spans.find(s => !s.parent_id) || spans[0];
                
                // Calculate total duration if root doesn't have it explicitly or trusting root
                const traceStart = new Date(root.start_time).getTime();
                const traceEnd = Math.max(...spans.map(s => new Date(s.start_time).getTime() + (s.duration_ms || 0)));
                const totalDuration = traceEnd - traceStart;

                // Infer snippet
                let snippet = root.name;
                const body = root.attributes['http.request.body'] || root.attributes['prompt'];
                if (body) snippet = body.slice(0, 50) + (body.length > 50 ? '...' : '');

                return {
                    id: root.trace_id,
                    root: root,
                    spans: spans,
                    startTime: traceStart,
                    duration: totalDuration || root.duration_ms, // fallback
                    snippet: snippet,
                    status: spans.some(s => s.status === 'ERROR' || s.status === 'fail') ? 'ERR' : 'OK'
                };
            });

            // 3. Sort by Recency (Newest First)
            groupedTraces.sort((a, b) => b.startTime - a.startTime);
        }

        // --- Render: Master List ---
        function renderRequestList() {
            const container = document.getElementById('requestList');
            document.getElementById('req-count').innerText = groupedTraces.length + ' traces';
            container.innerHTML = '';

            groupedTraces.forEach((trace, idx) => {
                const div = document.createElement('div');
                div.className = 'request-item';
                div.onclick = () => selectTrace(idx);
                div.id = 'trace-' + idx;
                
                const timeStr = new Date(trace.startTime).toLocaleTimeString();
                
                div.innerHTML = `
                    <div class="req-meta">
                        <span style="font-weight:bold; color:${trace.status === 'ERR' ? 'red' : 'green'}">${trace.status}</span>
                        <span>${timeStr}</span>
                    </div>
                    <div class="req-title">${escapeHtml(trace.snippet)}</div>
                    <div class="req-info">
                        <span>${trace.duration.toFixed(0)}ms</span>
                        <span>${trace.spans.length} spans</span>
                    </div>
                `;
                container.appendChild(div);
            });
        }

        // --- Render: Waterfall ---
        function selectTrace(idx) {
            document.querySelectorAll('.request-item').forEach(e => e.classList.remove('active'));
            document.getElementById('trace-' + idx).classList.add('active');

            const trace = groupedTraces[idx];
            
            // Update Header
            document.getElementById('detailHeader').style.display = 'block';
            document.getElementById('traceName').innerText = trace.snippet;
            document.getElementById('traceId').innerText = trace.id;
            document.getElementById('traceDuration').innerText = trace.duration.toFixed(0) + 'ms';
            document.getElementById('traceTime').innerText = new Date(trace.startTime).toLocaleString();

            const container = document.getElementById('waterfallDetails');
            container.innerHTML = ''; // clear

            // Render Ruler (approx)
            const ruler = document.createElement('div');
            ruler.className = 'timeline-ruler';
            for(let i=0; i<=5; i++) {
                const tick = document.createElement('div');
                tick.className = 'tick';
                tick.style.left = (i * 20) + '%';
                tick.innerText = Math.round((trace.duration / 5) * i) + 'ms';
                ruler.appendChild(tick);
            }
            container.appendChild(ruler);

            // Render Spans
            // Sort by start time for visual cascade
            // Depth/indentation logic is tricky without full tree walk, keeping flat chrono for robustness
            trace.spans.forEach(span => {
                const spanStart = new Date(span.start_time).getTime();
                const offsetMs = spanStart - trace.startTime;
                
                // Percentages
                const left = Math.max(0, (offsetMs / trace.duration) * 100);
                const width = Math.max(0.5, (span.duration_ms / trace.duration) * 100); // min 0.5% visibility

                // Color Logic
                let bgClass = 'bg-other';
                const name = span.name.toLowerCase();
                if (name.includes('litellm') || name.includes('completion')) bgClass = 'bg-llm';
                else if (name.includes('tool') || span.attributes['tool.name']) bgClass = 'bg-tool';
                else if (name.includes('postgres') || name.includes('db')) bgClass = 'bg-db';
                if (span.status === 'ERROR' || span.status === 'fail') bgClass = 'bg-error';

                const row = document.createElement('div');
                row.className = 'span-row';
                
                const bar = document.createElement('div');
                bar.className = `span-bar ${bgClass}`;
                bar.style.left = left + '%';
                bar.style.width = width + '%';
                bar.innerText = `${span.name} (${span.duration_ms.toFixed(0)}ms)`;
                bar.onclick = (e) => showDetails(span, e);

                row.appendChild(bar);
                container.appendChild(row);
            });
        }

        function showDetails(span, event) {
            event.stopPropagation();
            const drawer = document.getElementById('attrDrawer');
            document.getElementById('drawerTitle').innerText = span.name;
            document.getElementById('drawerContent').innerHTML = `
                <p><strong>Trace ID:</strong> ${span.trace_id}</p>
                <p><strong>Span ID:</strong> ${span.span_id}</p>
                <p><strong>Parent ID:</strong> ${span.parent_id || 'None'}</p>
                <p><strong>Status:</strong> ${span.status}</p>
                <p><strong>Start:</strong> ${span.start_time}</p>
                <p><strong>Duration:</strong> ${span.duration_ms} ms</p>
                <h4>Attributes</h4>
                <pre>${JSON.stringify(span.attributes, null, 2)}</pre>
            `;
            drawer.classList.add('open');
        }

        function closeDrawer() {
            document.getElementById('attrDrawer').classList.remove('open');
        }

        // --- Health Logic ---
        async function runHealthChecks() {
            const btn = document.getElementById('btnHealh');
            const grid = document.getElementById('healthGrid');
            btn.disabled = true;
            btn.innerText = "Running...";
            grid.innerHTML = '<div style="padding:20px;">Probing services...</div>';

            try {
                const res = await fetch('/diagnostics/run', { method: 'POST' });
                const results = await res.json();
                grid.innerHTML = '';
                
                results.forEach(r => {
                    const card = document.createElement('div');
                    card.className = `health-card ${r.status}`;
                    card.innerHTML = `
                        <h3>${r.component}</h3>
                        <div class="health-stat" style="color:${r.status==='ok'?'#2ecc71':'#e74c3c'}">${r.status.toUpperCase()}</div>
                        <div style="font-size:12px; color:#999">${r.latency_ms.toFixed(0)} ms</div>
                        ${r.message ? `<div style="color:#e74c3c; margin-top:5px; font-size:12px">${r.message}</div>` : ''}
                    `;
                    grid.appendChild(card);
                });
            } catch (e) {
                grid.innerHTML = `<div style="color:red">Error: ${e}</div>`;
            } finally {
                btn.disabled = false;
                btn.innerText = "Run Checks";
            }
        }

        function escapeHtml(text) {
            if (!text) return "";
            return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }
    </script>
</body>
</html>
"""
    return html_content
