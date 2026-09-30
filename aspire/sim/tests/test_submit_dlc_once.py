"""Check that success, timeout, and ambiguous output all forbid a second call."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "submit_dlc_once", Path(__file__).parents[1] / "scripts/common/submit_dlc_once.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SubmitOnceTests(unittest.TestCase):
    def check_outcome(self, result, expected_state):
        calls = []

        def fake(command, **kwargs):
            calls.append(command)
            if isinstance(result, Exception):
                raise result
            return result

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = MODULE.submit_once(root, ["fake-dlc-submit"], runner=fake)
            self.assertEqual(receipt["state"], expected_state)
            self.assertTrue((root / "submission-attempt.json").is_file())
            self.assertTrue((root / "submission-receipt.json").is_file())
            with self.assertRaises(FileExistsError):
                MODULE.submit_once(root, ["fake-dlc-submit"], runner=fake)
            self.assertEqual(len(calls), 1)

    def test_success_binds_one_job(self):
        self.check_outcome(subprocess.CompletedProcess(
            [], 0, "Created job dlc1234567890abcd\n", ""), "submitted")

    def test_timeout_does_not_retry(self):
        self.check_outcome(subprocess.TimeoutExpired("fake-dlc-submit", 180), "uncertain")

    def test_success_without_id_does_not_retry(self):
        self.check_outcome(subprocess.CompletedProcess([], 0, "Connection closed", ""), "uncertain")

    def test_multiple_ids_do_not_bind_arbitrarily(self):
        self.check_outcome(subprocess.CompletedProcess(
            [], 0, "dlc1234567890abcd dlc1234567890efgh", ""), "uncertain")

    def test_preexisting_claim_prevents_api_call(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "submission-attempt.json").write_text("{}")
            calls = []
            with self.assertRaises(FileExistsError):
                MODULE.submit_once(root, ["fake-dlc-submit"], runner=lambda *a, **k: calls.append(a))
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
