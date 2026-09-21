from __future__ import annotations

import json
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import duckdb
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

RELEASE = "2026-08-19.0"
DIVISION_AREA = (
    f"s3://overturemaps-us-west-2/release/{RELEASE}/"
    "theme=divisions/type=division_area/*"
)
CACHE_DIR = Path("city_cache")
CACHE_DIR.mkdir(exist_ok=True)
SIMPLIFY = 0.00002
MAX_FEATURES = 400

DB: duckdb.DuckDBPyConnection | None = None
DB_LOCK = threading.Lock()
REQUEST_LOCK = threading.Lock()
LAST_CITY: tuple[str, dict[str, Any]] | None = None


def slug(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_")
    return value[:120] or "city"


def init_db() -> duckdb.DuckDBPyConnection:
    db = duckdb.connect("exploremap.duckdb")
    db.execute("INSTALL httpfs")
    db.execute("LOAD httpfs")
    db.execute("INSTALL spatial")
    db.execute("LOAD spatial")
    db.execute("SET s3_region='us-west-2'")
    db.execute("SET enable_object_cache=true")
    db.execute("SET enable_http_metadata_cache=true")
    return db


@asynccontextmanager
async def lifespan(app: FastAPI):
    global DB
    DB = init_db()
    yield
    if DB is not None:
        DB.close()
        DB = None


app = FastAPI(title="ExploreMap City GeoJSON API", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)


async def reverse_city(lat: float, lon: float) -> tuple[str, str, str | None, list[float]] | None:
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "format": "jsonv2",
        "addressdetails": 1,
        "zoom": 10,
        "accept-language": "ru,en",
    }
    headers = {"User-Agent": "ExploreMap/1.0 (private city GeoJSON server)"}
    async with httpx.AsyncClient(timeout=4.0) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()

    address = data.get("address", {})
    name = (
        address.get("city")
        or address.get("town")
        or address.get("municipality")
        or address.get("village")
        or address.get("locality")
    )
    country = address.get("country_code", "").upper()
    region = address.get("ISO3166-2-lvl4")
    region = region.upper() if region else None

    raw_box = data.get("boundingbox")
    if not name or not country or not isinstance(raw_box, list) or len(raw_box) != 4:
        return None
    try:
        south, north, west, east = map(float, raw_box)
        # Small padding so districts touching the city edge are not clipped.
        pad_lat = max((north - south) * 0.03, 0.003)
        pad_lon = max((east - west) * 0.03, 0.003)
        return name, country, region, [west - pad_lon, south - pad_lat, east + pad_lon, north + pad_lat]
    except (TypeError, ValueError):
        return None


def query_districts(
    db: duckdb.DuckDBPyConnection,
    country: str,
    region: str | None,
    bbox: list[float],
) -> list[dict[str, Any]]:
    xmin, ymin, xmax, ymax = bbox
    point = f"ST_Point((? + ?)/2, (? + ?)/2)"
    region_filter = "AND region = ?" if region else ""

    q = f"""
        SELECT
            division_id,
            names.primary AS name,
            subtype,
            COALESCE(admin_level, 8) AS admin_level,
            ST_AsGeoJSON(ST_SimplifyPreserveTopology(geometry, ?)) AS geometry
        FROM read_parquet(
            '{DIVISION_AREA}',
            hive_partitioning=true,
            filename=true
        )
        WHERE country = ?
          {region_filter}
          AND subtype IN ('neighborhood', 'microhood', 'borough')
          AND COALESCE(is_land, true) = true
          AND bbox.xmin <= ? AND bbox.xmax >= ?
          AND bbox.ymin <= ? AND bbox.ymax >= ?
        ORDER BY ST_Distance(
            geometry,
            {point}
        )
        LIMIT {MAX_FEATURES}
    """

    params: list[Any] = [SIMPLIFY, country]
    if region:
        params.append(region)
    params += [xmax, xmin, ymax, ymin, xmin, xmax, ymin, ymax]

    rows = db.execute(q, params).fetchall()
    features: list[dict[str, Any]] = []
    for division_id, name, subtype, level, geometry_json in rows:
        if not geometry_json:
            continue
        try:
            geom = json.loads(geometry_json)
        except json.JSONDecodeError:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "district_id": str(division_id),
                "name": name or f"Район {division_id}",
                "admin_level": int(level),
                "subtype": subtype,
            },
            "geometry": geom,
        })
    return features


def build_payload(city_name: str, country: str, bbox: list[float], features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 2,
        "source": f"Overture Maps divisions {RELEASE}",
        "city_id": f"{country}_{slug(city_name)}",
        "city_name": city_name,
        "city_bbox": bbox,
        "geojson": {"type": "FeatureCollection", "features": features},
    }


@app.get("/health")
def health():
    return {"ok": True, "release": RELEASE, "db_ready": DB is not None}


@app.get("/api/v1/city-districts")
async def city_districts(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
):
    global LAST_CITY

    with REQUEST_LOCK:
        place = await reverse_city(latitude, longitude)
        if not place:
            raise HTTPException(404, "Город не определён")
        city_name, country, region, bbox = place
        cache_path = CACHE_DIR / f"{country}_{slug(city_name)}.json"

        if LAST_CITY and LAST_CITY[0] == str(cache_path) and Path(LAST_CITY[0]).exists():
            return JSONResponse(LAST_CITY[1])

        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                LAST_CITY = (str(cache_path), payload)
                return JSONResponse(payload)
            except Exception:
                cache_path.unlink(missing_ok=True)

        if DB is None:
            raise HTTPException(503, "DuckDB ещё запускается")

        try:
            features = query_districts(DB, country, region, bbox)
        except Exception as exc:
            raise HTTPException(502, f"Ошибка Overture/DuckDB: {exc}") from exc

        if not features and region:
            # Fallback without region when Nominatim's ISO code differs from Overture.
            features = query_districts(DB, country, None, bbox)

        if not features:
            raise HTTPException(404, f"Районы не найдены: {city_name}")

        payload = build_payload(city_name, country, bbox, features)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        LAST_CITY = (str(cache_path), payload)
        return JSONResponse(payload)
