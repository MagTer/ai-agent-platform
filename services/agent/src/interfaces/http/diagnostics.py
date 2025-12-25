# ruff: noqa: E501
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from core.core.config import Settings, get_settings
from core.diagnostics.service import DiagnosticsService, TraceSpan

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


def get_diagnostics_service(
    settings: Settings = Depends(get_settings),
) -> DiagnosticsService:
    return DiagnosticsService(settings)


@router.get("/traces", response_model=list[TraceSpan])
async def get_traces(
    limit: int = 50, service: DiagnosticsService = Depends(get_diagnostics_service)
) -> list[TraceSpan]:
    return service.get_recent_traces(limit)


@router.get("/", response_class=HTMLResponse)
async def diagnostics_dashboard(
    service: DiagnosticsService = Depends(get_diagnostics_service),
) -> str:
    # MVP Dashboard HTML
    # Pre-fetch traces to render initial view (or fetch via JS)
    # We will fetch via JS for valid interactive feeling

    html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agent Diagnostics</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; background: #f4f6f8; }
        .header { background: #2c3e50; color: white; padding: 1rem; }
        .container { padding: 20px; max-width: 1400px; margin: 0 auto; }
        
        .tabs { display: flex; border-bottom: 2px solid #ddd; margin-bottom: 20px; }
        .tab { padding: 10px 20px; cursor: pointer; border-bottom: 2px solid transparent; font-weight: 600; }
        .tab.active { border-bottom-color: #3498db; color: #3498db; }
        
        .panel { display: none; }
        .panel.active { display: block; }
        
        table { width: 100%; border-collapse: collapse; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; font-size: 14px; }
        th { background: #f8f9fa; font-weight: 600; color: #555; }
        tr:hover { background-color: #f1f1f1; }
        
        .status-ok { color: green; font-weight: bold; }
        .status-error { color: #e74c3c; font-weight: bold; }
        .long-duration { color: #d35400; font-weight: bold; }
        
        .details-row { display: none; background: #fafafa; }
        .details-pre { margin: 0; padding: 10px; background: #eee; border-radius: 4px; white-space: pre-wrap; font-family: monospace; font-size: 12px; }
        
        button.refresh { float: right; padding: 8px 16px; background: #3498db; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button.refresh:hover { background: #2980b9; }
    </style>
</head>
<body>
    <div class="header">
        <div style="max-width: 1400px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center;">
            <h1>🛠️ Platform Diagnostics</h1>
            <button class="refresh" onclick="loadTraces()">Refresh Traces</button>
        </div>
    </div>
    
    <div class="container">
        <div class="tabs">
            <div class="tab" onclick="switchTab('health')">Health Checks</div>
            <div class="tab active" onclick="switchTab('traces')">Trace Log</div>
        </div>
        
        <div id="health" class="panel">
            <div style="background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                <h3>System Health</h3>
                <p>Status: <span class="status-ok">ONLINE</span></p>
                <p><a href="/healthz" target="_blank">View Raw Health JSON</a></p>
            </div>
        </div>
        
        <div id="traces" class="panel active">
            <table id="traceTable">
                <thead>
                    <tr>
                        <th style="width: 50px"></th>
                        <th>Timestamp</th>
                        <th>Name</th>
                        <th>Status</th>
                        <th>Duration (ms)</th>
                        <th>Trace ID</th>
                        <th>Parent</th>
                    </tr>
                </thead>
                <tbody id="traceBody">
                    <tr><td colspan="7">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            
            // Find tab by text (hacky but simple)
            const index = tabId === 'health' ? 0 : 1;
            document.querySelectorAll('.tab')[index].classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }

        async function loadTraces() {
            const tbody = document.getElementById('traceBody');
            tbody.innerHTML = '<tr><td colspan="7">Loading...</td></tr>';
            
            try {
                const response = await fetch('/diagnostics/traces?limit=100');
                const traces = await response.json();
                
                tbody.innerHTML = '';
                traces.forEach(span => {
                    const rowId = `row-${span.span_id}`;
                    const durationClass = span.duration_ms > 1000 ? 'long-duration' : '';
                    const statusClass = span.status === 'ERROR' ? 'status-error' : 'status-ok';
                    
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td style="cursor: pointer; text-align: center" onclick="toggleDetails('${rowId}')">▶</td>
                        <td>${new Date(span.start_time).toLocaleTimeString()}</td>
                        <td>${escapeHtml(span.name)}</td>
                        <td class="${statusClass}">${span.status}</td>
                        <td class="${durationClass}">${span.duration_ms.toFixed(1)}</td>
                        <td style="font-family: monospace; font-size: 11px" title="${span.trace_id}">${span.trace_id.substring(0,8)}...</td>
                        <td style="font-family: monospace; font-size: 11px">${span.parent_id ? span.parent_id.substring(0,8) + '...' : '-'}</td>
                    `;
                    tbody.appendChild(tr);
                    
                    const trDetails = document.createElement('tr');
                    trDetails.id = rowId;
                    trDetails.className = 'details-row';
                    trDetails.innerHTML = `
                        <td colspan="7">
                            <div class="details-pre">
                                <strong>Attributes:</strong><br>
                                ${JSON.stringify(span.attributes, null, 2)}
                            </div>
                        </td>
                    `;
                    tbody.appendChild(trDetails);
                });
            } catch (e) {
                tbody.innerHTML = `<tr><td colspan="7" style="color:red">Failed to load traces: ${e}</td></tr>`;
            }
        }
        
        function toggleDetails(rowId) {
            const row = document.getElementById(rowId);
            if (row.style.display === 'table-row') {
                row.style.display = 'none';
            } else {
                row.style.display = 'table-row';
            }
        }

        function escapeHtml(text) {
            return text
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        // Initial load
        loadTraces();
    </script>
</body>
</html>
    """
    return html_content
