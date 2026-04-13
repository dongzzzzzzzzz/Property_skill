from __future__ import annotations

from statistics import mean
from typing import Any

from connectors.base import BasePropertyConnector
from geo import NominatimGeocoder, haversine_km
from helpers import (
    area_match_level,
    build_price_analysis,
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
    listing.price_analysis = build_price_analysis(listing)

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


def build_viewing_questions(
    listing: NormalizedListing | dict[str, Any],
    comparison_pool: list[NormalizedListing | dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    must_confirm_questions = []
    field_status = _item_value(listing, "field_status") or {}

    if field_status.get("bathrooms") == "unknown":
        must_confirm_questions.append("这套房是 1 卫还是 2 卫？卫生间是否为套内独立使用？")
    if field_status.get("area_size") == "unknown":
        must_confirm_questions.append("这套房的实际面积是多少？是建筑面积还是套内面积？")
    if field_status.get("parking") == "unknown":
        must_confirm_questions.append("车位是否包含在价格里？如果不包含，额外费用是多少？")
    if field_status.get("pet_policy") == "unknown":
        must_confirm_questions.append("宠物政策是什么？是否允许养猫/养狗，是否有额外 pet fee 或押金？")
    if field_status.get("price_period") == "unknown" and _item_value(listing, "rent_or_sale") == "rent":
        must_confirm_questions.append("这个报价是按月、按周还是按天计算？")

    risk_questions = []
    price_analysis = _item_value(listing, "price_analysis") or {}
    if not price_analysis.get("is_trustworthy_price", True):
        risk_questions.append("请确认这条价格是否为真实有效报价，以及是否还有额外费用。")
    if _item_value(listing, "price_is_estimated_monthly"):
        risk_questions.append("请确认平台上的原始报价口径，避免把日租/周租误当成月租。")
    if _item_value(listing, "image_quality") in {"placeholder_only", "missing"}:
        risk_questions.append("当前只有占位图或没有实拍图，能否提供真实室内照片或视频看房？")
    if ((_item_value(listing, "location_relevance") or {}).get("reason") or "").startswith("Outside"):
        risk_questions.append("请确认精确地址和通勤路线，避免它其实不在你想看的区域内。")
    if _item_value(listing, "location_text") is None:
        risk_questions.append("请确认这套房的精确地址、楼栋名和最近地标。")

    comparison_questions = []
    peers = [item for item in (comparison_pool or []) if _item_value(item, "canonical_id") != _item_value(listing, "canonical_id")]
    if peers:
        if field_status.get("bathrooms") == "unknown" and any(_item_field_status(item, "bathrooms") != "unknown" for item in peers):
            comparison_questions.append("和其他候选相比，这套房的卫浴配置会不会更弱？请确认 1 卫还是 2 卫。")
        if field_status.get("parking") == "unknown" and any(_item_field_status(item, "parking") != "unknown" for item in peers):
            comparison_questions.append("其他候选已经提到停车信息，这套房是否也有固定车位或附近停车方案？")
        if field_status.get("pet_policy") == "unknown" and any(_item_field_status(item, "pet_policy") != "unknown" for item in peers):
            comparison_questions.append("如果你要养宠，先确认这套房的宠物政策，避免它在这点上落后于其他候选。")
        if _item_value(listing, "image_quality") in {"placeholder_only", "missing"} and any(_item_value(item, "image_quality") == "real_images" for item in peers):
            comparison_questions.append("其他候选至少有实拍图，这套房能否补充真实照片，方便横向比较？")
        if _item_value(listing, "monthly_price_value") is not None:
            peer_prices = [
                _item_value(item, "monthly_price_value")
                for item in peers
                if _item_value(item, "monthly_price_value") is not None
            ]
            if peer_prices and _item_value(listing, "monthly_price_value") > min(peer_prices):
                comparison_questions.append("如果这套房价格更高，它比更便宜的候选多提供了哪些实际价值？")

    viewing_questions = list(dict.fromkeys(must_confirm_questions + risk_questions + comparison_questions))
    return {
        "viewing_questions": viewing_questions,
        "must_confirm_questions": must_confirm_questions,
        "risk_questions": risk_questions,
        "comparison_questions": comparison_questions,
    }


def build_compare_matrix(items: list[NormalizedListing | dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "columns": [],
            "rows": [],
            "summary": {},
            "comparison_takeaways": [],
        }

    columns = [
        {
            "canonical_id": _item_value(item, "canonical_id"),
            "title": _item_value(item, "title"),
        }
        for item in items
    ]
    rows = [
        _matrix_row(items, field="price", label="价格", formatter=_format_price_cell),
        _matrix_row(items, field="price_period", label="价格口径", formatter=lambda item: _item_value(item, "price_period") or "unknown"),
        _matrix_row(items, field="bedrooms", label="卧室数", formatter=lambda item: _format_number_cell(_item_value(item, "beds"), suffix=" bed")),
        _matrix_row(items, field="bathrooms", label="卫生间", formatter=lambda item: _format_number_cell(_item_value(item, "baths"), suffix=" bath")),
        _matrix_row(items, field="area_size", label="面积", formatter=_format_area_cell),
        _matrix_row(items, field="parking", label="停车", formatter=lambda item: _item_field_value(item, "parking") or "unknown"),
        _matrix_row(items, field="pet_policy", label="宠物政策", formatter=lambda item: _item_field_value(item, "pet_policy") or "unknown"),
        _matrix_row(items, field="images", label="图片质量", formatter=_format_image_cell),
        _matrix_row(items, field="location", label="位置", formatter=lambda item: _item_value(item, "location_text") or "unknown"),
        _matrix_row(items, field="location", label="位置相关性", formatter=lambda item: _item_location_reason(item)),
        _matrix_row(items, field="price", label="价格风险", formatter=lambda item: _format_price_warning(item)),
        _matrix_row(items, field="images", label="关键缺失字段", formatter=lambda item: ", ".join(_item_value(item, "missing_fields") or []) or "none"),
    ]

    cheapest = _best_by(items, lambda item: _item_value(item, "monthly_price_value"), lowest=True)
    most_complete = _best_by(items, lambda item: len(_item_value(item, "known_fields") or []), lowest=False)
    closest = _best_by(items, lambda item: _item_score(item, "location_relevance_score"), lowest=False)
    highest_uncertainty = _best_by(items, _uncertainty_score, lowest=False)

    comparison_takeaways = []
    if cheapest:
        comparison_takeaways.append(f"更便宜的是 {_item_value(cheapest, 'title')}。")
    if most_complete:
        comparison_takeaways.append(f"信息更完整的是 {_item_value(most_complete, 'title')}。")
    if closest:
        comparison_takeaways.append(f"更接近当前目标区域的是 {_item_value(closest, 'title')}。")
    if highest_uncertainty:
        comparison_takeaways.append(f"不确定性最大的是 {_item_value(highest_uncertainty, 'title')}，看房前要先补齐关键信息。")

    return {
        "columns": columns,
        "rows": rows,
        "summary": {
            "cheapest": _item_summary(cheapest),
            "most_complete": _item_summary(most_complete),
            "closest_to_target": _item_summary(closest),
            "highest_uncertainty": _item_summary(highest_uncertainty),
        },
        "comparison_takeaways": comparison_takeaways,
    }


def summarize_field_coverage(items: list[NormalizedListing | dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, dict[str, int]] = {}
    for item in items:
        statuses = _item_value(item, "field_status") or {}
        for field, status in statuses.items():
            bucket = status_counts.setdefault(field, {"present": 0, "inferred": 0, "unknown": 0})
            bucket[status] = bucket.get(status, 0) + 1

    known_fields = sorted(
        {
            field
            for item in items
            for field in (_item_value(item, "known_fields") or [])
        }
    )
    missing_fields = sorted(
        {
            field
            for item in items
            for field in (_item_value(item, "missing_fields") or [])
            if (_item_value(item, "field_status") or {}).get(field) == "unknown"
        }
    )
    return {
        "field_status": status_counts,
        "known_fields": known_fields,
        "missing_fields": missing_fields,
    }


def build_field_source_summary(item: NormalizedListing | dict[str, Any]) -> dict[str, list[str]]:
    field_sources = _item_value(item, "field_sources") or {}
    explicit = []
    inferred = []
    unknown = []
    for field, meta in field_sources.items():
        source = meta.get("source")
        status = meta.get("status")
        if status == "unknown" or source == "not_available":
            unknown.append(field)
        elif source in {"detail_page_field", "location_text_match"}:
            explicit.append(field)
        else:
            inferred.append(field)
    return {
        "explicit_fields": explicit,
        "inferred_fields": inferred,
        "unknown_fields": unknown,
    }


def _item_value(item: NormalizedListing | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _item_field_meta(item: NormalizedListing | dict[str, Any], field: str) -> dict[str, Any]:
    field_sources = _item_value(item, "field_sources") or {}
    return field_sources.get(field, {})


def _item_field_status(item: NormalizedListing | dict[str, Any], field: str) -> str:
    meta = _item_field_meta(item, field)
    return meta.get("status") or (_item_value(item, "field_status") or {}).get(field, "unknown")


def _item_field_value(item: NormalizedListing | dict[str, Any], field: str) -> Any:
    meta = _item_field_meta(item, field)
    return meta.get("value")


def _item_score(item: NormalizedListing | dict[str, Any], key: str) -> float:
    scores = _item_value(item, "scores") or {}
    return float(scores.get(key, 0.0))


def _matrix_row(
    items: list[NormalizedListing | dict[str, Any]],
    *,
    field: str,
    label: str,
    formatter,
) -> dict[str, Any]:
    return {
        "field": field,
        "label": label,
        "cells": [
            {
                "canonical_id": _item_value(item, "canonical_id"),
                "value": formatter(item),
                "status": _item_field_status(item, field),
                "source": _item_field_meta(item, field).get("source"),
                "confidence": _item_field_meta(item, field).get("confidence"),
            }
            for item in items
        ],
    }


def _format_number_cell(value: Any, *, suffix: str = "") -> str:
    if value is None:
        return "unknown"
    return f"{value:g}{suffix}"


def _format_price_cell(item: NormalizedListing | dict[str, Any]) -> str:
    price_text = _item_value(item, "price_text")
    monthly = _item_value(item, "monthly_price_value")
    if price_text and monthly and _item_value(item, "rent_or_sale") == "rent":
        return f"{price_text} -> ~{monthly:.0f}/month"
    return price_text or (f"{monthly:.0f}/month" if monthly else "unknown")


def _format_area_cell(item: NormalizedListing | dict[str, Any]) -> str:
    value = _item_value(item, "area_size_value")
    unit = _item_value(item, "area_size_unit")
    if value is None or not unit:
        return "unknown"
    return f"{value:g} {unit}"


def _format_image_cell(item: NormalizedListing | dict[str, Any]) -> str:
    quality = _item_value(item, "image_quality") or "missing"
    count = len(_item_value(item, "image_urls") or [])
    if quality == "placeholder_only":
        return f"placeholder only ({count})"
    if quality == "mixed":
        return f"mixed ({count})"
    if quality == "real_images":
        return f"real images ({count})"
    return "missing"


def _item_location_reason(item: NormalizedListing | dict[str, Any]) -> str:
    location_relevance = _item_value(item, "location_relevance") or {}
    return location_relevance.get("reason") or "unknown"


def _format_price_warning(item: NormalizedListing | dict[str, Any]) -> str:
    price_analysis = _item_value(item, "price_analysis") or {}
    if price_analysis.get("price_warning"):
        return price_analysis["price_warning"]
    return "none"


def _best_by(items: list[NormalizedListing | dict[str, Any]], scorer, *, lowest: bool) -> NormalizedListing | dict[str, Any] | None:
    candidates = []
    for item in items:
        value = scorer(item)
        if value is None:
            continue
        candidates.append((value, item))
    if not candidates:
        return None
    return min(candidates, key=lambda pair: pair[0])[1] if lowest else max(candidates, key=lambda pair: pair[0])[1]


def _uncertainty_score(item: NormalizedListing | dict[str, Any]) -> float:
    missing = len(_item_value(item, "missing_fields") or [])
    image_penalty = 1 if _item_value(item, "image_quality") in {"placeholder_only", "missing"} else 0
    suspicious = 1 if (_item_value(item, "price_anomaly") or {}).get("is_suspicious_low") else 0
    return float(missing + image_penalty + suspicious)


def _item_summary(item: NormalizedListing | dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "canonical_id": _item_value(item, "canonical_id"),
        "title": _item_value(item, "title"),
    }
