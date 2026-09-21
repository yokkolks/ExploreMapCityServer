from fastapi import FastAPI, HTTPException
import requests
import time

app = FastAPI(title="ExploreMapCityServer")

VERSION = 4

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {
    "User-Agent": "ExploreMap/1.0"
}

CITY_ID = "moscow"
CITY_NAME = "Москва"

CITY_BBOX = [37.15, 55.48, 37.95, 56.02]

DISTRICTS = [
    ("1257455", "Северное Бутово"),
    ("1257403", "Южное Бутово"),
    ("951334", "Ясенево"),
    ("951336", "Тёплый Стан"),
    ("950664", "Чертаново Южное"),
    ("951305", "Чертаново Центральное"),
]

district_cache = None
city_cache = None
last_load = 0


# ---------------------------------------------------------
# Nominatim
# ---------------------------------------------------------

def nominatim_search(query: str):
    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "json",
            "polygon_geojson": 1,
            "limit": 1,
            "addressdetails": 1,
        },
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        return None

    return data[0]


# ---------------------------------------------------------
# Районы
# ---------------------------------------------------------

def load_districts():
    global district_cache

    if district_cache is not None:
        return district_cache

    features = []

    for osm_id, name in DISTRICTS:
        try:
            result = nominatim_search(
                f"{name}, Москва, Россия"
            )

            if not result:
                print(f"Не найден район: {name}")
                continue

            geometry = result.get("geojson")

            if not geometry:
                print(f"Нет geometry: {name}")
                continue

            features.append({
                "type": "Feature",
                "properties": {
                    "district_id": osm_id,
                    "name": name,
                },
                "geometry": geometry,
            })

            print(f"Загружен район: {name}")

            # Nominatim не надо долбить слишком быстро
            time.sleep(1)

        except Exception as e:
            print(f"Ошибка района {name}: {e}")

    district_cache = {
        "type": "FeatureCollection",
        "features": features,
    }

    return district_cache


# ---------------------------------------------------------
# ГРАНИЦА МОСКВЫ
# ---------------------------------------------------------

def load_city():
    global city_cache

    if city_cache is not None:
        return city_cache

    print("Загружаю границу Москвы через Overpass...")

    query = """
    [out:json][timeout:120];

    relation
      ["boundary"="administrative"]
      ["name"="Москва"]
      ["admin_level"="4"];

    out geom;
    """

    response = requests.post(
        OVERPASS_URL,
        data=query,
        headers=HEADERS,
        timeout=150,
    )

    response.raise_for_status()

    data = response.json()

    features = []

    for element in data.get("elements", []):

        geometry = []

        for member in element.get("members", []):
            if member.get("type") != "way":
                continue

            coords = []

            for point in member.get("geometry", []):
                coords.append([
                    point["lon"],
                    point["lat"]
                ])

            if len(coords) >= 2:
                geometry.append(coords)

        if not geometry:
            continue

        # Собираем линии в MultiLineString.
        # MapLibre сможет показать их как границу.
        features.append({
            "type": "Feature",
            "properties": {
                "city_id": CITY_ID,
                "name": CITY_NAME,
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": geometry,
            },
        })

    if not features:
        raise RuntimeError(
            "Overpass не вернул границу Москвы"
        )

    city_cache = {
        "type": "FeatureCollection",
        "features": features,
    }

    print(
        f"Граница Москвы загружена: "
        f"{len(features)} объектов"
    )

    return city_cache


# ---------------------------------------------------------
# API
# ---------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": VERSION,
        "city": CITY_NAME,
    }


@app.get("/api/v1/city-districts")
def city_districts(
    latitude: float,
    longitude: float,
):
    try:
        districts = load_districts()
        city = load_city()

        return {
            "version": VERSION,

            "city_id": CITY_ID,
            "city_name": CITY_NAME,
            "city_bbox": CITY_BBOX,

            "geojson": districts,

            "city_geojson": city,
        }

    except Exception as e:
        print("API ERROR:", e)

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )