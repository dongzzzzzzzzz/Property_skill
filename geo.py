from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class NominatimGeocoder:
    def __init__(self, base_url: str | None = None, timeout: int = 10):
        self.base_url = base_url or os.getenv("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
        self.timeout = timeout

    def geocode(self, query: str | None) -> tuple[float, float] | None:
        if not query:
            return None
        url = (
            f"{self.base_url.rstrip('/')}/search?"
            + urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1})
        )
        request = urllib.request.Request(url, headers={"User-Agent": "property-skills/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        if not data:
            return None
        first = data[0]
        return float(first["lat"]), float(first["lon"])


class SchoolFinder:
    def __init__(self, overpass_url: str | None = None, timeout: int = 15):
        self.overpass_url = overpass_url or os.getenv("PROPERTY_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
        self.timeout = timeout

    def nearby_schools(
        self,
        lat: float | None,
        lng: float | None,
        radius_m: int = 1500,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        if lat is None or lng is None:
            return []
        query = f"""
        [out:json][timeout:25];
        (
          node["amenity"="school"](around:{radius_m},{lat},{lng});
          way["amenity"="school"](around:{radius_m},{lat},{lng});
          relation["amenity"="school"](around:{radius_m},{lat},{lng});
        );
        out center {limit};
        """
        request = urllib.request.Request(
            self.overpass_url,
            data=query.encode("utf-8"),
            headers={"User-Agent": "property-skills/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []

        schools = []
        for element in payload.get("elements", [])[:limit]:
            center = element.get("center", {})
            school_lat = element.get("lat", center.get("lat"))
            school_lng = element.get("lon", center.get("lon"))
            if school_lat is None or school_lng is None:
                continue
            schools.append(
                {
                    "name": element.get("tags", {}).get("name", "Unnamed school"),
                    "lat": float(school_lat),
                    "lng": float(school_lng),
                    "distance_km": round(haversine_km(lat, lng, float(school_lat), float(school_lng)), 2),
                    "source": "openstreetmap",
                }
            )
        return schools


def estimate_eta_minutes(distance_km: float, mode: str = "driving") -> int:
    speed_kmh = {
        "walking": 4.5,
        "cycling": 14.0,
        "driving": 28.0,
        "transit": 20.0,
    }.get(mode, 28.0)
    return max(1, round(distance_km / speed_kmh * 60))


def try_route_eta(
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    mode: str = "driving",
) -> tuple[int | None, bool]:
    api_key = os.getenv("ORS_API_KEY")
    if not api_key or not origin or not destination:
        return None, False

    profile = {
        "driving": "driving-car",
        "walking": "foot-walking",
        "cycling": "cycling-regular",
        "transit": "driving-car",
    }.get(mode, "driving-car")
    url = f"https://api.openrouteservice.org/v2/directions/{profile}/json"
    payload = json.dumps(
        {"coordinates": [[origin[1], origin[0]], [destination[1], destination[0]]]}
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
            "User-Agent": "property-skills/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None, False

    try:
        seconds = data["routes"][0]["summary"]["duration"]
    except (KeyError, IndexError, TypeError):
        return None, False
    return max(1, round(seconds / 60)), True

