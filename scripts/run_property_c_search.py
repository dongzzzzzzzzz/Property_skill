#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors import get_connector
from workflows.property_c import search_properties


def main() -> None:
    parser = argparse.ArgumentParser(description="Low-freedom wrapper for Property C search")
    parser.add_argument("--provider", default="ok")
    parser.add_argument("--country", default="singapore")
    parser.add_argument("--city", default="singapore")
    parser.add_argument("--lang", default="en")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--budget-min", type=float)
    parser.add_argument("--budget-max", type=float)
    parser.add_argument("--bedrooms", type=float)
    parser.add_argument("--property-type")
    parser.add_argument("--rent-or-sale", choices=["rent", "sale"])
    parser.add_argument("--area")
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--near")
    parser.add_argument("--near-lat", type=float)
    parser.add_argument("--near-lng", type=float)
    parser.add_argument("--radius-km", type=float)
    parser.add_argument("--nyc-area-mode", choices=["core", "include_jersey", "any"], default="core")
    parser.add_argument("--exclude-suspicious-low", action="store_true")
    parser.add_argument("--max-results", type=int, default=100)

    args = parser.parse_args()
    connector = get_connector(args.provider)
    payload = search_properties(
        connector,
        keyword=args.keyword,
        country=args.country,
        city=args.city,
        lang=args.lang,
        max_results=args.max_results,
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
    print(
        json.dumps(
            {
                "provider": args.provider,
                "input": payload["input"],
                "data": payload,
                "warnings": payload["warnings"],
                "confidence": payload["confidence"],
                "source_trace": ["property_c_wrapper.search_properties", "connector.search_property|browse_property", "connector.get_listing_detail"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
