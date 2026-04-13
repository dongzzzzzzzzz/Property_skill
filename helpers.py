from __future__ import annotations

import hashlib
import math
import re
from statistics import median
from typing import Any, Iterable

from models import NormalizedListing, SourceListing

BED_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:bed|beds|bedroom|bedrooms|br)\b", re.I),
]
BATH_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:bath|baths|bathroom|bathrooms)\b", re.I),
]
AREA_PATTERNS = [
    re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square feet)\b", re.I),
    re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:sq\.?\s*m|sqm|m2|m²|square meters?)\b", re.I),
]
PRICE_PATTERN = re.compile(r"(?P<currency>[$€£AEDSGDUSDCA$AU$HK$MYR₹])?\s*(?P<number>\d[\d,]*(?:\.\d+)?)", re.I)
PLACEHOLDER_PRICE_PATTERN = re.compile(r"[$€£]?\s*\d[\d,]*x{1,}\b", re.I)
DAILY_PERIOD_PATTERN = re.compile(r"\b(daily|per day|/day|nightly|per night|/night)\b", re.I)
WEEKLY_PERIOD_PATTERN = re.compile(r"\b(weekly|per week|/week)\b", re.I)
MONTHLY_PERIOD_PATTERN = re.compile(r"\b(monthly|per month|/month)\b", re.I)
FEATURE_KEYWORDS = {
    "furnished": ["furnished", "fully furnished", "partially furnished"],
    "parking": ["parking", "carpark", "garage"],
    "pet friendly": ["pet friendly", "pets allowed", "pet allowed"],
    "near mrt/subway": ["mrt", "subway", "metro", "near station", "near train"],
    "balcony": ["balcony"],
    "gym": ["gym", "fitness"],
    "pool": ["pool", "swimming pool"],
}
PROPERTY_TYPES = [
    "apartment",
    "condo",
    "condominium",
    "house",
    "studio",
    "room",
    "townhouse",
    "villa",
]
PLACEHOLDER_IMAGE_MARKERS = [
    "carddefault",
    "placeholder",
    "default-image",
    "defaultimg",
    "no-image",
]
PET_ALLOWED_KEYWORDS = ["pet friendly", "pets allowed", "pet allowed", "allow cat", "allow dog"]
PET_BLOCKED_KEYWORDS = ["no pets", "pets not allowed", "pet not allowed"]
NYC_AREA_KEYWORDS = {
    "long island city": ["long island city", "lic"],
    "jersey city": ["jersey city", "journal square", "newport", "hoboken", "new jersey"],
    "manhattan": ["manhattan", "midtown", "upper east side", "upper west side", "chelsea", "soho", "tribeca", "east village", "west village", "financial district"],
    "brooklyn": ["brooklyn", "williamsburg", "bushwick", "park slope", "bed stuy", "bed-stuy", "dumbo"],
    "queens": ["queens", "astoria", "flushing", "jamaica", "forest hills", "sunnyside", "elmhurst"],
    "bronx": ["bronx"],
    "staten island": ["staten island"],
}
NYC_CORE_AREAS = {"manhattan", "brooklyn", "queens", "bronx", "staten island", "long island city"}
SUSPICIOUS_RENTAL_KEYWORDS = [
    "short-term",
    "short term",
    "sublet",
    "sublease",
    "nightly",
    "daily",
    "5 days",
    "6 days",
]
FIELD_NAMES = [
    "price",
    "price_period",
    "location",
    "bedrooms",
    "bathrooms",
    "area_size",
    "parking",
    "pet_policy",
    "images",
]


def _compact_text(*parts: str | None) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def build_canonical_id(provider: str, listing_id: str | None, url: str | None) -> str:
    if listing_id:
        return f"{provider}:{listing_id}"
    digest_source = url or provider
    return f"{provider}:{hashlib.sha256(digest_source.encode('utf-8')).hexdigest()[:16]}"


def detect_price_period(*texts: str | None) -> str:
    text = _compact_text(*texts).lower()
    if not text:
        return "unknown"
    if DAILY_PERIOD_PATTERN.search(text):
        return "daily"
    if WEEKLY_PERIOD_PATTERN.search(text):
        return "weekly"
    if MONTHLY_PERIOD_PATTERN.search(text):
        return "monthly"
    return "unknown"


def normalize_monthly_price(price_value: float | None, price_period: str) -> tuple[float | None, bool]:
    if price_value is None:
        return None, False
    if price_period == "daily":
        return round(price_value * 30, 2), True
    if price_period == "weekly":
        return round(price_value * 4.33, 2), True
    return price_value, False


def parse_price(price_text: str | None, *context_texts: str | None) -> tuple[float | None, str | None, str, float | None, bool]:
    if not price_text:
        return None, None, "unknown", None, False
    if PLACEHOLDER_PRICE_PATTERN.search(price_text):
        currency_match = re.search(r"[$€£]", price_text)
        return None, currency_match.group(0) if currency_match else None, "unknown", None, False
    matches = list(PRICE_PATTERN.finditer(price_text.replace("/month", "").replace("per month", "")))
    if not matches:
        return None, None, "unknown", None, False
    match = matches[0]
    number = float(match.group("number").replace(",", ""))
    currency = match.group("currency")
    if not currency:
        lowered = price_text.lower()
        if "sgd" in lowered:
            currency = "SGD"
        elif "usd" in lowered:
            currency = "USD"
        elif "aed" in lowered:
            currency = "AED"
        elif "cad" in lowered:
            currency = "CAD"
    price_period = detect_price_period(price_text, *context_texts)
    monthly_price_value, estimated = normalize_monthly_price(number, price_period)
    return number, currency, price_period, monthly_price_value, estimated


def infer_rent_or_sale(title: str, description: str | None) -> str | None:
    text = _compact_text(title, description).lower()
    if any(token in text for token in ["rent", "lease", "per month", "/month", "monthly", "per week", "/week", "weekly", "per day", "/day", "daily"]):
        return "rent"
    if any(token in text for token in ["sale", "sell", "freehold", "purchase", "buy"]):
        return "sale"
    return None


def infer_property_type(title: str, description: str | None) -> str | None:
    text = _compact_text(title, description).lower()
    for property_type in PROPERTY_TYPES:
        if property_type in text:
            if property_type == "condominium":
                return "condo"
            return property_type
    return None


def _extract_number(patterns: list[re.Pattern[str]], text: str, *, studio_as_zero: bool = False) -> float | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return float(match.group(1))
    if studio_as_zero and "studio" in text.lower():
        return 0.0
    return None


def infer_beds(title: str, description: str | None) -> float | None:
    return _extract_number(BED_PATTERNS, _compact_text(title, description), studio_as_zero=True)


def infer_baths(title: str, description: str | None) -> float | None:
    return _extract_number(BATH_PATTERNS, _compact_text(title, description))


def infer_area_size(title: str, description: str | None) -> tuple[float | None, str | None]:
    text = _compact_text(title, description)
    for pattern in AREA_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = float(match.group(1).replace(",", ""))
        matched_text = match.group(0).lower()
        unit = "sqft" if any(token in matched_text for token in ["sqft", "sq ft", "square feet"]) else "sqm"
        return value, unit
    return None, None


def extract_features(title: str, description: str | None) -> list[str]:
    text = _compact_text(title, description).lower()
    features = []
    for feature, keywords in FEATURE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            features.append(feature)
    return sorted(features)


def classify_image_quality(image_urls: Iterable[str] | None) -> str:
    urls = [url for url in (image_urls or []) if url]
    if not urls:
        return "missing"
    placeholder_count = 0
    for url in urls:
        lowered = url.lower()
        if any(marker in lowered for marker in PLACEHOLDER_IMAGE_MARKERS):
            placeholder_count += 1
    if placeholder_count == len(urls):
        return "placeholder_only"
    if placeholder_count:
        return "mixed"
    return "real_images"


def infer_pet_policy(title: str, description: str | None) -> str | None:
    text = _compact_text(title, description).lower()
    if any(keyword in text for keyword in PET_BLOCKED_KEYWORDS):
        return "pets not allowed"
    if any(keyword in text for keyword in PET_ALLOWED_KEYWORDS):
        return "pets allowed"
    return None


def looks_like_placeholder_price(price_text: str | None) -> bool:
    return bool(price_text and PLACEHOLDER_PRICE_PATTERN.search(price_text))


def classify_nyc_area(title: str, location_text: str | None, description: str | None) -> tuple[str | None, str | None, str | None]:
    text = _compact_text(location_text, title, description).lower()
    if not text:
        return None, None, None
    if "new york" not in text and "manhattan" not in text and "brooklyn" not in text and "queens" not in text and "bronx" not in text and "staten island" not in text and "jersey city" not in text and "hoboken" not in text and "newport" not in text and "long island city" not in text and "lic" not in text:
        return None, None, None

    matched_area = None
    matched_length = -1
    for area_name, keywords in NYC_AREA_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text and len(keyword) > matched_length:
                matched_area = area_name
                matched_length = len(keyword)

    if matched_area is None:
        return "nyc", None, None
    if matched_area in NYC_CORE_AREAS:
        borough = "queens" if matched_area == "long island city" else matched_area
        return "nyc", matched_area, borough
    return "nyc", matched_area, matched_area


def find_target_nyc_area(area: str | None, keyword: str | None, city: str | None) -> str | None:
    city_lower = (city or "").lower()
    if city_lower not in {"new-york", "new york"} and "new york" not in _compact_text(area, keyword).lower():
        return None
    text = _compact_text(area, keyword).lower()
    for area_name, keywords in NYC_AREA_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return area_name
    if city_lower in {"new-york", "new york"}:
        return "nyc"
    if "new york" in text:
        return "nyc"
    return None


def area_match_level(listing: NormalizedListing, area: str | None, city: str | None = None, keyword: str | None = None) -> str:
    target_area = find_target_nyc_area(area, keyword, city)
    if target_area:
        if listing.sub_area == target_area:
            return "exact"
        if target_area == "nyc":
            if listing.metro_area == "nyc":
                return "metro"
            if listing.sub_area in NYC_CORE_AREAS or listing.borough in {"manhattan", "brooklyn", "queens", "bronx", "staten island"}:
                return "metro"
            if listing.sub_area == "jersey city":
                return "outside"
            return "outside"
        if listing.borough and (listing.borough == target_area or (target_area == "long island city" and listing.borough == "queens")):
            return "borough"
        return "outside"

    if not area:
        return "exact"
    area_text = area.lower()
    haystack = _compact_text(listing.location_text, listing.area_name, listing.title, listing.description).lower()
    return "exact" if area_text in haystack else "outside"


def location_relevance_score(listing: NormalizedListing, area: str | None, city: str | None = None, keyword: str | None = None) -> tuple[float, str]:
    match_level = area_match_level(listing, area, city=city, keyword=keyword)
    target_area = find_target_nyc_area(area, keyword, city)
    if match_level == "exact":
        return 35.0, f"Matched target area: {target_area or area or listing.sub_area or 'search area'}"
    if match_level == "borough":
        return 28.0, f"Matched target borough: {listing.borough or listing.sub_area}"
    if match_level == "metro":
        if listing.sub_area == "jersey city":
            return 5.0, "Outside core NYC area: Jersey City"
        return 18.0, f"Matched core NYC area: {listing.sub_area or listing.borough or 'NYC'}"
    return 3.0, f"Outside target area: {listing.sub_area or listing.borough or listing.location_text or 'unknown'}"


def detect_price_anomaly(
    listing: NormalizedListing,
    peer_monthly_prices: Iterable[float],
    *,
    city: str | None = None,
) -> dict[str, object]:
    peer_values = sorted(value for value in peer_monthly_prices if value is not None)
    is_nyc = (city or "").lower() in {"new-york", "new york"} or listing.metro_area == "nyc"
    text = _compact_text(listing.title, listing.description, listing.price_text).lower()

    reasons: list[str] = []
    severity = "none"
    suspicious = False

    if listing.price_period == "unknown" and any(token in text for token in ["daily", "weekly", "monthly", "per month", "per day", "per week"]):
        suspicious = True
        severity = "medium"
        reasons.append("Price period looks ambiguous.")

    if listing.monthly_price_value is not None and is_nyc and listing.monthly_price_value < 300:
        suspicious = True
        severity = "high"
        reasons.append("Monthly-normalized price is unusually low for NYC.")

    if any(token in text for token in SUSPICIOUS_RENTAL_KEYWORDS):
        suspicious = True
        severity = "high" if severity == "none" else severity
        reasons.append("Listing looks like short-term or sublet inventory.")

    if peer_values and listing.monthly_price_value is not None:
        p25, median_value, _ = percentile_range(peer_values)
        if p25 is not None and listing.monthly_price_value < p25 * 0.35:
            suspicious = True
            severity = "high"
            reasons.append("Price is far below the current result-set p25.")
        if median_value is not None and listing.monthly_price_value < median_value * 0.4 and information_completeness(listing) < 0.7:
            suspicious = True
            severity = "high"
            reasons.append("Price is far below the median and the listing is incomplete.")

    return {
        "is_suspicious_low": suspicious,
        "reason": " ".join(dict.fromkeys(reasons)) if reasons else None,
        "severity": severity,
    }


def _source_for_pattern(title: str, description: str | None, patterns: list[re.Pattern[str]]) -> str:
    if any(pattern.search(title or "") for pattern in patterns):
        return "listing_title"
    if any(pattern.search(description or "") for pattern in patterns):
        return "listing_description"
    return "not_available"


def _source_for_keywords(title: str, description: str | None, keywords: Iterable[str]) -> str:
    lowered_title = (title or "").lower()
    lowered_description = (description or "").lower()
    if any(keyword in lowered_title for keyword in keywords):
        return "listing_title"
    if any(keyword in lowered_description for keyword in keywords):
        return "listing_description"
    return "not_available"


def _field_entry(
    *,
    value: Any,
    status: str,
    source: str,
    confidence: str,
) -> dict[str, Any]:
    return {
        "value": value,
        "status": status,
        "source": source,
        "confidence": confidence,
    }


def _build_field_metadata(
    source: SourceListing,
    *,
    price_value: float | None,
    price_period: str,
    monthly_price_value: float | None,
    price_is_estimated_monthly: bool,
    beds: float | None,
    baths: float | None,
    area_size_value: float | None,
    area_size_unit: str | None,
    features: list[str],
    image_quality: str,
    pet_policy: str | None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[str], list[str]]:
    title = source.title or ""
    description = source.description
    parking_source = _source_for_keywords(title, description, FEATURE_KEYWORDS["parking"])
    pet_source = (
        _source_for_keywords(title, description, PET_BLOCKED_KEYWORDS)
        if pet_policy == "pets not allowed"
        else _source_for_keywords(title, description, PET_ALLOWED_KEYWORDS)
    )
    field_sources = {
        "price": _field_entry(
            value=monthly_price_value if monthly_price_value is not None else price_value,
            status="present" if price_value is not None else "unknown",
            source="price_text_normalization" if price_value is not None else "not_available",
            confidence="medium_low" if price_is_estimated_monthly else "high" if price_value is not None else "none",
        ),
        "price_period": _field_entry(
            value=price_period if price_period != "unknown" else None,
            status="present" if price_period != "unknown" else "unknown",
            source="price_text_normalization" if price_period != "unknown" else "not_available",
            confidence="medium" if price_period != "unknown" else "none",
        ),
        "location": _field_entry(
            value=source.location_text,
            status="present" if source.location_text else "unknown",
            source="location_text_match" if source.location_text else "not_available",
            confidence="high" if source.location_text else "none",
        ),
        "bedrooms": _field_entry(
            value=beds,
            status="inferred" if beds is not None else "unknown",
            source=_source_for_pattern(title, description, BED_PATTERNS) if beds is not None else "not_available",
            confidence="medium" if beds is not None else "none",
        ),
        "bathrooms": _field_entry(
            value=baths,
            status="inferred" if baths is not None else "unknown",
            source=_source_for_pattern(title, description, BATH_PATTERNS) if baths is not None else "not_available",
            confidence="medium" if baths is not None else "none",
        ),
        "area_size": _field_entry(
            value=f"{area_size_value:g} {area_size_unit}" if area_size_value is not None and area_size_unit else None,
            status="inferred" if area_size_value is not None else "unknown",
            source=_source_for_pattern(title, description, AREA_PATTERNS) if area_size_value is not None else "not_available",
            confidence="medium" if area_size_value is not None else "none",
        ),
        "parking": _field_entry(
            value="mentioned" if "parking" in features else None,
            status="inferred" if "parking" in features else "unknown",
            source=parking_source if "parking" in features else "not_available",
            confidence="medium" if "parking" in features else "none",
        ),
        "pet_policy": _field_entry(
            value=pet_policy,
            status="inferred" if pet_policy else "unknown",
            source=pet_source if pet_policy else "not_available",
            confidence="medium" if pet_policy else "none",
        ),
        "images": _field_entry(
            value={"count": len(source.image_urls), "quality": image_quality} if source.image_urls else None,
            status="present" if image_quality in {"real_images", "mixed"} else "unknown",
            source="detail_page_field" if source.image_urls else "not_available",
            confidence="high" if image_quality == "real_images" else "medium" if image_quality == "mixed" else "none",
        ),
    }
    field_status = {field: entry["status"] for field, entry in field_sources.items()}
    known_fields = [field for field in FIELD_NAMES if field_status.get(field) != "unknown"]
    missing_fields = [field for field in FIELD_NAMES if field_status.get(field) == "unknown"]
    return field_status, field_sources, known_fields, missing_fields


def build_price_analysis(listing: NormalizedListing) -> dict[str, Any]:
    warnings: list[str] = []
    confidence = "high"
    trustworthy = True

    if listing.price_is_estimated_monthly:
        confidence = "medium_low"
        warnings.append("This listing was normalized from a daily/weekly rate.")
    elif listing.price_period == "unknown":
        confidence = "medium"

    if looks_like_placeholder_price(listing.price_text):
        trustworthy = False
        confidence = "none"
        warnings.append("The listing uses a placeholder-style price and should not be compared as a real quote.")

    anomaly_reason = listing.price_anomaly.get("reason")
    if listing.price_anomaly.get("is_suspicious_low"):
        trustworthy = False
        confidence = "low" if confidence != "none" else confidence
        if anomaly_reason:
            warnings.append(anomaly_reason)

    if listing.price_value is None:
        trustworthy = False
        confidence = "none"
        warnings.append("The page does not provide a trustworthy numeric price.")

    unique_warnings = list(dict.fromkeys(warnings))
    return {
        "is_trustworthy_price": trustworthy,
        "price_confidence": confidence,
        "price_warning": " ".join(unique_warnings) if unique_warnings else None,
    }


def normalize_listing(source: SourceListing) -> NormalizedListing:
    price_value, currency, price_period, monthly_price_value, price_is_estimated_monthly = parse_price(
        source.price_text, source.title, source.description
    )
    beds = infer_beds(source.title, source.description)
    baths = infer_baths(source.title, source.description)
    area_size_value, area_size_unit = infer_area_size(source.title, source.description)
    features = extract_features(source.title, source.description)
    image_quality = classify_image_quality(source.image_urls)
    pet_policy = infer_pet_policy(source.title, source.description)
    canonical_id = build_canonical_id(source.provider, source.listing_id, source.url)
    metro_area, sub_area, borough = classify_nyc_area(source.title, source.location_text, source.description)

    lat = source.raw.get("lat")
    lng = source.raw.get("lng")
    confidence = 0.3
    if monthly_price_value is not None:
        confidence += 0.2
    if beds is not None:
        confidence += 0.15
    if source.location_text:
        confidence += 0.15
    if source.description:
        confidence += 0.15
    if features:
        confidence += 0.05

    area_name = None
    if source.location_text:
        area_name = source.location_text.split(",")[0].strip()

    field_status, field_sources, known_fields, missing_fields = _build_field_metadata(
        source,
        price_value=price_value,
        price_period=price_period,
        monthly_price_value=monthly_price_value,
        price_is_estimated_monthly=price_is_estimated_monthly,
        beds=beds,
        baths=baths,
        area_size_value=area_size_value,
        area_size_unit=area_size_unit,
        features=features,
        image_quality=image_quality,
        pet_policy=pet_policy,
    )

    listing = NormalizedListing(
        provider=source.provider,
        provider_listing_id=source.listing_id or canonical_id.split(":", 1)[-1],
        canonical_id=canonical_id,
        url=source.url,
        title=source.title,
        price_text=source.price_text,
        price_value=price_value,
        currency=currency,
        price_period=price_period,
        monthly_price_value=monthly_price_value,
        price_is_estimated_monthly=price_is_estimated_monthly,
        rent_or_sale=infer_rent_or_sale(source.title, _compact_text(source.description, source.price_text)),
        location_text=source.location_text,
        area_name=area_name,
        metro_area=metro_area,
        sub_area=sub_area,
        borough=borough,
        lat=_safe_float(lat),
        lng=_safe_float(lng),
        beds=beds,
        baths=baths,
        area_size_value=area_size_value,
        area_size_unit=area_size_unit,
        property_type=infer_property_type(source.title, source.description),
        features=features,
        description=source.description,
        image_urls=list(source.image_urls),
        posted_time=source.posted_time,
        seller_name=source.seller_name,
        parse_confidence=min(confidence, 1.0),
        warnings=(
            ["This listing was normalized from a daily/weekly rate."]
            if price_is_estimated_monthly
            else []
        ),
        image_quality=image_quality,
        field_status=field_status,
        field_sources=field_sources,
        known_fields=known_fields,
        missing_fields=missing_fields,
    )
    listing.price_analysis = build_price_analysis(listing)
    return listing


def _safe_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile_range(values: Iterable[float]) -> tuple[float | None, float | None, float | None]:
    ordered = sorted(values)
    if not ordered:
        return None, None, None
    if len(ordered) == 1:
        value = ordered[0]
        return value, value, value
    mid = float(median(ordered))
    p25_index = max(0, math.floor((len(ordered) - 1) * 0.25))
    p75_index = min(len(ordered) - 1, math.ceil((len(ordered) - 1) * 0.75))
    return float(ordered[p25_index]), mid, float(ordered[p75_index])


def normalize_feature_input(features: Iterable[str] | None) -> list[str]:
    return sorted({feature.strip().lower() for feature in (features or []) if feature and feature.strip()})


def contains_area(listing: NormalizedListing, area: str | None) -> bool:
    return area_match_level(listing, area) != "outside"


def feature_match_score(listing: NormalizedListing, required_features: Iterable[str] | None) -> float:
    expected = normalize_feature_input(required_features)
    if not expected:
        return 1.0
    if not listing.features:
        return 0.0
    matched = len(set(expected) & set(normalize_feature_input(listing.features)))
    return matched / len(expected)


def information_completeness(listing: NormalizedListing) -> float:
    checks = [
        bool(listing.price_value is not None),
        bool(listing.location_text),
        bool(listing.description),
        bool(listing.image_quality in {"real_images", "mixed"}),
        bool(listing.beds is not None),
        bool(listing.property_type),
    ]
    return sum(checks) / len(checks)
