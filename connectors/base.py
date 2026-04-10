from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from models import Capability, SourceListing


class ConnectorError(RuntimeError):
    pass


class UnsupportedCapabilityError(ConnectorError):
    def __init__(self, connector_name: str, capability: str):
        super().__init__(f"{connector_name} does not support capability: {capability}")
        self.connector_name = connector_name
        self.capability = capability


class BasePropertyConnector(ABC):
    name = "base"
    capabilities: frozenset[Capability] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def require(self, capability: Capability) -> None:
        if not self.supports(capability):
            raise UnsupportedCapabilityError(self.name, capability.value)

    @abstractmethod
    def search_property(
        self,
        *,
        keyword: str,
        country: str,
        city: str,
        lang: str,
        max_results: int,
    ) -> list[SourceListing]:
        raise NotImplementedError

    @abstractmethod
    def browse_property(
        self,
        *,
        country: str,
        city: str,
        lang: str,
        max_results: int,
    ) -> list[SourceListing]:
        raise NotImplementedError

    @abstractmethod
    def get_listing_detail(self, *, url: str) -> SourceListing:
        raise NotImplementedError

    def build_publish_payload(self, **_: object) -> dict:
        raise UnsupportedCapabilityError(self.name, Capability.PUBLISH_DRAFT.value)

    def submit_listing(self, **_: object) -> dict:
        raise UnsupportedCapabilityError(self.name, Capability.PUBLISH_SUBMIT.value)

    def list_leads(self, **_: object) -> list[dict]:
        raise UnsupportedCapabilityError(self.name, Capability.LEAD_INBOX.value)

    def reply_lead(self, **_: object) -> dict:
        raise UnsupportedCapabilityError(self.name, Capability.LEAD_REPLY.value)

