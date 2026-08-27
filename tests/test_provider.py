from __future__ import annotations

from pathlib import Path
import copy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "plugin"))

from hermes_vault import HermesVaultProvider  # noqa: E402


class ProviderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.provider = HermesVaultProvider()
        self.provider.initialize("session-1", hermes_home=self.tempdir.name, platform="cli")

    def tearDown(self) -> None:
        self.provider.shutdown()
        self.tempdir.cleanup()

    def test_provider_contract_and_automatic_recall(self) -> None:
        self.assertTrue(self.provider.is_available())
        self.assertEqual(self.provider.pre_compress_checkpoint_api_version, 2)
        names = {schema["name"] for schema in self.provider.get_tool_schemas()}
        self.assertEqual(
            names,
            {
                "vault_search", "vault_get", "vault_upsert", "vault_supersede",
                "vault_forget", "vault_checkpoint", "vault_status", "vault_reindex",
            },
        )
        self.provider.handle_tool_call(
            "vault_upsert",
            {
                "kind": "preference",
                "canonical_key": "operator.preference",
                "title": "Operator preference",
                "content": "Prefer concise validation reports.",
                "source_type": "user_explicit",
                "trust": "high",
                "confidence": 1.0,
            },
        )
        context = self.provider.prefetch("concise validation reports", session_id="session-2")
        self.assertIn("Prefer concise validation reports", context)
        self.assertIsNotNone(self.provider.recall_status())
        self.provider.sync_turn("turn evidence", "turn answer", session_id="session-2")
        self.provider.on_memory_write(
            "add", "user", "User wants durable reports.", {"write_origin": "user"}
        )
        self.assertIn("vault_status", names)
        self.assertIn("integrity_ok", self.provider.handle_tool_call("vault_status", {}))

    def test_precompress_checkpoint_is_durable_and_shutdown_is_clean(self) -> None:
        text = self.provider.on_pre_compress(
            [
                {"role": "user", "content": "checkpoint evidence"},
                {"role": "tool", "content": "ignored tool data"},
            ]
        )
        self.assertIn("checkpoint", text.lower())
        self.assertIn("last_checkpoint", self.provider.handle_tool_call("vault_status", {}))

    def test_required_checkpoint_failure_blocks_lossy_compression(self) -> None:
        """A failed durable hook must leave the pre-compression transcript intact."""
        transcript = [
            {"role": "user", "content": "retain this original evidence"},
            {"role": "assistant", "content": "original assistant response"},
        ]
        original = copy.deepcopy(transcript)
        checkpoint_failure = OSError("synthetic durable checkpoint failure")
        checkpoint = Mock(side_effect=checkpoint_failure)
        lossy_compress = Mock(
            return_value=[{"role": "user", "content": "lossy summary"}]
        )

        def required_checkpoint_then_compress(messages):
            # This is the production ordering: the v2 provider hook completes
            # before the host is allowed to replace the transcript.
            self.provider.on_pre_compress(messages)
            return lossy_compress(messages)

        with patch.object(self.provider._store, "checkpoint", checkpoint):
            with self.assertRaises(OSError) as raised:
                required_checkpoint_then_compress(transcript)

        self.assertIs(raised.exception, checkpoint_failure)
        checkpoint.assert_called_once()
        lossy_compress.assert_not_called()
        self.assertEqual(transcript, original)
        self.assertIn("durable checkpoint failure", str(raised.exception))

    def test_tool_errors_are_structured(self) -> None:
        response = self.provider.handle_tool_call("vault_get", {})
        self.assertIn("error", response)


if __name__ == "__main__":
    unittest.main()
