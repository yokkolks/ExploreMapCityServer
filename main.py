from __future__ import annotations

import json
import time
from typing import Any

import requests
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ExploreMap City Server", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Реальные административные районы Москвы.
# Это не ЖК и не отдельные кварталы.
SOUTHWEST_DISTRICTS = [
    ("Северное Бутово", 1257455),
    ("Южное Бутово", 1257403),
    ("Ясенево", 951334),
    ("Тёплый Стан", 951336),
    ("Чертаново Южное", 950664),
    ("Чертаново Центральное", 951305),
]

PACK_BBOX = [
    37.4558032,
    55.4906574,
    37.6333166,
    55.6494629,
]

NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"
USER_AGENT = "ExploreMap/1.0 (GPS exploration game)"

last_nominatim_request = 0.0


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": 3,
    }


def download_districts() -> dict[str, Any]:
    global last_nominatim_request

    wait = 1.1 - (time.time() - last_nominatim_request)

    if wait > 0:
        time.sleep(wait)

    ids = ",".join(
        f"R{relation_id}"
        for _, relation_id in SOUTHWEST_DISTRICTS
    )

    response = requests.get(
        NOMINATIM_URL,
        params={
            "osm_ids": ids,
            "format": "geojson",
            "polygon_geojson": 1,
            "polygon_threshold": 0.00001,
            "accept-language": "ru",
        },
        headers={
            "User-Agent": USER_AGENT,
        },
        timeout=60,
    )

    last_nominatim_request = time.time()

    response.raise_for_status()

    raw = response.json()

    features = raw.get("features", [])

    wanted = {
        str(relation_id): name
        for name, relation_id in SOUTHWEST_DISTRICTS
    }

    result_features = []

    for feature in features:
        props = feature.get("properties") or {}

        osm_id = str(props.get("osm_id", ""))

        if osm_id not in wanted:
            continue

        geometry = feature.get("geometry")

        if not geometry:
            continue

        name = wanted[osm_id]

        result_features.append(
            {
                "type": "Feature",
                "properties": {
                    "district_id": osm_id,
                    "osm_id": int(osm_id),
                    "name": name,
                    "admin_level": 8,
                },
                "geometry": geometry,
            }
        )

    if not result_features:
        raise RuntimeError(
            "Nominatim не вернул ни одного района"
        )

    if not any(
        f["properties"]["district_id"] == "1257455"
        for f in result_features
    ):
        raise RuntimeError(
            "Не удалось получить геометрию Северного Бутово"
        )

    return {
        "type": "FeatureCollection",
        "features": result_features,
    }


@app.get("/api/v1/city-districts")
def city_districts(
    latitude: float = Query(..., ge=-90, le=90),
    longitude: float = Query(..., ge=-180, le=180),
):
    geojson = download_districts()

    return {
        "version": 3,
        "city_id": "moscow_southwest",
        "city_name": "Москва",
        "city_bbox": PACK_BBOX,
        "geojson": geojson,
    }