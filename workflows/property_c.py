from __future__ import annotations

from statistics import mean

from connectors.base import BasePropertyConnector
from geo import NominatimGeocoder, SchoolFinder, estimate_eta_minutes, haversine_km, try_route_eta
from helpers import area_match_level, normalize_feature_input
from workflows.common import (
    add_match_reasons,
    build_query_from_inputs,
    build_viewing_questions,
    filter_listings,
    hydrate_and_normalize,
    listing_price_summary,
    score_listing,
)


def search_properties(
    connector: BasePropertyConnector,
    *,
    keyword: str = "",
    country: str = "singapore",
    city: str = "singapore",
    lang: str = "en",
    max_results: int = 10,
    detail_limit: int = 10,
    budget_min: float | None = None,
    budget_max: float | None = None,
    bedrooms: float | None = None,
    property_type: str | None = None,
    area: str | None = None,
    features: list[str] | None = None,
    near: str | None = None,
    near_lat: float | None = None,
    near_lng: float | None = None,
    radius_km: float | None = None,
    rent_or_sale: str | None = None,
    nyc_area_mode: str = "core",
    exclude_suspicious_low: bool = False,
) -> dict:
    geocoder = NominatimGeocoder()
    required_features = normalize_feature_input(features)
    intent_rent_or_sale = _infer_rent_or_sale_intent(keyword, rent_or_sale)
    search_rounds = _build_search_rounds(
        keyword=keyword,
        area=area,
        property_type=property_type,
        rent_or_sale=intent_rent_or_sale,
        bedrooms=bedrooms,
    )
    target_point = _resolve_target_point(near=near, near_lat=near_lat, near_lng=near_lng, geocoder=geocoder)
    normalized, search_meta = _collect_search_candidates(
        connector,
        geocoder=geocoder,
        country=country,
        city=city,
        lang=lang,
        max_results=max_results,
        detail_limit=detail_limit,
        search_rounds=search_rounds,
        budget_min=budget_min,
        budget_max=budget_max,
        bedrooms=bedrooms,
        property_type=property_type,
        area=area,
        keyword=keyword,
        required_features=required_features,
        target_point=target_point,
        radius_km=radius_km,
        nyc_area_mode=nyc_area_mode,
        rent_or_sale=intent_rent_or_sale,
    )
    filtered = filter_listings(
        normalized,
        budget_min=budget_min,
        budget_max=budget_max,
        bedrooms=bedrooms,
        property_type=property_type,
        area=area,
        city=city,
        keyword=keyword,
        required_features=required_features,
        near_point=target_point,
        radius_km=radius_km,
        nyc_area_mode=nyc_area_mode,
    )
    peers = filtered or normalized
    enriched = []
    excluded_outlier_count = 0
    strict_candidates = []
    excluded_summary = _initialize_excluded_summary()
    for listing in normalized:
        if not listing.price_anomaly or not listing.location_relevance:
            score_listing(
                listing,
                normalized,
                required_features=required_features,
                target_point=target_point,
                area=area,
                city=city,
                keyword=keyword,
            )
        strict_eval = _evaluate_strict_match(
            listing,
            city=city,
            area=area,
            keyword=keyword,
            bedrooms=bedrooms,
            property_type=property_type,
            rent_or_sale=intent_rent_or_sale,
            nyc_area_mode=nyc_area_mode,
        )
        _update_excluded_summary(excluded_summary, strict_eval)

    for listing in peers:
        scores = score_listing(
            listing,
            peers,
            required_features=required_features,
            target_point=target_point,
            area=area,
            city=city,
            keyword=keyword,
        )
        add_match_reasons(
            listing,
            required_features=required_features,
            target_point=target_point,
            area=area,
            city=city,
            keyword=keyword,
        )
        extra = build_viewing_questions(listing)
        listing_payload = listing.to_dict()
        listing_payload["scores"] = scores
        listing_payload.update(extra)
        if target_point and listing.lat is not None and listing.lng is not None:
            listing_payload["distance_km"] = round(
                haversine_km(target_point[0], target_point[1], listing.lat, listing.lng), 2
            )

        strict_eval = _evaluate_strict_match(
            listing,
            city=city,
            area=area,
            keyword=keyword,
            bedrooms=bedrooms,
            property_type=property_type,
            rent_or_sale=intent_rent_or_sale,
            nyc_area_mode=nyc_area_mode,
        )
        if exclude_suspicious_low and listing.price_anomaly.get("is_suspicious_low"):
            excluded_outlier_count += 1
            continue
        enriched.append(listing_payload)
        if strict_eval["is_strict_match"]:
            strict_candidates.append(
                {
                    **listing_payload,
                    "fit_reasons": strict_eval["fit_reasons"],
                    "tradeoffs": strict_eval["tradeoffs"],
                    "why_recommended": _build_why_recommended(listing, scores, len(strict_candidates)),
                }
            )

    enriched.sort(key=lambda item: item["scores"]["total_score"], reverse=True)
    strict_candidates.sort(key=lambda item: item["scores"]["total_score"], reverse=True)
    if exclude_suspicious_low:
        strict_candidates = [item for item in strict_candidates if not item["price_anomaly"].get("is_suspicious_low")]
        excluded_outlier_count = max(excluded_outlier_count, excluded_summary["suspicious_count"])

    result_quality = _assess_result_quality(
        strict_match_count=len(strict_candidates),
        total_candidates=len(peers),
        excluded_summary=excluded_summary,
    )
    recommended_listings = _select_recommendations(strict_candidates, result_quality["level"])
    decision_summary = _build_decision_summary(result_quality, len(recommended_listings))
    next_step_suggestion = _build_next_step_suggestions(
        result_quality=result_quality,
        excluded_summary=excluded_summary,
        city=city,
        area=area,
        bedrooms=bedrooms,
        property_type=property_type,
        rent_or_sale=intent_rent_or_sale,
    )
    user_facing_response = _build_user_facing_response(
        decision_summary=decision_summary,
        recommended_listings=recommended_listings,
        excluded_summary=excluded_summary,
        result_quality=result_quality,
        next_step_suggestion=next_step_suggestion,
    )
    return {
        "input": {
            "keyword": keyword,
            "country": country,
            "city": city,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "bedrooms": bedrooms,
            "property_type": property_type,
            "rent_or_sale": intent_rent_or_sale,
            "area": area,
            "features": required_features,
            "near": near,
            "near_lat": near_lat,
            "near_lng": near_lng,
            "radius_km": radius_km,
            "nyc_area_mode": nyc_area_mode,
            "exclude_suspicious_low": exclude_suspicious_low,
        },
        "summary": {
            "provider_results": search_meta["provider_results"],
            "search_rounds": search_meta["search_rounds"],
            "matched_results": len(enriched),
            "price_overview_monthly": listing_price_summary(peers),
            "excluded_outlier_count": excluded_outlier_count,
            "area_distribution": _area_distribution(peers),
        },
        "listings": enriched,
        "result_quality": result_quality,
        "strict_match_count": len(strict_candidates),
        "recommended_listings": recommended_listings,
        "excluded_summary": excluded_summary,
        "decision_summary": decision_summary,
        "next_step_suggestion": next_step_suggestion,
        "user_facing_response": user_facing_response,
        "warnings": _build_search_warnings(
            target_point=target_point,
            filtered=enriched,
            required_features=required_features,
            nyc_area_mode=nyc_area_mode,
            excluded_outlier_count=excluded_outlier_count,
            used_near_filter=bool(near or (near_lat is not None and near_lng is not None)),
        ),
        "confidence": _confidence_from_results(enriched),
    }


def _collect_search_candidates(
    connector: BasePropertyConnector,
    *,
    geocoder: NominatimGeocoder,
    country: str,
    city: str,
    lang: str,
    max_results: int,
    detail_limit: int,
    search_rounds: list[str],
    budget_min: float | None,
    budget_max: float | None,
    bedrooms: float | None,
    property_type: str | None,
    area: str | None,
    keyword: str,
    required_features: list[str],
    target_point: tuple[float, float] | None,
    radius_km: float | None,
    nyc_area_mode: str,
    rent_or_sale: str | None,
) -> tuple[list, dict[str, object]]:
    seen_ids: set[str] = set()
    collected = []
    provider_results = 0
    round_summaries = []

    for index, query in enumerate(search_rounds[:3], start=1):
        source_listings = (
            connector.search_property(
                keyword=query,
                country=country,
                city=city,
                lang=lang,
                max_results=max_results,
            )
            if query
            else connector.browse_property(country=country, city=city, lang=lang, max_results=max_results)
        )
        provider_results += len(source_listings)
        normalized_round = hydrate_and_normalize(
            connector,
            source_listings,
            detail_limit=detail_limit,
            geocoder=geocoder,
        )
        new_items = 0
        for listing in normalized_round:
            if listing.canonical_id in seen_ids:
                continue
            seen_ids.add(listing.canonical_id)
            collected.append(listing)
            new_items += 1

        filtered = filter_listings(
            collected,
            budget_min=budget_min,
            budget_max=budget_max,
            bedrooms=bedrooms,
            property_type=property_type,
            area=area,
            city=city,
            keyword=keyword or query,
            required_features=required_features,
            near_point=target_point,
            radius_km=radius_km,
            nyc_area_mode=nyc_area_mode,
        )
        strict_count = sum(
            1
            for listing in filtered
            if _evaluate_strict_match(
                listing,
                city=city,
                area=area,
                keyword=keyword or query,
                bedrooms=bedrooms,
                property_type=property_type,
                rent_or_sale=rent_or_sale,
                nyc_area_mode=nyc_area_mode,
            )["is_strict_match"]
        )
        round_summaries.append(
            {
                "round": index,
                "query": query or "(browse)",
                "provider_results": len(source_listings),
                "new_candidates": new_items,
                "strict_match_count": strict_count,
            }
        )
        if strict_count >= 2:
            break

    return collected, {
        "provider_results": provider_results,
        "search_rounds": round_summaries,
    }


def _build_search_rounds(
    *,
    keyword: str,
    area: str | None,
    property_type: str | None,
    rent_or_sale: str | None,
    bedrooms: float | None,
) -> list[str]:
    queries: list[str] = []

    def add_query(value: str | None) -> None:
        if not value:
            return
        cleaned = " ".join(value.split()).strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    add_query(keyword)
    add_query(
        build_query_from_inputs(
            area=area,
            property_type=property_type,
            rent_or_sale=rent_or_sale,
            bedrooms=bedrooms,
            keyword=None,
        )
    )
    bed_token = ""
    if bedrooms is not None:
        bed_token = "studio" if bedrooms == 0 else f"{int(bedrooms)} bedroom"
    sale_token = "for sale" if rent_or_sale == "sale" else "for rent" if rent_or_sale == "rent" else ""
    if property_type:
        alt_type = "apartment" if property_type.lower() == "house" else "house"
        add_query(" ".join(part for part in [bed_token, alt_type, sale_token] if part))
    add_query(" ".join(part for part in [bed_token.replace("bedroom", "br") if bed_token else "", "home", sale_token] if part))
    if not queries:
        queries.append("")
    return queries[:3]


def _infer_rent_or_sale_intent(keyword: str, explicit_value: str | None) -> str | None:
    if explicit_value:
        return explicit_value
    lowered = keyword.lower()
    if any(token in lowered for token in [" for sale", "buy", "purchase", "sale", "sell"]):
        return "sale"
    if any(token in lowered for token in [" for rent", "rent", "lease", "sublet", "sublease"]):
        return "rent"
    return None


def _initialize_excluded_summary() -> dict[str, int]:
    return {
        "off_target_location_count": 0,
        "hard_constraint_miss_count": 0,
        "suspicious_count": 0,
        "wrong_bedroom_count": 0,
        "wrong_property_type_count": 0,
        "wrong_rent_or_sale_count": 0,
    }


def _evaluate_strict_match(
    listing,
    *,
    city: str,
    area: str | None,
    keyword: str,
    bedrooms: float | None,
    property_type: str | None,
    rent_or_sale: str | None,
    nyc_area_mode: str,
) -> dict[str, object]:
    fit_reasons = []
    tradeoffs = []
    hard_miss = False
    off_target = False
    suspicious = bool(listing.price_anomaly.get("is_suspicious_low"))

    match_level = area_match_level(listing, area, city=city, keyword=keyword)
    if match_level != "outside":
        if listing.sub_area:
            fit_reasons.append(f"位置仍在目标范围内：{listing.sub_area}")
        elif listing.location_text:
            fit_reasons.append(f"位置文本与目标城市匹配：{listing.location_text}")
    else:
        off_target = True
        tradeoffs.append("位置明显偏离目标区域")

    if (city or "").lower() in {"new-york", "new york"} and nyc_area_mode == "core" and listing.sub_area == "jersey city":
        off_target = True
        tradeoffs.append("位于 Jersey City，不属于默认优先的 NYC 核心区域")

    if bedrooms is not None:
        if listing.beds == bedrooms:
            fit_reasons.append(f"满足 {int(bedrooms)} 室要求")
        else:
            hard_miss = True
            tradeoffs.append(f"卧室数不符，当前识别为 {listing.beds if listing.beds is not None else '未知'}")

    if property_type:
        if listing.property_type == property_type.lower():
            fit_reasons.append(f"房型匹配：{property_type.lower()}")
        else:
            hard_miss = True
            tradeoffs.append(f"房型不符，当前更像 {listing.property_type or '未知类型'}")

    if rent_or_sale:
        if listing.rent_or_sale == rent_or_sale:
            fit_reasons.append("买卖意图匹配")
        else:
            hard_miss = True
            tradeoffs.append(f"买卖意图不符，当前识别为 {listing.rent_or_sale or '未知'}")

    if suspicious:
        tradeoffs.append(listing.price_anomaly.get("reason") or "价格或房源类型存在异常")

    if not listing.image_urls:
        tradeoffs.append("图片较少或缺失")
    if not listing.description:
        tradeoffs.append("描述信息不完整")

    return {
        "is_strict_match": not off_target and not hard_miss and not suspicious,
        "off_target": off_target,
        "hard_constraint_miss": hard_miss,
        "suspicious": suspicious,
        "wrong_bedroom": bool(bedrooms is not None and listing.beds != bedrooms),
        "wrong_property_type": bool(property_type and listing.property_type != property_type.lower()),
        "wrong_rent_or_sale": bool(rent_or_sale and listing.rent_or_sale != rent_or_sale),
        "fit_reasons": fit_reasons or ["基础条件部分匹配"],
        "tradeoffs": tradeoffs[:3],
    }


def _update_excluded_summary(summary: dict[str, int], evaluation: dict[str, object]) -> None:
    if evaluation["off_target"]:
        summary["off_target_location_count"] += 1
    if evaluation["hard_constraint_miss"]:
        summary["hard_constraint_miss_count"] += 1
    if evaluation["suspicious"]:
        summary["suspicious_count"] += 1
    if evaluation["wrong_bedroom"]:
        summary["wrong_bedroom_count"] += 1
    if evaluation["wrong_property_type"]:
        summary["wrong_property_type_count"] += 1
    if evaluation["wrong_rent_or_sale"]:
        summary["wrong_rent_or_sale_count"] += 1


def _build_why_recommended(listing, scores: dict, current_rank: int) -> str:
    if current_rank == 0:
        return "它是这批严格匹配候选里最值得先核实的一套。"
    if scores["completeness_score"] >= 12:
        return "虽然不是最便宜，但信息更完整，适合排在前面核实。"
    return "虽然有短板，但它仍比其余候选更接近你的硬条件。"


def _assess_result_quality(
    *,
    strict_match_count: int,
    total_candidates: int,
    excluded_summary: dict[str, int],
) -> dict[str, object]:
    if strict_match_count >= 2:
        level = "high"
        label = "可信可看"
    elif strict_match_count == 1:
        level = "medium"
        label = "勉强可看"
    else:
        level = "low"
        label = "暂不可信"
    return {
        "level": level,
        "label": label,
        "strict_match_count": strict_match_count,
        "total_candidates": total_candidates,
        "off_target_location_count": excluded_summary["off_target_location_count"],
        "hard_constraint_miss_count": excluded_summary["hard_constraint_miss_count"],
        "suspicious_count": excluded_summary["suspicious_count"],
    }


def _select_recommendations(strict_candidates: list[dict], quality_level: str) -> list[dict]:
    max_items = 2 if quality_level == "high" else 1 if quality_level == "medium" else 0
    recommendations = []
    selected = strict_candidates[:max_items]
    for index, item in enumerate(selected):
        recommendations.append(
            {
                "canonical_id": item["canonical_id"],
                "title": item["title"],
                "url": item["url"],
                "price_text": item["price_text"],
                "monthly_price_value": item["monthly_price_value"],
                "location_text": item["location_text"],
                "rent_or_sale": item["rent_or_sale"],
                "fit_reasons": item["fit_reasons"],
                "tradeoffs": item["tradeoffs"],
                "recommendation_reason": _compose_recommendation_reason(item, selected, strict_candidates, index),
                "why_recommended": item["why_recommended"],
                "scores": item["scores"],
            }
        )
    return recommendations


def _build_decision_summary(result_quality: dict[str, object], recommendation_count: int) -> str:
    level = result_quality["level"]
    strict_match_count = result_quality["strict_match_count"]
    if level == "high":
        return f"这轮结果里有 {strict_match_count} 套较可信候选，我只保留了最值得先看的 {recommendation_count} 套。"
    if level == "medium":
        return "这轮只找到 1 套相对靠谱的候选，其余结果要么区域跑偏，要么不满足你的硬条件。"
    return "这轮没有找到足够可信的候选，继续硬推具体房源会误导你。"


def _build_next_step_suggestions(
    *,
    result_quality: dict[str, object],
    excluded_summary: dict[str, int],
    city: str,
    area: str | None,
    bedrooms: float | None,
    property_type: str | None,
    rent_or_sale: str | None,
) -> list[str]:
    suggestions = []
    if result_quality["level"] == "low" and (city or "").lower() in {"new-york", "new york"}:
        suggestions.append("把范围收紧到 Manhattan、Brooklyn 或 Queens，再单独搜一轮。")
    if excluded_summary["wrong_property_type_count"] > excluded_summary["off_target_location_count"] and property_type:
        suggestions.append(f"如果你接受，可把房型从 {property_type} 放宽到 apartment/condo。")
    if excluded_summary["wrong_bedroom_count"] and bedrooms is not None:
        suggestions.append(f"当前 {int(bedrooms)} 室严格匹配偏少，可以确认是否接受相邻户型。")
    if rent_or_sale == "sale":
        suggestions.append("下一轮建议显式加上 for sale，避免租房或短租结果混入。")
    if area:
        suggestions.append(f"可以继续指定 {area} 内的细分板块，提高结果纯度。")
    return suggestions[:3] or ["先继续补召回，再决定要不要放宽条件。"]


def _build_user_facing_response(
    *,
    decision_summary: str,
    recommended_listings: list[dict],
    excluded_summary: dict[str, int],
    result_quality: dict[str, object],
    next_step_suggestion: list[str],
) -> str:
    lines = [decision_summary, ""]
    if recommended_listings:
        lines.append("推荐结果：")
        for index, item in enumerate(recommended_listings, start=1):
            lines.append(f"{index}. {item['title']}")
            price_line = item["price_text"] or "价格待确认"
            if item.get("monthly_price_value") and item.get("rent_or_sale") == "rent":
                price_line += f"（按月口径约 {item['monthly_price_value']:.0f}）"
            lines.append(f"价格：{price_line}")
            if item.get("location_text"):
                lines.append(f"位置：{item['location_text']}")
            lines.append("匹配点：" + "；".join(item["fit_reasons"]))
            lines.append("推荐理由：" + item["recommendation_reason"])
            lines.append("明显短板：" + ("；".join(item["tradeoffs"]) if item["tradeoffs"] else "暂无明显短板"))
            lines.append("为什么仍然推荐：" + item["why_recommended"])
            if item.get("url"):
                lines.append(f"查看详情：{item['url']}")
            lines.append("")
        lines.append("为什么只推荐这些：这些房源至少通过了位置、买卖意图和户型等硬条件校验，其余候选没有达到推荐标准。")
    else:
        lines.append("这轮暂不推荐具体房源，因为当前结果里没有足够可信的候选。")

    lines.append("")
    excluded_parts = [
        f"{excluded_summary['off_target_location_count']} 套位置明显跑偏" if excluded_summary["off_target_location_count"] else "",
        f"{excluded_summary['wrong_bedroom_count']} 套不满足卧室数要求" if excluded_summary["wrong_bedroom_count"] else "",
        f"{excluded_summary['wrong_property_type_count']} 套房型不符" if excluded_summary["wrong_property_type_count"] else "",
        f"{excluded_summary['wrong_rent_or_sale_count']} 套买卖意图不明确或不符" if excluded_summary["wrong_rent_or_sale_count"] else "",
        f"{excluded_summary['suspicious_count']} 套属于短租/异常价格" if excluded_summary["suspicious_count"] else "",
    ]
    lines.append("本轮未推荐原因：" + ("；".join(part for part in excluded_parts if part) or "其余候选没有通过硬条件校验。"))
    lines.append(f"本轮判断：{result_quality['label']}。")
    lines.append("下一步建议：" + "；".join(next_step_suggestion))
    return "\n".join(line for line in lines if line is not None)


def _compose_recommendation_reason(
    item: dict,
    selected: list[dict],
    strict_candidates: list[dict],
    index: int,
) -> str:
    strict_count = len(strict_candidates)
    lead = f"它是当前仅有的 {strict_count} 套严格匹配房源之一"
    if strict_count == 1:
        lead = "它是当前唯一通过硬条件校验的房源"

    prices = [
        candidate["monthly_price_value"]
        for candidate in strict_candidates
        if candidate.get("monthly_price_value") is not None
    ]
    item_price = item.get("monthly_price_value")
    price_reason = ""
    if item_price is not None and prices:
        if item_price == min(prices):
            price_reason = "而且在严格匹配候选里价格更低"
        elif item_price == max(prices) and len(prices) > 1:
            price_reason = "虽然价格不是最低，但并没有因为低价牺牲匹配度"

    completeness = item["scores"].get("completeness_score", 0)
    completeness_reason = ""
    if completeness >= 12:
        completeness_reason = "信息完整度也更高"
    elif completeness < 9:
        completeness_reason = "信息完整度一般"

    location_score = item["scores"].get("location_relevance_score", 0)
    location_reason = "位置相关性更强" if location_score >= 25 else ""

    reasons = [part for part in [price_reason, completeness_reason, location_reason] if part]
    if index == 0 and not reasons:
        reasons.append("综合条件在当前候选里最均衡")
    if index > 0 and not reasons:
        reasons.append("它仍然比其余未推荐候选更贴近你的要求")

    return lead + "，" + "、".join(reasons) + "，所以值得优先看。"


def compare_properties(
    connector: BasePropertyConnector,
    *,
    urls: list[str],
) -> dict:
    normalized = hydrate_and_normalize(
        connector,
        [connector.get_listing_detail(url=url) for url in urls],
        detail_limit=len(urls),
    )
    peer_group = normalized
    compared = []
    for listing in normalized:
        scores = score_listing(listing, peer_group)
        compared.append(
            {
                **listing.to_dict(),
                "scores": scores,
                **build_viewing_questions(listing),
            }
        )
    compared.sort(key=lambda item: item["scores"]["total_score"], reverse=True)
    recommendation = compared[0]["canonical_id"] if compared else None
    return {
        "input": {"urls": urls},
        "comparison": compared,
        "recommended_listing_id": recommendation,
        "recommended_reason": "Best overall balance of price, completeness, and fit." if recommendation else None,
        "warnings": [],
        "confidence": _confidence_from_results(compared),
    }


def estimate_total_cost(
    *,
    price: float,
    rent_or_sale: str,
    deposit_months: float = 2.0,
    parking_monthly: float = 0.0,
    commute_cost_per_trip: float = 0.0,
    commute_days_per_month: int = 20,
    down_payment_ratio: float = 0.2,
    annual_interest_rate: float = 0.04,
    mortgage_years: int = 30,
) -> dict:
    if rent_or_sale == "rent":
        monthly_total = price + parking_monthly + commute_cost_per_trip * commute_days_per_month * 2
        return {
            "rent_or_sale": "rent",
            "monthly_rent": price,
            "deposit": round(price * deposit_months, 2),
            "monthly_parking": parking_monthly,
            "monthly_commute": round(commute_cost_per_trip * commute_days_per_month * 2, 2),
            "estimated_monthly_total": round(monthly_total, 2),
        }

    loan_principal = price * (1 - down_payment_ratio)
    monthly_rate = annual_interest_rate / 12
    total_payments = mortgage_years * 12
    monthly_payment = 0.0
    if monthly_rate > 0:
        monthly_payment = (
            loan_principal
            * monthly_rate
            * (1 + monthly_rate) ** total_payments
            / ((1 + monthly_rate) ** total_payments - 1)
        )
    return {
        "rent_or_sale": "sale",
        "total_price": price,
        "down_payment": round(price * down_payment_ratio, 2),
        "loan_principal": round(loan_principal, 2),
        "estimated_monthly_mortgage": round(monthly_payment, 2),
    }


def score_value(
    connector: BasePropertyConnector,
    *,
    url: str,
    comparable_urls: list[str] | None = None,
) -> dict:
    subject = hydrate_and_normalize(connector, [connector.get_listing_detail(url=url)], detail_limit=1)[0]
    peers = [subject]
    if comparable_urls:
        peers = hydrate_and_normalize(
            connector,
            [connector.get_listing_detail(url=item) for item in comparable_urls],
            detail_limit=len(comparable_urls),
        )
    scores = score_listing(subject, peers)
    return {
        "listing": subject.to_dict(),
        "scores": scores,
        "warnings": [] if comparable_urls else ["Score is based on limited peer context."],
        "confidence": round(subject.parse_confidence, 2),
    }


def find_nearby_schools(
    *,
    url: str | None = None,
    connector: BasePropertyConnector | None = None,
    location: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: int = 1500,
) -> dict:
    geocoder = NominatimGeocoder()
    if url and connector:
        listing = hydrate_and_normalize(connector, [connector.get_listing_detail(url=url)], detail_limit=1, geocoder=geocoder)[0]
        lat, lng = listing.lat, listing.lng
        location = listing.location_text or location
    if lat is None or lng is None:
        coords = geocoder.geocode(location)
        if coords:
            lat, lng = coords
    schools = SchoolFinder().nearby_schools(lat, lng, radius_m=radius_m)
    return {
        "location": location,
        "lat": lat,
        "lng": lng,
        "radius_m": radius_m,
        "schools": schools,
        "warnings": [] if schools else ["No nearby schools found or location could not be resolved."],
        "confidence": 0.55 if schools else 0.2,
    }


def build_commute_summary(
    *,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    mode: str = "driving",
) -> dict:
    if not origin or not destination:
        return {"eta_minutes": None, "distance_km": None, "is_estimated": True}
    distance = round(haversine_km(origin[0], origin[1], destination[0], destination[1]), 2)
    route_eta, exact = try_route_eta(origin, destination, mode=mode)
    eta = route_eta if exact else estimate_eta_minutes(distance, mode=mode)
    return {
        "eta_minutes": eta,
        "distance_km": distance,
        "is_estimated": not exact,
        "mode": mode,
    }


def _resolve_target_point(
    *,
    near: str | None,
    near_lat: float | None,
    near_lng: float | None,
    geocoder: NominatimGeocoder,
) -> tuple[float, float] | None:
    if near_lat is not None and near_lng is not None:
        return near_lat, near_lng
    if near:
        return geocoder.geocode(near)
    return None


def _build_search_warnings(
    *,
    target_point: tuple[float, float] | None,
    filtered: list[dict] | list,
    required_features: list[str],
    nyc_area_mode: str,
    excluded_outlier_count: int,
    used_near_filter: bool,
) -> list[str]:
    warnings = []
    if used_near_filter and target_point is None:
        warnings.append("GPS-nearby filtering was skipped because the target location could not be resolved.")
    if required_features:
        warnings.append("Feature matching is keyword-based in v1.0 and may miss synonyms.")
    if not filtered:
        warnings.append("No listings matched the current filters.")
    if nyc_area_mode != "any":
        warnings.append("NYC searches prioritize core borough relevance over nearby out-of-area listings.")
    if excluded_outlier_count:
        warnings.append(f"Excluded {excluded_outlier_count} suspiciously low-priced listing(s).")
    return warnings


def _confidence_from_results(results: list[dict] | list) -> float:
    if not results:
        return 0.2
    confidences = []
    for result in results:
        if isinstance(result, dict):
            confidences.append(float(result.get("parse_confidence", 0.5)))
        else:
            confidences.append(float(getattr(result, "parse_confidence", 0.5)))
    return round(mean(confidences), 2)


def _area_distribution(listings: list) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for listing in listings:
        area_key = listing.sub_area or listing.borough or listing.area_name or "unknown"
        distribution[area_key] = distribution.get(area_key, 0) + 1
    return dict(sorted(distribution.items(), key=lambda item: (-item[1], item[0])))
