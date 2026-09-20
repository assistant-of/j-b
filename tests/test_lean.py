"""Optional kernel tests run automatically wherever Lean 4 is installed."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scrubber.verify import lean_certificate
from .fixtures import fixture


@unittest.skipUnless(shutil.which("lean"), "Lean 4 is not installed")
class LeanKernelTests(unittest.TestCase):
    def compile(self, mutate=None):
        context, plan, _, draft = fixture()
        candidate = draft["candidates"][0]
        if mutate:
            mutate(context, plan, candidate)
        # Even when the recorded checks claim success, Lean must inspect the text.
        report = {"errors": [], "review": [], "pending": []}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "constraints.lean"
            path.write_text(lean_certificate(report, candidate, plan, context["job"]), encoding="utf-8")
            return subprocess.run(["lean", str(path)], capture_output=True, text=True, timeout=60)

    def test_valid_keywords_are_proved(self):
        result = self.compile()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_keyword_refutes_even_if_recorded_checks_claim_pass(self):
        def mutate(context, plan, candidate):
            candidate["sections"][0]["entries"][0]["bullets"][0]["text"] = "Built a dashboard."
        self.assertNotEqual(self.compile(mutate).returncode, 0)

    def test_keyword_absent_from_job_is_refuted(self):
        self.assertNotEqual(self.compile(lambda context, *_: context.update(job="A different vacancy.")).returncode, 0)

    def test_empty_keyword_mapping_is_refuted(self):
        def mutate(context, plan, candidate):
            candidate["sections"][0]["entries"][0]["bullets"][0]["requirements"] = []
        self.assertNotEqual(self.compile(mutate).returncode, 0)

    def test_word_boundaries_are_enforced(self):
        def mutate(context, plan, candidate):
            plan["requirements"][0]["keyword"] = "Java"
            context["job"] = "Java and unit tests"
            candidate["sections"][0]["entries"][0]["bullets"][0]["text"] = "Built JavaScript tools."
        self.assertNotEqual(self.compile(mutate).returncode, 0)


if __name__ == "__main__":
    unittest.main()
