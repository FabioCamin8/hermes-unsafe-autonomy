from __future__ import annotations

from pathlib import Path
import unittest


HEALTH = Path(__file__).resolve().parents[1] / "scripts" / "health.sh"


class HealthContractTests(unittest.TestCase):
    def test_codex_probe_uses_bounded_retry(self) -> None:
        source = HEALTH.read_text(encoding="utf-8")
        self.assertIn("probe_with_retry", source)
        self.assertIn("for attempt in 1 2 3; do", source)
        self.assertIn("(( attempt < 3 )) && sleep 1", source)


if __name__ == "__main__":
    unittest.main()
