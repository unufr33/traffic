"""Air quality: fine-dust (PM2.5) readings from open sensor.community sensors around Sofia."""
import math
import threading
import time
from datetime import datetime, timezone

import requests

URL = (
    "https://data.sensor.community/airrohr/v1/filter/area=42.6977,23.3219,20"
    "&type=SDS011,PMS5003,PMS7003,SPS30,HPM,PMS1003,PMS3003,NextPM"
)
TTL = 300  # the API caches for 5 minutes and asks not to be polled faster
MAX_AGE_MIN = 60  # ignore sensors that stopped reporting

# upper PM2.5 limit (µg/m³), color, label: the European Air Quality Index bands
BANDS = [
    (10, "#50f0e6", "Добро"),
    (20, "#50ccaa", "Задоволително"),
    (25, "#f0e641", "Умерено"),
    (50, "#ff5050", "Лошо"),
    (75, "#960032", "Много лошо"),
    (math.inf, "#7d2181", "Изключително лошо"),
]
DARK_TEXT = {"#50f0e6", "#50ccaa", "#f0e641"}  # bands light enough to need dark numbers

_lock = threading.Lock()
_cache = {"at": 0.0, "data": []}


def band(pm25: float):
    for limit, color, label in BANDS:
        if pm25 < limit:
            return color, label


def sensors():
    """Outdoor PM sensors with a recent reading: [{lat, lon, pm25, pm10, age_min}]. Cached for TTL seconds."""
    with _lock:
        if time.time() - _cache["at"] < TTL and _cache["data"]:
            return _cache["data"]
        r = requests.get(URL, timeout=30)
        r.raise_for_status()
        now, out = datetime.now(timezone.utc), []
        for x in r.json():
            loc = x["location"]
            if loc.get("indoor"):
                continue
            vals = {v["value_type"]: v["value"] for v in x["sensordatavalues"]}
            try:
                pm25 = float(vals["P2"])
                pm10 = float(vals["P1"]) if "P1" in vals else None
                age = (now - datetime.fromisoformat(x["timestamp"]).replace(tzinfo=timezone.utc)).total_seconds() / 60
                lat, lon = float(loc["latitude"]), float(loc["longitude"])
            except (KeyError, ValueError):
                continue
            if not (0 <= pm25 <= 500) or age > MAX_AGE_MIN:
                continue  # broken sensor or stale reading
            if pm10 is not None and pm25 > pm10 * 1.1 + 1:
                continue  # PM2.5 cannot exceed PM10: a faulty or humidity-fouled sensor
            out.append({"lat": lat, "lon": lon, "pm25": pm25, "pm10": pm10, "age_min": int(age)})
        _cache.update(at=time.time(), data=out)
        return out
