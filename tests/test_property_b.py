from __future__ import annotations

import unittest

from models import SourceListing
from workflows.property_b import (
    check_listing_readiness,
    generate_listing_draft,
    suggest_listing_price,
    summarize_area_price,
)


class FakeConnector:
    def search_property(self, **_kwargs):
        return [
            SourceListing(provider="ok", title="2BR Apartment", price_text="SGD 3000/month", location_text="Bedok, Singapore", url="u1"),
            SourceListing(provider="ok", title="2BR Apartment", price_text="SGD 3200/month", location_text="Bedok, Singapore", url="u2"),
            SourceListing(provider="ok", title="2BR Apartment", price_text="SGD 3400/month", location_text="Bedok, Singapore", url="u3"),
        ]

    def browse_property(self, **_kwargs):
        return self.search_property()

    def get_listing_detail(self, *, url: str):
        mapping = {
            "u1": ("2BR Furnished Apartment", "SGD 3000/month"),
            "u2": ("2BR Apartment with Parking", "SGD 3200/month"),
            "u3": ("2BR Apartment near MRT", "SGD 3400/month"),
        }
        title, price = mapping[url]
        return SourceListing(
            provider="ok",
            title=title,
            price_text=price,
            location_text="Bedok, Singapore",
            url=url,
            description=f"{title}. 2 bedroom apartment with 2 bathrooms.",
            image_urls=["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"],
            raw={"lat": 1.323, "lng": 103.93},
        )


class PropertyBWorkflowTests(unittest.TestCase):
    def test_summarize_area_price_returns_median(self) -> None:
        payload = summarize_area_price(FakeConnector(), area="Bedok", property_type="apartment", rent_or_sale="rent")
        self.assertEqual(payload["summary"]["sample_size"], 3)
        self.assertEqual(payload["summary"]["median"], 3200.0)

    def test_suggest_listing_price_uses_comparables(self) -> None:
        payload = suggest_listing_price(FakeConnector(), url="u1", features=["furnished"])
        self.assertGreater(payload["suggested_max"], payload["suggested_min"])
        self.assertGreater(payload["comparables_used"], 0)

    def test_generate_listing_draft_and_readiness(self) -> None:
        draft = generate_listing_draft(
            location="Bedok, Singapore",
            price=3200,
            rent_or_sale="rent",
            property_type="apartment",
            bedrooms=2,
            bathrooms=2,
            features=["furnished", "parking"],
            image_count=8,
        )
        readiness = check_listing_readiness(
            title=draft["title"],
            description=draft["description"],
            location="Bedok, Singapore",
            price=3200,
            bedrooms=2,
            bathrooms=2,
            image_count=8,
            contact_methods=["whatsapp"],
        )
        self.assertTrue(readiness["ready_to_post"])


if __name__ == "__main__":
    unittest.main()

