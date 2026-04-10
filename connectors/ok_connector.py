from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from connectors.base import BasePropertyConnector, ConnectorError
from models import Capability, SourceListing


class OKConnector(BasePropertyConnector):
    name = "ok"
    capabilities = frozenset(
        {
            Capability.SEARCH_PROPERTY,
            Capability.BROWSE_PROPERTY,
            Capability.GET_LISTING_DETAIL,
        }
    )

    def __init__(
        self,
        root_dir: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        self.skill_root = self._resolve_skill_root(root_dir)
        self._runner = runner or subprocess.run

    @staticmethod
    def _resolve_skill_root(root_dir: str | Path | None) -> Path:
        candidate = Path(
            root_dir
            or os.getenv("PROPERTY_OK_SKILL_ROOT")
            or "/Users/a58/Desktop/ok-core-skill/skills/ok-core-skill"
        ).expanduser()
        direct_cli = candidate / "scripts" / "cli.py"
        nested_cli = candidate / "skills" / "ok-core-skill" / "scripts" / "cli.py"
        if direct_cli.exists():
            return candidate
        if nested_cli.exists():
            return candidate / "skills" / "ok-core-skill"
        raise ConnectorError(f"Unable to locate ok-core-skill CLI from: {candidate}")

    def _run_cli(self, args: list[str]) -> dict:
        command = ["uv", "run", "python", "scripts/cli.py", *args]
        completed = self._runner(
            command,
            cwd=str(self.skill_root),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise ConnectorError(
                f"ok-core-skill command failed: {' '.join(command)}\n"
                f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
            )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ConnectorError(
                f"ok-core-skill returned invalid JSON for {' '.join(command)}: {completed.stdout}"
            ) from exc

    def search_property(
        self,
        *,
        keyword: str,
        country: str = "singapore",
        city: str = "singapore",
        lang: str = "en",
        max_results: int = 10,
    ) -> list[SourceListing]:
        payload = self._run_cli(
            [
                "search",
                "--keyword",
                keyword,
                "--country",
                country,
                "--city",
                city,
                "--lang",
                lang,
                "--max-results",
                str(max_results),
            ]
        )
        return [self._build_source_listing(item) for item in payload.get("listings", [])]

    def browse_property(
        self,
        *,
        country: str = "singapore",
        city: str = "singapore",
        lang: str = "en",
        max_results: int = 10,
    ) -> list[SourceListing]:
        payload = self._run_cli(
            [
                "browse-category",
                "--category",
                "property",
                "--country",
                country,
                "--city",
                city,
                "--lang",
                lang,
                "--max-results",
                str(max_results),
            ]
        )
        return [self._build_source_listing(item) for item in payload.get("listings", [])]

    def get_listing_detail(self, *, url: str) -> SourceListing:
        payload = self._run_cli(["get-listing", "--url", url])
        return self._build_source_listing(payload)

    def _build_source_listing(self, payload: dict) -> SourceListing:
        images = payload.get("images") or []
        if payload.get("image_url"):
            images = [payload["image_url"], *images]
        return SourceListing(
            provider=self.name,
            title=payload.get("title") or "",
            price_text=payload.get("price"),
            location_text=payload.get("location"),
            url=payload.get("url"),
            image_urls=[image for image in images if image],
            listing_id=payload.get("listing_id"),
            description=payload.get("description"),
            seller_name=payload.get("seller_name"),
            posted_time=payload.get("posted_time"),
            category=payload.get("category"),
            raw=dict(payload),
        )

