"""Sofia GTFS data: static timetable (cached on disk) and live vehicle positions."""
import csv
import io
import os
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

BASE = "https://gtfs.sofiatraffic.bg/api/v1"
DATA_DIR = os.environ.get("DATA_DIR", "data")
MAX_AGE = 24 * 3600

TYPE_NAMES = {0: "Трамвай", 1: "Метро", 3: "Автобус", 11: "Тролей"}
TYPE_COLORS = {0: "#f59e0b", 1: "#16a34a", 3: "#dc2626", 11: "#2563eb"}

# Cyrillic letters that look like Latin ones, so "7a" finds "7А".
_LOOKALIKE = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")


def _seconds(hms: str) -> int:
    """GTFS time of day in seconds; hours may pass 24 for trips running after midnight."""
    h, m, s = (int(p) for p in hms.split(":"))
    return h * 3600 + m * 60 + s


def norm(name: str) -> str:
    return name.strip().upper().translate(_LOOKALIKE)


@dataclass
class Vehicle:
    vehicle_id: str
    line: str
    route_type: int
    lat: float
    lon: float
    speed_kmh: float
    next_stop: str
    age_s: int
    direction: int | None = None  # index of the route's direction (see Transit.route_shapes), if known
    headsign: str = ""


class Transit:
    def __init__(self):
        self.routes = {}  # route_id -> (short_name, long_name, route_type)
        self.by_line = defaultdict(list)  # normalized short name -> [route_id]
        self.stops = {}  # stop_id -> (name, lat, lon)
        self.route_colors = {}  # route_id -> "#rrggbb"
        self.metro_stations = []  # (name, lat, lon, [(line, color)], stop_id)
        self.metro_times = {}  # stop_id -> {"weekday"|"weekend": [(line, color, headsign, first_secs, last_secs)]}
        self.route_shapes = {}  # route_id -> [shape_id] (most used per direction)
        self.route_trips = {}  # route_id -> [trip_id] (one typical trip per direction)
        self.trip_shape = {}  # trip_id -> shape_id
        self.shape_heads = {}  # shape_id -> headsign (where the vehicle is heading)
        self.trip_head = {}  # trip_id -> headsign
        self.dir_by_head = {}  # (route_id, headsign) -> direction index into route_shapes (None = unknown)
        self.variant_shape = {}  # (route_id, headsign) -> its most common shape, for inferring direction
        self._stops_cache = {}
        self._shape_cache = {}
        self._load()

    def _static_zip(self) -> zipfile.ZipFile:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "static.zip")
        if not os.path.exists(path) or time.time() - os.path.getmtime(path) > MAX_AGE:
            r = requests.get(f"{BASE}/static", timeout=120)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        return zipfile.ZipFile(path)

    def _rows(self, name):
        with self.zip.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))

    def _load(self):
        self.zip = self._static_zip()
        for r in self._rows("routes.txt"):
            rt = int(r["route_type"] or 3)
            self.routes[r["route_id"]] = (r["route_short_name"], r["route_long_name"], rt)
            self.by_line[norm(r["route_short_name"])].append(r["route_id"])
            self.route_colors[r["route_id"]] = "#" + (r["route_color"] or "555555")
        for r in self._rows("stops.txt"):
            self.stops[r["stop_id"]] = (r["stop_name"], float(r["stop_lat"]), float(r["stop_lon"]))
        # Sofia leaves direction_id empty, so a route's two directions are told apart by headsign.
        counts = defaultdict(Counter)  # (route_id, headsign) -> shape_id -> trips
        sample = {}  # (route_id, headsign, shape_id) -> first trip seen
        metro_trips = {}  # trip_id -> (metro route_id, service_id, headsign)
        for r in self._rows("trips.txt"):
            key = (r["route_id"], r["trip_headsign"])
            self.trip_shape[r["trip_id"]] = r["shape_id"]
            self.trip_head[r["trip_id"]] = r["trip_headsign"]
            counts[key][r["shape_id"]] += 1
            sample.setdefault(key + (r["shape_id"],), r["trip_id"])
            if self.routes[r["route_id"]][2] == 1:
                metro_trips[r["trip_id"]] = (r["route_id"], r["service_id"], r["trip_headsign"])
        by_route = defaultdict(list)
        for (route_id, headsign), c in counts.items():
            by_route[route_id].append((sum(c.values()), headsign, c))
            self.variant_shape[(route_id, headsign)] = c.most_common(1)[0][0]
        for route_id, options in by_route.items():
            for _, headsign, c in sorted(options, key=lambda o: -o[0])[:2]:  # the two busiest headsigns
                shape_id = c.most_common(1)[0][0]
                if shape_id in self.route_shapes.get(route_id, []):
                    continue  # both headsigns run the same path (a loop)
                self.route_shapes.setdefault(route_id, []).append(shape_id)
                self.shape_heads[shape_id] = headsign
                self.dir_by_head[(route_id, headsign)] = len(self.route_shapes[route_id]) - 1
                self.route_trips.setdefault(route_id, []).append(sample[(route_id, headsign, shape_id)])
        self._load_metro_stations(metro_trips)

    def _load_metro_stations(self, metro_info):
        """Metro stations, the lines calling at each, and first/last departures per direction.

        `metro_info` maps each metro trip_id to (route_id, service_id, headsign)."""
        rows = defaultdict(list)  # trip_id -> [(stop_sequence, stop_id, departure_seconds)]
        for r in self._rows("stop_times.txt"):
            if r["trip_id"] in metro_info:
                rows[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"], _seconds(r["departure_time"] or r["arrival_time"])))
        for seq in rows.values():
            seq.sort()

        lines = defaultdict(set)  # stop_id -> metro route_ids
        for trip_id, seq in rows.items():
            for _, stop_id, _ in seq:
                lines[stop_id].add(metro_info[trip_id][0])
        for stop_id, rids in lines.items():
            name, lat, lon = self.stops[stop_id]
            ordered = sorted(rids, key=lambda i: self.routes[i][0])
            self.metro_stations.append((name, lat, lon, [(self.routes[i][0], self.route_colors[i]) for i in ordered], stop_id))

        # Each route has two main headsigns (its termini); a trip's direction is the main stop order it travels along,
        # which also folds depot and short-turn runs into the right direction.
        heads = Counter((rid, head) for rid, _, head in metro_info.values())
        order = {}  # (route_id, main headsign) -> [stop_id, ...]
        for (rid, head), _ in heads.most_common():
            if sum(1 for r, _ in order if r == rid) < 2:
                trip = next(tid for tid, (r, _, h) in metro_info.items() if r == rid and h == head)
                order[(rid, head)] = [s for _, s, _ in rows[trip]]

        def direction(trip_id):
            rid, _, head = metro_info[trip_id]
            first, last = rows[trip_id][0][1], rows[trip_id][-1][1]
            for (r, h), stops in order.items():
                if r == rid and first in stops and last in stops and stops.index(first) < stops.index(last):
                    return h
            return head if (rid, head) in order else None

        active = self._metro_service_days({svc for _, svc, _ in metro_info.values()})
        spans = defaultdict(lambda: defaultdict(lambda: [None, None]))  # (stop, kind) -> (route, head) -> [first, last]
        for trip_id, seq in rows.items():
            rid, svc, _ = metro_info[trip_id]
            head = direction(trip_id)
            if head is None:
                continue
            for kind, services in active.items():
                if svc not in services:
                    continue
                for _, stop_id, at in seq[:-1]:  # the last stop is only an arrival
                    span = spans[(stop_id, kind)][(rid, head)]
                    span[0] = at if span[0] is None else min(span[0], at)
                    span[1] = at if span[1] is None else max(span[1], at)
        for (stop_id, kind), by_dir in spans.items():
            self.metro_times.setdefault(stop_id, {})[kind] = sorted(
                ((self.routes[rid][0], self.route_colors[rid], head, first, last) for (rid, head), (first, last) in by_dir.items()),
                key=lambda x: (x[0], x[2]),
            )

    def _metro_service_days(self, services):
        """Service ids running on a typical weekday and on a Saturday, taking the next such dates in the timetable."""
        by_date = defaultdict(set)
        for r in self._rows("calendar_dates.txt"):
            if r["exception_type"] == "1" and r["service_id"] in services:
                by_date[r["date"]].add(r["service_id"])
        today = datetime.now(ZoneInfo("Europe/Sofia")).strftime("%Y%m%d")
        dates = sorted(d for d in by_date if d >= today) or sorted(by_date)

        def first_on(weekdays):
            return next((d for d in dates if datetime.strptime(d, "%Y%m%d").weekday() in weekdays), None)

        return {"weekday": by_date.get(first_on({1, 2, 3}), set()), "weekend": by_date.get(first_on({5}), set())}

    def find_routes(self, line: str, route_type=None):
        """Routes matching a line number and/or vehicle type (None = any)."""
        if line.strip():
            ids = self.by_line.get(norm(line), [])
        else:
            ids = list(self.routes)
        return [i for i in ids if route_type is None or self.routes[i][2] == route_type]

    def shapes(self, route_ids):
        """Polylines [[lat, lon], ...] for the given routes."""
        return self._shape_points({s for rid in route_ids for s in self.route_shapes.get(rid, [])})

    def _shape_points(self, wanted):
        missing = set(wanted) - self._shape_cache.keys()
        if missing:
            pts = defaultdict(list)
            for r in self._rows("shapes.txt"):
                if r["shape_id"] in missing:
                    pts[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"])))
            for sid in missing:
                self._shape_cache[sid] = [[la, lo] for _, la, lo in sorted(pts[sid])]
        return {sid: self._shape_cache[sid] for sid in wanted}

    def direction_for(self, route_id, headsign):
        """Direction index (into route_shapes) of a trip heading to `headsign`, or None if unknown.

        Short-turn trips have their own headsigns; they follow the drawn direction they move forward along."""
        key = (route_id, headsign)
        if key not in self.dir_by_head:
            self.dir_by_head[key] = self._infer_direction(key)
        return self.dir_by_head[key]

    def _infer_direction(self, key):
        route_id = key[0]
        variant, chosen = self.variant_shape.get(key), self.route_shapes.get(route_id, [])
        if not variant or not chosen:
            return None
        pts = self._shape_points({variant, *chosen})
        path = pts[variant]
        if len(path) < 2:
            return None

        def nearest(line, p):
            i = min(range(len(line)), key=lambda k: (line[k][0] - p[0]) ** 2 + (line[k][1] - p[1]) ** 2)
            return i, ((line[i][0] - p[0]) ** 2 + (line[i][1] - p[1]) ** 2) ** 0.5

        best, best_score = None, 0
        for idx, sid in enumerate(chosen):
            line = pts[sid]
            (i0, d0), (i1, d1) = nearest(line, path[0]), nearest(line, path[-1])
            if max(d0, d1) < 0.004 and i1 - i0 > best_score:  # both ends within ~400 m of the drawn path
                best, best_score = idx, i1 - i0
        return best

    def stops_for(self, route_ids):
        """Stops along the given routes, as (stop_id, name, lat, lon, route_type, direction), in travel order."""
        key = tuple(sorted(route_ids))
        if key not in self._stops_cache:
            trips = {t: (rid, i) for rid in route_ids for i, t in enumerate(self.route_trips.get(rid, []))}
            seq = defaultdict(list)
            for r in self._rows("stop_times.txt"):
                if r["trip_id"] in trips:
                    seq[r["trip_id"]].append((int(r["stop_sequence"]), r["stop_id"]))
            out, seen = [], set()
            for trip_id, (rid, direction) in trips.items():
                for _, stop_id in sorted(seq[trip_id]):
                    if stop_id not in seen and stop_id in self.stops:
                        seen.add(stop_id)
                        name, lat, lon = self.stops[stop_id]
                        out.append((stop_id, name, lat, lon, self.routes[rid][2], direction))
            self._stops_cache[key] = out
        return self._stops_cache[key]

    def arrivals(self, stop_id, route_ids, limit=8):
        """Predicted arrivals at a stop: [(line, route_type, epoch_seconds, direction, headsign)], soonest first."""
        r = requests.get(f"{BASE}/trip-updates", timeout=15)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        wanted, now, out = set(route_ids), time.time(), []
        for e in feed.entity:
            tu = e.trip_update
            if tu.trip.route_id not in wanted:
                continue
            for u in tu.stop_time_update:
                if u.stop_id == stop_id:
                    at = u.arrival.time or u.departure.time
                    if at and at >= now - 30:
                        name, _, rtype = self.routes[tu.trip.route_id]
                        head = self.trip_head.get(tu.trip.trip_id, "")
                        out.append((name, rtype, at, self.direction_for(tu.trip.route_id, head), head))
                    break
        return sorted(out, key=lambda a: a[2])[:limit]

    def vehicles(self, route_ids) -> list[Vehicle]:
        r = requests.get(f"{BASE}/vehicle-positions", timeout=15)
        r.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)
        wanted, now, out = set(route_ids), time.time(), []
        for e in feed.entity:
            v = e.vehicle
            if v.trip.route_id not in wanted:
                continue
            name, _, rtype = self.routes[v.trip.route_id]
            stop = self.stops.get(v.stop_id)
            head = self.trip_head.get(v.trip.trip_id, "")
            out.append(Vehicle(
                vehicle_id=v.vehicle.id or e.id,
                line=name,
                route_type=rtype,
                lat=v.position.latitude,
                lon=v.position.longitude,
                speed_kmh=v.position.speed,
                next_stop=stop[0] if stop else "",
                age_s=max(0, int(now - v.timestamp)) if v.timestamp else 0,
                direction=self.direction_for(v.trip.route_id, head),
                headsign=head,
            ))
        return out
