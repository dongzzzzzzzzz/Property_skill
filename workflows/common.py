from __future__ import annotations

from statistics import mean

from connectors.base import BasePropertyConnector
from geo import NominatimGeocoder, haversine_km
from helpers import (
    area_match_level,
    contains_area,
    detect_price_anomaly,
    feature_match_score,
    find_target_nyc_area,
    information_completeness,
    location_relevance_score,
    normalize_feature_input,
    normalize_listing,
    percentile_range,
)
from models import NormalizedListing, SourceListing


def hydrate_and_normalize(
    connector: BasePropertyConnector,
    source_listings: list[SourceListing],
    *,
    detail_limit: int = 10,
    geocoder: NominatimGeocoder | None = None,
) -> list[NormalizedListing]:
    geocoder = geocoder or NominatimGeocoder()
    hydrated: list[NormalizedListing] = []
    for index, source in enumerate(source_listings):
        detailed = source
        if index < detail_limit and source.url:
            try:
                detailed = connector.get_listing_detail(url=source.url)
            except Exception:
                detailed = source
        listing = normalize_listing(detailed)
        if (listing.lat is None or listing.lng is None) and listing.location_text:
            coords = geocoder.geocode(listing.location_text)
            if coords:
                listing.lat, listing.lng = coords
        hydrated.append(listing)
    return hydrated


def filter_listings(
    listings: list[NormalizedListing],
    *,
    budget_min: float | None = None,
    budget_max: float | None = None,
    bedrooms: float | None = None,
    property_type: str | None = None,
    area: str | None = None,
    city: str | None = None,
    keyword: str | None = None,
    required_features: list[str] | None = None,
    near_point: tuple[float, float] | None = None,
    radius_km: float | None = None,
    nyc_area_mode: str = "core",
) -> list[NormalizedListing]:
    required_features = normalize_feature_input(required_features)
    target_area = find_target_nyc_area(area, keyword, city)
    filtered = []
    for listing in listings:
        listing_price = listing.monthly_price_value if listing.monthly_price_value is not None else listing.price_value
        if budget_min is not None and listing_price is not None and listing_price < budget_min:
            continue
        if budget_max is not None and listing_price is not None and listing_price > budget_max:
            continue
        if bedrooms is not None and listing.beds is not None and listing.beds < bedrooms:
            continue
        if property_type and listing.property_type and listing.property_type != property_type.lower():
            continue
        match_level = area_match_level(listing, area, city=city, keyword=keyword)
        if (city or "").lower() == "new-york" and nyc_area_mode == "core" and listing.sub_area == "jersey city":
            if target_area and target_area != "nyc":
                continue
        elif match_level == "outside":
            continue
        if required_features and not set(required_features).issubset(set(listing.features)):
            continue
        if near_point and radius_km and listing.lat is not None and listing.lng is not None:
            distance = haversine_km(near_point[0], near_point[1], listing.lat, listing.lng)
            if distance > radius_km:
                continue
        filtered.append(listing)
    return filtered


def listing_price_summary(listings: list[NormalizedListing]) -> dict[str, float | int | None]:
    values = [listing.monthly_price_value for listing in listings if listing.monthly_price_value is not None and not listing.price_anomaly.get("is_suspicious_low")]
    p25, med, p75 = percentile_range(values)
    return {
        "sample_size": len(values),
        "p25": p25,
        "median": med,
        "p75": p75,
        "average": round(mean(values), 2) if values else None,
    }


def build_query_from_inputs(
    *,
    area: str | None = None,
    property_type: str | None = None,
    rent_or_sale: str | None = None,
    bedrooms: float | None = None,
    keyword: str | None = None,
) -> str:
    if keyword:
        return keyword
    parts = []
    if area:
        parts.append(area)
    if bedrooms is not None:
        if bedrooms == 0:
            parts.append("studio")
        else:
            parts.append(f"{int(bedrooms)} bedroom")
    if property_type:
        parts.append(property_type)
    if rent_or_sale:
        parts.append("for rent" if rent_or_sale == "rent" else "for sale")
    return " ".join(parts).strip()


def find_comparable_listings(
    subject: NormalizedListing,
    market: list[NormalizedListing],
    *,
    max_items: int = 5,
) -> list[NormalizedListing]:
    comparables = []
    for listing in market:
        if listing.canonical_id == subject.canonical_id:
            continue
        if subject.rent_or_sale and listing.rent_or_sale and subject.rent_or_sale != listing.rent_or_sale:
            continue
        if subject.property_type and listing.property_type and subject.property_type != listing.property_type:
            continue
        if subject.beds is not None and listing.beds is not None and abs(subject.beds - listing.beds) > 1:
            continue
        if subject.area_name and listing.area_name and subject.area_name.lower() != listing.area_name.lower():
            if subject.lat is not None and subject.lng is not None and listing.lat is not None and listing.lng is not None:
                if haversine_km(subject.lat, subject.lng, listing.lat, listing.lng) > 3:
                    continue
        comparables.append(listing)
    comparables.sort(key=lambda item: comparable_distance(subject, item))
    return comparables[:max_items]


def comparable_distance(subject: NormalizedListing, candidate: NormalizedListing) -> tuple[float, float, float]:
    bed_gap = abs((subject.beds or 0) - (candidate.beds or 0))
    price_gap = abs((subject.price_value or 0) - (candidate.price_value or 0))
    location_gap = 0.0
    if (
        subject.lat is not None
        and subject.lng is not None
        and candidate.lat is not None
        and candidate.lng is not None
    ):
        location_gap = haversine_km(subject.lat, subject.lng, candidate.lat, candidate.lng)
    return (bed_gap, location_gap, price_gap)


def add_match_reasons(
    listing: NormalizedListing,
    *,
    required_features: list[str] | None = None,
    target_point: tuple[float, float] | None = None,
    area: str | None = None,
    city: str | None = None,
    keyword: str | None = None,
) -> None:
    reasons = []
    if listing.monthly_price_value is not None:
        reasons.append(f"Normalized monthly price is {listing.monthly_price_value:.0f}.")
    if listing.beds is not None:
        reasons.append(f"Detected {listing.beds:g} bedroom layout.")
    if required_features:
        overlap = sorted(set(required_features) & set(listing.features))
        if overlap:
            reasons.append("Matches requested features: " + ", ".join(overlap) + ".")
    if target_point and listing.lat is not None and listing.lng is not None:
        distance = haversine_km(target_point[0], target_point[1], listing.lat, listing.lng)
        reasons.append(f"Approx. {distance:.1f} km from the target area.")
    else:
        _, location_reason = location_relevance_score(listing, area, city=city, keyword=keyword)
        reasons.append(location_reason)
    if listing.image_urls:
        reasons.append(f"Has {len(listing.image_urls)} image(s).")
    if listing.price_is_estimated_monthly:
        reasons.append("Monthly price was normalized from a daily/weekly rate.")
    listing.match_reasons = reasons


def score_listing(
    listing: NormalizedListing,
    peers: list[NormalizedListing],
    *,
    required_features: list[str] | None = None,
    target_point: tuple[float, float] | None = None,
    area: str | None = None,
    city: str | None = None,
    keyword: str | None = None,
) -> dict[str, float | None]:
    peer_prices = [peer.monthly_price_value for peer in peers if peer.monthly_price_value is not None]
    location_score, location_reason = location_relevance_score(listing, area, city=city, keyword=keyword)
    listing.location_relevance = {"score": round(location_score, 2), "reason": location_reason}

    listing.price_anomaly = detect_price_anomaly(listing, peer_prices, city=city)

    price_score = 25.0
    listing_price = listing.monthly_price_value
    if listing_price is not None and peer_prices:
        cheaper_than = sum(1 for peer_price in peer_prices if peer_price >= listing_price)
        price_score = 8 + 17 * (cheaper_than / len(peer_prices))

    feature_score = 15 * feature_match_score(listing, required_features)
    completeness_score = 15 * information_completeness(listing)
    anomaly_penalty = -20.0 if listing.price_anomaly.get("is_suspicious_low") else 0.0

    distance_bonus = 0.0
    if target_point and listing.lat is not None and listing.lng is not None:
        distance = haversine_km(target_point[0], target_point[1], listing.lat, listing.lng)
        distance_bonus = max(0.0, 8 - min(distance, 8))
        listing.location_relevance["distance_km"] = round(distance, 2)
        listing.location_relevance["reason"] = f"{listing.location_relevance['reason']}. Distance-assisted ranking."

    location_relevance_total = min(35.0, location_score + distance_bonus)
    total = round(price_score + feature_score + completeness_score + location_relevance_total + anomaly_penalty, 2)
    return {
        "price_score": round(price_score, 2),
        "feature_score": round(feature_score, 2),
        "completeness_score": round(completeness_score, 2),
        "location_relevance_score": round(location_relevance_total, 2),
        "anomaly_penalty": round(anomaly_penalty, 2),
        "total_score": total,
    }


def build_viewing_questions(listing: NormalizedListing) -> dict[str, list[str]]:
    questions = [
        "What is included in the rent or sale price?",
        "Has the property had any recent maintenance or repairs?",
        "Are there any building or management fees not listed here?",
    ]
    if "parking" not in listing.features:
        questions.append("Is parking available nearby and what does it cost?")
    if "pet friendly" not in listing.features:
        questions.append("What is the pet policy for this property?")
    risk_questions = []
    if listing.price_value is None:
        risk_questions.append("Confirm the exact asking price and any hidden fees.")
    if not listing.image_urls:
        risk_questions.append("Ask for additional photos or a live video tour.")
    if listing.location_text is None:
        risk_questions.append("Confirm the exact address and nearest landmarks.")
    return {
        "viewing_questions": questions,
        "risk_questions": risk_questions,
    }
