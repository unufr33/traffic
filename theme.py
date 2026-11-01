"""Look and feel: colors, glass panels, dark mode and map styling."""
from nicegui import ui

FONTS = '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&subset=cyrillic&display=swap" rel="stylesheet">'

CSS = """
:root{--panel:rgba(255,255,255,.88);--panel-border:rgba(255,255,255,.75);--text:#0f172a;--muted:#64748b;--soft:rgba(15,23,42,.05);--line:rgba(15,23,42,.09)}
body.body--dark{--panel:rgba(17,24,39,.88);--panel-border:rgba(255,255,255,.09);--text:#e5e7eb;--muted:#9ca3af;--soft:rgba(255,255,255,.07);--line:rgba(255,255,255,.12)}
body{margin:0;font-family:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
.q-page{padding:0!important}

.glass{background:var(--panel);-webkit-backdrop-filter:blur(18px) saturate(1.5);backdrop-filter:blur(18px) saturate(1.5);
  border:1px solid var(--panel-border);border-radius:22px;box-shadow:0 14px 44px rgba(2,6,23,.24);color:var(--text)}
.glass .q-field--outlined .q-field__control{background:var(--soft)}
.muted{color:var(--muted)}
.soft{background:var(--soft);border-radius:16px}
.chip{border-radius:999px;padding:1px 10px;font-weight:700;min-width:40px;text-align:center;line-height:1.5;font-size:13px}
.kind-btn{border-radius:14px!important;min-height:58px}
.sheet-scroll{scrollbar-width:thin}

/* sidebar slides off to the left; a handle on its edge hides and shows it */
.sidebar{transition:transform .3s ease,opacity .3s ease}
.sidebar.closed{transform:translateX(calc(-100% - 24px));opacity:0;pointer-events:none}
.panel-handle{position:fixed;top:calc(50% - 34px);left:calc(min(390px,100vw - 24px) + 12px);z-index:1000;width:26px;height:68px;
  padding:0;border-radius:0 14px 14px 0;transition:left .3s ease;cursor:pointer;
  background:var(--panel);-webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);
  border:1px solid var(--panel-border);border-left:none;box-shadow:6px 6px 20px rgba(2,6,23,.2);color:var(--text)}
.panel-handle.closed{left:0}

/* map controls: zoom moves to the bottom-right, away from the sidebar */
.leaflet-top.leaflet-left{left:auto!important;right:12px!important;top:auto!important;bottom:34px!important}
.leaflet-control-zoom{border:none!important;border-radius:14px!important;overflow:hidden;box-shadow:0 8px 24px rgba(2,6,23,.25)!important}
.leaflet-control-zoom a{width:38px!important;height:38px!important;line-height:38px!important}
.leaflet-tooltip,.leaflet-popup-content-wrapper{border-radius:12px;border:none;box-shadow:0 10px 28px rgba(2,6,23,.28);font-family:inherit;font-size:13px}

/* dark mode: the street map is inverted so Cyrillic labels stay; markers sit in other panes and are untouched */
body.body--dark .leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.92) contrast(.9) saturate(.7)}
body.body--dark .leaflet-container{background:#1b2230}
body.body--dark .leaflet-tooltip,body.body--dark .leaflet-popup-content-wrapper,body.body--dark .leaflet-popup-tip{background:#1f2937;color:#e5e7eb}
body.body--dark .leaflet-tooltip-top:before{border-top-color:#1f2937}
body.body--dark .leaflet-control-attribution{background:rgba(17,24,39,.7)!important;color:#9ca3af}
body.body--dark .leaflet-control-attribution a{color:#93c5fd}
"""


def apply() -> None:
    """Install fonts, CSS and the color palette on the current page."""
    ui.colors(primary="#2563eb", secondary="#7c3aed", accent="#0ea5e9")
    ui.add_head_html(FONTS)
    ui.add_css(CSS)
