from __future__ import annotations

from pathlib import Path
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.sh"


class BootstrapContractTests(unittest.TestCase):
    def test_hermes_binary_is_resolved_before_first_use(self) -> None:
        source = BOOTSTRAP.read_text(encoding="utf-8")
        assignment = source.index("hermes_bin=${HERMES_BIN:-$hermes_user_home/.local/bin/hermes}")
        first_use = source.index("[[ -x $hermes_bin ]]")
        self.assertLess(assignment, first_use)


if __name__ == "__main__":
    unittest.main()
