from __future__ import annotations

import unittest

from scripts.mcp_probe import page_id_candidates


class ProbeTests(unittest.TestCase):
    def test_page_id_candidates_accept_safe_numeric_ids_without_leaking_listing(self) -> None:
        result = {
            "content": [
                {
                    "type": "text",
                    "text": "## Pages\n1: 1234567890123456: private title (https://private.example)\n",
                }
            ]
        }
        self.assertEqual(page_id_candidates(result), [1, 1234567890123456])

    def test_page_id_candidates_reject_numbers_that_cannot_round_trip_as_json(self) -> None:
        result = {
            "content": [
                {
                    "type": "text",
                    "text": "page: 9007199254740992 and page: 42",
                }
            ]
        }
        self.assertEqual(page_id_candidates(result), [42])


if __name__ == "__main__":
    unittest.main()
