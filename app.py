"""Live map of Sofia: public transport by line (with stops and arrival predictions) and air quality."""
import asyncio
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from nicegui import run, ui

import air
import theme
from markers import air_icon, badge_icon, direction_color, line_colors, station_icon, stop_icon
from transit import TYPE_COLORS, TYPE_NAMES, Transit

SOFIA = (42.6977, 23.3219)
# route_type (None = any) -> (label, material icon)
KINDS = {None: ("Всички", "directions_transit"), 3: ("Автобус", "directions_bus"), 11: ("Тролей", "electric_bolt"), 0: ("Трамвай", "tram")}
REFRESH_S = 60
SIDEBAR_PX = 390  # width of the sidebar; the map keeps clear of it when zooming to results
SOFIA_TZ = ZoneInfo("Europe/Sofia")

transit = None  # shared, read-only timetable; built once
transit_loading = False


def clock(seconds: int) -> str:
    """HH:MM from a GTFS time of day (hours may run past 24 after midnight)."""
    return f"{seconds // 3600 % 24:02d}:{seconds // 60 % 60:02d}"


def natural(name: str):
    """Sort key so 2 < 9 < 10 < 72 < 280 < TM1."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def chip(text: str, color: str, text_color: str = "#fff"):
    """A rounded line badge, like the ones on the map."""
    return ui.label(text).classes("chip").style(f"background:{color};color:{text_color}")


@ui.page("/")
async def index():
    global transit, transit_loading
    # Per-page state: each browser tab has its own map, layers and selection.
    route_ids = []
    # colors: line -> color when showing a whole type; stop: (id, name) of the open arrivals panel;
    # last: (line, type) to restore when coming back from the air-quality tab
    state = {"mode": "transport", "title": "", "colors": None, "type_view": None, "stop": None, "air_data": None, "last": None, "closed": False}
    layers, route_layers, stop_layers, station_layers, air_layers = [], [], [], [], []

    theme.apply()
    dark = ui.dark_mode(False)
    m = ui.leaflet(center=SOFIA, zoom=12).style("position:fixed;inset:0;width:100%;height:100%;z-index:0")
    spinner = ui.spinner(size="xl").classes("absolute-center z-[1000]")

    # ---- sidebar --------------------------------------------------------------------------------------------------
    sidebar = ui.element("div").classes("glass sidebar absolute top-3 left-3 bottom-3 z-[1000] flex flex-col p-4 gap-3").style("width:min(390px,calc(100vw - 24px))")
    with sidebar:
        with ui.row().classes("items-center justify-between no-wrap w-full"):
            with ui.row().classes("items-center gap-3 no-wrap"):
                ui.icon("directions_transit").classes("text-white text-2xl p-2").style("border-radius:14px;background:linear-gradient(135deg,#2563eb,#7c3aed)")
                with ui.column().classes("gap-0"):
                    ui.label("София на живо").classes("text-lg font-bold leading-tight")
                    ui.label("транспорт и качество на въздуха").classes("text-xs muted")
            with ui.row().classes("gap-0 no-wrap"):
                dark_btn = ui.button(icon="dark_mode", on_click=lambda: toggle_dark()).props("flat round dense").tooltip("Тъмен режим")

        with ui.tabs(value="transport", on_change=lambda e: on_tab(e.value)).props("dense no-caps align=justify indicator-color=primary").classes("w-full") as tabs:
            ui.tab("transport", label="Транспорт", icon="directions_bus")
            ui.tab("air", label="Въздух", icon="air")
        tabs.set_visibility(False)  # until the timetable has loaded

        # transport controls
        with ui.column().classes("w-full gap-2") as transport_box:
            kind = {"type": None}
            kind_buttons = {}

            def pick_kind(t):
                kind["type"] = t
                for k, b in kind_buttons.items():
                    b.props(remove="flat unelevated outline")
                    b.props("unelevated color=primary text-color=white" if k == t else "flat color=grey-6")
                    b.classes(remove="soft" if k == t else "", add="" if k == t else "soft")

            with ui.row().classes("w-full no-wrap gap-2"):
                for k, (label, icon) in KINDS.items():
                    kind_buttons[k] = ui.button(label, icon=icon, on_click=lambda _, k=k: pick_kind(k)).props("stack dense no-caps").classes("flex-1 kind-btn")
            line_input = ui.input(placeholder="Номер на линия, напр. 280, 9, TM1, 7А").props("outlined rounded dense clearable").classes("w-full")
            with line_input.add_slot("prepend"):
                ui.icon("search")
            error = ui.label().classes("text-negative text-sm")
            ui.button("Покажи на картата", icon="map", on_click=lambda: submit()).props("rounded no-caps unelevated color=primary").classes("w-full")
            ui.label("Оставете номера празен, за да видите всички линии от избрания вид.").classes("text-xs muted")
            line_input.on("keydown.enter", lambda: submit())
        transport_box.set_visibility(False)
        pick_kind(None)

        # results
        details = ui.column().classes("sheet-scroll w-full gap-3").style("flex:1 1 0;min-height:0;overflow-y:auto")
        with details:
            with ui.row().classes("soft w-full items-center no-wrap px-3 py-1 gap-2"):
                ui.icon("schedule").classes("muted")
                status = ui.label("Зареждане на разписанието…").classes("text-sm flex-1")
                back = ui.button(icon="arrow_back", on_click=lambda: show("", state["type_view"])).props("flat round dense").tooltip("Всички линии")
                back.set_visibility(False)
                ui.button(icon="refresh", on_click=lambda: refresh()).props("flat round dense").tooltip("Обнови")

            with ui.column().classes("w-full items-center gap-2 py-6") as empty_hint:
                ui.icon("touch_app").classes("text-4xl muted")
                ui.label("Изберете вид превозно средство и линия, за да видите къде са превозните средства на живо.").classes("text-sm muted text-center")
            empty_hint.set_visibility(False)

            # which color is which direction (single-line view only)
            with ui.column().classes("soft w-full p-3 gap-1") as direction_box:
                ui.label("Посока към").classes("text-xs font-semibold muted")
                direction_body = ui.column().classes("gap-1")
            direction_box.set_visibility(False)

            # arrivals at a bus stop, or first/last train at a metro station
            with ui.column().classes("soft w-full p-3 gap-1") as panel:
                with ui.row().classes("w-full items-center justify-between no-wrap"):
                    panel_title = ui.label().classes("text-base font-bold")
                    ui.button(icon="close", on_click=lambda: close_panel()).props("flat round dense")
                panel_sub = ui.label().classes("text-xs font-semibold muted")
                panel_body = ui.column().classes("gap-2 w-full")
            panel.set_visibility(False)

            # air-quality scale (only in that mode)
            with ui.column().classes("soft w-full p-3 gap-2") as air_legend:
                ui.label("Фини прахови частици ФПЧ2.5, µg/m³").classes("text-xs font-semibold muted")
                lower = 0
                for limit, color, label in air.BANDS:
                    rng = f"{lower}–{limit}" if limit != float("inf") else f"над {lower}"
                    with ui.row().classes("items-center gap-3 no-wrap"):
                        ui.element("span").style(f"width:16px;height:16px;border-radius:50%;background:{color};flex:none;box-shadow:inset 0 0 0 2px rgba(255,255,255,.7)")
                        ui.label(label).classes("text-sm flex-1")
                        ui.label(rng).classes("text-xs muted")
                    lower = limit
            air_legend.set_visibility(False)

    handle = ui.button(icon="chevron_left", on_click=lambda: set_sidebar(state["closed"])).props("flat dense").classes("panel-handle").tooltip("Скрий / покажи панела")

    # ---- sidebar / theme helpers ----------------------------------------------------------------------------------
    def set_sidebar(open_: bool):
        """Slide the panel in or out to the left."""
        state["closed"] = not open_
        for el in (sidebar, handle):
            el.classes(remove="closed" if open_ else "", add="" if open_ else "closed")
        handle.props(f"icon={'chevron_left' if open_ else 'chevron_right'}")

    def toggle_dark():
        dark.value = not dark.value
        dark_btn.props(f"icon={'light_mode' if dark.value else 'dark_mode'}")

    def clear(group):
        for layer in group:
            if layer in m.layers:
                m.remove_layer(layer)
        group.clear()

    def close_panel():
        state["stop"] = None
        panel.set_visibility(False)

    def show_stations():
        if station_layers:
            return
        for name, lat, lon, lines, stop_id in transit.metro_stations:  # fixed layer, below the vehicle badges
            station = m.marker(latlng=(lat, lon), options={"zIndexOffset": -1000})
            station.run_method(":setIcon", station_icon(lines, stop_id))
            station.run_method("bindTooltip", f"<b>Метро {name}</b><br>" + ", ".join(ln for ln, _ in lines), {"direction": "top", "offset": [0, -14]})
            station_layers.append(station)

    # ---- tabs -----------------------------------------------------------------------------------------------------
    async def on_tab(value):
        if value == "air":
            await show_air()
        else:
            await leave_air()

    async def leave_air():
        state["mode"] = "transport"
        clear(air_layers)
        state["air_data"] = None
        air_legend.set_visibility(False)
        transport_box.set_visibility(True)
        show_stations()
        if state["last"]:
            await show(*state["last"])
        else:
            empty_hint.set_visibility(True)
            status.set_text("Изберете линия")

    # ---- transport ------------------------------------------------------------------------------------------------
    async def submit():
        line = (line_input.value or "").strip()
        rtype = kind["type"]
        if not line and rtype is None:
            error.set_text("Въведете номер на линия или изберете вид превозно средство.")
            return
        if not transit.find_routes(line, rtype):
            what = f" ({KINDS[rtype][0].lower()})" if rtype is not None else ""
            error.set_text(f"Не е намерена линия „{line}“{what} в София.")
            return
        error.set_text("")
        state["type_view"] = None
        await show(line, rtype)

    async def show(line, rtype):
        """Show one line (with its stops), or (empty line) every line of a vehicle type."""
        state["mode"] = "transport"
        state["last"] = (line, rtype)
        clear(air_layers)
        state["air_data"] = None
        air_legend.set_visibility(False)
        empty_hint.set_visibility(False)
        show_stations()
        close_panel()
        direction_box.set_visibility(False)
        ids = transit.find_routes(line, rtype)
        if not line:
            state["type_view"] = rtype
        back.set_visibility(bool(line) and state["type_view"] is not None)
        state["colors"] = None if line else line_colors(sorted({transit.routes[i][0] for i in ids}, key=natural))
        title = state["title"] = f"Линия {line}" if line else f"Всички линии – {KINDS[rtype][0].lower()}"
        route_ids[:] = ids
        status.set_text(f"{title}: зареждане…")
        clear(route_layers)
        clear(stop_layers)
        clear(layers)
        # route lines and stops only for a single line: a whole-type view would be unreadable
        shapes, stops = await asyncio.gather(run.io_bound(transit.shapes, ids), run.io_bound(transit.stops_for, ids)) if line else ({}, [])
        if ids != route_ids:  # another selection arrived while loading
            return
        direction_body.clear()
        multi_type = len({transit.routes[rid][2] for rid in ids}) > 1
        for rid in ids:
            rtype_ = transit.routes[rid][2]
            for i, sid in enumerate(transit.route_shapes.get(rid, [])):
                if not shapes.get(sid):
                    continue
                color = direction_color(rtype_, i)
                # white casing under the colored line keeps the route readable on busy map areas
                route_layers.append(m.generic_layer(name="polyline", args=[shapes[sid], {"color": "#fff", "weight": 10, "opacity": 0.9, "lineCap": "round", "lineJoin": "round"}]))
                path = m.generic_layer(name="polyline", args=[shapes[sid], {"color": color, "weight": 6, "opacity": 0.95, "lineCap": "round", "lineJoin": "round"}])
                head = transit.shape_heads.get(sid, "")
                path.run_method("bindTooltip", f"→ {head}", {"sticky": True})
                route_layers.append(path)
                with direction_body:
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.element("span").style(f"width:26px;height:6px;border-radius:3px;background:{color};flex:none")
                        ui.label((f"{TYPE_NAMES.get(rtype_, '')} " if multi_type else "") + head).classes("text-sm")
        direction_box.set_visibility(bool(line) and bool(route_layers))
        for stop_id, name, lat, lon, stop_type, direction in stops:
            stop = m.marker(latlng=(lat, lon), options={"zIndexOffset": -500})
            stop.run_method(":setIcon", stop_icon(direction_color(stop_type, direction), stop_id))
            stop.run_method("bindTooltip", name, {"direction": "top", "offset": [0, -7]})
            stop_layers.append(stop)
        await refresh(fit=True)

    async def pick_stop(e):
        stop_id = e.args["id"]
        state["stop"] = (stop_id, transit.stops[stop_id][0])
        panel_title.set_text(state["stop"][1])
        panel_sub.set_text("Следващи пристигания")
        panel_body.clear()
        with panel_body:
            ui.label("Зареждане…").classes("text-sm muted")
        panel.set_visibility(True)
        await load_arrivals()

    def pick_station(e):
        """Metro station panel: first and last departure per line and direction, weekday and weekend."""
        stop_id = e.args["id"]
        state["stop"] = None  # not a bus stop: nothing to refresh
        panel_title.set_text("Метро " + transit.stops[stop_id][0])
        panel_sub.set_text("Първи и последен влак")
        panel_body.clear()
        times = transit.metro_times.get(stop_id, {})
        with panel_body:
            if not times:
                ui.label("Няма данни за разписанието.").classes("text-sm muted")
            for kind_, label in (("weekday", "Делник"), ("weekend", "Събота и неделя")):
                if kind_ not in times:
                    continue
                ui.label(label).classes("text-xs font-semibold q-mt-sm")
                for line, color, head, first, last in times[kind_]:
                    with ui.row().classes("items-center gap-3 no-wrap"):
                        chip(line, color, "#222" if color.lower() == "#ffde17" else "#fff")
                        with ui.column().classes("gap-0"):
                            ui.label(f"→ {head}").classes("text-xs muted")
                            ui.label(f"първи {clock(first)} · последен {clock(last)}").classes("text-sm")
        panel.set_visibility(True)

    async def load_arrivals():
        if not state["stop"] or not route_ids:
            return
        stop_id = state["stop"][0]
        try:
            arrivals, err = await run.io_bound(transit.arrivals, stop_id, list(route_ids)), ""
        except Exception as e:
            arrivals, err = [], f"Грешка при данните: {e}"
        if not state["stop"] or state["stop"][0] != stop_id:  # panel was closed or switched meanwhile
            return
        panel_body.clear()
        now = datetime.now(SOFIA_TZ).timestamp()
        with panel_body:
            if err:
                ui.label(err).classes("text-sm text-negative")
            elif not arrivals:
                ui.label("Няма предстоящи пристигания по тази линия.").classes("text-sm muted")
            for line, rtype, at, direction, head in arrivals:
                minutes = round((at - now) / 60)
                eta = "пристига" if minutes < 1 else f"след {minutes} мин"
                color = direction_color(rtype, direction)  # same color as that direction on the map
                with ui.row().classes("items-center gap-3 no-wrap w-full"):
                    chip(line, color)
                    with ui.column().classes("gap-0 flex-1"):
                        ui.label(eta).classes("text-sm font-semibold")
                        if head:
                            ui.label(f"→ {head}").classes("text-xs").style(f"color:{color}")
                    ui.label(datetime.fromtimestamp(at, SOFIA_TZ).strftime("%H:%M")).classes("text-sm muted")

    # ---- air quality ----------------------------------------------------------------------------------------------
    async def show_air():
        state["mode"] = "air"
        route_ids.clear()
        for group in (route_layers, stop_layers, layers, station_layers):
            clear(group)
        close_panel()
        direction_box.set_visibility(False)
        empty_hint.set_visibility(False)
        transport_box.set_visibility(False)
        back.set_visibility(False)
        air_legend.set_visibility(True)
        m.run_map_method("fitBounds", [[42.62, 23.20], [42.77, 23.45]], {"paddingTopLeft": [SIDEBAR_PX + 30, 20], "paddingBottomRight": [20, 20]})
        status.set_text("Качество на въздуха: зареждане…")
        await refresh_air()

    async def refresh_air():
        try:
            data = await run.io_bound(air.sensors)
        except Exception as e:
            status.set_text(f"Грешка при данните: {e}")
            return
        if state["mode"] != "air":
            return
        if data is not state["air_data"]:  # sensors are cached for 5 min; only redraw on new data
            state["air_data"] = data
            clear(air_layers)
            for s in data:
                color, label = air.band(s["pm25"])
                marker = m.marker(latlng=(s["lat"], s["lon"]))
                marker.run_method(":setIcon", air_icon(color, s["pm25"]))
                tip = f"<b>ФПЧ2.5: {s['pm25']:.1f} µg/m³</b> · {label}"
                if s["pm10"] is not None:
                    tip += f"<br>ФПЧ10: {s['pm10']:.1f} µg/m³"
                tip += f"<br>Обновено преди {s['age_min']} мин"
                marker.run_method("bindTooltip", tip, {"direction": "top", "offset": [0, -12]})
                air_layers.append(marker)
        at = datetime.now(SOFIA_TZ).strftime("%H:%M:%S")
        status.set_text(f"Качество на въздуха: {len(data)} датчика · {at}")

    # ---- live refresh ---------------------------------------------------------------------------------------------
    async def refresh(fit=False):
        if state["mode"] == "air":
            await refresh_air()
            return
        if not route_ids:
            return
        ids = list(route_ids)
        try:
            vehicles = await run.io_bound(transit.vehicles, ids)
        except Exception as e:  # keep the last known positions on a flaky feed
            status.set_text(f"Грешка при данните: {e}")
            return
        if ids != route_ids:  # line changed while we were fetching
            return
        clear(layers)
        per_line = state["colors"] is not None
        for v in vehicles:
            color = state["colors"].get(v.line, "#555") if per_line else direction_color(v.route_type, v.direction)
            layer = m.marker(latlng=(v.lat, v.lon))
            layer.run_method(":setIcon", badge_icon(color, v.line if per_line else "", v.route_type))
            tip = f"<b>{TYPE_NAMES.get(v.route_type, 'Превозно средство')} {v.line}</b> · {v.vehicle_id}<br>{v.speed_kmh:.0f} км/ч"
            if v.next_stop:
                tip += f"<br>Следваща спирка: {v.next_stop}"
            if v.headsign:
                tip += f"<br>Посока: {v.headsign}"
            tip += f"<br>Обновено преди {v.age_s} с"
            if per_line:  # hover for details (a click on the badge isolates the line)
                layer.run_method("bindTooltip", tip, {"direction": "top", "offset": [0, -12]})
            else:
                layer.run_method("bindPopup", tip)
            layers.append(layer)
        name = state["title"]
        at = datetime.now(SOFIA_TZ).strftime("%H:%M:%S")
        status.set_text(f"{name}: {len(vehicles)} превозни средства · {at}" if vehicles else f"{name}: в момента няма движещи се превозни средства · {at}")
        if fit and vehicles:
            m.run_map_method("fitBounds", [[v.lat, v.lon] for v in vehicles], {"paddingTopLeft": [SIDEBAR_PX + 30, 60], "paddingBottomRight": [60, 60], "maxZoom": 15})
        await load_arrivals()

    ui.on("line_pick", lambda e: show(e.args["line"], e.args["type"]))
    ui.on("stop_pick", pick_stop)
    ui.on("station_pick", pick_station)
    ui.timer(REFRESH_S, refresh)

    if transit is None:
        if not transit_loading:
            transit_loading = True
            transit = await run.io_bound(Transit)
        else:  # another tab is already building it
            while transit is None:
                await ui.context.client.connected()
                await run.io_bound(lambda: __import__("time").sleep(0.5))
    # icon commands are dropped unless the browser is connected and the map has initialised
    await ui.context.client.connected()
    await m.initialized()
    show_stations()
    spinner.delete()
    tabs.set_visibility(True)
    transport_box.set_visibility(True)
    empty_hint.set_visibility(True)
    status.set_text("Изберете линия")
    if await ui.run_javascript("window.innerWidth") < 700:  # phones: start with the map, open the panel from the menu button
        set_sidebar(False)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="София – градски транспорт на живо", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), reload=False, show=False)
