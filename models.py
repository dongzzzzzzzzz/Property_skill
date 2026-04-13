from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Capability(str, Enum):
    SEARCH_PROPERTY = "search_property"
    BROWSE_PROPERTY = "browse_property"
    GET_LISTING_DETAIL = "get_listing_detail"
    PUBLISH_DRAFT = "publish_draft"
    PUBLISH_SUBMIT = "publish_submit"
    LEAD_INBOX = "lead_inbox"
    LEAD_REPLY = "lead_reply"


@dataclass
class SourceListing:
    provider: str
    title: str
    price_text: str | None = None
    location_text: str | None = None
    url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    listing_id: str | None = None
    description: str | None = None
    seller_name: str | None = None
    posted_time: str | None = None
    category: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedListing:
    provider: str
    provider_listing_id: str
    canonical_id: str
    url: str | None
    title: str
    price_text: str | None
    price_value: float | None
    currency: str | None
    rent_or_sale: str | None
    location_text: str | None
    area_name: str | None
    lat: float | None
    lng: float | None
    beds: float | None
    baths: float | None
    property_type: str | None
    price_period: str = "unknown"
    monthly_price_value: float | None = None
    price_is_estimated_monthly: bool = False
    metro_area: str | None = None
    sub_area: str | None = None
    borough: str | None = None
    area_size_value: float | None = None
    area_size_unit: str | None = None
    features: list[str] = field(default_factory=list)
    description: str | None = None
    image_urls: list[str] = field(default_factory=list)
    posted_time: str | None = None
    seller_name: str | None = None
    parse_confidence: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    location_relevance: dict[str, Any] = field(default_factory=dict)
    price_anomaly: dict[str, Any] = field(default_factory=dict)
    price_analysis: dict[str, Any] = field(default_factory=dict)
    image_quality: str = "missing"
    field_status: dict[str, str] = field(default_factory=dict)
    field_sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    known_fields: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConnectorResult:
    listings: list[SourceListing]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "listings": [listing.to_dict() for listing in self.listings],
            "warnings": list(self.warnings),
        }
