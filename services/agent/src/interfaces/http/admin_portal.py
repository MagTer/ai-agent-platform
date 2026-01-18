# ruff: noqa: E501
"""Unified admin portal with navigation to all admin sections."""

from __future__ import annotations

import html

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from interfaces.http.admin_auth import AdminUser, verify_admin_user

router = APIRouter(
    prefix="/platformadmin",
    tags=["platform-admin"],
)


@router.get("/", response_class=HTMLResponse)
async def admin_portal(admin: AdminUser = Depends(verify_admin_user)) -> str:
    """Unified admin portal landing page.

    Returns:
        HTML page with navigation to all admin sections.

    Security:
        Requires admin role via Entra ID authentication.
    """
    # Escape user data for safe HTML rendering
    user_email = html.escape(admin.email)
    user_name = html.escape(admin.display_name or admin.email.split("@")[0])
    user_initial = user_name[0].upper()

    # Use string replacement to avoid escaping all CSS braces
    template = _get_admin_portal_template()
    return (
        template.replace("{{USER_NAME}}", user_name)
        .replace("{{USER_EMAIL}}", user_email)
        .replace("{{USER_INITIAL}}", user_initial)
    )


def _get_admin_portal_template() -> str:
    """Return the admin portal HTML template."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Portal - AI Agent Platform</title>
    <style>
        :root {
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --bg: #f8fafc;
            --bg-card: #fff;
            --border: #e2e8f0;
            --text: #1e293b;
            --text-muted: #64748b;
            --success: #10b981;
            --warning: #f59e0b;
            --error: #ef4444;
        }

        * { box-sizing: border-box; }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            margin: 0;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }

        .header {
            background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
            color: white;
            padding: 20px 20px 40px;
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            max-width: 1000px;
            margin: 0 auto 20px;
        }

        .header-content {
            text-align: center;
        }

        .header h1 {
            margin: 0 0 8px 0;
            font-size: 28px;
            font-weight: 600;
        }

        .header p {
            margin: 0;
            opacity: 0.8;
            font-size: 14px;
        }

        .user-info {
            display: flex;
            align-items: center;
            gap: 12px;
            background: rgba(255, 255, 255, 0.1);
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 14px;
        }

        .user-avatar {
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: var(--primary);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 14px;
        }

        .user-details {
            text-align: left;
        }

        .user-name {
            font-weight: 500;
        }

        .user-email {
            font-size: 12px;
            opacity: 0.7;
        }

        .logout-btn {
            background: rgba(255, 255, 255, 0.15);
            border: 1px solid rgba(255, 255, 255, 0.2);
            color: white;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s;
        }

        .logout-btn:hover {
            background: rgba(255, 255, 255, 0.25);
        }

        .container {
            max-width: 1000px;
            margin: -40px auto 40px;
            padding: 0 20px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            text-decoration: none;
            color: inherit;
            transition: all 0.2s ease;
            display: block;
        }

        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            border-color: var(--primary);
        }

        .card-icon {
            width: 48px;
            height: 48px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            margin-bottom: 16px;
        }

        .card-icon.blue { background: #dbeafe; }
        .card-icon.green { background: #d1fae5; }
        .card-icon.purple { background: #ede9fe; }
        .card-icon.orange { background: #ffedd5; }
        .card-icon.pink { background: #fce7f3; }

        .card h2 {
            margin: 0 0 8px 0;
            font-size: 18px;
            font-weight: 600;
        }

        .card p {
            margin: 0;
            font-size: 14px;
            color: var(--text-muted);
            line-height: 1.5;
        }

        .card .endpoint {
            margin-top: 12px;
            font-size: 12px;
            color: var(--primary);
            font-family: monospace;
        }

        .section-title {
            font-size: 12px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin: 32px 0 16px;
        }

        .status-bar {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 16px;
        }

        .status-item {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 14px;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
        }

        .status-dot.loading {
            background: var(--warning);
            animation: pulse 1s infinite;
        }

        .status-dot.error {
            background: var(--error);
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .refresh-btn {
            background: var(--bg);
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
        }

        .refresh-btn:hover {
            background: var(--border);
        }

        .footer {
            text-align: center;
            padding: 20px;
            color: var(--text-muted);
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div></div>
            <div class="user-info">
                <div class="user-avatar">{{USER_INITIAL}}</div>
                <div class="user-details">
                    <div class="user-name">{{USER_NAME}}</div>
                    <div class="user-email">{{USER_EMAIL}}</div>
                </div>
                <a href="/" class="logout-btn" title="Return to Open WebUI">Logout</a>
            </div>
        </div>
        <div class="header-content">
            <h1>Admin Portal</h1>
            <p>AI Agent Platform Administration</p>
        </div>
    </div>

    <div class="container">
        <div class="status-bar">
            <div class="status-item">
                <span class="status-dot loading" id="statusDot"></span>
                <span id="statusText">Checking system health...</span>
            </div>
            <button class="refresh-btn" onclick="checkHealth()">Refresh Status</button>
        </div>

        <div class="section-title">Monitoring & Diagnostics</div>
        <div class="grid">
            <a href="/platformadmin/diagnostics/" class="card">
                <div class="card-icon blue">&#128200;</div>
                <h2>Diagnostics</h2>
                <p>System health monitoring, trace analysis, and component status checks.</p>
                <div class="endpoint">/platformadmin/diagnostics/</div>
            </a>
        </div>

        <div class="section-title">User Management</div>
        <div class="grid">
            <a href="/platformadmin/users/" class="card">
                <div class="card-icon blue">&#128100;</div>
                <h2>Users</h2>
                <p>Manage user accounts, roles, and permissions across the platform.</p>
                <div class="endpoint">/platformadmin/users/</div>
            </a>

            <a href="/platformadmin/credentials/" class="card">
                <div class="card-icon purple">&#128273;</div>
                <h2>Credentials</h2>
                <p>Manage encrypted credentials (PATs, API tokens) for users.</p>
                <div class="endpoint">/platformadmin/credentials/</div>
            </a>
        </div>

        <div class="section-title">Feature Management</div>
        <div class="grid">
            <a href="/platformadmin/price-tracker/" class="card">
                <div class="card-icon green">&#128181;</div>
                <h2>Price Tracker</h2>
                <p>Manage product price tracking, store links, deals monitoring, and price alerts.</p>
                <div class="endpoint">/platformadmin/price-tracker/</div>
            </a>

            <a href="/platformadmin/mcp/" class="card">
                <div class="card-icon purple">&#128268;</div>
                <h2>MCP Servers</h2>
                <p>Configure and monitor Model Context Protocol server connections.</p>
                <div class="endpoint">/platformadmin/mcp/</div>
            </a>

            <a href="/platformadmin/contexts/" class="card">
                <div class="card-icon orange">&#128451;</div>
                <h2>Contexts</h2>
                <p>Manage conversation contexts and associated resources.</p>
                <div class="endpoint">/platformadmin/contexts/</div>
            </a>

            <a href="/platformadmin/oauth/" class="card">
                <div class="card-icon pink">&#128274;</div>
                <h2>OAuth Settings</h2>
                <p>Configure OAuth providers and manage authentication tokens.</p>
                <div class="endpoint">/platformadmin/oauth/</div>
            </a>
        </div>
    </div>

    <div class="footer">
        AI Agent Platform &middot; Admin Portal
    </div>

    <script>
        async function checkHealth() {
            const dot = document.getElementById('statusDot');
            const text = document.getElementById('statusText');

            dot.className = 'status-dot loading';
            text.textContent = 'Checking system health...';

            try {
                const response = await fetch('/platformadmin/diagnostics/summary');
                if (response.ok) {
                    const data = await response.json();
                    const status = data.overall_status || 'UNKNOWN';

                    if (status === 'HEALTHY') {
                        dot.className = 'status-dot';
                        text.textContent = 'System healthy - All components operational';
                    } else if (status === 'DEGRADED') {
                        dot.className = 'status-dot loading';
                        text.textContent = 'System degraded - Some components need attention';
                    } else {
                        dot.className = 'status-dot error';
                        text.textContent = 'System issues detected - Check diagnostics';
                    }
                } else {
                    dot.className = 'status-dot error';
                    text.textContent = 'Unable to fetch health status';
                }
            } catch (e) {
                dot.className = 'status-dot error';
                text.textContent = 'Connection error - Check if agent is running';
            }
        }

        // Check health on page load
        checkHealth();

        // Refresh every 30 seconds
        setInterval(checkHealth, 30000);
    </script>
</body>
</html>"""


__all__ = ["router"]
