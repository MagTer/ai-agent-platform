# ruff: noqa: E501
"""Shared components for admin portal UI consistency."""

from __future__ import annotations

import html
from dataclasses import dataclass

from fastapi.responses import HTMLResponse


class UTF8HTMLResponse(HTMLResponse):
    """HTMLResponse with explicit UTF-8 charset for proper Unicode support."""

    media_type = "text/html; charset=utf-8"


@dataclass
class NavItem:
    """Navigation menu item."""

    title: str
    href: str
    icon: str
    section: str


# Define all admin navigation items
ADMIN_NAV_ITEMS: list[NavItem] = [
    NavItem("Dashboard", "/platformadmin/", "&#127968;", "home"),
    NavItem("Diagnostics", "/platformadmin/diagnostics/", "&#128200;", "monitoring"),
    NavItem("Debug Logs", "/platformadmin/debug/", "&#128270;", "monitoring"),
    NavItem("Contexts", "/platformadmin/contexts/", "&#128451;", "features"),
    NavItem("Users", "/platformadmin/users/", "&#128100;", "users"),
    NavItem("Credentials", "/platformadmin/credentials/", "&#128273;", "users"),
    NavItem("Price Tracker", "/platformadmin/price-tracker/", "&#128181;", "features"),
    NavItem("Chat", "/", "&#128172;", "external"),
    NavItem("Open WebUI Admin", "/admin/", "&#128279;", "external"),
]


def get_admin_nav_css() -> str:
    """Return shared CSS for admin navigation."""
    return """
        :root {
            --nav-width: 220px;
            --header-height: 56px;
            --primary: #2563eb;
            --primary-dark: #1d4ed8;
            --bg: #f8fafc;
            --bg-nav: #1e293b;
            --bg-card: #fff;
            --border: #e2e8f0;
            --text: #1e293b;
            --text-muted: #64748b;
            --text-nav: #94a3b8;
            --text-nav-active: #fff;
            --success: #10b981;
            --warning: #f59e0b;
            --error: #ef4444;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
        }

        /* Layout */
        .admin-layout {
            display: flex;
            min-height: 100vh;
        }

        /* Sidebar */
        .admin-sidebar {
            width: var(--nav-width);
            background: var(--bg-nav);
            color: var(--text-nav);
            position: fixed;
            top: 0;
            left: 0;
            bottom: 0;
            display: flex;
            flex-direction: column;
            z-index: 100;
        }

        .sidebar-header {
            padding: 16px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }

        .sidebar-logo {
            font-size: 14px;
            font-weight: 600;
            color: #fff;
            text-decoration: none;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .sidebar-logo span {
            font-size: 18px;
        }

        .sidebar-nav {
            flex: 1;
            overflow-y: auto;
            padding: 12px 0;
        }

        .nav-section {
            padding: 8px 16px 4px;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #64748b;
        }

        .nav-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 16px;
            color: var(--text-nav);
            text-decoration: none;
            font-size: 13px;
            transition: all 0.15s;
            border-left: 3px solid transparent;
        }

        .nav-item:hover {
            background: rgba(255,255,255,0.05);
            color: #fff;
        }

        .nav-item.active {
            background: rgba(37, 99, 235, 0.2);
            color: var(--text-nav-active);
            border-left-color: var(--primary);
        }

        .nav-icon {
            font-size: 16px;
            width: 20px;
            text-align: center;
        }

        .sidebar-footer {
            padding: 12px 16px;
            border-top: 1px solid rgba(255,255,255,0.1);
            font-size: 11px;
            color: #64748b;
        }

        /* Main content */
        .admin-main {
            flex: 1;
            margin-left: var(--nav-width);
            display: flex;
            flex-direction: column;
            min-height: 100vh;
        }

        /* Top header */
        .admin-header {
            height: var(--header-height);
            background: var(--bg-card);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 24px;
            position: sticky;
            top: 0;
            z-index: 50;
        }

        .breadcrumbs {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 13px;
        }

        .breadcrumbs a {
            color: var(--text-muted);
            text-decoration: none;
        }

        .breadcrumbs a:hover {
            color: var(--primary);
        }

        .breadcrumbs .separator {
            color: var(--border);
        }

        .breadcrumbs .current {
            color: var(--text);
            font-weight: 500;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .user-menu {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 6px 12px;
            background: var(--bg);
            border-radius: 6px;
            font-size: 13px;
        }

        .user-avatar {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: var(--primary);
            color: #fff;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 600;
            font-size: 12px;
        }

        .logout-link {
            color: var(--text-muted);
            text-decoration: none;
            font-size: 12px;
            padding: 6px 10px;
            border-radius: 4px;
            transition: all 0.15s;
        }

        .logout-link:hover {
            background: var(--bg);
            color: var(--text);
        }

        /* Page content */
        .admin-content {
            flex: 1;
            padding: 24px;
        }

        .page-title {
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 20px;
        }

        /* Common card styles */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 16px;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }

        .card-title {
            font-size: 14px;
            font-weight: 600;
        }

        /* Buttons */
        .btn {
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text);
            transition: all 0.15s;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }

        .btn:hover {
            background: var(--bg);
            border-color: var(--text-muted);
        }

        .btn-primary {
            background: var(--primary);
            border-color: var(--primary);
            color: #fff;
        }

        .btn-primary:hover {
            background: var(--primary-dark);
        }

        .btn-sm {
            padding: 5px 10px;
            font-size: 12px;
        }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
        }

        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid var(--border);
        }

        th {
            background: var(--bg);
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            color: var(--text-muted);
        }

        tr:hover {
            background: var(--bg);
        }

        /* Badges */
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 500;
        }

        .badge-success { background: #d1fae5; color: #065f46; }
        .badge-warning { background: #fef3c7; color: #92400e; }
        .badge-error { background: #fee2e2; color: #991b1b; }
        .badge-info { background: #dbeafe; color: #1e40af; }
        .badge-muted { background: #e5e7eb; color: #374151; }

        /* Status indicators */
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }

        .status-dot.ok { background: var(--success); }
        .status-dot.warning { background: var(--warning); }
        .status-dot.error { background: var(--error); }

        /* Stats boxes */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .stat-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }

        .stat-value {
            font-size: 24px;
            font-weight: 600;
            color: var(--primary);
        }

        .stat-label {
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }

        /* Loading state */
        .loading {
            color: var(--text-muted);
            font-style: italic;
            text-align: center;
            padding: 20px;
        }

        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: var(--text-muted);
        }

        .empty-state-icon {
            font-size: 48px;
            margin-bottom: 16px;
            opacity: 0.5;
        }

        /* Shared toast notification */
        .shared-toast {
            position: fixed;
            bottom: 20px;
            right: 20px;
            padding: 12px 20px;
            border-radius: 6px;
            color: white;
            font-size: 14px;
            font-weight: 500;
            z-index: 9999;
            display: none;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            max-width: 400px;
        }
        .shared-toast.success { background: var(--success); display: block; }
        .shared-toast.error { background: var(--error); display: block; }
        .shared-toast.warning { background: var(--warning); display: block; }
        .shared-toast.info { background: var(--primary); display: block; }
    """


def get_admin_sidebar_html(active_page: str) -> str:
    """Generate sidebar HTML with active page highlighted.

    Args:
        active_page: The current page identifier (e.g., 'users', 'diagnostics')
    """
    sections = {
        "home": "Home",
        "monitoring": "Monitoring",
        "users": "User Management",
        "features": "Features",
        "external": "Open WebUI",
    }

    current_section = None
    nav_html = []

    for item in ADMIN_NAV_ITEMS:
        # Add section header if new section
        if item.section != current_section:
            current_section = item.section
            if item.section != "home":
                nav_html.append(f'<div class="nav-section">{sections[item.section]}</div>')

        # Determine if this is the active item
        is_active = False
        if active_page == "/":
            is_active = item.href == "/platformadmin/"
        elif (
            active_page.startswith("/platformadmin/contexts/")
            and item.href == "/platformadmin/contexts/"
        ):
            # Context detail pages highlight the Contexts nav item
            is_active = True
        elif (
            active_page.startswith("/platformadmin/users/") and item.href == "/platformadmin/users/"
        ):
            # User detail pages highlight the Users nav item
            is_active = True
        else:
            is_active = item.href.rstrip("/").endswith(active_page.rstrip("/"))
        active_class = " active" if is_active else ""

        nav_html.append(
            f'<a href="{item.href}" class="nav-item{active_class}">'
            f'<span class="nav-icon">{item.icon}</span>'
            f"{item.title}"
            f"</a>"
        )

    return f"""
    <aside class="admin-sidebar">
        <div class="sidebar-header">
            <a href="/platformadmin/" class="sidebar-logo">
                <span>&#9881;</span>
                Admin Portal
            </a>
        </div>
        <nav class="sidebar-nav">
            {''.join(nav_html)}
        </nav>
        <div class="sidebar-footer">
            AI Agent Platform
        </div>
    </aside>
    """


def get_admin_header_html(
    breadcrumbs: list[tuple[str, str]],
    user_name: str,
    user_email: str,
) -> str:
    """Generate header HTML with breadcrumbs and user info.

    Args:
        breadcrumbs: List of (label, url) tuples. Last item is current page (no link).
        user_name: Display name of logged-in user
        user_email: Email of logged-in user
    """
    # Escape user data
    safe_name = html.escape(user_name)
    user_initial = safe_name[0].upper() if safe_name else "?"

    # Build breadcrumb HTML
    crumbs = ['<a href="/platformadmin/">Admin</a>']
    for i, (label, url) in enumerate(breadcrumbs):
        if i == len(breadcrumbs) - 1:
            # Last item is current page
            crumbs.append(f'<span class="current">{html.escape(label)}</span>')
        else:
            crumbs.append(f'<a href="{url}">{html.escape(label)}</a>')

    breadcrumb_html = '<span class="separator">/</span>'.join(crumbs)

    return f"""
    <header class="admin-header">
        <div class="breadcrumbs">
            {breadcrumb_html}
        </div>
        <div class="header-actions">
            <div class="user-menu">
                <div class="user-avatar">{user_initial}</div>
                <span>{safe_name}</span>
            </div>
            <a href="/" class="logout-link">Exit Admin</a>
        </div>
    </header>
    """


def render_admin_page(
    title: str,
    active_page: str,
    content: str,
    user_name: str,
    user_email: str,
    breadcrumbs: list[tuple[str, str]] | None = None,
    extra_css: str = "",
    extra_js: str = "",
) -> str:
    """Render a complete admin page with navigation.

    Args:
        title: Page title for browser tab
        active_page: Current page identifier for nav highlighting
        content: Main page content HTML
        user_name: Display name of logged-in user
        user_email: Email of logged-in user
        breadcrumbs: Optional list of (label, url) tuples
        extra_css: Additional CSS to include
        extra_js: Additional JavaScript to include

    Returns:
        Complete HTML page string
    """
    if breadcrumbs is None:
        breadcrumbs = [(title, "#")]

    sidebar = get_admin_sidebar_html(active_page)
    header = get_admin_header_html(breadcrumbs, user_name, user_email)
    base_css = get_admin_nav_css()

    # CSRF protection and error handling JavaScript utilities
    csrf_js = """
        // CSRF token utilities
        function getCsrfToken() {
            const cookies = document.cookie.split(';');
            for (let cookie of cookies) {
                const [name, value] = cookie.trim().split('=');
                if (name === 'csrf_token') {
                    return decodeURIComponent(value);
                }
            }
            return null;
        }

        // Override fetch to automatically include CSRF token
        const originalFetch = window.fetch;
        window.fetch = function(url, options = {}) {
            // Only add CSRF token for POST/DELETE/PUT/PATCH to /platformadmin/
            const method = (options.method || 'GET').toUpperCase();
            const needsCsrf = ['POST', 'DELETE', 'PUT', 'PATCH'].includes(method);
            const isPlatformAdmin = typeof url === 'string' && url.startsWith('/platformadmin/');

            if (needsCsrf && isPlatformAdmin) {
                const csrfToken = getCsrfToken();
                if (csrfToken) {
                    options.headers = options.headers || {};
                    if (options.headers instanceof Headers) {
                        options.headers.set('X-CSRF-Token', csrfToken);
                    } else {
                        options.headers['X-CSRF-Token'] = csrfToken;
                    }
                } else {
                    console.warn('CSRF token not found in cookie');
                }
            }

            return originalFetch(url, options);
        };

        // Shared toast notification utility
        function showToast(message, type = 'info') {
            let toast = document.getElementById('shared-toast');
            if (!toast) {
                toast = document.createElement('div');
                toast.id = 'shared-toast';
                toast.className = 'shared-toast';
                document.body.appendChild(toast);
            }
            toast.textContent = message;
            toast.className = 'shared-toast ' + type;
            setTimeout(() => { toast.className = 'shared-toast'; }, 4000);
        }

        // Error-handling fetch wrapper
        async function fetchWithErrorHandling(url, options = {}) {
            try {
                const response = await fetch(url, options);

                if (!response.ok) {
                    let errorMsg = 'Request failed: ' + response.status;
                    try {
                        const errorData = await response.json();
                        errorMsg = errorData.detail || errorData.message || errorMsg;
                    } catch {
                        errorMsg = await response.text() || errorMsg;
                    }
                    showToast(errorMsg, 'error');
                    return null;
                }

                return response;
            } catch (error) {
                const msg = error.message || 'Network error';
                showToast('Network error: ' + msg, 'error');
                console.error('Fetch error:', error);
                return null;
            }
        }
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)} - Admin Portal</title>
    <style>
        {base_css}
        {extra_css}
    </style>
</head>
<body>
    <div class="admin-layout">
        {sidebar}
        <main class="admin-main">
            {header}
            <div class="admin-content">
                {content}
            </div>
        </main>
    </div>
    <script>
        {csrf_js}
        {extra_js}
    </script>
</body>
</html>"""


__all__ = [
    "ADMIN_NAV_ITEMS",
    "NavItem",
    "UTF8HTMLResponse",
    "get_admin_header_html",
    "get_admin_nav_css",
    "get_admin_sidebar_html",
    "render_admin_page",
]
