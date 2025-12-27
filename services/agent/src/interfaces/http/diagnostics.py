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
        .layout { display: flex; flex: 1; overflow: hidden; flex-direction: row; } /* Explicit row */
        
        /* Sidebar */
        .sidebar { width: var(--sidebar-w); background: #fff; border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
        .sidebar-header { padding: 12px; border-bottom: 1px solid var(--border); background: #f9fafb; font-size: 12px; font-weight: 600; color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; }
        .request-list { flex: 1; overflow-y: auto; }
        
        .req-card { padding: 16px; border-bottom: 1px solid var(--border); cursor: pointer; transition: all 0.1s; border-left: 3px solid transparent; }
        .req-card:hover { background: #f9fafb; }
        .req-card.active { background: #eff6ff; border-left-color: var(--primary); }
        
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
        
        /* Colors */
        .bg-ai { background: #3b82f6; }
        .bg-tool { background: #14b8a6; }
        .bg-db { background: #f59e0b; }
        .bg-err { background: #ef4444; }
        .bg-def { background: #9ca3af; }

        /* Health Grid Styles */
        .health-screen { padding: 20px; overflow-y: auto; width: 100%; box-sizing: border-box; display: none; }
        .health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; margin-top: 20px; }
        
        .health-card { background: white; padding: 20px; border-radius: 8px; border: 1px solid var(--border); border-top-width: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); transition: transform 0.1s; }
        .health-card:hover { transform: translateY(-2px); box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
        
        .health-card.ok { border-top-color: var(--success); }
        .health-card.fail { border-top-color: var(--error); }
        
        .hc-header { display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px; }
        .hc-title { font-weight: 600; font-size: 14px; margin: 0; }
        .hc-badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; font-weight: bold; text-transform: uppercase; }
        .hc-badge.ok { background: #d1fae5; color: #065f46; }
        .hc-badge.fail { background: #fee2e2; color: #991b1b; }
        
        .hc-stat { font-size: 20px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
        .hc-meta { font-size: 11px; color: var(--text-muted); }
        .hc-error { color: var(--error); font-size: 12px; margin-top: 8px; background: #fef2f2; padding: 8px; border-radius: 4px; border: 1px solid #fecaca; }

        /* Drawer for Details */
        .drawer { position: fixed; right: -450px; top: 57px; bottom: 0; width: 450px; background: white; border-left: 1px solid var(--border); box-shadow: -4px 0 15px rgba(0,0,0,0.05); transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1); z-index: 100; display: flex; flex-direction: column; }
        .drawer.open { right: 0; }
        
        .drawer-header { padding: 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #fff; }
        .drawer-content { flex: 1; overflow-y: auto; padding: 20px; background: #f9fafb; }
        .drawer pre { background: #1e293b; color: #e2e8f0; padding: 12px; border-radius: 6px; font-size: 11px; overflow-x: auto; font-family: 'Menlo', monospace; }
        .close-drawer { cursor: pointer; font-size: 20px; color: var(--text-muted); width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; border-radius: 4px; }
        .close-drawer:hover { background: #f3f4f6; }
        
        .screen { display: none; height: 100%; } /* Removed flex-direction column force */
        
        .tab-nav { display: flex; gap: 24px; font-size: 13px; font-weight: 500; height: 100%; }
        .nav-item { display: flex; align-items: center; cursor: pointer; border-bottom: 2px solid transparent; color: var(--text-muted); transition: all 0.2s; padding: 0 4px; }
        .nav-item:hover { color: var(--primary); }
        .nav-item.active { border-bottom-color: var(--primary); color: var(--primary); }

        .hidden { display: none !important; }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">🚀 Agent Flight Recorder</div>
        
        <div class="tab-nav">
            <div id="tab-traces" class="nav-item active" onclick="switchTab('traces')">Trace Waterfall</div>
            <div id="tab-health" class="nav-item" onclick="switchTab('health')">System Health</div>
        </div>

        <div style="display:flex; gap:10px">
            <button class="btn btn-primary" onclick="refreshCurrent()">Refresh</button>
        </div>
    </div>

    <!-- Trace Screen -->
    <div class="layout screen" id="view-traces" style="display:flex">
        <div class="sidebar">
            <div class="sidebar-header">
                <span>RECENT REQUESTS</span>
                <span id="trace-count">0</span>
            </div>
            <div class="request-list" id="reqList">
                <div style="padding:20px; text-align:center; color:#999">Loading...</div>
            </div>
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

                <div class="props-panel" id="propsPanel" style="display:none"> <!-- Keeping raw HTML logic simpler -->
                    <div class="props-header">
                        <span>Properties</span>
                        <span style="cursor:pointer" onclick="toggleProps()">▼</span>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Health Screen -->
    <div class="screen health-screen" id="view-health">
        <div style="max-width: 1200px; margin: 0 auto; width: 100%;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <h2 style="margin:0; font-size:20px;">System Health</h2>
                    <div style="color:var(--text-muted); font-size:13px; margin-top:4px">Run integration tests to verify platform components.</div>
                </div>
                <button class="btn btn-primary" id="btnRunHealth" onclick="runHealthChecks()">Run Integration Tests</button>
            </div>
            
            <div class="health-grid" id="healthGrid">
                <div style="grid-column: 1/-1; padding:40px; text-align:center; color:var(--text-muted); border: 2px dashed var(--border); border-radius:8px;">
                    Click "Run Integration Tests" to start probing.
                </div>
            </div>
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
        let traceGroups = [];
        let currentTab = 'traces';

        // --- Init ---
        // Global scope helpers for onclick
        window.switchTab = switchTab;
        window.refreshCurrent = refreshCurrent;
        window.runHealthChecks = runHealthChecks;
        window.selectTrace = selectTrace;
        window.showProps = showProps;
        window.closeDrawer = closeDrawer;
        window.toggleProps = toggleProps;

        // Auto Load
        loadTraces();

        function switchTab(tab) {
            currentTab = tab;
            // Nav Updates
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            document.getElementById(`tab-${tab}`).classList.add('active');
            
            // View Logic: Handle Display Modes
            document.querySelectorAll('.screen').forEach(el => el.style.display = 'none');
            
            const view = document.getElementById(`view-${tab}`);
            if(tab === 'traces') {
                view.style.display = 'flex'; // Traces needs flex row
            } else {
                view.style.display = 'block'; // Health needs block
            }
        }

        function refreshCurrent() {
            if(currentTab === 'traces') loadTraces();
            else runHealthChecks();
        }

        // --- Helpers ---
        function extractUserIntent(span) {
            if (!span || !span.attributes) return "System Action";
            
            // 1. Try body parsing
            const body = span.attributes['http.request.body'] || span.attributes['body'] || span.attributes['payload'];
            if (body) {
                try {
                    let obj = typeof body === 'string' ? JSON.parse(body) : body;
                    // OpenAI Format
                    if (obj.messages && Array.isArray(obj.messages)) {
                        const lastUser = obj.messages.filter(m => m.role === 'user').pop();
                        if (lastUser) return lastUser.content;
                    }
                    // Direct Prompt
                    if (obj.prompt) return obj.prompt;
                    if (obj.query) return obj.query;
                } catch (e) {
                    // Fallback for non-JSON strings that look like questions
                    if (typeof body === 'string' && body.length > 5) return body;
                }
            }
            
            // 2. Fallback to name/method
            return span.name || "Unknown Request";
        }

        function formatSpanName(span) {
            if (!span) return 'Unknown';
            const name = span.name || '';
            const attrs = span.attributes || {};
            
            if (name.startsWith('executor.step_run')) {
                const stepId = attrs['step'] || attrs['step_id'] || '';
                return `👣 Plan Step ${stepId}`;
            }
            if (name.includes('llm.call') || attrs['type'] === 'ai') {
                const model = attrs['model'] || 'AI';
                return `🧠 AI Generation (${model})`;
            }
            if (name.includes('tool') || attrs['tool.name']) {
                return `🛠️ Tool: ${attrs['tool.name'] || name}`;
            }
            if (name.includes('postgres') || name.includes('db')) {
                return `💾 Database`;
            }
            return name;
        }

        // --- Trace Logic ---
        async function loadTraces() {
            const list = document.getElementById('reqList');
            if(list) list.innerHTML = '<div style="padding:20px; text-align:center; color:#999">Loading...</div>';
            
            try {
                const res = await fetch('/diagnostics/traces?limit=1000');
                if(!res.ok) throw new Error("API " + res.status);
                traceGroups = await res.json();
                renderList();
            } catch (e) {
                if(list) list.innerHTML = `<div style="padding:20px; color:red">Error: ${e}</div>`;
            }
        }

        function renderList() {
            const list = document.getElementById('reqList');
            if(!list) return;
            const countEl = document.getElementById('trace-count');
            if(countEl) countEl.innerText = traceGroups.length;
            
            list.innerHTML = '';

            traceGroups.forEach((g, idx) => {
                const el = document.createElement('div');
                el.className = 'req-card';
                el.id = `card-${idx}`;
                el.onclick = () => selectTrace(idx);
                
                const time = new Date(g.start_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
                const dur = (g.total_duration_ms / 1000).toFixed(2) + 's';
                const statusType = g.status === 'ERR' ? 'err' : 'ok';
                
                // NEW: Use intent extractor
                const intent = extractUserIntent(g.root);
                
                el.innerHTML = `
                    <div class="req-top">
                        <span class="req-status ${statusType}"></span>
                        <span class="req-time">${time}</span>
                    </div>
                    <div class="req-query" style="font-weight:600; color:#1f2937">${escapeHtml(intent)}</div>
                    <div class="req-meta">
                        <span class="badge">${dur}</span>
                        <span class="badge">${g.spans.length} spans</span>
                    </div>
                `;
                list.appendChild(el);
            });
        }

        function selectTrace(idx) {
            document.querySelectorAll('.req-card').forEach(c => c.classList.remove('active'));
            const card = document.getElementById(`card-${idx}`);
            if(card) card.classList.add('active');
            
            const g = traceGroups[idx];
            if(!g) return;

            document.getElementById('emptyState').classList.add('hidden');
            document.getElementById('detailView').classList.remove('hidden');
            
            // NEW: Use intent as title
            document.getElementById('dTitle').innerText = extractUserIntent(g.root);
            document.getElementById('dId').innerText = g.trace_id || 'N/A';
            document.getElementById('dTime').innerText = new Date(g.start_time).toLocaleString();
            document.getElementById('dDur').innerText = `${g.total_duration_ms.toFixed(0)} ms`;
            
            renderWaterfall(g);
        }

        function renderWaterfall(g) {
            const container = document.getElementById('waterfall');
            if(!container) return;
            container.innerHTML = '';
            
            if(!g.spans || g.spans.length === 0) {
                container.innerHTML = '<div style="padding:20px;color:#999">No spans found.</div>';
                return;
            }

            const totalDur = Math.max(g.total_duration_ms, 1);
            const baseTime = new Date(g.start_time).getTime();

            g.spans.forEach(span => {
                const start = new Date(span.start_time).getTime();
                const offset = start - baseTime;
                
                const left = (offset / totalDur) * 100;
                const width = Math.max((span.duration_ms / totalDur) * 100, 0.5);
                
                let bg = 'bg-def';
                const rawName = (span.name || '').toLowerCase();
                const type = (span.attributes && span.attributes['type']) || '';
                
                if (rawName.includes('completion') || rawName.includes('litellm') || type === 'ai') bg = 'bg-ai';
                else if (rawName.includes('tool') || (span.attributes && span.attributes['tool.name'])) bg = 'bg-tool';
                else if (rawName.includes('postgres') || name.includes('db')) bg = 'bg-db';
                if (span.status === 'ERROR' || span.status === 'fail') bg = 'bg-err';

                // NEW: Use formatted name
                const niceName = formatSpanName(span);

                const row = document.createElement('div');
                row.className = 'span-row';
                
                const bar = document.createElement('div');
                bar.className = `span-bar ${bg}`;
                bar.style.left = `${left}%`;
                bar.style.width = `${width}%`;
                bar.innerText = niceName; 
                bar.title = `${niceName} (${span.duration_ms.toFixed(0)}ms)`; // Simple tooltip
                bar.onclick = () => showProps(span); 
                
                row.appendChild(bar);
                container.appendChild(row);
            });
        }

        function showProps(span) {
             const content = document.getElementById('drawerContent');
             if(!content) return;
             
             // NEW: Header uses nice name
             const niceName = formatSpanName(span);
             
             content.innerHTML = `
                <div style="margin-bottom:12px">
                    <div style="font-weight:600; font-size:16px">${escapeHtml(niceName)}</div>
                    <div style="color:#666; font-size:12px; margin-top:4px">ID: ${span.span_id}</div>
                    <div style="color:#999; font-size:11px">Raw: ${escapeHtml(span.name)}</div>
                </div>
                <pre>${JSON.stringify(span, null, 2)}</pre>
             `;
             const drawer = document.getElementById('attrDrawer');
             if(drawer) drawer.classList.add('open');
        }

        function closeDrawer() {
            const drawer = document.getElementById('attrDrawer');
            if(drawer) drawer.classList.remove('open');
        }
        
        function toggleProps() {
             // NOOP - Drawer replaced panel
        }
        
        function escapeHtml(str) {
            if(!str) return '';
            const s = String(str);
            if (s.length > 300) return s.substring(0, 300) + '...'; // Truncate long strings for display
            return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }
    </script>
</body>
</html>
"""
    return html_content
