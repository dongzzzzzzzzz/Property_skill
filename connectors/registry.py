from __future__ import annotations

import os

from connectors.base import BasePropertyConnector, ConnectorError
from connectors.ok_connector import OKConnector

CONNECTOR_FACTORIES = {
    "ok": OKConnector,
}


def get_connector(name: str | None = None, **kwargs: object) -> BasePropertyConnector:
    connector_name = (name or os.getenv("PROPERTY_PROVIDER_DEFAULT") or "ok").lower()
    factory = CONNECTOR_FACTORIES.get(connector_name)
    if not factory:
        supported = ", ".join(sorted(CONNECTOR_FACTORIES))
        raise ConnectorError(f"Unknown connector '{connector_name}'. Supported: {supported}")
    return factory(**kwargs)


def list_connectors() -> list[str]:
    return sorted(CONNECTOR_FACTORIES)

