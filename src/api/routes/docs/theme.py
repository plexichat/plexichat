"""
Theme and CSS generation for documentation.

This module handles all CSS generation for the documentation system.
"""

from .config import DocsConfig


def build_brand_styles(conf: DocsConfig) -> str:
    """Build shared landing-inspired styles for docs surfaces."""
    theme = conf.theme
    return f"""
        :root {{
            --bg: {theme.background_color};
            --surface: {theme.surface_color};
            --text: {theme.text_color};
            --text-muted: {theme.text_muted};
            --accent: {theme.accent_color};
            --accent-hover: {theme.accent_hover};
            --border: {theme.border_color};
            --border-light: {theme.border_light};
            --font-main: {theme.font_family};
            --font-code: {theme.code_font};
            --font-size-base: {theme.font_size_base};
            --line-height: {theme.line_height};
            --border-radius-small: {theme.border_radius_small};
            --border-radius-medium: {theme.border_radius_medium};
            --border-radius-large: {theme.border_radius_large};
            --transition-speed: {theme.transition_speed};
            --sidebar-width: {theme.sidebar_width};
            --content-max-width: {theme.content_max_width};
            --spacing-xs: {theme.spacing_xs};
            --spacing-sm: {theme.spacing_sm};
            --spacing-md: {theme.spacing_md};
            --spacing-lg: {theme.spacing_lg};
            --spacing-xl: {theme.spacing_xl};
            --spacing-2xl: {theme.spacing_2xl};
        }}

        * {{ box-sizing: border-box; }}

        html {{ scroll-behavior: smooth; }}

        body {{
            margin: 0;
            color: var(--text);
            background: var(--bg);
            font-family: var(--font-main);
            font-size: var(--font-size-base);
            line-height: var(--line-height);
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }}

        a:focus-visible,
        button:focus-visible,
        [role="button"]:focus-visible,
        input:focus-visible,
        select:focus-visible,
        textarea:focus-visible {{
            outline: 2px solid rgba(59, 130, 246, 0.7);
            outline-offset: 2px;
        }}

        .docs-layout {{
            display: grid;
            grid-template-columns: var(--sidebar-width) 1fr;
            min-height: 100vh;
        }}

        .sidebar {{
            background: var(--surface);
            border-right: 1px solid var(--border);
            height: 100vh;
            overflow-y: auto;
            padding: var(--spacing-xl) var(--spacing-lg);
            position: sticky;
            top: 0;
        }}

        .sidebar-header {{
            margin-bottom: var(--spacing-xl);
        }}

        .brand-mark {{
            color: var(--text);
            display: inline-block;
            font-size: 1.125rem;
            font-weight: 600;
            letter-spacing: 0.025em;
            text-decoration: none;
            margin-bottom: var(--spacing-sm);
        }}

        .brand-mark:hover {{
            color: var(--accent);
        }}

        .brand-mark span {{ color: var(--accent); }}

        .sidebar-caption {{
            color: var(--text-muted);
            display: block;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            margin-bottom: var(--spacing-md);
            text-transform: uppercase;
        }}

        .sidebar-header h3 {{
            font-size: 1rem;
            font-weight: 600;
            margin: 0 0 var(--spacing-sm);
            color: var(--text);
        }}

        .sidebar-description {{
            color: var(--text-muted);
            font-size: 0.875rem;
            margin: 0;
            line-height: 1.5;
        }}

        .nav-category {{
            color: var(--text-muted);
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.1em;
            margin: var(--spacing-xl) var(--spacing-md) var(--spacing-sm);
            text-transform: uppercase;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: var(--spacing-sm);
        }}

        .nav-category::after {{
            content: "+";
            font-size: 0.875rem;
            transition: transform var(--transition-speed) ease;
        }}

        .nav-category.collapsed::after {{
            transform: rotate(-45deg);
        }}

        .nav-list {{ list-style: none; margin: 0; padding: 0; }}

        .nav-list li + li {{ margin-top: 2px; }}

        .nav-list a {{
            border: 1px solid transparent;
            border-radius: var(--border-radius-medium);
            color: var(--text-muted);
            display: block;
            font-size: 0.875rem;
            padding: var(--spacing-sm) var(--spacing-md);
            text-decoration: none;
            transition: all var(--transition-speed) ease;
        }}

        .nav-list a:hover,
        .nav-list a.active {{
            background: var(--surface);
            border-color: var(--border-light);
            color: var(--text);
        }}

        .nav-list a.active {{
            background: rgba(59, 130, 246, 0.12);
            border-color: rgba(59, 130, 246, 0.28);
            color: var(--text);
            position: relative;
        }}

        .nav-list a.active:before {{
            content: "";
            position: absolute;
            left: -1px;
            top: 8px;
            bottom: 8px;
            width: 2px;
            background: var(--accent);
            border-radius: 1px;
        }}

        .nav-list.collapsed {{ display: none; }}

        .docs-main {{
            padding: 0;
            display: flex;
            justify-content: center;
        }}

        .page-card {{
            background: var(--bg);
            border-radius: 0;
            box-shadow: none;
            overflow: hidden;
            position: relative;
            width: 100%;
            max-width: var(--content-max-width);
            margin: 0 auto;
        }}

        .shell-header {{
            border-bottom: 1px solid var(--border);
        }}

        .shell-header-inner {{
            padding: var(--spacing-2xl) var(--spacing-xl) var(--spacing-lg);
        }}

        .shell-brand-block {{ margin-bottom: var(--spacing-lg); }}

        .surface-badge {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-large);
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.75rem;
            font-weight: 600;
            letter-spacing: 0.05em;
            padding: var(--spacing-xs) var(--spacing-md);
            text-transform: uppercase;
            margin-bottom: var(--spacing-md);
        }}

        .shell-title {{
            font-size: 2.5rem;
            font-weight: 600;
            letter-spacing: -0.025em;
            line-height: 1.1;
            margin: 0 0 var(--spacing-md);
            color: var(--text);
        }}

        .shell-summary {{
            color: var(--text-muted);
            font-size: 1.125rem;
            margin: 0 0 var(--spacing-lg);
            line-height: 1.5;
        }}

        .surface-nav {{
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
        }}

        .surface-link {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            color: var(--text-muted);
            display: inline-flex;
            font-size: 0.875rem;
            font-weight: 500;
            padding: var(--spacing-sm) var(--spacing-md);
            text-decoration: none;
            transition: all var(--transition-speed) ease;
        }}

        .surface-link:hover,
        .surface-link.active {{
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        .content-container {{
            padding: var(--spacing-2xl) var(--spacing-xl);
        }}

        .content-container > :first-child {{ margin-top: 0; }}

        h1, h2, h3, h4, h5, h6 {{
            color: var(--text);
            font-weight: 600;
            line-height: 1.25;
            margin-top: var(--spacing-2xl);
            margin-bottom: var(--spacing-lg);
        }}

        h1 {{
            font-size: 2rem;
            letter-spacing: -0.025em;
            border-bottom: 1px solid var(--border);
            padding-bottom: var(--spacing-lg);
        }}

        h2 {{
            font-size: 1.5rem;
            letter-spacing: -0.025em;
        }}

        h3 {{
            font-size: 1.25rem;
        }}

        h4 {{
            font-size: 1rem;
        }}

        p, li, td, th {{ 
            color: var(--text-muted); 
            font-size: 1rem;
            line-height: 1.6;
        }}

        strong {{ 
            color: var(--text);
            font-weight: 600;
        }}

        a {{ 
            color: var(--accent); 
            text-decoration: none;
            transition: color var(--transition-speed) ease;
        }}

        a:hover {{ 
            color: var(--accent-hover);
        }}

        ul, ol {{ 
            margin: var(--spacing-lg) 0 var(--spacing-lg); 
            padding-left: var(--spacing-xl);
        }}

        li + li {{ margin-top: var(--spacing-sm); }}

        code {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-small);
            color: var(--text);
            font-family: var(--font-code);
            font-size: 0.875em;
            padding: 2px var(--spacing-xs);
        }}

        pre {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            overflow-x: auto;
            padding: var(--spacing-lg);
            margin: var(--spacing-lg) 0;
        }}

        pre code {{
            background: transparent;
            border: none;
            padding: 0;
            font-size: 0.875rem;
            line-height: 1.5;
        }}

        .code-block {{ 
            margin: var(--spacing-lg) 0; 
            position: relative;
        }}

        .copy-btn {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-small);
            color: var(--text-muted);
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            font-family: var(--font-main);
            font-size: 0.75rem;
            font-weight: 500;
            gap: var(--spacing-xs);
            padding: var(--spacing-xs) var(--spacing-sm);
            position: absolute;
            right: var(--spacing-md);
            top: var(--spacing-md);
            transition: all var(--transition-speed) ease;
        }}

        .copy-btn:hover {{ 
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        .table-wrapper {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            margin: var(--spacing-lg) 0;
            overflow-x: auto;
        }}

        table {{ 
            border-collapse: collapse; 
            width: 100%;
        }}

        th, td {{
            border-bottom: 1px solid var(--border);
            padding: var(--spacing-md);
            text-align: left;
        }}

        th {{
            background: var(--bg);
            color: var(--text);
            font-size: 0.875rem;
            font-weight: 600;
            letter-spacing: 0.025em;
            text-transform: uppercase;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        .note {{
            background: var(--surface);
            border: 1px solid var(--border-light);
            border-left: 4px solid var(--accent);
            border-radius: var(--border-radius-medium);
            margin: var(--spacing-lg) 0;
            padding: var(--spacing-md);
        }}

        .footer {{
            border-top: 1px solid var(--border);
            color: var(--text-muted);
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-md);
            margin-top: var(--spacing-2xl);
            padding: var(--spacing-lg) 0;
            font-size: 0.875rem;
        }}

        .footer-runtime {{
            display: flex;
            flex-wrap: wrap;
            gap: var(--spacing-sm);
            margin-top: var(--spacing-md);
        }}

        .footer-runtime span {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-small);
            color: var(--text-muted);
            font-size: 0.75rem;
            padding: var(--spacing-xs) var(--spacing-sm);
        }}

        .footer-runtime span.accent {{
            background: var(--accent);
            border-color: var(--accent);
            color: white;
        }}

        /* OpenAPI overrides */
        .plexi-openapi-page #swagger-ui,
        .plexi-openapi-page redoc {{
            display: block;
            padding: 0;
            position: relative;
            z-index: 1;
        }}

        .plexi-openapi-page .swagger-ui {{ color: var(--text); }}

        .plexi-openapi-page .swagger-ui .topbar {{ display: none; }}

        .plexi-openapi-page .swagger-ui .info,
        .plexi-openapi-page .swagger-ui .scheme-container,
        .plexi-openapi-page .swagger-ui .opblock,
        .plexi-openapi-page .swagger-ui .responses-wrapper,
        .plexi-openapi-page .swagger-ui .parameters-container,
        .plexi-openapi-page .swagger-ui .model-box {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            box-shadow: none;
        }}

        .plexi-openapi-page .swagger-ui .scheme-container {{
            margin: var(--spacing-lg) 0;
            padding: var(--spacing-md);
        }}

        .plexi-openapi-page .swagger-ui .info .title,
        .plexi-openapi-page .swagger-ui .info hgroup.main h2,
        .plexi-openapi-page .swagger-ui .info h1,
        .plexi-openapi-page .swagger-ui .opblock-tag {{
            color: var(--text);
            font-family: var(--font-main);
        }}

        .plexi-openapi-page .swagger-ui .info p,
        .plexi-openapi-page .swagger-ui .info li,
        .plexi-openapi-page .swagger-ui .markdown p,
        .plexi-openapi-page .swagger-ui .markdown li,
        .plexi-openapi-page .swagger-ui .response-col_description__inner p {{
            color: var(--text-muted);
        }}

        .plexi-openapi-page .swagger-ui .opblock {{
            overflow: hidden;
        }}

        .plexi-openapi-page .swagger-ui .opblock-summary {{
            align-items: center;
            border-color: var(--border);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-get {{
            background: var(--surface);
            border-color: var(--border);
            border-left: 3px solid rgba(34, 197, 94, 0.55);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-post,
        .plexi-openapi-page .swagger-ui .opblock.opblock-put,
        .plexi-openapi-page .swagger-ui .opblock.opblock-patch {{
            background: var(--surface);
            border-color: var(--border);
            border-left: 3px solid rgba(59, 130, 246, 0.55);
        }}

        .plexi-openapi-page .swagger-ui .opblock.opblock-delete {{
            background: var(--surface);
            border-color: var(--border);
            border-left: 3px solid rgba(239, 68, 68, 0.55);
        }}

        .plexi-openapi-page .swagger-ui .btn,
        .plexi-openapi-page .swagger-ui button,
        .plexi-openapi-page .swagger-ui select,
        .plexi-openapi-page .swagger-ui input,
        .plexi-openapi-page .swagger-ui textarea {{
            border-radius: var(--border-radius-small);
            font-family: var(--font-main);
        }}

        .plexi-openapi-page .swagger-ui input,
        .plexi-openapi-page .swagger-ui textarea,
        .plexi-openapi-page .swagger-ui select {{
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .btn.authorize,
        .plexi-openapi-page .swagger-ui .btn.execute,
        .plexi-openapi-page .swagger-ui .download-url-wrapper .select-label select {{
            border-color: var(--accent);
        }}
        
        .plexi-openapi-page .swagger-ui .btn.execute,
        .plexi-openapi-page .swagger-ui .btn.authorize {{
            background: var(--accent);
            color: white;
        }}

        .plexi-openapi-page .swagger-ui table tbody tr td,
        .plexi-openapi-page .swagger-ui table thead tr th,
        .plexi-openapi-page .swagger-ui .parameter__name,
        .plexi-openapi-page .swagger-ui .response-col_status {{
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .model,
        .plexi-openapi-page .swagger-ui .prop-type,
        .plexi-openapi-page .swagger-ui .tab li,
        .plexi-openapi-page .swagger-ui .parameter__type,
        .plexi-openapi-page .swagger-ui .parameter__deprecated,
        .plexi-openapi-page .swagger-ui .response-col_links {{
            color: var(--text-muted);
        }}

        .plexi-openapi-page .swagger-ui section.models {{
            border: 1px solid var(--border);
            border-radius: var(--border-radius-medium);
            overflow: hidden;
        }}

        .plexi-openapi-page .swagger-ui section.models h4,
        .plexi-openapi-page .swagger-ui section.models h5 {{
            color: var(--text);
        }}

        .plexi-openapi-page .swagger-ui .model-toggle:after {{
            background: var(--accent);
        }}

        .plexi-openapi-page .menu-content,
        .plexi-openapi-page [role="search"] input,
        .plexi-openapi-page .api-content,
        .plexi-openapi-page .redoc-json,
        .plexi-openapi-page .redoc-markdown code,
        .plexi-openapi-page .redoc-markdown pre {{
            font-family: var(--font-main) !important;
        }}

        .plexi-openapi-page .menu-content {{
            background: var(--surface) !important;
            border-right: 1px solid var(--border) !important;
        }}

        .plexi-openapi-page .api-content {{
            background: transparent !important;
        }}

        .plexi-openapi-page .api-info h1,
        .plexi-openapi-page h1,
        .plexi-openapi-page h2,
        .plexi-openapi-page h3,
        .plexi-openapi-page h4,
        .plexi-openapi-page h5 {{
            color: var(--text) !important;
        }}

        .plexi-openapi-page .swagger-ui,
        .plexi-openapi-page .swagger-ui .markdown,
        .plexi-openapi-page .swagger-ui .renderedMarkdown,
        .plexi-openapi-page .swagger-ui .opblock-summary-description,
        .plexi-openapi-page .swagger-ui .response-col_description__inner,
        .plexi-openapi-page .swagger-ui .parameter__name,
        .plexi-openapi-page .swagger-ui .parameter__type,
        .plexi-openapi-page .swagger-ui .prop-type,
        .plexi-openapi-page .swagger-ui .model,
        .plexi-openapi-page .swagger-ui .model-title,
        .plexi-openapi-page .swagger-ui .tab li {{
            color: var(--text-muted) !important;
        }}

        .plexi-openapi-page .swagger-ui .opblock-summary-method,
        .plexi-openapi-page .swagger-ui .opblock-tag,
        .plexi-openapi-page .swagger-ui .info .title,
        .plexi-openapi-page .swagger-ui h1,
        .plexi-openapi-page .swagger-ui h2,
        .plexi-openapi-page .swagger-ui h3,
        .plexi-openapi-page .swagger-ui h4,
        .plexi-openapi-page .swagger-ui h5 {{
            color: var(--text) !important;
        }}

        .plexi-openapi-page [role="search"] input {{
            background: var(--surface) !important;
            border: 1px solid var(--border) !important;
            border-radius: var(--border-radius-medium) !important;
            box-shadow: none !important;
            color: var(--text) !important;
        }}

        .plexi-openapi-page code,
        .plexi-openapi-page pre,
        .plexi-openapi-page table {{
            border-color: var(--border) !important;
        }}

        .plexi-openapi-page pre,
        .plexi-openapi-page code {{
            background: var(--surface) !important;
            border-radius: var(--border-radius-small) !important;
        }}

        @media (max-width: 1024px) {{
            .docs-layout {{ grid-template-columns: 1fr; }}
            .sidebar {{
                border-right: 0;
                border-bottom: 1px solid var(--border);
                height: auto;
                position: relative;
                top: auto;
            }}
            .content-container {{
                padding: var(--spacing-xl) var(--spacing-lg);
            }}
        }}

        @media (max-width: 768px) {{
            .shell-header {{
                padding: var(--spacing-lg) var(--spacing-md);
            }}
            .content-container {{
                padding: var(--spacing-lg) var(--spacing-md);
            }}
            .shell-title {{
                font-size: 2rem;
            }}
            .surface-link {{ 
                width: 100%; 
                justify-content: center; 
            }}
        }}
    """
