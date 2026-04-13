from __future__ import annotations

from statistics import mean

from connectors.base import BasePropertyConnector
from geo import NominatimGeocoder, SchoolFinder, estimate_eta_minutes, haversine_km, try_route_eta
from helpers import area_match_level, normalize_feature_input
from workflows.common import (
    add_match_reasons,
    build_compare_matrix,
    build_field_source_summary,
    build_query_from_inputs,
    build_viewing_questions,
    filter_listings,
    hydrate_and_normalize,
    listing_price_summary,
    score_listing,
    summarize_field_coverage,
)


def search_properties(
    connector: BasePropertyConnector,
    *,
    keyword: str = "",
    country: str = "singapore",
    city: str = "singapore",
    lang: str = "en",
    max_results: int = 100,
    detail_limit: int = 20,
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
        listing_payload["field_source_summary"] = build_field_source_summary(listing)
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
        listing_payload["fit_reasons"] = strict_eval["fit_reasons"]
        listing_payload["tradeoffs"] = strict_eval["tradeoffs"]
        listing_payload["strict_evaluation"] = {
            "is_strict_match": strict_eval["is_strict_match"],
            "off_target": strict_eval["off_target"],
            "hard_constraint_miss": strict_eval["hard_constraint_miss"],
            "suspicious": strict_eval["suspicious"],
        }
        if exclude_suspicious_low and listing.price_anomaly.get("is_suspicious_low"):
            excluded_outlier_count += 1
            continue
        enriched.append(listing_payload)
        if strict_eval["is_strict_match"] and listing.detail_hydrated:
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

    area_distribution = _area_distribution(peers)
    result_quality = _assess_result_quality(
        strict_match_count=len(strict_candidates),
        total_candidates=len(peers),
        excluded_summary=excluded_summary,
    )
    decision_context = _build_decision_context(
        city=city,
        area=area,
        keyword=keyword,
        budget_max=budget_max,
        peers=peers,
        strict_candidates=strict_candidates,
        result_quality=result_quality,
        area_distribution=area_distribution,
    )
    recommended_listings, watchlist_candidates = _build_output_candidates(
        strict_candidates=strict_candidates,
        enriched=enriched,
        decision_mode=decision_context["decision_mode"],
        city=city,
        area=area,
        keyword=keyword,
        budget_max=budget_max,
    )
    candidate_source = recommended_listings if decision_context["decision_mode"] == "recommend" else watchlist_candidates
    compare_matrix = build_compare_matrix(candidate_source[:3]) if candidate_source else build_compare_matrix(enriched[:3])
    field_coverage = summarize_field_coverage(candidate_source[:3]) if candidate_source else summarize_field_coverage(enriched[:3])
    confidence_basis = _build_confidence_basis(
        candidate_pool_size=search_meta["candidate_pool_size"],
        detail_hydrated_count=search_meta["detail_hydrated_count"],
        shallow_only_count=search_meta["shallow_only_count"],
    )
    compare_matrix["sample_basis"] = confidence_basis
    compare_matrix["comparison_takeaways"] = [
        confidence_basis,
        *(compare_matrix.get("comparison_takeaways") or []),
    ]
    decision_summary = _build_decision_summary(
        decision_mode=decision_context["decision_mode"],
        result_quality=result_quality,
        recommendation_count=len(recommended_listings),
        watchlist_count=len(watchlist_candidates),
        candidate_pool_size=search_meta["candidate_pool_size"],
        detail_hydrated_count=search_meta["detail_hydrated_count"],
        shallow_only_count=search_meta["shallow_only_count"],
    )
    next_step_suggestion = _build_next_step_suggestions(
        decision_mode=decision_context["decision_mode"],
        result_quality=result_quality,
        excluded_summary=excluded_summary,
        city=city,
        area=area,
        bedrooms=bedrooms,
        property_type=property_type,
        rent_or_sale=intent_rent_or_sale,
    )
    analysis_sections = _build_analysis_sections(
        decision_mode=decision_context["decision_mode"],
        result_judgement=decision_context["result_judgement"],
        query_fit_summary=decision_context["query_fit_summary"],
        recommended_listings=recommended_listings,
        watchlist_candidates=watchlist_candidates,
        excluded_summary=excluded_summary,
        next_step_suggestion=next_step_suggestion,
    )
    analysis_sections["comparison_takeaways"] = compare_matrix.get("comparison_takeaways", [])
    analysis_sections["field_coverage"] = field_coverage
    user_facing_response = _build_user_facing_response(
        decision_mode=decision_context["decision_mode"],
        result_judgement=decision_context["result_judgement"],
        query_fit_summary=decision_context["query_fit_summary"],
        decision_summary=decision_summary,
        recommended_listings=recommended_listings,
        watchlist_candidates=watchlist_candidates,
        excluded_summary=excluded_summary,
        result_quality=result_quality,
        analysis_sections=analysis_sections,
        compare_matrix=compare_matrix,
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
            "candidate_pool_size": search_meta["candidate_pool_size"],
            "search_rounds": search_meta["search_rounds"],
            "matched_results": len(enriched),
            "detail_hydrated_count": search_meta["detail_hydrated_count"],
            "shallow_only_count": search_meta["shallow_only_count"],
            "confidence_basis": confidence_basis,
            "price_overview_monthly": listing_price_summary(peers),
            "excluded_outlier_count": excluded_outlier_count,
            "area_distribution": area_distribution,
        },
        "field_status": field_coverage["field_status"],
        "known_fields": field_coverage["known_fields"],
        "missing_fields": field_coverage["missing_fields"],
        "listings": enriched,
        "compare_matrix": compare_matrix,
        "result_quality": result_quality,
        "decision_mode": decision_context["decision_mode"],
        "result_judgement": decision_context["result_judgement"],
        "query_fit_summary": decision_context["query_fit_summary"],
        "strict_match_count": len(strict_candidates),
        "recommended_listings": recommended_listings,
        "watchlist_candidates": watchlist_candidates,
        "excluded_summary": excluded_summary,
        "decision_summary": decision_summary,
        "analysis_sections": analysis_sections,
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
    stage_targets = _detail_stage_targets(max_results=max_results, detail_limit=detail_limit)
    strict_count = 0

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
            detail_limit=0,
            geocoder=geocoder,
        )
        new_items = 0
        for listing in normalized_round:
            if listing.canonical_id in seen_ids:
                continue
            seen_ids.add(listing.canonical_id)
            collected.append(listing)
            new_items += 1

        target_detail_count = stage_targets[min(index - 1, len(stage_targets) - 1)]
        hydrated_now = _hydrate_detail_stage(
            connector,
            collected,
            geocoder=geocoder,
            target_detail_count=target_detail_count,
        )

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
            if getattr(listing, "detail_hydrated", False)
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
                "detail_hydrated_count": hydrated_now,
                "shallow_only_count": max(0, len(collected) - hydrated_now),
            }
        )
        if strict_count >= 2:
            break

    return collected, {
        "provider_results": provider_results,
        "candidate_pool_size": len(collected),
        "search_rounds": round_summaries,
        "detail_hydrated_count": sum(1 for listing in collected if getattr(listing, "detail_hydrated", False)),
        "shallow_only_count": sum(1 for listing in collected if not getattr(listing, "detail_hydrated", False)),
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


def _detail_stage_targets(*, max_results: int, detail_limit: int) -> list[int]:
    initial = min(max_results, max(20, detail_limit))
    second = min(max_results, max(40, initial))
    third = min(max_results, max(60, second))
    targets = [initial, second, third]
    deduped = []
    for value in targets:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _hydrate_detail_stage(
    connector: BasePropertyConnector,
    listings: list,
    *,
    geocoder: NominatimGeocoder,
    target_detail_count: int,
) -> int:
    hydrated = 0
    for index, listing in enumerate(listings):
        if index >= target_detail_count:
            break
        if getattr(listing, "detail_hydrated", False):
            hydrated += 1
            continue
        if not listing.url:
            continue
        try:
            detailed = connector.get_listing_detail(url=listing.url)
        except Exception:
            continue
        normalized = hydrate_and_normalize(
            connector,
            [detailed],
            detail_limit=0,
            geocoder=geocoder,
        )[0]
        normalized.detail_hydrated = True
        listings[index] = normalized
        hydrated += 1
    return hydrated


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

    if not getattr(listing, "detail_hydrated", False):
        tradeoffs.append("当前只有浅层列表数据，缺少详情页核验")

    if listing.image_quality in {"placeholder_only", "missing"}:
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


def _build_decision_context(
    *,
    city: str,
    area: str | None,
    keyword: str,
    budget_max: float | None,
    peers: list,
    strict_candidates: list[dict],
    result_quality: dict[str, object],
    area_distribution: dict[str, int],
) -> dict[str, str]:
    is_nyc = (city or "").lower() in {"new-york", "new york"}
    generic_city_query = is_nyc and not area and all(
        token not in (keyword or "").lower()
        for token in ["manhattan", "brooklyn", "queens", "bronx", "staten island", "long island city", "lic"]
    )
    strict_count = len(strict_candidates)
    total_count = len(peers) or 1
    dominant_area = next(iter(area_distribution), "unknown")
    dominant_ratio = (area_distribution.get(dominant_area, 0) / total_count) if total_count else 0.0
    lic_jersey_count = sum(
        1
        for listing in peers
        if getattr(listing, "sub_area", None) in {"long island city", "jersey city"}
        or getattr(listing, "borough", None) == "queens"
    )
    manhattan_brooklyn_count = sum(
        1
        for listing in peers
        if getattr(listing, "sub_area", None) in {"manhattan", "brooklyn"}
        or getattr(listing, "borough", None) in {"manhattan", "brooklyn"}
    )

    if generic_city_query and lic_jersey_count / total_count >= 0.5 and manhattan_brooklyn_count == 0:
        budget_text = f"在 {int(budget_max):,} 预算内，" if budget_max else ""
        return {
            "decision_mode": "explain_only",
            "result_judgement": "这批结果更像 Queens/LIC 或 Jersey City 的样本，不适合直接当成纽约核心区推荐。",
            "query_fit_summary": (
                f"{budget_text}当前平台结果主要集中在 {dominant_area} 一带，"
                "更适合接受通勤换价格的人做参考；如果你心里想的是曼哈顿或布鲁克林核心区，这批结果不建议直接拿来下判断。"
            ),
        }

    if result_quality["level"] == "low":
        return {
            "decision_mode": "explain_only",
            "result_judgement": "这批结果暂时不能直接拿来选房，只能帮助你判断平台当前的供给偏向。",
            "query_fit_summary": "当前结果里缺少足够可信且贴合需求的候选，如果现在直接推荐，会比帮助更容易误导。",
        }

    if result_quality["level"] == "medium" or strict_count == 1:
        return {
            "decision_mode": "watchlist",
            "result_judgement": "这轮只有少量可继续观察的候选，先别把它们当成最终推荐。",
            "query_fit_summary": "当前样本能帮你缩小方向，但还不足以支持直接做决定，更适合作为下一轮精搜的参考。",
        }

    if generic_city_query and dominant_ratio >= 0.75 and result_quality["level"] == "high" and total_count >= 4:
        return {
            "decision_mode": "watchlist",
            "result_judgement": "这批结果基本可看，但区域分布过于集中，先保留观察更稳妥。",
            "query_fit_summary": f"当前房源主要集中在 {dominant_area}，虽然条件大体匹配，但还不够均衡，不建议只看这一个区域就下结论。",
        }

    return {
        "decision_mode": "recommend",
        "result_judgement": "这批结果和你的搜索目标基本一致，可以直接从下面的候选开始看。",
        "query_fit_summary": "当前至少有 2 套以上条件贴合、风险可控的候选，可以先优先比较这几套，再决定是否继续扩大搜索范围。",
    }


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


def _build_output_candidates(
    *,
    strict_candidates: list[dict],
    enriched: list[dict],
    decision_mode: str,
    city: str,
    area: str | None,
    keyword: str,
    budget_max: float | None,
) -> tuple[list[dict], list[dict]]:
    if decision_mode == "recommend":
        selected = strict_candidates[:2]
        return (
            [
                _build_candidate_card(
                    item,
                    decision_mode=decision_mode,
                    strict_candidates=strict_candidates,
                    comparison_pool=selected,
                    index=index,
                    city=city,
                    area=area,
                    keyword=keyword,
                    budget_max=budget_max,
                )
                for index, item in enumerate(selected)
            ],
            [],
        )

    pool = strict_candidates[:2] if strict_candidates else enriched[:2]
    watchlist = [
        _build_candidate_card(
            item,
            decision_mode=decision_mode,
            strict_candidates=strict_candidates,
            comparison_pool=pool,
            index=index,
            city=city,
            area=area,
            keyword=keyword,
            budget_max=budget_max,
        )
        for index, item in enumerate(pool)
    ]
    return [], watchlist


def _build_candidate_card(
    item: dict,
    *,
    decision_mode: str,
    strict_candidates: list[dict],
    comparison_pool: list[dict],
    index: int,
    city: str,
    area: str | None,
    keyword: str,
    budget_max: float | None,
) -> dict:
    fit_reasons = item.get("fit_reasons") or ["与本次搜索条件部分匹配"]
    tradeoffs = item.get("tradeoffs") or []
    suitable_for, not_suitable_for = _infer_audience_fit(item, city=city, area=area, keyword=keyword, budget_max=budget_max)
    decision_tag = _decision_tag_for_item(item, decision_mode)
    compared_advantages, compared_disadvantages = _compare_candidate_against_pool(item, comparison_pool)
    why_not_ideal = "；".join(tradeoffs) if tradeoffs else _fallback_why_not_ideal(item, decision_mode, city=city)
    if decision_mode == "recommend":
        decision_reason = _compose_recommendation_reason(item, strict_candidates, index)
    elif decision_mode == "watchlist":
        decision_reason = _compose_watchlist_reason(item, strict_candidates, index)
    else:
        decision_reason = _compose_explain_reason(item, city=city)
    field_source_summary = build_field_source_summary(item)
    question_bundle = build_viewing_questions(item, comparison_pool=comparison_pool)

    return {
        "canonical_id": item["canonical_id"],
        "title": item["title"],
        "url": item.get("url"),
        "primary_image_url": item.get("primary_image_url"),
        "image_urls": item.get("image_urls", []),
        "image_quality": item.get("image_quality"),
        "image_note": item.get("image_note"),
        "price_text": item.get("price_text"),
        "monthly_price_value": item.get("monthly_price_value"),
        "location_text": item.get("location_text"),
        "rent_or_sale": item.get("rent_or_sale"),
        "fit_reasons": fit_reasons,
        "tradeoffs": tradeoffs,
        "fit_for_user": "；".join(fit_reasons),
        "why_not_ideal": why_not_ideal,
        "decision_reason": decision_reason,
        "recommendation_reason": decision_reason,
        "suitable_for": suitable_for,
        "not_suitable_for": not_suitable_for,
        "decision_tag": decision_tag,
        "field_status": item.get("field_status", {}),
        "field_sources": item.get("field_sources", {}),
        "known_fields": item.get("known_fields", []),
        "missing_fields": item.get("missing_fields", []),
        "field_source_summary": field_source_summary,
        "price_analysis": item.get("price_analysis", {}),
        "compared_advantages": compared_advantages,
        "compared_disadvantages": compared_disadvantages,
        "key_missing_fields": item.get("missing_fields", []),
        "why_recommended": item.get("why_recommended"),
        **question_bundle,
        "scores": item.get("scores", {}),
    }


def _decision_tag_for_item(item: dict, decision_mode: str) -> str:
    if decision_mode == "recommend":
        return "can_consider"
    if item.get("strict_evaluation", {}).get("is_strict_match"):
        return "not_enough_to_recommend"
    return "mismatch_with_goal"


def _fallback_why_not_ideal(item: dict, decision_mode: str, *, city: str) -> str:
    missing_fields = item.get("missing_fields") or []
    if missing_fields:
        return "当前仍有这些关键信息待确认：" + "、".join(missing_fields[:4])
    if decision_mode == "explain_only" and (city or "").lower() in {"new-york", "new york"}:
        return "它本身不一定有明显问题，但所在区域更像替代选项，不足以代表你真正想看的纽约核心区选择。"
    if decision_mode == "watchlist":
        return "它本身条件不差，但当前可对照样本太少，还不足以直接下结论。"
    return "暂无明显短板"


def _compose_recommendation_reason(item: dict, strict_candidates: list[dict], index: int) -> str:
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

    completeness = item.get("scores", {}).get("completeness_score", 0)
    completeness_reason = "信息完整度也更高" if completeness >= 12 else "信息完整度一般" if completeness < 9 else ""

    location_score = item.get("scores", {}).get("location_relevance_score", 0)
    location_reason = "位置相关性更强" if location_score >= 25 else ""

    reasons = [part for part in [price_reason, completeness_reason, location_reason] if part]
    if index == 0 and not reasons:
        reasons.append("综合条件在当前候选里最均衡")
    if index > 0 and not reasons:
        reasons.append("它仍然比其余未推荐候选更贴近你的要求")

    return lead + "，" + "、".join(reasons) + "，所以值得优先看。"


def _compose_watchlist_reason(item: dict, strict_candidates: list[dict], index: int) -> str:
    strict_count = len(strict_candidates)
    if strict_count <= 1:
        return "它基本符合当前搜索条件，但当前样本太少，还不够支撑直接推荐，更适合作为继续观察的候选。"
    if index == 0:
        return "它在当前候选里条件相对靠前，但结果分布不够均衡，先保留观察，比直接推荐更稳妥。"
    return "它有一定参考价值，但还不足以单独支撑决策，建议先和更多同类房源一起比较。"


def _compose_explain_reason(item: dict, *, city: str) -> str:
    location = item.get("location_text") or item.get("title") or "当前样本"
    if (city or "").lower() in {"new-york", "new york"}:
        return f"{location} 说明这个平台当前更容易搜到周边或替代区域样本，但它不足以直接代表你真正想看的纽约核心区选择。"
    return f"{location} 可以作为平台当前可见样本参考，但还不足以直接作为推荐结果。"


def _infer_audience_fit(
    item: dict,
    *,
    city: str,
    area: str | None,
    keyword: str,
    budget_max: float | None,
) -> tuple[str, str]:
    location_text = (item.get("location_text") or "").lower()
    title_text = (item.get("title") or "").lower()
    text = f"{location_text} {title_text}"
    suitable = []
    unsuitable = []

    if budget_max and item.get("monthly_price_value") is not None and item["monthly_price_value"] <= budget_max * 0.6:
        suitable.append("预算优先的人")
    if "furnished" in text:
        suitable.append("希望尽快入住的人")
    if "subway" in text or "mrt" in text or "metro" in text:
        suitable.append("依赖公共交通通勤的人")
    if any(token in text for token in ["long island city", "queens", "jersey city"]):
        suitable.append("接受通勤换价格的人")
    if (city or "").lower() in {"new-york", "new york"} and any(token in text for token in ["long island city", "queens", "jersey city"]):
        unsuitable.append("只看曼哈顿或布鲁克林核心区的人")
    if "studio" in text or "room" in text:
        unsuitable.append("对空间和居住功能要求更高的家庭")
    if area:
        unsuitable.append(f"只接受 {area} 精确板块的人")

    suitable_text = "、".join(dict.fromkeys(suitable)) if suitable else "想先摸清平台上可见样本的人"
    unsuitable_text = "、".join(dict.fromkeys(unsuitable)) if unsuitable else "希望一步到位锁定最终房源的人"
    return suitable_text, unsuitable_text


def _compare_candidate_against_pool(item: dict, comparison_pool: list[dict]) -> tuple[list[str], list[str]]:
    if not comparison_pool:
        return [], []

    advantages = []
    disadvantages = []
    prices = [candidate.get("monthly_price_value") for candidate in comparison_pool if candidate.get("monthly_price_value") is not None]
    completeness_scores = [
        candidate.get("scores", {}).get("completeness_score", 0)
        for candidate in comparison_pool
    ]
    location_scores = [
        candidate.get("scores", {}).get("location_relevance_score", 0)
        for candidate in comparison_pool
    ]

    item_price = item.get("monthly_price_value")
    if item_price is not None and prices:
        if item_price == min(prices):
            advantages.append("在当前候选里价格更低")
        elif item_price == max(prices) and len(prices) > 1:
            disadvantages.append("在当前候选里价格更高")

    completeness = item.get("scores", {}).get("completeness_score", 0)
    if completeness_scores:
        if completeness == max(completeness_scores):
            advantages.append("已知信息相对更完整")
        elif completeness == min(completeness_scores) and len(completeness_scores) > 1:
            disadvantages.append("关键字段缺失相对更多")

    location_score = item.get("scores", {}).get("location_relevance_score", 0)
    if location_scores:
        if location_score == max(location_scores):
            advantages.append("位置更接近这次搜索目标")
        elif location_score == min(location_scores) and len(location_scores) > 1:
            disadvantages.append("位置不如其他候选贴近目标区域")

    if item.get("image_quality") == "real_images":
        advantages.append("至少提供了可用实拍图")
    elif item.get("image_quality") in {"placeholder_only", "missing"}:
        disadvantages.append("当前只有占位图或没有实拍图")

    if item.get("missing_fields"):
        if "bathrooms" in item["missing_fields"]:
            disadvantages.append("卫生间信息仍未知")
        if "parking" in item["missing_fields"]:
            disadvantages.append("停车信息仍未知")
        if "pet_policy" in item["missing_fields"]:
            disadvantages.append("宠物政策仍未知")

    return list(dict.fromkeys(advantages))[:3], list(dict.fromkeys(disadvantages))[:3]


def _build_decision_summary(
    *,
    decision_mode: str,
    result_quality: dict[str, object],
    recommendation_count: int,
    watchlist_count: int,
    candidate_pool_size: int,
    detail_hydrated_count: int,
    shallow_only_count: int,
) -> str:
    basis = f"本轮共扫描 {candidate_pool_size} 条平台样本，其中 {detail_hydrated_count} 条进入详情分析"
    if shallow_only_count:
        basis += f"，另有 {shallow_only_count} 条仅保留浅层样本。"
    else:
        basis += "。"
    if decision_mode == "recommend":
        return f"{basis} 这轮结果和你的目标基本一致，我保留了 {recommendation_count} 套最值得先看的候选。"
    if decision_mode == "watchlist":
        return f"{basis} 这轮只有 {watchlist_count} 套可继续观察的候选，但还不够稳，不建议直接当成最终推荐。"
    return f"{basis} 这轮结果更适合帮助你判断平台供给方向，而不是直接替你做选房决定。"


def _build_next_step_suggestions(
    *,
    decision_mode: str,
    result_quality: dict[str, object],
    excluded_summary: dict[str, int],
    city: str,
    area: str | None,
    bedrooms: float | None,
    property_type: str | None,
    rent_or_sale: str | None,
) -> list[str]:
    suggestions = []
    if decision_mode == "explain_only" and (city or "").lower() in {"new-york", "new york"}:
        suggestions.append("先单独搜 Manhattan、Brooklyn 和 Queens，分开看平台真实供给。")
    if result_quality["level"] == "low" and (city or "").lower() in {"new-york", "new york"}:
        suggestions.append("把范围收紧到 Manhattan、Brooklyn 或 Queens，再单独搜一轮。")
    if excluded_summary["wrong_property_type_count"] > excluded_summary["off_target_location_count"] and property_type:
        suggestions.append(f"如果你接受，可把房型从 {property_type} 放宽到 apartment/condo。")
    if excluded_summary["wrong_bedroom_count"] and bedrooms is not None:
        suggestions.append(f"当前 {int(bedrooms)} 室严格匹配偏少，可以确认是否接受相邻户型。")
    if rent_or_sale == "sale":
        suggestions.append("下一轮建议显式加上 for sale，避免租房或短租结果混入。")
    if rent_or_sale == "rent":
        suggestions.append("下一轮可以加上 monthly rent 或排除 short-term，减少短租样本干扰。")
    if area:
        suggestions.append(f"可以继续指定 {area} 内的细分板块，提高结果纯度。")
    return suggestions[:3] or ["先继续补召回，再决定要不要放宽条件。"]


def _build_confidence_basis(
    *,
    candidate_pool_size: int,
    detail_hydrated_count: int,
    shallow_only_count: int,
) -> str:
    basis = f"本轮共扫描 {candidate_pool_size} 条平台样本，其中 {detail_hydrated_count} 条进入详情分析"
    if shallow_only_count:
        return basis + f"，另有 {shallow_only_count} 条仍是浅层样本。"
    return basis + "。"


def _build_analysis_sections(
    *,
    decision_mode: str,
    result_judgement: str,
    query_fit_summary: str,
    recommended_listings: list[dict],
    watchlist_candidates: list[dict],
    excluded_summary: dict[str, int],
    next_step_suggestion: list[str],
) -> dict[str, object]:
    candidate_source = recommended_listings if decision_mode == "recommend" else watchlist_candidates
    candidate_analysis = [
        {
            "title": item["title"],
            "decision_tag": item["decision_tag"],
            "decision_reason": item["decision_reason"],
            "fit_for_user": item["fit_for_user"],
            "why_not_ideal": item["why_not_ideal"],
            "compared_advantages": item.get("compared_advantages", []),
            "compared_disadvantages": item.get("compared_disadvantages", []),
            "missing_fields": item.get("missing_fields", []),
        }
        for item in candidate_source
    ]
    excluded_parts = [
        f"{excluded_summary['off_target_location_count']} 套位置明显跑偏" if excluded_summary["off_target_location_count"] else "",
        f"{excluded_summary['wrong_bedroom_count']} 套不满足卧室数要求" if excluded_summary["wrong_bedroom_count"] else "",
        f"{excluded_summary['wrong_property_type_count']} 套房型不符" if excluded_summary["wrong_property_type_count"] else "",
        f"{excluded_summary['wrong_rent_or_sale_count']} 套买卖意图不明确或不符" if excluded_summary["wrong_rent_or_sale_count"] else "",
        f"{excluded_summary['suspicious_count']} 套属于短租/异常价格" if excluded_summary["suspicious_count"] else "",
    ]
    return {
        "judgement": result_judgement,
        "fit_analysis": query_fit_summary,
        "candidate_analysis": candidate_analysis,
        "why_not_direct_recommendation": (
            "；".join(part for part in excluded_parts if part)
            if decision_mode != "recommend"
            else "其余候选没有达到当前推荐标准。"
        ),
        "next_steps": list(next_step_suggestion),
    }


def _build_user_facing_response(
    *,
    decision_mode: str,
    result_judgement: str,
    query_fit_summary: str,
    decision_summary: str,
    recommended_listings: list[dict],
    watchlist_candidates: list[dict],
    excluded_summary: dict[str, int],
    result_quality: dict[str, object],
    analysis_sections: dict[str, object],
    compare_matrix: dict[str, object],
    next_step_suggestion: list[str],
) -> str:
    lines = [
        f"一句话判断：{result_judgement}",
        f"总体分析：{query_fit_summary}",
        f"结论依据：{decision_summary}",
        "",
    ]
    candidate_source = recommended_listings if decision_mode == "recommend" else watchlist_candidates
    if candidate_source:
        title = "推荐结果：" if decision_mode == "recommend" else "可参考观察项：" if decision_mode == "watchlist" else "当前平台可见样本（不直接推荐）："
        lines.append(title)
        for index, item in enumerate(candidate_source, start=1):
            lines.append(f"{index}. {item['title']}")
            price_line = item["price_text"] or "价格待确认"
            if item.get("monthly_price_value") and item.get("rent_or_sale") == "rent":
                price_line += f"（按月口径约 {item['monthly_price_value']:.0f}）"
            lines.append(f"价格：{price_line}")
            if item.get("location_text"):
                lines.append(f"位置：{item['location_text']}")
            if item.get("primary_image_url"):
                lines.append(f"图片：{item['primary_image_url']}")
            if item.get("image_note"):
                lines.append("图片说明：" + item["image_note"])
            lines.append("为什么它贴合这次搜索：" + item["fit_for_user"])
            lines.append("为什么它又不是最理想选择：" + item["why_not_ideal"])
            lines.append("这次怎么处理它：" + item["decision_reason"])
            if item.get("compared_advantages"):
                lines.append("和其他候选相比的优势：" + "；".join(item["compared_advantages"]))
            if item.get("compared_disadvantages"):
                lines.append("和其他候选相比的短板：" + "；".join(item["compared_disadvantages"]))
            source_summary = item.get("field_source_summary") or {}
            explicit_fields = "、".join(source_summary.get("explicit_fields") or [])
            inferred_fields = "、".join(source_summary.get("inferred_fields") or [])
            unknown_fields = "、".join(source_summary.get("unknown_fields") or [])
            if explicit_fields or inferred_fields or unknown_fields:
                parts = []
                if explicit_fields:
                    parts.append(f"页面明确给出的信息：{explicit_fields}")
                if inferred_fields:
                    parts.append(f"根据标题/描述推断的信息：{inferred_fields}")
                if unknown_fields:
                    parts.append(f"当前仍未知的信息：{unknown_fields}")
                lines.append("字段说明：" + "；".join(parts))
            if item.get("must_confirm_questions"):
                lines.append("看房前优先确认：" + "；".join(item["must_confirm_questions"][:2]))
            if item.get("price_analysis", {}).get("price_warning"):
                lines.append("价格提醒：" + item["price_analysis"]["price_warning"])
            lines.append(f"更适合谁：{item['suitable_for']}")
            lines.append(f"不太适合谁：{item['not_suitable_for']}")
            if item.get("url"):
                lines.append(f"查看详情：{item['url']}")
            lines.append("")
        if decision_mode == "recommend":
            lines.append("为什么只推荐这些：这些房源至少通过了位置、买卖意图和户型等硬条件校验，其余候选没有达到推荐标准。")
    else:
        lines.append("候选分析：当前没有足够可信的候选可供展开。")

    lines.append("")
    comparison_takeaways = compare_matrix.get("comparison_takeaways") or []
    if comparison_takeaways:
        lines.append("这轮主要比较了什么：" + "；".join(comparison_takeaways))
    sample_basis = compare_matrix.get("sample_basis")
    if sample_basis:
        if "100 条" in sample_basis or "60 条" in sample_basis or "40 条" in sample_basis or "20 条" in sample_basis:
            lines.append("样本说明：这轮结论基于较大样本池，不是只看前几条结果得出的。")
        else:
            lines.append("样本说明：虽然我扩大了搜索范围，但平台当前有效样本仍然有限。")
    if decision_mode != "recommend":
        lines.append("为什么这次不直接推荐：" + analysis_sections["why_not_direct_recommendation"])
    else:
        lines.append("为什么这次可以直接推荐：至少有 2 套以上候选同时满足核心条件，且没有明显异常。")
    lines.append(f"本轮结果可信度：{result_quality['label']}。")
    lines.append("下一步建议：" + "；".join(next_step_suggestion))
    return "\n".join(line for line in lines if line is not None)


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
        question_bundle = build_viewing_questions(listing, comparison_pool=peer_group)
        compared.append(
            {
                **listing.to_dict(),
                "scores": scores,
                **question_bundle,
                "field_source_summary": build_field_source_summary(listing),
            }
        )
    compared.sort(key=lambda item: item["scores"]["total_score"], reverse=True)
    recommendation = compared[0]["canonical_id"] if compared else None
    compare_matrix = build_compare_matrix(compared)
    compare_matrix["sample_basis"] = f"本轮共比较 {len(compared)} 条已进入详情分析的房源。"
    compare_matrix["comparison_takeaways"] = [
        compare_matrix["sample_basis"],
        *(compare_matrix.get("comparison_takeaways") or []),
    ]
    field_coverage = summarize_field_coverage(compared)
    return {
        "input": {"urls": urls},
        "comparison": compared,
        "compare_matrix": compare_matrix,
        "field_status": field_coverage["field_status"],
        "known_fields": field_coverage["known_fields"],
        "missing_fields": field_coverage["missing_fields"],
        "recommended_listing_id": recommendation,
        "recommended_reason": (
            "；".join(compare_matrix.get("comparison_takeaways") or []) if recommendation else None
        ),
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
    compare_candidates = [subject] + [peer for peer in peers if peer.canonical_id != subject.canonical_id]
    compare_matrix = build_compare_matrix(compare_candidates[:3])
    compare_matrix["sample_basis"] = (
        f"当前性价比评分基于 {len(compare_candidates[:3])} 条候选比较，其中包含 1 条目标房源详情。"
    )
    compare_matrix["comparison_takeaways"] = [
        compare_matrix["sample_basis"],
        *(compare_matrix.get("comparison_takeaways") or []),
    ]
    return {
        "listing": {
            **subject.to_dict(),
            **build_viewing_questions(subject, comparison_pool=compare_candidates[:3]),
            "field_source_summary": build_field_source_summary(subject),
        },
        "scores": scores,
        "compare_matrix": compare_matrix,
        "field_status": subject.field_status,
        "known_fields": subject.known_fields,
        "missing_fields": subject.missing_fields,
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
