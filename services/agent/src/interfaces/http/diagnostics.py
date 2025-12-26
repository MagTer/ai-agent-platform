# ruff: noqa: E501
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from core.core.config import Settings, get_settings
from core.diagnostics.service import DiagnosticsService, TestResult, TraceGroup, TraceSpan

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
        .layout { display: flex; flex: 1; overflow: hidden; }
        
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

        /* Properties Panel */
        .props-panel { height: 300px; border-top: 1px solid var(--border); background: #fff; display: flex; flex-direction: column; }
        .props-header { padding: 10px 20px; background: #f9fafb; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 12px; text-transform: uppercase; color: var(--text-muted); display: flex; justify-content: space-between; }
        .props-content { flex: 1; overflow: auto; padding: 0; }
        .props-pre { margin: 0; padding: 20px; font-family: 'Menlo', 'Monaco', monospace; font-size: 12px; line-height: 1.5; color: #374151; }
        
        .btn { border: 1px solid var(--border); background: white; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500; cursor: pointer; transition: background 0.1s; }
        .btn:hover { background: #f3f4f6; }
        .btn-primary { background: var(--primary); color: white; border-color: var(--primary); }
        .btn-primary:hover { background: #1d4ed8; }

        .hidden { display: none !important; }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">🚀 Agent Flight Recorder</div>
        <div style="display:flex; gap:10px">
            <button class="btn" onclick="runHealth()">Check Health</button>
            <button class="btn btn-primary" onclick="loadTraces()">Refresh</button>
        </div>
    </div>

    <div class="layout">
        <!-- Sidebar -->
        <div class="sidebar">
            <div class="sidebar-header">
                <span>RECENT REQUESTS</span>
                <span id="trace-count">0</span>
            </div>
            <div class="request-list" id="reqList">
                <div style="padding:20px; text-align:center; color:#999">Loading...</div>
            </div>
        </div>

        <!-- Main Area -->
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

                <div class="props-panel" id="propsPanel">
                    <div class="props-header">
                        <span>Properties</span>
                        <span style="cursor:pointer" onclick="toggleProps()">▼</span>
                    </div>
                    <div class="props-content">
                        <pre class="props-pre" id="propsPre">Select a span to view attributes.</pre>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let traceGroups = [];

        async function loadTraces() {
            const list = document.getElementById('reqList');
            list.innerHTML = '<div style="padding:20px; text-align:center; color:#999">Loading...</div>';
            
            try {
                const res = await fetch('/diagnostics/traces?limit=1000');
                traceGroups = await res.json();
                renderList();
            } catch (e) {
                list.innerHTML = `<div style="padding:20px; color:red">Error: ${e}</div>`;
            }
        }

        function renderList() {
            const list = document.getElementById('reqList');
            document.getElementById('trace-count').innerText = traceGroups.length;
            list.innerHTML = '';

            traceGroups.forEach((g, idx) => {
                const el = document.createElement('div');
                el.className = 'req-card';
                el.id = `card-${idx}`;
                el.onclick = () => selectTrace(idx);
                
                const time = new Date(g.start_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
                const dur = (g.total_duration_ms / 1000).toFixed(2) + 's';
                const statusType = g.status === 'ERR' ? 'err' : 'ok';
                
                el.innerHTML = `
                    <div class="req-top">
                        <span class="req-status ${statusType}"></span>
                        <span class="req-time">${time}</span>
                    </div>
                    <div class="req-query">${escapeHtml(g.snippet)}</div>
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
            document.getElementById(`card-${idx}`).classList.add('active');
            
            const g = traceGroups[idx];
            document.getElementById('emptyState').classList.add('hidden');
            document.getElementById('detailView').classList.remove('hidden');
            
            // Header
            document.getElementById('dTitle').innerText = g.snippet;
            document.getElementById('dId').innerText = `TRACE: ${g.trace_id}`;
            document.getElementById('dTime').innerText = new Date(g.start_time).toLocaleString();
            document.getElementById('dDur').innerText = `${g.total_duration_ms.toFixed(0)} ms`;
            
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
                
                const left = (offset / totalDur) * 100;
                const width = Math.max((span.duration_ms / totalDur) * 100, 0.5);
                
                // Color Logic
                let bg = 'bg-def';
                const name = span.name.toLowerCase();
                const type = span.attributes['type'] || '';
                
                if (name.includes('completion') || name.includes('litellm') || type === 'ai') bg = 'bg-ai';
                else if (name.includes('tool') || span.attributes['tool.name']) bg = 'bg-tool';
                else if (name.includes('postgres') || name.includes('db')) bg = 'bg-db';
                
                if (span.status === 'ERROR' || span.status === 'fail') bg = 'bg-err';

                const row = document.createElement('div');
                row.className = 'span-row';
                
                const bar = document.createElement('div');
                bar.className = `span-bar ${bg}`;
                bar.style.left = `${left}%`;
                bar.style.width = `${width}%`;
                bar.innerText = span.name;
                bar.onclick = () => showProps(span);
                
                row.appendChild(bar);
                container.appendChild(row);
            });
            
            // Reset props
            document.getElementById('propsPre').innerText = 'Select a span to view attributes.';
        }

        function showProps(span) {
            document.getElementById('propsPre').innerText = JSON.stringify(span, null, 2);
        }

        function toggleProps() {
            const panel = document.getElementById('propsPanel');
            if (panel.style.height === '40px') panel.style.height = '300px';
            else panel.style.height = '40px';
        }

        async function runHealth() {
            try {
               await fetch('/diagnostics/run', {method:'POST'});
               alert("Health checks triggered (check logs/response)");
            } catch(e) { alert(e); }
        }

        function escapeHtml(str) {
            if(!str) return '';
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        // Init
        loadTraces();
    </script>
</body>
</html>
"""
    return html_content
