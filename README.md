# София на живо · Sofia Live

A live map of Sofia's public transport and air quality, written in Python with [NiceGUI](https://nicegui.io).
Pick a bus, trolleybus or tram line and watch its vehicles move in real time, see where they are heading,
when they reach a stop, and when the first and last metro train leaves. The interface is in Bulgarian.

## Features

**Transport**
- **Track a line.** Type a number (`280`, `9`, `TM1`, `7А` – Latin letters match their Cyrillic twins) and choose
  *Всички / Автобус / Тролей / Трамвай*. Vehicles appear on the map and refresh every minute.
- **Two directions, two colors.** The route, its stops and its vehicles are colored by direction, with a small key
  naming the destination of each color. Short-turn and depot trips are matched to the right direction.
- **Stops with arrival predictions.** Click a stop on a single line to see the next arrivals, colored by direction.
- **Whole fleets.** Leave the number empty to see every line of a vehicle type, each in its own color with the line
  number on the marker. Click a marker to isolate that line.
- **Metro stations.** All 50 stations are always on the map. Click one for the first and last train per line and
  direction, on weekdays and on weekends.

**Air quality**
- A separate tab with fine-dust (PM2.5) readings from about 600 community sensors around Sofia, colored by the
  European Air Quality Index bands.

**Interface**
- Frosted-glass sidebar that slides away to the left with the handle on its edge.
- Dark mode with a dark map that keeps the Cyrillic street labels.
- On narrow screens the panel starts hidden (the phone layout is not yet well tested).

## Quick start

With Docker:

```bash
docker volume create sofia-transit-data   # first time only: keeps the downloaded timetable
docker compose up -d --build
```

Then open <http://localhost:8080>. Stop it with `docker compose down`.

Without Docker (Python 3.10 or newer):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The first start downloads the ~19 MB timetable and takes a few seconds; it is cached and refreshed daily.

| Variable   | Default | Purpose                                         |
|------------|---------|-------------------------------------------------|
| `PORT`     | `8080`  | Port the app listens on                         |
| `DATA_DIR` | `data`  | Where the downloaded timetable is cached        |

## Where the data comes from

| What | Source |
|------|--------|
| Timetable, routes, stops, shapes | Sofia's static GTFS feed – `gtfs.sofiatraffic.bg/api/v1/static` |
| Vehicle positions | GTFS-realtime – `gtfs.sofiatraffic.bg/api/v1/vehicle-positions` |
| Stop arrival predictions | GTFS-realtime – `gtfs.sofiatraffic.bg/api/v1/trip-updates` |
| Air quality | [sensor.community](https://sensor.community) open sensor network |
| Map tiles | [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors |

Please check each provider's terms before reusing the data elsewhere.

## How it works

```
app.py       the page: sidebar, tabs, map layers, live refresh
transit.py   GTFS loading, route shapes and stops, vehicle and arrival feeds, metro first/last times
air.py       sensor.community readings, filtering and air-quality bands
markers.py   map markers (line badges, stops, metro stations, sensors) and direction colors
theme.py     fonts, colors, glass panels, dark mode and map styling
```

A few details worth knowing:

- Sofia leaves `direction_id` empty in its timetable, so the two directions of a line are told apart by trip
  headsign (destination name). Trips that run only part of a line are matched by checking which drawn direction
  their path follows.
- Live vehicles and arrivals are fetched only for the selected line, so the feeds are hit once a minute at most
  per open page. Air-quality data is cached for five minutes, as sensor.community asks.
- Sensor readings where PM2.5 exceeds PM10 are dropped as faulty. A few extreme values can still be real local
  pollution or fog-fouled sensors; treat single outliers with care.

## Known limits

- Metro times are the planned timetable. Sofia does not publish live metro train positions.
- "Weekday" and "weekend" metro times use a typical Tuesday–Thursday and Saturday; public holidays can differ.
- The air-quality color bands follow the European Air Quality Index as the author recalled them – verify the
  thresholds before relying on them for health decisions.
- The app keeps one shared timetable in memory and is built for a handful of users, not heavy traffic.
