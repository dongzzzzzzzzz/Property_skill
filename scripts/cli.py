#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors import get_connector
from connectors.base import ConnectorError, UnsupportedCapabilityError
from workflows import (
    check_listing_readiness,
    compare_properties,
    estimate_total_cost,
    find_comparables,
    find_nearby_schools,
    generate_listing_draft,
    generate_reply_templates,
    score_value,
    search_properties,
    suggest_listing_price,
    summarize_area_price,
)


def _emit(payload: dict, exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(exit_code)


def _error(code: str, message: str, *, capability: str | None = None, retriable: bool = False) -> None:
    payload = {
        "error": {
            "code": code,
            "message": message,
            "retriable": retriable,
            "capability": capability,
        }
    }
    _emit(payload, exit_code=2)


def _add_connector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", default="ok")


def _add_locale_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--country", default="singapore")
    parser.add_argument("--city", default="singapore")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--max-results", type=int, default=10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Property vertical skills CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("search_properties")
    _add_connector_args(p)
    _add_locale_args(p)
    p.add_argument("--keyword", default="")
    p.add_argument("--detail-limit", type=int, default=10)
    p.add_argument("--budget-min", type=float)
    p.add_argument("--budget-max", type=float)
    p.add_argument("--bedrooms", type=float)
    p.add_argument("--property-type")
    p.add_argument("--rent-or-sale", choices=["rent", "sale"])
    p.add_argument("--area")
    p.add_argument("--feature", action="append", default=[])
    p.add_argument("--near")
    p.add_argument("--near-lat", type=float)
    p.add_argument("--near-lng", type=float)
    p.add_argument("--radius-km", type=float)
    p.add_argument("--nyc-area-mode", choices=["core", "include_jersey", "any"], default="core")
    p.add_argument("--exclude-suspicious-low", action="store_true")

    p = subparsers.add_parser("compare_properties")
    _add_connector_args(p)
    p.add_argument("--url", action="append", required=True)

    p = subparsers.add_parser("estimate_total_cost")
    p.add_argument("--price", type=float, required=True)
    p.add_argument("--rent-or-sale", required=True, choices=["rent", "sale"])
    p.add_argument("--deposit-months", type=float, default=2.0)
    p.add_argument("--parking-monthly", type=float, default=0.0)
    p.add_argument("--commute-cost-per-trip", type=float, default=0.0)
    p.add_argument("--commute-days-per-month", type=int, default=20)
    p.add_argument("--down-payment-ratio", type=float, default=0.2)
    p.add_argument("--annual-interest-rate", type=float, default=0.04)
    p.add_argument("--mortgage-years", type=int, default=30)

    p = subparsers.add_parser("score_value")
    _add_connector_args(p)
    p.add_argument("--url", required=True)
    p.add_argument("--comparable-url", action="append", default=[])

    p = subparsers.add_parser("find_nearby_schools")
    _add_connector_args(p)
    p.add_argument("--url")
    p.add_argument("--location")
    p.add_argument("--lat", type=float)
    p.add_argument("--lng", type=float)
    p.add_argument("--radius-m", type=int, default=1500)

    p = subparsers.add_parser("summarize_area_price")
    _add_connector_args(p)
    _add_locale_args(p)
    p.add_argument("--keyword")
    p.add_argument("--area")
    p.add_argument("--property-type")
    p.add_argument("--rent-or-sale", choices=["rent", "sale"])
    p.add_argument("--bedrooms", type=float)

    p = subparsers.add_parser("find_comparables")
    _add_connector_args(p)
    _add_locale_args(p)
    p.add_argument("--url")
    p.add_argument("--keyword")
    p.add_argument("--area")
    p.add_argument("--property-type")
    p.add_argument("--rent-or-sale", choices=["rent", "sale"])
    p.add_argument("--bedrooms", type=float)

    p = subparsers.add_parser("suggest_listing_price")
    _add_connector_args(p)
    _add_locale_args(p)
    p.add_argument("--url")
    p.add_argument("--keyword")
    p.add_argument("--area")
    p.add_argument("--property-type")
    p.add_argument("--rent-or-sale", choices=["rent", "sale"])
    p.add_argument("--bedrooms", type=float)
    p.add_argument("--feature", action="append", default=[])

    p = subparsers.add_parser("generate_listing_draft")
    p.add_argument("--location", required=True)
    p.add_argument("--price", type=float)
    p.add_argument("--price-text")
    p.add_argument("--rent-or-sale", choices=["rent", "sale"])
    p.add_argument("--property-type")
    p.add_argument("--bedrooms", type=float)
    p.add_argument("--bathrooms", type=float)
    p.add_argument("--feature", action="append", default=[])
    p.add_argument("--highlight", action="append", default=[])
    p.add_argument("--image-count", type=int, default=0)

    p = subparsers.add_parser("check_listing_readiness")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--location")
    p.add_argument("--price", type=float)
    p.add_argument("--bedrooms", type=float)
    p.add_argument("--bathrooms", type=float)
    p.add_argument("--image-count", type=int, default=0)
    p.add_argument("--contact-method", action="append", default=[])

    p = subparsers.add_parser("generate_reply_templates")
    p.add_argument("--listing-title", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--price-text")
    p.add_argument("--contact-name", default="there")

    args = parser.parse_args()

    try:
        if args.command == "search_properties":
            connector = get_connector(args.provider)
            payload = search_properties(
                connector,
                keyword=args.keyword,
                country=args.country,
                city=args.city,
                lang=args.lang,
                max_results=args.max_results,
                detail_limit=args.detail_limit,
                budget_min=args.budget_min,
                budget_max=args.budget_max,
                bedrooms=args.bedrooms,
                property_type=args.property_type,
                rent_or_sale=args.rent_or_sale,
                area=args.area,
                features=args.feature,
                near=args.near,
                near_lat=args.near_lat,
                near_lng=args.near_lng,
                radius_km=args.radius_km,
                nyc_area_mode=args.nyc_area_mode,
                exclude_suspicious_low=args.exclude_suspicious_low,
            )
            _emit({"provider": args.provider, "input": payload["input"], "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["connector.search_property" if args.keyword else "connector.browse_property", "connector.get_listing_detail", "property_c.search_properties"]})

        if args.command == "compare_properties":
            connector = get_connector(args.provider)
            payload = compare_properties(connector, urls=args.url)
            _emit({"provider": args.provider, "input": {"urls": args.url}, "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["connector.get_listing_detail", "property_c.compare_properties"]})

        if args.command == "estimate_total_cost":
            payload = estimate_total_cost(
                price=args.price,
                rent_or_sale=args.rent_or_sale,
                deposit_months=args.deposit_months,
                parking_monthly=args.parking_monthly,
                commute_cost_per_trip=args.commute_cost_per_trip,
                commute_days_per_month=args.commute_days_per_month,
                down_payment_ratio=args.down_payment_ratio,
                annual_interest_rate=args.annual_interest_rate,
                mortgage_years=args.mortgage_years,
            )
            _emit({"provider": None, "input": vars(args), "data": payload, "warnings": [], "confidence": 0.8, "source_trace": ["property_c.estimate_total_cost"]})

        if args.command == "score_value":
            connector = get_connector(args.provider)
            payload = score_value(connector, url=args.url, comparable_urls=args.comparable_url)
            _emit({"provider": args.provider, "input": {"url": args.url, "comparable_urls": args.comparable_url}, "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["connector.get_listing_detail", "property_c.score_value"]})

        if args.command == "find_nearby_schools":
            connector = get_connector(args.provider) if args.url else None
            payload = find_nearby_schools(
                connector=connector,
                url=args.url,
                location=args.location,
                lat=args.lat,
                lng=args.lng,
                radius_m=args.radius_m,
            )
            _emit({"provider": args.provider if args.url else None, "input": {"url": args.url, "location": args.location, "lat": args.lat, "lng": args.lng, "radius_m": args.radius_m}, "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["property_c.find_nearby_schools"]})

        if args.command == "summarize_area_price":
            connector = get_connector(args.provider)
            payload = summarize_area_price(
                connector,
                keyword=args.keyword,
                country=args.country,
                city=args.city,
                lang=args.lang,
                max_results=args.max_results,
                area=args.area,
                property_type=args.property_type,
                rent_or_sale=args.rent_or_sale,
                bedrooms=args.bedrooms,
            )
            _emit({"provider": args.provider, "input": payload["input"], "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["connector.search_property|browse_property", "connector.get_listing_detail", "property_b.summarize_area_price"]})

        if args.command == "find_comparables":
            connector = get_connector(args.provider)
            payload = find_comparables(
                connector,
                url=args.url,
                keyword=args.keyword,
                country=args.country,
                city=args.city,
                lang=args.lang,
                max_results=args.max_results,
                area=args.area,
                property_type=args.property_type,
                rent_or_sale=args.rent_or_sale,
                bedrooms=args.bedrooms,
            )
            _emit({"provider": args.provider, "input": {"url": args.url, "keyword": args.keyword, "area": args.area, "property_type": args.property_type, "rent_or_sale": args.rent_or_sale, "bedrooms": args.bedrooms}, "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["connector.get_listing_detail", "connector.search_property|browse_property", "property_b.find_comparables"]})

        if args.command == "suggest_listing_price":
            connector = get_connector(args.provider)
            payload = suggest_listing_price(
                connector,
                url=args.url,
                keyword=args.keyword,
                country=args.country,
                city=args.city,
                lang=args.lang,
                max_results=args.max_results,
                area=args.area,
                property_type=args.property_type,
                rent_or_sale=args.rent_or_sale,
                bedrooms=args.bedrooms,
                features=args.feature,
            )
            _emit({"provider": args.provider, "input": {"url": args.url, "keyword": args.keyword, "area": args.area, "property_type": args.property_type, "rent_or_sale": args.rent_or_sale, "bedrooms": args.bedrooms, "features": args.feature}, "data": payload, "warnings": payload["warnings"], "confidence": payload["confidence"], "source_trace": ["connector.get_listing_detail", "connector.search_property|browse_property", "property_b.suggest_listing_price"]})

        if args.command == "generate_listing_draft":
            payload = generate_listing_draft(
                location=args.location,
                price=args.price,
                price_text=args.price_text,
                rent_or_sale=args.rent_or_sale,
                property_type=args.property_type,
                bedrooms=args.bedrooms,
                bathrooms=args.bathrooms,
                features=args.feature,
                highlights=args.highlight,
                image_count=args.image_count,
            )
            _emit({"provider": None, "input": vars(args), "data": payload, "warnings": [], "confidence": 0.8, "source_trace": ["property_b.generate_listing_draft"]})

        if args.command == "check_listing_readiness":
            payload = check_listing_readiness(
                title=args.title,
                description=args.description,
                location=args.location,
                price=args.price,
                bedrooms=args.bedrooms,
                bathrooms=args.bathrooms,
                image_count=args.image_count,
                contact_methods=args.contact_method,
            )
            _emit({"provider": None, "input": vars(args), "data": payload, "warnings": payload["warnings"], "confidence": 0.75, "source_trace": ["property_b.check_listing_readiness"]})

        if args.command == "generate_reply_templates":
            payload = generate_reply_templates(
                listing_title=args.listing_title,
                location=args.location,
                price_text=args.price_text,
                contact_name=args.contact_name,
            )
            _emit({"provider": None, "input": vars(args), "data": payload, "warnings": [], "confidence": 0.75, "source_trace": ["property_b.generate_reply_templates"]})

    except UnsupportedCapabilityError as exc:
        _error("unsupported_capability", str(exc), capability=exc.capability)
    except ConnectorError as exc:
        _error("connector_error", str(exc), retriable=True)
    except Exception as exc:  # pragma: no cover - top-level guard
        _error("unexpected_error", str(exc), retriable=False)


if __name__ == "__main__":
    main()
