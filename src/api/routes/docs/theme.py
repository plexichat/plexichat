"""
Theme and CSS generation for documentation.

This module handles all CSS generation for the documentation system.
"""

from .config import DocsConfig


def build_brand_styles(conf: DocsConfig) -> str:
    """Build shared landing-inspired styles for docs surfaces."""
    return """
        :root {
            --bg: #fafafa;
            --surface: #ffffff;
            --text: #18181b;
            --text-muted: #3f3f46;
            --accent: #6366f1;
            --accent-hover: #4f46e5;
            --border: #e4e4e7;
            --border-light: #f4f4f5;
            --font-main: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
            --font-code: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, 'Courier New', monospace;
            --font-size-base: 16px;
            --line-height: 1.75;
            --sidebar-width: 260px;
            --spacing-xs: 4px;
            --spacing-sm: 8px;
            --spacing-md: 12px;
            --spacing-lg: 20px;
            --spacing-xl: 28px;
            --spacing-2xl: 36px;
            --radius-sm: 4px;
            --radius-md: 6px;
            --radius-lg: 8px;
        }

        * { box-sizing: border-box; }

        html { scroll-behavior: smooth; }

        body {
            margin: 0;
            color: var(--text);
            background: var(--bg);
            font-family: var(--font-main);
            font-size: var(--font-size-base);
            line-height: var(--line-height);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        a:focus-visible,
        button:focus-visible,
        [role="button"]:focus-visible,
        input:focus-visible,
        select:focus-visible,
        textarea:focus-visible {
            outline: 2px solid var(--accent);
            outline-offset: 2px;
        }

        .docs-layout {
            display: grid;
            grid-template-columns: var(--sidebar-width) 1fr;
            min-height: 100vh;
        }

        .sidebar {
            background: var(--surface);
            border-right: 1px solid var(--border);
            height: 100vh;
            overflow-y: auto;
            padding: var(--spacing-xl) var(--spacing-md);
            position: sticky;
            top: 0;
            scrollbar-width: thin;
            scrollbar-color: var(--border) transparent;
        }

        .sidebar::-webkit-scrollbar {
            width: 6px;
        }

        .sidebar::-webkit-scrollbar-thumb {
            background-color: var(--border);
            border-radius: 3px;
        }

        .sidebar-header {
            margin-bottom: var(--spacing-xl);
        }

        .brand-mark {
            color: var(--text);
            display: inline-block;
            font-size: 1.25rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            text-decoration: none;
            margin-bottom: var(--spacing-md);
        }

        .brand-mark:hover {
            color: var(--accent);
        }

        .brand-mark span { color: var(--accent); }

        .sidebar-caption {
            color: var(--text-muted);
            display: block;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.08em;
            margin-bottom: var(--spacing-md);
            text-transform: uppercase;
        }

        .sidebar-description {
            color: var(--text-muted);
            font-size: 0.875rem;
            margin: 0;
            line-height: 1.5;
        }

        .nav-category {
            color: var(--text-muted);
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            margin: var(--spacing-xl) var(--spacing-md) var(--spacing-sm);
            text-transform: uppercase;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
            user-select: none;
            transition: color 0.15s ease;
        }

        .nav-category:hover {
            color: var(--text);
        }

        .nav-category::after {
            content: "−";
            font-size: 0.875rem;
            font-weight: bold;
        }

        .nav-category.collapsed::after {
            content: "+";
        }

        .nav-list { list-style: none; margin: 0; padding: 0; }

        .nav-list li + li { margin-top: 2px; }

        .nav-list a {
            border: 1px solid transparent;
            border-radius: var(--radius-sm);
            color: #27272a;
            display: block;
            font-size: 0.95rem;
            padding: 6px var(--spacing-sm);
            text-decoration: none;
            transition: all 0.15s ease;
            line-height: 1.4;
        }

        .nav-list a:hover {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }

        .nav-list a.active {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
            font-weight: 500;
        }

        .nav-list.collapsed { display: none; }

        .docs-main {
            padding: 0;
            width: 100%;
            min-width: 0;
        }

        .docs-content {
            max-width: 820px;
            margin: 0 auto;
            padding: 0 var(--spacing-xl);
        }

        .shell-header {
            border-bottom: 1px solid var(--border);
            padding: var(--spacing-2xl) 0 var(--spacing-lg);
        }

        .shell-brand-block { margin-bottom: var(--spacing-lg); }

        .surface-badge {
            background: var(--accent);
            border-radius: var(--radius-lg);
            color: #fff;
            display: inline-flex;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            padding: 3px var(--spacing-md);
            margin-bottom: var(--spacing-md);
            text-transform: uppercase;
        }

        .shell-title {
            font-size: 2rem;
            font-weight: 700;
            letter-spacing: -0.03em;
            line-height: 1.2;
            margin: 0 0 var(--spacing-sm);
            color: var(--text);
        }

        .shell-summary {
            color: var(--text-muted);
            font-size: 1.05rem;
            margin: 0 0 var(--spacing-lg);
            line-height: 1.5;
        }

        .surface-nav {
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }

        .surface-link {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.875rem;
            font-weight: 500;
            padding: var(--spacing-sm) var(--spacing-md);
            text-decoration: none;
            transition: all 0.15s ease;
        }

        .surface-link:hover,
        .surface-link.active {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }

        .content-container {
            padding: var(--spacing-2xl) 0;
            max-width: 100%;
        }

        .content-container > :first-child { margin-top: 0; }

        h1, h2, h3, h4, h5, h6 {
            color: var(--text);
            font-weight: 600;
            line-height: 1.3;
            margin-top: var(--spacing-2xl);
            margin-bottom: var(--spacing-md);
        }

        h1 {
            font-size: 1.75rem;
            letter-spacing: -0.02em;
            border-bottom: 1px solid var(--border);
            padding-bottom: var(--spacing-md);
        }

        h2 {
            font-size: 1.4rem;
            letter-spacing: -0.015em;
        }

        h3 { font-size: 1.15rem; }

        h4 { font-size: 1rem; }

        p, li, td, th {
            color: var(--text);
            font-size: 1rem;
            line-height: 1.75;
        }

        p { margin: 0 0 var(--spacing-lg); }

        strong { color: var(--text); font-weight: 600; }

        a {
            color: var(--accent);
            text-decoration: none;
            transition: color 0.15s ease;
        }

        a:hover {
            color: var(--accent-hover);
            text-decoration: underline;
        }

        ul, ol {
            margin: 0 0 var(--spacing-lg);
            padding-left: var(--spacing-xl);
        }

        li + li { margin-top: var(--spacing-sm); }

        code {
            background: #f1f5f9;
            border: 1px solid #e2e8f0;
            border-radius: var(--radius-sm);
            color: #0f172a;
            font-family: var(--font-code);
            font-size: 0.875em;
            padding: 2px var(--spacing-xs);
        }

        pre {
            background: #0f172a;
            border-radius: var(--radius-md);
            overflow-x: auto;
            padding: var(--spacing-lg);
            margin: 0 0 var(--spacing-lg);
        }

        pre code {
            background: transparent;
            border: none;
            padding: 0;
            font-size: 0.875rem;
            line-height: 1.6;
            color: #e2e8f0;
        }

        .code-block {
            margin: 0 0 var(--spacing-lg);
            position: relative;
        }

        .copy-btn {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: var(--radius-sm);
            color: #94a3b8;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            font-family: var(--font-main);
            font-size: 0.75rem;
            font-weight: 500;
            gap: var(--spacing-xs);
            padding: var(--spacing-xs) var(--spacing-sm);
            position: absolute;
            right: var(--spacing-sm);
            top: var(--spacing-sm);
            transition: all 0.15s ease;
        }

        .copy-btn:hover {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }

        .table-wrapper {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            margin: 0 0 var(--spacing-lg);
            overflow-x: auto;
        }

        table {
            border-collapse: collapse;
            width: 100%;
        }

        th, td {
            border-bottom: 1px solid var(--border);
            padding: var(--spacing-sm) var(--spacing-md);
            text-align: left;
            font-size: 0.9rem;
        }

        th {
            background: var(--bg);
            color: var(--text-muted);
            font-size: 0.8rem;
            font-weight: 600;
            letter-spacing: 0.025em;
            text-transform: uppercase;
        }

        tr:last-child td {
            border-bottom: none;
        }

        .note {
            background: #f0fdf4;
            border: 1px solid #bbf7d0;
            border-left: 4px solid #22c55e;
            border-radius: var(--radius-md);
            margin: 0 0 var(--spacing-lg);
            padding: var(--spacing-md) var(--spacing-lg);
            color: #166534;
            font-size: 0.9rem;
        }

        .footer {
            border-top: 1px solid var(--border);
            color: var(--text-muted);
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-md);
            padding: var(--spacing-lg) 0;
            font-size: 0.85rem;
        }

        .footer-runtime {
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
            margin-top: var(--spacing-md);
        }

        .footer-runtime span {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            color: var(--text-muted);
            font-size: 0.75rem;
            padding: var(--spacing-xs) var(--spacing-sm);
        }

        .footer-runtime span.accent {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }

        .related-links {
            border-top: 1px solid var(--border);
            margin-top: var(--spacing-xl);
            padding: var(--spacing-lg) 0;
        }

        .related-links h4 {
            color: var(--text-muted);
            font-size: 0.8rem;
            font-weight: 600;
            margin: 0 0 var(--spacing-md);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .related-links-list {
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }

        .related-link {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            color: var(--text-muted);
            display: inline-block;
            font-size: 0.85rem;
            padding: var(--spacing-xs) var(--spacing-md);
            text-decoration: none;
            transition: all 0.15s ease;
        }

        .related-link:hover {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }

        /* OpenAPI overrides */
        .plexi-openapi-page #swagger-ui,
        .plexi-openapi-page redoc {
            display: block;
            padding: 0;
            position: relative;
            z-index: 1;
        }

        .plexi-openapi-page .docs-content {
            max-width: 100%;
            padding: 0;
        }

        .plexi-openapi-page .content-container {
            padding: 0;
        }

        .plexi-swagger-page .swagger-ui {
            color: var(--text);
        }

        .plexi-swagger-page .swagger-ui .topbar { display: none; }

        .plexi-swagger-page .swagger-ui .info {
            margin: var(--spacing-xl) 0;
        }

        .plexi-swagger-page .swagger-ui .info .title {
            color: var(--text);
            font-family: var(--font-main);
            font-size: 1.75rem;
        }

        .plexi-swagger-page .swagger-ui .info p,
        .plexi-swagger-page .swagger-ui .info li {
            color: var(--text-muted);
        }

        .plexi-swagger-page .swagger-ui .scheme-container {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            box-shadow: none;
            margin: 0 0 var(--spacing-lg);
            padding: var(--spacing-md);
        }

        .plexi-swagger-page .swagger-ui .opblock-tag {
            color: var(--text);
            font-family: var(--font-main);
            font-size: 1.15rem;
        }

        .plexi-swagger-page .swagger-ui .opblock-tag:hover {
            background: rgba(99, 102, 241, 0.06);
        }

        .plexi-swagger-page .swagger-ui .opblock {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            box-shadow: none;
            margin: 0 0 var(--spacing-md);
            overflow: hidden;
        }

        .plexi-swagger-page .swagger-ui .opblock .opblock-summary {
            border-color: var(--border);
        }

        .plexi-swagger-page .swagger-ui .opblock.opblock-get {
            border-left: 3px solid #22c55e;
        }

        .plexi-swagger-page .swagger-ui .opblock.opblock-post,
        .plexi-swagger-page .swagger-ui .opblock.opblock-put,
        .plexi-swagger-page .swagger-ui .opblock.opblock-patch {
            border-left: 3px solid var(--accent);
        }

        .plexi-swagger-page .swagger-ui .opblock.opblock-delete {
            border-left: 3px solid #ef4444;
        }

        .plexi-swagger-page .swagger-ui .opblock-body {
            background: var(--surface);
        }

        .plexi-swagger-page .swagger-ui .btn,
        .plexi-swagger-page .swagger-ui button,
        .plexi-swagger-page .swagger-ui select,
        .plexi-swagger-page .swagger-ui input,
        .plexi-swagger-page .swagger-ui textarea {
            border-radius: var(--radius-sm);
            font-family: var(--font-main);
        }

        .plexi-swagger-page .swagger-ui input,
        .plexi-swagger-page .swagger-ui textarea,
        .plexi-swagger-page .swagger-ui select {
            background: var(--bg);
            border: 1px solid var(--border);
            color: var(--text);
        }

        .plexi-swagger-page .swagger-ui .btn.authorize,
        .plexi-swagger-page .swagger-ui .btn.execute {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }

        .plexi-swagger-page .swagger-ui .btn.authorize:hover,
        .plexi-swagger-page .swagger-ui .btn.execute:hover {
            background: var(--accent-hover);
        }

        .plexi-swagger-page .swagger-ui .parameters-container,
        .plexi-swagger-page .swagger-ui .responses-wrapper {
            background: var(--bg);
            border-radius: var(--radius-md);
            padding: var(--spacing-md);
        }

        .plexi-swagger-page .swagger-ui table tbody tr td,
        .plexi-swagger-page .swagger-ui table thead tr th,
        .plexi-swagger-page .swagger-ui .parameter__name,
        .plexi-swagger-page .swagger-ui .response-col_status {
            color: var(--text);
        }

        .plexi-swagger-page .swagger-ui .parameter__type,
        .plexi-swagger-page .swagger-ui .parameter__deprecated,
        .plexi-swagger-page .swagger-ui .response-col_links,
        .plexi-swagger-page .swagger-ui .opblock-summary-description {
            color: var(--text-muted);
        }

        .plexi-swagger-page .swagger-ui .model-box {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
        }

        .plexi-swagger-page .swagger-ui .model {
            color: var(--text-muted);
        }

        .plexi-swagger-page .swagger-ui section.models {
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            overflow: hidden;
        }

        .plexi-swagger-page .swagger-ui section.models h4,
        .plexi-swagger-page .swagger-ui section.models h5 {
            color: var(--text);
        }

        .plexi-swagger-page .swagger-ui .model-toggle:after {
            background: var(--accent);
        }

        .plexi-swagger-page .swagger-ui .loading-container {
            padding: var(--spacing-2xl) 0;
        }

        .plexi-swagger-page .swagger-ui .loading-container .loading {
            border-color: var(--border);
            border-top-color: var(--accent);
        }

        .plexi-redoc-page redoc {
            display: block;
        }

        .plexi-redoc-page .menu-content,
        .plexi-redoc-page [role="search"] input,
        .plexi-redoc-page .api-content,
        .plexi-redoc-page .redoc-json,
        .plexi-redoc-page .redoc-markdown code,
        .plexi-redoc-page .redoc-markdown pre {
            font-family: var(--font-main) !important;
        }

        .plexi-redoc-page .menu-content {
            background: var(--bg) !important;
            border-right: 1px solid var(--border) !important;
        }

        .plexi-redoc-page [role="search"] input {
            background: var(--surface) !important;
            border: 1px solid var(--border) !important;
            border-radius: var(--radius-md) !important;
            box-shadow: none !important;
            color: var(--text) !important;
        }

        .plexi-redoc-page .api-content {
            background: transparent !important;
        }

        .plexi-redoc-page .api-info h1,
        .plexi-redoc-page h1,
        .plexi-redoc-page h2,
        .plexi-redoc-page h3,
        .plexi-redoc-page h4,
        .plexi-redoc-page h5 {
            color: var(--text) !important;
        }

        .plexi-redoc-page .api-info p {
            color: var(--text-muted) !important;
        }

        .plexi-redoc-page code,
        .plexi-redoc-page pre,
        .plexi-redoc-page table {
            border-color: var(--border) !important;
        }

        .plexi-redoc-page pre,
        .plexi-redoc-page code {
            background: #0f172a !important;
            border-radius: var(--radius-sm) !important;
            color: #e2e8f0 !important;
        }

        @media (max-width: 1024px) {
            .docs-layout { grid-template-columns: 1fr; }
            .sidebar {
                border-right: 0;
                border-bottom: 1px solid var(--border);
                height: auto;
                position: relative;
                top: auto;
            }
            .docs-content {
                padding: 0 var(--spacing-lg);
            }
        }

        @media (max-width: 768px) {
            .shell-title {
                font-size: 1.5rem;
            }
            .docs-content {
                padding: 0 var(--spacing-md);
            }
            .content-container {
                padding: var(--spacing-xl) 0;
            }
            .surface-link {
                width: 100%;
                justify-content: center;
            }
        }
    """
