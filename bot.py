import math
import json
import asyncio
import httpx
import os
from typing import Optional, Tuple

ORS_API_KEY = os.getenv("ORS_API_KEY") # Ключ клади в переменные окружения

def haversine_miles(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Прямая дистанция между [lon, lat] точками в милях."""
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))

async def ors_geocode(client: httpx.AsyncClient, place: str) -> Optional[Tuple[float, float]]:
    """Turn an address into [lon, lat] via OpenRouteService."""
    r = await client.get(
        "https://api.openrouteservice.org/geocode/search",
        params={"api_key": ORS_API_KEY, "text": place, "size": 1, "boundary.country": "US"},
    )
    if r.status_code!= 200:
        print(f"ORS geocode HTTP {r.status_code}: {r.text[:300]}")
        return None
    data = r.json()
    features = data.get("features") or []
    if not features:
        print(f"ORS geocode: no result for '{place}'")
        return None
    return features[0]["geometry"]["coordinates"]

async def ors_directions(client: httpx.AsyncClient, coords: list, profile: str) -> Optional[int]:
    """Один запрос маршрута. Возвращает мили или None, логирует ошибку полностью."""
    r = await client.post(
        f"https://api.openrouteservice.org/v2/directions/{profile}",
        headers={"Authorization": ORS_API_KEY, "Content-Type": "application/json"},
        json={"coordinates": coords},
    )
    if r.status_code!= 200:
        print(f"ORS {profile} HTTP {r.status_code}: {r.text[:300]}")
        return None
    data = r.json()
    routes = data.get("routes")
    if not routes:
        print(f"ORS {profile} no routes: {json.dumps(data)[:300]}")
        return None
    return round(routes[0]["summary"]["distance"] / 1609.34)

async def real_route_miles(pickup: str, delivery: str) -> Optional[int]:
    """Мили по дорогам: hgv → car → haversine×1.22. Возвращает int или None."""
    if not ORS_API_KEY:
        print("ORS_API_KEY не задан")
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            start = await ors_geocode(client, pickup)
            end = await ors_geocode(client, delivery)
            if not start or not end:
                return None

            # 1) Грузовой профиль
            miles = await ors_directions(client, [start, end], "driving-hgv")
            if miles:
                print(f"Найден маршрут driving-hgv: {miles} mi")
                return miles

            # 2) Fallback: легковой профиль
            miles = await ors_directions(client, [start, end], "driving-car")
            if miles:
                print(f"ORS: hgv failed, used driving-car: {miles} mi")
                return miles

            # 3) Fallback: прямая × дорожный коэффициент
            est = round(haversine_miles(start, end) * 1.22)
            print(f"ORS: both profiles failed, haversine estimate = {est} mi")
            return est
    except Exception as e:
        print(f"ORS exception: {type(e).__name__}: {e}")
        return None

async def main():
    """Пример использования"""
    if not ORS_API_KEY:
        print("Установи переменную окружения: export ORS_API_KEY=твой_ключ")
        return

    pickup = input("Адрес загрузки: ").strip()
    delivery = input("Адрес выгрузки: ").strip()

    print("Считаю маршрут...")
    miles = await real_route_miles(pickup, delivery)

    if miles:
        print(f"Расстояние: {miles} миль")
    else:
        print("Не удалось посчитать расстояние")

if __name__ == "__main__":
    asyncio.run(main())
