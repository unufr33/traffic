"""Map markers (Leaflet divIcons) and the colors they use."""
import colorsys
import json

import air
from transit import TYPE_COLORS


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
