from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from connectors.ok_connector import OKConnector


class OKConnectorTests(unittest.TestCase):
    def test_resolves_nested_ok_skill_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            nested_cli = repo_root / "skills" / "ok-core-skill" / "scripts" / "cli.py"
            nested_cli.parent.mkdir(parents=True)
            nested_cli.write_text("#!/usr/bin/env python3\n")
            connector = OKConnector(root_dir=repo_root, runner=self._fake_runner({}))
            self.assertEqual(connector.skill_root, repo_root / "skills" / "ok-core-skill")

    def test_search_property_invokes_ok_cli(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            payload = {
                "listings": [
                    {
                        "title": "2BR Condo",
                        "price": "SGD 3200/month",
                        "location": "Bedok, Singapore",
                        "url": "https://example.com/1",
                    }
                ]
            }
            return self._completed(payload)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "cli.py").write_text("#!/usr/bin/env python3\n")
            connector = OKConnector(root_dir=root, runner=runner)
            listings = connector.search_property(
                keyword="apartment",
                country="singapore",
                city="singapore",
                lang="en",
                max_results=5,
            )

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].title, "2BR Condo")
        self.assertIn("search", calls[0][0])
        self.assertIn("--keyword", calls[0][0])

    def _fake_runner(self, payload):
        def runner(*_args, **_kwargs):
            return self._completed(payload)

        return runner

    @staticmethod
    def _completed(payload):
        return type(
            "CompletedProcess",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            },
        )()


if __name__ == "__main__":
    unittest.main()

