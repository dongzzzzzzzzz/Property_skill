from __future__ import annotations

import unittest

from models import SourceListing
from helpers import area_match_level, classify_nyc_area, infer_rent_or_sale, parse_price
from workflows.property_c import compare_properties, estimate_total_cost, search_properties


class FakeConnector:
    def search_property(self, **_kwargs):
        return [
            SourceListing(provider="ok", title="2BR Furnished Apartment", price_text="SGD 3200/month", location_text="Bedok, Singapore", url="u1"),
            SourceListing(provider="ok", title="1BR Apartment", price_text="SGD 2200/month", location_text="Bedok, Singapore", url="u2"),
        ]

    def browse_property(self, **_kwargs):
        return self.search_property()

    def get_listing_detail(self, *, url: str):
        if url == "u1":
            return SourceListing(
                provider="ok",
                title="2BR Furnished Apartment near MRT",
                price_text="SGD 3200/month",
                location_text="Bedok, Singapore",
                url=url,
                description="2 bedroom apartment, furnished, parking, near MRT.",
                raw={"lat": 1.323, "lng": 103.93},
                image_urls=["a.jpg", "b.jpg"],
            )
        return SourceListing(
            provider="ok",
            title="1BR Apartment",
            price_text="SGD 2200/month",
            location_text="Bedok, Singapore",
            url=url,
            description="1 bedroom apartment.",
            raw={"lat": 1.333, "lng": 103.91},
            image_urls=["a.jpg"],
        )


class NYCConnector:
    def search_property(self, **_kwargs):
        return [
            SourceListing(provider="ok", title="Short-term studio apartment", price_text="$100 Daily", location_text="36 Journal Square Plaza, Jersey City, NJ", url="ny1"),
            SourceListing(provider="ok", title="2BR Apartment in Manhattan", price_text="$3500 Monthly", location_text="Upper West Side, Manhattan, New York, NY", url="ny2"),
            SourceListing(provider="ok", title="1BR Apartment in Long Island City", price_text="$3000 Monthly", location_text="Long Island City, Queens, New York, NY", url="ny3"),
        ]

    def browse_property(self, **_kwargs):
        return self.search_property()

    def get_listing_detail(self, *, url: str):
        mapping = {
            "ny1": SourceListing(
                provider="ok",
                title="Short-term studio apartment",
                price_text="$100 Daily",
                location_text="36 Journal Square Plaza, Jersey City, NJ",
                url=url,
                description="Short-term 5 days sublet in Jersey City near PATH.",
                image_urls=["a.jpg", "b.jpg"],
            ),
            "ny2": SourceListing(
                provider="ok",
                title="2BR Apartment in Manhattan",
                price_text="$3500 Monthly",
                location_text="Upper West Side, Manhattan, New York, NY",
                url=url,
                description="2 bedroom apartment in Manhattan with gym and furnished rooms.",
                image_urls=["a.jpg", "b.jpg", "c.jpg"],
            ),
            "ny3": SourceListing(
                provider="ok",
                title="1BR Apartment in Long Island City",
                price_text="$3000 Monthly",
                location_text="Long Island City, Queens, New York, NY",
                url=url,
                description="1 bedroom apartment in LIC, close to subway.",
                image_urls=["a.jpg", "b.jpg", "c.jpg"],
            ),
        }
        return mapping[url]


class NoisyNYCSaleConnector:
    def search_property(self, **kwargs):
        keyword = (kwargs.get("keyword") or "").lower()
        if "3 bedroom apartment for sale" in keyword:
            return [
                SourceListing(provider="ok", title="Brooklyn 3BR Apartment For Sale", price_text="$2200000", location_text="Brooklyn, New York, NY", url="sale2"),
                SourceListing(provider="ok", title="Pasadena 3BR Home", price_text="$1800000", location_text="Pasadena, CA", url="sale3"),
            ]
        if "3br home for sale" in keyword:
            return [
                SourceListing(provider="ok", title="Jersey City 3BR Home For Sale", price_text="$1400000", location_text="Jersey City, NJ", url="sale4"),
            ]
        return [
            SourceListing(provider="ok", title="Irvine 3BR House For Sale", price_text="$2500000", location_text="Irvine, CA", url="sale5"),
            SourceListing(provider="ok", title="Manhattan 3BR House For Sale", price_text="$4500000", location_text="Manhattan, New York, NY", url="sale1"),
            SourceListing(provider="ok", title="Santa Ana 2BR Condo For Sale", price_text="$900000", location_text="Santa Ana, CA", url="sale6"),
        ]

    def browse_property(self, **kwargs):
        return self.search_property(**kwargs)

    def get_listing_detail(self, *, url: str):
        mapping = {
            "sale1": SourceListing(
                provider="ok",
                title="Manhattan 3BR House For Sale",
                price_text="$4500000",
                location_text="Upper West Side, Manhattan, New York, NY",
                url=url,
                description="3 bedroom house for sale in Manhattan with detailed photos.",
                image_urls=["1.jpg", "2.jpg", "3.jpg"],
            ),
            "sale2": SourceListing(
                provider="ok",
                title="Brooklyn 3BR Apartment For Sale",
                price_text="$2200000",
                location_text="Park Slope, Brooklyn, New York, NY",
                url=url,
                description="3 bedroom apartment for sale in Brooklyn with full description.",
                image_urls=["1.jpg", "2.jpg", "3.jpg"],
            ),
            "sale3": SourceListing(
                provider="ok",
                title="Pasadena 3BR Home",
                price_text="$1800000",
                location_text="Pasadena, CA",
                url=url,
                description="3 bedroom home in California.",
                image_urls=["1.jpg"],
            ),
            "sale4": SourceListing(
                provider="ok",
                title="Jersey City 3BR Home For Sale",
                price_text="$1400000",
                location_text="Jersey City, NJ",
                url=url,
                description="3 bedroom home for sale in Jersey City.",
                image_urls=["1.jpg", "2.jpg"],
            ),
            "sale5": SourceListing(
                provider="ok",
                title="Irvine 3BR House For Sale",
                price_text="$2500000",
                location_text="Irvine, CA",
                url=url,
                description="3 bedroom house for sale in Irvine.",
                image_urls=["1.jpg"],
            ),
            "sale6": SourceListing(
                provider="ok",
                title="Santa Ana 2BR Condo For Sale",
                price_text="$900000",
                location_text="Santa Ana, CA",
                url=url,
                description="2 bedroom condo for sale in Santa Ana.",
                image_urls=["1.jpg"],
            ),
        }
        return mapping[url]


class SkewedNYCRentalConnector:
    def search_property(self, **_kwargs):
        return [
            SourceListing(provider="ok", title="LIC Studio in Jackson Park", price_text="$2900 Monthly", location_text="Long Island City, Queens, New York, NY", url="rent1"),
            SourceListing(provider="ok", title="LIC Studio with Gym", price_text="$3000 Monthly", location_text="Long Island City, Queens, New York, NY", url="rent2"),
            SourceListing(provider="ok", title="LIC High Floor 1BR", price_text="$3500 Monthly", location_text="Long Island City, Queens, New York, NY", url="rent3"),
            SourceListing(provider="ok", title="Elmhurst Furnished 4BR", price_text="$3520 Monthly", location_text="Elmhurst, Queens, New York, NY", url="rent4"),
            SourceListing(provider="ok", title="Jersey City Short-term Room", price_text="$100 Daily", location_text="Jersey City, NJ", url="rent5"),
        ]

    def browse_property(self, **_kwargs):
        return self.search_property()

    def get_listing_detail(self, *, url: str):
        mapping = {
            "rent1": SourceListing(
                provider="ok",
                title="LIC Studio in Jackson Park",
                price_text="$2900 Monthly",
                location_text="Long Island City, Queens, New York, NY",
                url=url,
                description="Studio apartment in Long Island City near subway and gym.",
                image_urls=["1.jpg", "2.jpg"],
            ),
            "rent2": SourceListing(
                provider="ok",
                title="LIC Studio with Gym",
                price_text="$3000 Monthly",
                location_text="Long Island City, Queens, New York, NY",
                url=url,
                description="Studio apartment with laundry and gym in Long Island City.",
                image_urls=["1.jpg", "2.jpg"],
            ),
            "rent3": SourceListing(
                provider="ok",
                title="LIC High Floor 1BR",
                price_text="$3500 Monthly",
                location_text="Long Island City, Queens, New York, NY",
                url=url,
                description="1 bedroom high floor apartment in Long Island City with cinema and terrace.",
                image_urls=["1.jpg", "2.jpg", "3.jpg"],
            ),
            "rent4": SourceListing(
                provider="ok",
                title="Elmhurst Furnished 4BR",
                price_text="$3520 Monthly",
                location_text="Elmhurst, Queens, New York, NY",
                url=url,
                description="Furnished 4 bedroom apartment near subway in Elmhurst.",
                image_urls=["1.jpg", "2.jpg", "3.jpg"],
            ),
            "rent5": SourceListing(
                provider="ok",
                title="Jersey City Short-term Room",
                price_text="$100 Daily",
                location_text="Jersey City, NJ",
                url=url,
                description="Short-term room in Jersey City near PATH.",
                image_urls=["1.jpg"],
            ),
        }
        return mapping[url]


class PropertyCWorkflowTests(unittest.TestCase):
    def test_price_period_normalization(self) -> None:
        value, currency, period, monthly, estimated = parse_price("$100 Daily", "short-term apartment")
        self.assertEqual(value, 100.0)
        self.assertEqual(currency, "$")
        self.assertEqual(period, "daily")
        self.assertEqual(monthly, 3000.0)
        self.assertTrue(estimated)

    def test_monthly_keyword_implies_rent_intent(self) -> None:
        self.assertEqual(infer_rent_or_sale("LIC Studio", "Available monthly with gym"), "rent")

    def test_search_filters_by_budget_bedrooms_and_features(self) -> None:
        payload = search_properties(
            FakeConnector(),
            keyword="apartment",
            budget_max=3300,
            bedrooms=2,
            features=["furnished"],
            near_lat=1.323,
            near_lng=103.93,
            radius_km=3,
        )
        self.assertEqual(payload["summary"]["matched_results"], 1)
        self.assertEqual(payload["listings"][0]["beds"], 2.0)
        self.assertIn("furnished", payload["listings"][0]["features"])
        self.assertEqual(payload["decision_mode"], "watchlist")

    def test_compare_properties_recommends_best_listing(self) -> None:
        payload = compare_properties(FakeConnector(), urls=["u1", "u2"])
        self.assertEqual(payload["recommended_listing_id"], payload["comparison"][0]["canonical_id"])
        self.assertEqual(len(payload["comparison"]), 2)

    def test_estimate_total_cost_for_rent(self) -> None:
        payload = estimate_total_cost(
            price=3200,
            rent_or_sale="rent",
            deposit_months=2,
            parking_monthly=100,
            commute_cost_per_trip=3,
            commute_days_per_month=20,
        )
        self.assertEqual(payload["deposit"], 6400)
        self.assertGreater(payload["estimated_monthly_total"], 3200)

    def test_classify_nyc_areas(self) -> None:
        self.assertEqual(classify_nyc_area("Apartment", "Long Island City, Queens, New York", None)[1], "long island city")
        self.assertEqual(classify_nyc_area("Apartment", "Brooklyn, New York", None)[2], "brooklyn")
        self.assertEqual(classify_nyc_area("Apartment", "Jersey City, NJ", None)[1], "jersey city")

    def test_detect_price_anomaly_for_short_term_low_price(self) -> None:
        normalized_payload = search_properties(
            NYCConnector(),
            keyword="apartment",
            country="usa",
            city="new-york",
            max_results=3,
            detail_limit=3,
            nyc_area_mode="core",
        )
        first = next(item for item in normalized_payload["listings"] if item["url"] == "ny1")
        self.assertTrue(first["price_anomaly"]["is_suspicious_low"])

    def test_new_york_search_demotes_jersey_city_and_keeps_core_nyc_first(self) -> None:
        payload = search_properties(
            NYCConnector(),
            keyword="apartment",
            country="usa",
            city="new-york",
            budget_max=10000,
            max_results=3,
            detail_limit=3,
            nyc_area_mode="core",
        )
        self.assertNotEqual(payload["listings"][0]["sub_area"], "jersey city")
        jersey = next(item for item in payload["listings"] if item["sub_area"] == "jersey city")
        self.assertLess(jersey["scores"]["total_score"], payload["listings"][0]["scores"]["total_score"])
        self.assertEqual(jersey["price_period"], "daily")
        self.assertEqual(jersey["monthly_price_value"], 3000.0)

    def test_area_specific_search_prefers_matching_borough(self) -> None:
        payload = search_properties(
            NYCConnector(),
            keyword="apartment",
            country="usa",
            city="new-york",
            area="queens",
            max_results=3,
            detail_limit=3,
        )
        self.assertEqual(payload["listings"][0]["borough"], "queens")
        self.assertEqual(payload["listings"][0]["sub_area"], "long island city")
        self.assertEqual(area_match_level(type("Obj", (), payload["listings"][0])(), "queens", city="new-york", keyword="apartment"), "borough")

    def test_exclude_suspicious_low_removes_outlier(self) -> None:
        payload = search_properties(
            NYCConnector(),
            keyword="apartment",
            country="usa",
            city="new-york",
            max_results=3,
            detail_limit=3,
            exclude_suspicious_low=True,
        )
        self.assertEqual(payload["summary"]["excluded_outlier_count"], 1)
        self.assertTrue(all(not item["price_anomaly"]["is_suspicious_low"] for item in payload["listings"]))

    def test_search_builds_user_facing_recommendations(self) -> None:
        payload = search_properties(
            NoisyNYCSaleConnector(),
            keyword="3 bedroom house for sale",
            country="usa",
            city="new-york",
            bedrooms=3,
            property_type="house",
            rent_or_sale="sale",
            max_results=5,
            detail_limit=5,
        )
        self.assertEqual(payload["result_quality"]["level"], "medium")
        self.assertEqual(payload["strict_match_count"], 1)
        self.assertEqual(payload["decision_mode"], "watchlist")
        self.assertEqual(payload["recommended_listings"], [])
        self.assertEqual(len(payload["watchlist_candidates"]), 1)
        self.assertIn("可参考观察项", payload["user_facing_response"])
        self.assertIn("为什么这次不直接推荐", payload["user_facing_response"])
        self.assertIn("继续观察", payload["watchlist_candidates"][0]["decision_reason"])
        self.assertIn("查看详情", payload["user_facing_response"])
        self.assertTrue(all("CA" not in (item["location_text"] or "") for item in payload["watchlist_candidates"]))

    def test_low_quality_results_do_not_force_recommendations(self) -> None:
        payload = search_properties(
            NoisyNYCSaleConnector(),
            keyword="3 bedroom house for sale",
            country="usa",
            city="new-york",
            bedrooms=5,
            property_type="house",
            rent_or_sale="sale",
            max_results=5,
            detail_limit=5,
        )
        self.assertEqual(payload["result_quality"]["level"], "low")
        self.assertEqual(payload["recommended_listings"], [])
        self.assertEqual(payload["decision_mode"], "explain_only")
        self.assertIn("不能直接拿来选房", payload["result_judgement"])

    def test_clean_non_nyc_search_can_still_recommend(self) -> None:
        payload = search_properties(
            FakeConnector(),
            keyword="apartment",
            country="singapore",
            city="singapore",
            max_results=2,
            detail_limit=2,
        )
        self.assertEqual(payload["decision_mode"], "recommend")
        self.assertGreaterEqual(len(payload["recommended_listings"]), 2)
        self.assertEqual(payload["watchlist_candidates"], [])

    def test_skewed_nyc_results_switch_to_explain_only(self) -> None:
        payload = search_properties(
            SkewedNYCRentalConnector(),
            keyword="apartment",
            country="usa",
            city="new-york",
            rent_or_sale="rent",
            budget_max=10000,
            max_results=5,
            detail_limit=5,
        )
        self.assertEqual(payload["decision_mode"], "explain_only")
        self.assertEqual(payload["recommended_listings"], [])
        self.assertGreaterEqual(len(payload["watchlist_candidates"]), 1)
        self.assertIn("Queens/LIC", payload["result_judgement"])
        self.assertIn("不直接推荐", payload["user_facing_response"])


if __name__ == "__main__":
    unittest.main()
