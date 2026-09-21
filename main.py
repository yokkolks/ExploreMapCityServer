from fastapi import FastAPI
import requests

app = FastAPI()

VERSION = 5

NOMINATIM_URL = "https://nominatim.openstreetmap.org/lookup"

DISTRICTS = [
    ("1257455", "Северное Бутово"),
    ("1257403", "Южное Бутово"),
    ("951334", "Ясенево"),
    ("951336", "Тёплый Стан"),
    ("950664", "Чертаново Южное"),
    ("951305", "Чертаново Центральное"),
]

district_cache = None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION
    }


def load_districts():
    global district_cache

    if district_cache is not None:
        return district_cache

    features = []

    for osm_id, name in DISTRICTS:
        try:
            response = requests.get(
                NOMINATIM_URL,
                params={
                    "format": "json",
                    "osm_ids": f"R{osm_id}",
                    "polygon_geojson": 1
                },
                headers={
                    "User-Agent": "ExploreMap/1.0"
                },
                timeout=30
            )

            response.raise_for_status()
            data = response.json()

            if not data:
                print(f"Не найден район: {name}")
                continue

            geometry = data[0].get("geojson")

            if not geometry:
                print(f"Нет геометрии: {name}")
                continue

            features.append({
                "type": "Feature",
                "properties": {
                    "id": osm_id,
                    "district_id": osm_id,
                    "name": name
                },
                "geometry": geometry
            })

            print(f"Загружен район: {name}")

        except Exception as e:
            print(f"Ошибка района {name}: {e}")

    district_cache = {
        "type": "FeatureCollection",
        "features": features
    }

    print(f"Всего районов: {len(features)}")

    return district_cache


@app.get("/api/v1/city-districts")
def city_districts(
    latitude: float,
    longitude: float
):
    return {
        "version": VERSION,
        "city_id": "moscow",
        "city_name": "Москва",
        "city_bbox": [
            37.15,
            55.48,
            37.95,
            56.02
        ],
        "geojson": load_districts()
    }