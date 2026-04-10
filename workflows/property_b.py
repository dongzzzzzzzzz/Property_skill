from __future__ import annotations

from statistics import median

from connectors.base import BasePropertyConnector
from helpers import normalize_feature_input, percentile_range
from workflows.common import (
    build_query_from_inputs,
    find_comparable_listings,
    hydrate_and_normalize,
    listing_price_summary,
)


def summarize_area_price(
    connector: BasePropertyConnector,
    *,
    keyword: str | None = None,
    country: str = "singapore",
    city: str = "singapore",
    lang: str = "en",
    max_results: int = 12,
    area: str | None = None,
    property_type: str | None = None,
    rent_or_sale: str | None = None,
    bedrooms: float | None = None,
) -> dict:
    query = build_query_from_inputs(
        area=area,
        property_type=property_type,
        rent_or_sale=rent_or_sale,
        bedrooms=bedrooms,
        keyword=keyword,
    )
    source = (
        connector.search_property(keyword=query, country=country, city=city, lang=lang, max_results=max_results)
        if query
        else connector.browse_property(country=country, city=city, lang=lang, max_results=max_results)
    )
    market = hydrate_and_normalize(connector, source, detail_limit=min(max_results, 10))
    summary = listing_price_summary(market)
    return {
        "input": {
            "keyword": keyword,
            "area": area,
            "property_type": property_type,
            "rent_or_sale": rent_or_sale,
            "bedrooms": bedrooms,
            "country": country,
            "city": city,
        },
        "summary": summary,
        "sample_listings": [listing.to_dict() for listing in market[:5]],
        "warnings": [
            "Prices reflect currently visible listing samples from the source skill, not closed transactions."
        ],
        "confidence": 0.75 if summary["sample_size"] >= 5 else 0.45,
    }


def find_comparables(
    connector: BasePropertyConnector,
    *,
    url: str | None = None,
    keyword: str | None = None,
    country: str = "singapore",
    city: str = "singapore",
    lang: str = "en",
    max_results: int = 12,
    area: str | None = None,
    property_type: str | None = None,
    rent_or_sale: str | None = None,
    bedrooms: float | None = None,
) -> dict:
    subject = None
    if url:
        subject = hydrate_and_normalize(connector, [connector.get_listing_detail(url=url)], detail_limit=1)[0]
        area = area or subject.area_name
        property_type = property_type or subject.property_type
        rent_or_sale = rent_or_sale or subject.rent_or_sale
        bedrooms = bedrooms if bedrooms is not None else subject.beds

    query = build_query_from_inputs(
        area=area,
        property_type=property_type,
        rent_or_sale=rent_or_sale,
        bedrooms=bedrooms,
        keyword=keyword,
    )
    source = (
        connector.search_property(keyword=query, country=country, city=city, lang=lang, max_results=max_results)
        if query
        else connector.browse_property(country=country, city=city, lang=lang, max_results=max_results)
    )
    market = hydrate_and_normalize(connector, source, detail_limit=min(max_results, 10))
    if subject is None:
        filtered = [
            listing
            for listing in market
            if (not property_type or listing.property_type == property_type)
            and (bedrooms is None or listing.beds is None or abs(listing.beds - bedrooms) <= 1)
        ][:5]
    else:
        filtered = find_comparable_listings(subject, market, max_items=5)

    return {
        "subject": subject.to_dict() if subject else None,
        "comparables": [listing.to_dict() for listing in filtered],
        "warnings": [] if filtered else ["No comparables were found with the current constraints."],
        "confidence": 0.75 if len(filtered) >= 3 else 0.45,
    }


def suggest_listing_price(
    connector: BasePropertyConnector,
    *,
    url: str | None = None,
    keyword: str | None = None,
    country: str = "singapore",
    city: str = "singapore",
    lang: str = "en",
    max_results: int = 12,
    area: str | None = None,
    property_type: str | None = None,
    rent_or_sale: str | None = None,
    bedrooms: float | None = None,
    features: list[str] | None = None,
) -> dict:
    comparable_payload = find_comparables(
        connector,
        url=url,
        keyword=keyword,
        country=country,
        city=city,
        lang=lang,
        max_results=max_results,
        area=area,
        property_type=property_type,
        rent_or_sale=rent_or_sale,
        bedrooms=bedrooms,
    )
    comparables = comparable_payload["comparables"]
    comparable_prices = [item["price_value"] for item in comparables if item.get("price_value") is not None]
    p25, med, p75 = percentile_range(comparable_prices)
    feature_boost = 0.0
    normalized_features = normalize_feature_input(features)
    if "furnished" in normalized_features:
        feature_boost += 0.03
    if "parking" in normalized_features:
        feature_boost += 0.03
    if "pet friendly" in normalized_features:
        feature_boost += 0.02
    if med is None:
        return {
            "suggested_min": None,
            "suggested_max": None,
            "comparables_used": len(comparables),
            "warnings": ["Not enough priced comparables to suggest a range."],
            "confidence": 0.2,
        }

    suggested_min = round((p25 or med) * (1 + feature_boost), 2)
    suggested_max = round((p75 or med) * (1 + feature_boost), 2)
    rationale = [
        f"Comparable median price is {med:.2f}.",
        f"Suggested range anchors to comparable p25/p75: {(p25 or med):.2f} to {(p75 or med):.2f}.",
    ]
    if feature_boost > 0:
        rationale.append(f"Applied a {feature_boost * 100:.0f}% premium for strong listing features.")
    return {
        "subject": comparable_payload["subject"],
        "suggested_min": suggested_min,
        "suggested_max": suggested_max,
        "comparable_median": med,
        "comparables_used": len(comparables),
        "rationale": rationale,
        "warnings": [] if len(comparables) >= 3 else ["Suggestion is based on a small comparable set."],
        "confidence": 0.8 if len(comparables) >= 5 else 0.55,
    }


def generate_listing_draft(
    *,
    location: str,
    price: float | None = None,
    price_text: str | None = None,
    rent_or_sale: str | None = None,
    property_type: str | None = None,
    bedrooms: float | None = None,
    bathrooms: float | None = None,
    features: list[str] | None = None,
    highlights: list[str] | None = None,
    image_count: int = 0,
) -> dict:
    feature_list = normalize_feature_input(features)
    highlight_list = [item.strip() for item in (highlights or []) if item and item.strip()]
    generated_title = _build_title(
        location=location,
        price=price,
        price_text=price_text,
        rent_or_sale=rent_or_sale,
        property_type=property_type,
        bedrooms=bedrooms,
        features=feature_list,
    )
    bullets = _build_highlights(
        location=location,
        price=price,
        price_text=price_text,
        property_type=property_type,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        features=feature_list,
        highlights=highlight_list,
        image_count=image_count,
    )
    description = _build_description(
        generated_title,
        bullets,
        rent_or_sale=rent_or_sale,
        features=feature_list,
    )
    faq = [
        {"question": "Is the property still available?", "answer": "Yes, feel free to message for the latest availability."},
        {"question": "Can I arrange a viewing?", "answer": "Viewings can be coordinated once your preferred timing is shared."},
        {"question": "Are there any extra fees?", "answer": "Please confirm deposits, management fees, and parking costs during inquiry."},
    ]
    return {
        "title": generated_title,
        "highlights": bullets,
        "description": description,
        "faq": faq,
        "posting_checklist": [
            "Confirm exact asking price and payment terms.",
            "Upload clear photos for every major room.",
            "Double-check location, bedroom count, and bathroom count.",
            "Prepare answers for parking, pet policy, and viewing availability.",
        ],
    }


def check_listing_readiness(
    *,
    title: str | None = None,
    description: str | None = None,
    location: str | None = None,
    price: float | None = None,
    bedrooms: float | None = None,
    bathrooms: float | None = None,
    image_count: int = 0,
    contact_methods: list[str] | None = None,
) -> dict:
    score = 100
    missing = []
    warnings = []
    if not title or len(title.strip()) < 12:
        score -= 15
        missing.append("A clear title with at least 12 characters.")
    if not description or len(description.strip()) < 80:
        score -= 20
        missing.append("A fuller description with size, layout, and nearby highlights.")
    if not location:
        score -= 15
        missing.append("A precise area or address reference.")
    if price is None:
        score -= 20
        missing.append("An asking price.")
    if bedrooms is None:
        score -= 10
        warnings.append("Bedroom count is missing.")
    if bathrooms is None:
        score -= 5
        warnings.append("Bathroom count is missing.")
    if image_count < 5:
        score -= 10
        warnings.append("Five or more images are recommended for stronger conversion.")
    if not contact_methods:
        score -= 10
        missing.append("At least one contact method.")
    return {
        "readiness_score": max(score, 0),
        "ready_to_post": score >= 75 and not missing,
        "missing_items": missing,
        "warnings": warnings,
    }


def generate_reply_templates(
    *,
    listing_title: str,
    location: str,
    price_text: str | None = None,
    contact_name: str = "there",
) -> dict:
    price_fragment = f" The current asking price is {price_text}." if price_text else ""
    return {
        "availability_reply": (
            f"Hi {contact_name}, thanks for your interest in {listing_title} in {location}."
            f"{price_fragment} Let me know your preferred viewing time and I can share the latest availability."
        ),
        "viewing_reply": (
            f"Hi {contact_name}, thanks for reaching out. Viewings for {listing_title} can be arranged in {location}."
            " Please share two or three time windows that work for you."
        ),
        "negotiation_reply": (
            f"Hi {contact_name}, thank you for the offer on {listing_title}. I will review it carefully and"
            " get back to you after checking current market comparables and viewing interest."
        ),
        "documents_reply": (
            f"Hi {contact_name}, before we move forward on {listing_title}, please share the documents normally"
            " required for verification in your market, and I will confirm the next steps."
        ),
    }


def _build_title(
    *,
    location: str,
    price: float | None,
    price_text: str | None,
    rent_or_sale: str | None,
    property_type: str | None,
    bedrooms: float | None,
    features: list[str],
) -> str:
    bedrooms_text = ""
    if bedrooms is not None:
        bedrooms_text = "Studio" if bedrooms == 0 else f"{bedrooms:g}BR"
    type_text = property_type.title() if property_type else "Property"
    mode_text = "for Rent" if rent_or_sale == "rent" else "for Sale" if rent_or_sale == "sale" else ""
    headline = " • ".join(part for part in [bedrooms_text, type_text, location, mode_text] if part)
    if "furnished" in features:
        headline = f"{headline} • Furnished"
    if price_text:
        headline = f"{headline} • {price_text}"
    elif price is not None:
        headline = f"{headline} • {price:.0f}"
    return headline


def _build_highlights(
    *,
    location: str,
    price: float | None,
    price_text: str | None,
    property_type: str | None,
    bedrooms: float | None,
    bathrooms: float | None,
    features: list[str],
    highlights: list[str],
    image_count: int,
) -> list[str]:
    bullets = []
    if property_type:
        bullets.append(f"{property_type.title()} located in {location}.")
    else:
        bullets.append(f"Property located in {location}.")
    if bedrooms is not None:
        bullets.append("Studio layout." if bedrooms == 0 else f"{bedrooms:g} bedroom layout.")
    if bathrooms is not None:
        bullets.append(f"{bathrooms:g} bathroom(s).")
    if price_text:
        bullets.append(f"Asking price: {price_text}.")
    elif price is not None:
        bullets.append(f"Asking price: {price:.0f}.")
    if features:
        bullets.append("Key features: " + ", ".join(features) + ".")
    bullets.extend(highlights)
    bullets.append(f"Current image count: {image_count}.")
    return bullets


def _build_description(
    title: str,
    bullets: list[str],
    *,
    rent_or_sale: str | None,
    features: list[str],
) -> str:
    mode_text = "renters" if rent_or_sale == "rent" else "buyers" if rent_or_sale == "sale" else "interested viewers"
    opening = f"{title}. Suitable for {mode_text} looking for a clear, practical option."
    body = " ".join(bullets)
    closing = "Please reach out for viewing arrangements, extra photos, and fee confirmation."
    if "pet friendly" in features:
        closing = "Pet-friendly inquiries are welcome. " + closing
    return " ".join([opening, body, closing])

