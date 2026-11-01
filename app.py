"""Live map of Sofia: public transport by line (with stops and arrival predictions) and air quality."""
import asyncio
import colorsys
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from nicegui import run, ui

import air
from transit import TYPE_COLORS, TYPE_NAMES, Transit

SOFIA = (42.6977, 23.3219)
# route_type (None = any) -> (label, material icon)
KINDS = {None: ("Всички", "directions_transit"), 3: ("Автобус", "directions_bus"), 11: ("Тролей", "electric_bolt"), 0: ("Трамвай", "tram")}
REFRESH_S = 60
SOFIA_TZ = ZoneInfo("Europe/Sofia")

transit = None  # shared, read-only timetable; built once
transit_loading = False


def clock(seconds: int) -> str:
    """HH:MM from a GTFS time of day (hours may run past 24 after midnight)."""
    return f"{seconds // 3600 % 24:02d}:{seconds // 60 % 60:02d}"


def natural(name: str):
    """Sort key so 2 < 9 < 10 < 72 < 280 < TM1."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def badge_icon(color: str, text: str = "", route_type: int | None = None) -> str:
    """JS for a Leaflet divIcon: a colored pill with the line number inside (plain dot when no text)."""
    h = 26 if text else 20
    w = max(h, 9 * len(text) + 14) if text else h
    pick = ""
    if text:  # clicking a numbered badge asks the server to isolate that line
        pick = f""" onclick='emitEvent("line_pick", {json.dumps({"line": text, "type": route_type})})'"""
    html = (
        f'<div{pick} style="cursor:{"pointer" if text else "default"};box-sizing:border-box;width:{w}px;height:{h}px;border-radius:{h // 2}px;background:{color};'
        f'border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.55);color:#fff;'
        f'font:700 12px/{h - 4}px sans-serif;text-align:center;white-space:nowrap">{text}</div>'
    )
    return f"L.divIcon({{html: {json.dumps(html)}, className: '', iconSize: [{w}, {h}], iconAnchor: [{w // 2}, {h // 2}]}})"


def station_icon(lines, stop_id: str) -> str:
    """JS for a metro station badge: an "М" in a white disc ringed in the colors of the lines that stop there."""
    colors = [c for _, c in lines]
    if len(colors) == 1:
        ring = colors[0]
    else:
        step = 100 / len(colors)
        ring = "conic-gradient(" + ",".join(f"{c} {i * step:.0f}% {(i + 1) * step:.0f}%" for i, c in enumerate(colors)) + ")"
    html = (
        f"<div onclick='emitEvent(\"station_pick\", {json.dumps({'id': stop_id})})' "
        f'style="cursor:pointer;box-sizing:border-box;width:26px;height:26px;border-radius:50%;background:{ring};'
        f'box-shadow:0 1px 4px rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center">'
        f'<div style="width:18px;height:18px;border-radius:50%;background:#fff;color:#222;'
        f'font:800 12px/18px sans-serif;text-align:center">М</div></div>'
    )
    return f"L.divIcon({{html: {json.dumps(html)}, className: '', iconSize: [26, 26], iconAnchor: [13, 13]}})"


def stop_icon(color: str, stop_id: str) -> str:
    """JS for a stop marker: a white disc ringed in the line's color. Clicking asks the server for arrivals."""
    html = (
        f"<div onclick='emitEvent(\"stop_pick\", {json.dumps({'id': stop_id})})' "
        f'style="cursor:pointer;box-sizing:border-box;width:12px;height:12px;border-radius:50%;background:#fff;'
        f'border:3px solid {color};box-shadow:0 1px 2px rgba(0,0,0,.5)"></div>'
    )
    return f"L.divIcon({{html: {json.dumps(html)}, className: '', iconSize: [12, 12], iconAnchor: [6, 6]}})"


def air_icon(color: str, pm25: float) -> str:
    """JS for a sensor badge: the rounded PM2.5 value on the air-quality band color."""
    text = f"{pm25:.0f}"
    w = max(24, 8 * len(text) + 12)
    fg = "#222" if color in air.DARK_TEXT else "#fff"
    html = (
        f'<div style="box-sizing:border-box;width:{w}px;height:24px;border-radius:12px;background:{color};'
        f'border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.55);color:{fg};'
        f'font:700 11px/20px sans-serif;text-align:center">{text}</div>'
    )
    return f"L.divIcon({{html: {json.dumps(html)}, className: '', iconSize: [{w}, 24], iconAnchor: [{w // 2}, 12]}})"


def direction_color(route_type: int, direction: int | None) -> str:
    """Line color for a vehicle type; the second direction gets the hue rotated by ~160° so the two read apart."""
    base = TYPE_COLORS.get(route_type, "#555555")
    if not direction:
        return base
    r, g, b = (int(base[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + 0.45) % 1, l, sat)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def line_colors(lines):
    """One distinct color per line, spread around the hue wheel (golden angle)."""
    return {ln: f"hsl({i * 137.508 % 360:.0f},{80 if i % 2 else 65}%,{42 if i % 3 else 52}%)" for i, ln in enumerate(lines)}


@ui.page("/")
async def index():
    global transit, transit_loading
    # Per-page state: each browser tab has its own map, layers and selection.
    route_ids = []
    # colors: line -> color when showing a whole type; stop: (id, name) of the open arrivals panel
    state = {"mode": "transport", "title": "", "colors": None, "type_view": None, "stop": None, "air_data": None}
    layers, route_layers, stop_layers, station_layers, air_layers = [], [], [], [], []

    ui.add_head_html("<style>body{margin:0}.q-page{padding:0!important}</style>")
    m = ui.leaflet(center=SOFIA, zoom=12).classes("w-full").style("height:100vh;width:100vw")
    with ui.row().classes("absolute top-3 right-3 z-[1000] items-center bg-white rounded shadow px-3 py-1 gap-3"):
        status = ui.label("Зареждане на разписанието…")
        back = ui.button("Всички линии", icon="arrow_back", on_click=lambda: show("", state["type_view"])).props("dense flat")
        back.set_visibility(False)
        ui.button("Обнови", icon="refresh", on_click=lambda: refresh()).props("dense flat")
        ui.button("Смени", icon="search", on_click=lambda: ask_line()).props("dense flat")
    spinner = ui.spinner(size="xl").classes("absolute-center z-[1000]")

    # arrivals panel (opens when a stop is clicked)
    with ui.card().classes("absolute bottom-6 left-3 z-[1000] w-72 p-3 gap-1 overflow-auto").style("max-height:75vh") as panel:
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            panel_title = ui.label().classes("text-subtitle1 text-bold")
            ui.button(icon="close", on_click=lambda: close_panel()).props("flat dense round")
        panel_sub = ui.label().classes("text-xs text-grey-7")
        panel_body = ui.column().classes("gap-1 w-full")
    panel.set_visibility(False)

    # which color is which direction (single-line view only)
    with ui.card().classes("absolute bottom-6 right-3 z-[1000] p-3 gap-1") as direction_card:
        ui.label("Посока към").classes("text-xs text-grey-7")
        direction_body = ui.column().classes("gap-1")
    direction_card.set_visibility(False)

    # air-quality scale (only in that mode)
    with ui.card().classes("absolute bottom-6 left-3 z-[1000] p-3 gap-1") as air_legend:
        ui.label("ФПЧ2.5, µg/m³").classes("text-xs text-grey-7")
        lower = 0
        for limit, color, label in air.BANDS:
            rng = f"{lower}–{limit}" if limit != float("inf") else f">{lower}"
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.element("span").style(f"width:14px;height:14px;border-radius:50%;background:{color};flex:none")
                ui.label(f"{label} ({rng})").classes("text-sm")
            lower = limit
    air_legend.set_visibility(False)

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label("Какво искате да следите?").classes("text-h6")
        mode = {"v": "transport"}
        mode_buttons = {}

        def pick_mode(v):
            mode["v"] = v
            for k, b in mode_buttons.items():
                b.props(f"color={'primary' if k == v else 'grey-7'}")
                b.props("unelevated" if k == v else "outline")
            transport_box.set_visibility(v == "transport")
            air_box.set_visibility(v == "air")

        with ui.row().classes("w-full justify-between no-wrap"):
            for k, (label, icon) in {"transport": ("Транспорт", "directions_bus"), "air": ("Качество на въздуха", "air")}.items():
                mode_buttons[k] = ui.button(label, icon=icon, on_click=lambda _, k=k: pick_mode(k)).props("stack dense no-caps").classes("flex-1")

        with ui.column().classes("w-full gap-2") as transport_box:
            kind = {"type": None}
            kind_buttons = {}

            def pick_kind(t):
                kind["type"] = t
                for k, b in kind_buttons.items():
                    b.props(f"color={'primary' if k == t else 'grey-7'}")
                    b.props("unelevated" if k == t else "outline")

            with ui.row().classes("w-full justify-between no-wrap"):
                for k, (label, icon) in KINDS.items():
                    kind_buttons[k] = ui.button(label, icon=icon, on_click=lambda _, k=k: pick_kind(k)).props("stack dense no-caps").classes("flex-1")
            pick_kind(None)
            line_input = ui.input(label="Номер на линия (по избор при избран вид)", placeholder="напр. 280, 9, TM1, 7А").props("autofocus outlined dense").classes("w-full")
        with ui.column().classes("w-full") as air_box:
            ui.label("Измервания на фини прахови частици (ФПЧ2.5) от гражданските датчици на sensor.community около София.").classes("text-sm text-grey-8")
        pick_mode("transport")
        error = ui.label().classes("text-negative text-sm")
        line_input.on("keydown.enter", lambda: submit())
        ui.button("Покажи на картата", on_click=lambda: submit()).classes("w-full")

    def ask_line():
        error.set_text("")
        dialog.open()

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

    async def submit():
        if mode["v"] == "air":
            dialog.close()
            await show_air()
            return
        line = line_input.value.strip()
        rtype = kind["type"]
        if not line and rtype is None:
            error.set_text("Въведете номер на линия или изберете вид превозно средство.")
            return
        if not transit.find_routes(line, rtype):
            what = f" ({KINDS[rtype][0].lower()})" if rtype is not None else ""
            error.set_text(f"Не е намерена линия „{line}“{what} в София.")
            return
        dialog.close()
        state["type_view"] = None
        await show(line, rtype)

    async def show(line, rtype):
        """Show one line (with its stops), or (empty line) every line of a vehicle type."""
        state["mode"] = "transport"
        clear(air_layers)
        state["air_data"] = None
        air_legend.set_visibility(False)
        show_stations()
        close_panel()
        direction_card.set_visibility(False)
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
                        ui.element("span").style(f"width:22px;height:6px;border-radius:3px;background:{color};flex:none")
                        ui.label((f"{TYPE_NAMES.get(rtype_, '')} " if multi_type else "") + head).classes("text-sm")
        direction_card.set_visibility(bool(line) and bool(route_layers))
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
            ui.label("Зареждане…").classes("text-sm text-grey-7")
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
                ui.label("Няма данни за разписанието.").classes("text-sm text-grey-7")
            for kind, label in (("weekday", "Делник"), ("weekend", "Събота и неделя")):
                if kind not in times:
                    continue
                ui.label(label).classes("text-xs text-bold q-mt-sm")
                for line, color, head, first, last in times[kind]:
                    with ui.row().classes("items-center gap-2 no-wrap"):
                        ui.label(line).style(f"background:{color};color:{'#222' if color.lower() == '#ffde17' else '#fff'};border-radius:10px;padding:0 8px;font-weight:700;min-width:38px;text-align:center")
                        with ui.column().classes("gap-0"):
                            ui.label(f"→ {head}").classes("text-xs")
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
                ui.label("Няма предстоящи пристигания по тази линия.").classes("text-sm text-grey-7")
            for line, rtype, at, direction, head in arrivals:
                minutes = round((at - now) / 60)
                eta = "пристига" if minutes < 1 else f"след {minutes} мин"
                clock = datetime.fromtimestamp(at, SOFIA_TZ).strftime("%H:%M")
                color = direction_color(rtype, direction)  # same color as that direction on the map
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.label(line).style(f"background:{color};color:#fff;border-radius:10px;padding:0 8px;font-weight:700;min-width:38px;text-align:center")
                    with ui.column().classes("gap-0"):
                        ui.label(f"{eta} · {clock}").classes("text-sm")
                        if head:
                            ui.label(f"→ {head}").classes("text-xs").style(f"color:{color}")

    async def show_air():
        state["mode"] = "air"
        route_ids.clear()
        for group in (route_layers, stop_layers, layers, station_layers):
            clear(group)
        close_panel()
        direction_card.set_visibility(False)
        back.set_visibility(False)
        air_legend.set_visibility(True)
        m.run_map_method("setView", list(SOFIA), 12)
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
        status.set_text(f"{name}: {len(vehicles)} превозни средства в движение · {at}" if vehicles else f"{name}: в момента няма движещи се превозни средства · {at}")
        if fit and vehicles:
            m.run_map_method("fitBounds", [[v.lat, v.lon] for v in vehicles], {"padding": [60, 60], "maxZoom": 15})
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
    status.set_text("Изберете линия")
    spinner.delete()
    ask_line()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="София – градски транспорт на живо", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), reload=False, show=False)
