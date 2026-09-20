import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scrubber.workflow import new_project, read_json, run, verify_project, write_json
from .fixtures import FakeBackend, compiled, fixture


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.context, self.plan, self.outline, self.draft = fixture()
        self.project = new_project(Path(self.tmp.name), "test / ../ job", self.context)
        self.backend = FakeBackend([self.plan, self.outline, self.draft])

    def complete(self, **kwargs):
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            return run(self.project, self.backend, ask=lambda _: "yes", emit=lambda _: None, **kwargs)

    def test_end_to_end_artifacts_and_resume_no_extra_model_calls(self):
        self.assertEqual(set(self.complete().values()), {"pass"})
        self.assertEqual(len(self.backend.calls), 3)
        for name in ("faithful", "balanced", "targeted"):
            for file in ("resume.tex", "cover-letter.md", "cover-letter.tex", "verification.json", "constraints.lean", "evidence.json"):
                self.assertTrue((self.project / name / file).is_file())
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            run(self.project, self.backend, ask=lambda _: self.fail("Approval unexpectedly repeated"), emit=lambda _: None)
        self.assertEqual(len(self.backend.calls), 3)

    def test_plan_rejection_stops_before_outline_generation(self):
        with self.assertRaises(InterruptedError):
            run(self.project, self.backend, ask=lambda _: "quit", emit=lambda _: None)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertFalse((self.project / "outline.json").exists())

    def test_revision_feedback_reaches_backend(self):
        self.backend = FakeBackend([self.plan, self.plan, self.outline, self.draft])
        answers = iter(["Emphasize testing", "yes", "yes", "yes", "yes", "yes"])
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            run(self.project, self.backend, ask=lambda _: next(answers), emit=lambda _: None)
        self.assertEqual(self.backend.calls[1][1]["revision_feedback"], "Emphasize testing")

    def test_missing_compiler_retains_draft_but_never_passes(self):
        with patch("scrubber.render.shutil.which", return_value=None):
            result = run(self.project, self.backend, ask=lambda _: "yes", emit=lambda _: None)
        self.assertEqual(set(result.values()), {"pending"})

    def test_claim_rejection_never_passes(self):
        answers = iter(["yes", "yes", "no", "no", "no"])
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            result = run(self.project, self.backend, ask=lambda _: next(answers), emit=lambda _: None)
        self.assertEqual(set(result.values()), {"review"})

    def test_edited_plan_invalidates_approval(self):
        self.complete()
        self.plan["gaps"].append("New gap")
        write_json(self.project / "plan.json", self.plan)
        with self.assertRaisesRegex(ValueError, "changed|not approved"):
            verify_project(self.project, emit=lambda _: None)
        self.assertEqual(read_json(self.project / "results.json"), {"status": "in_progress"})

    def test_compiler_failure_cannot_leave_previous_pass_report(self):
        self.complete()
        with patch("scrubber.workflow.compile_tex", side_effect=RuntimeError("Compiler failed")):
            with self.assertRaisesRegex(RuntimeError, "Compiler failed"):
                verify_project(self.project, emit=lambda _: None)
        self.assertEqual(read_json(self.project / "faithful/verification.json")["status"], "pending")
        self.assertEqual(read_json(self.project / "results.json"), {"status": "in_progress"})

    def test_edited_sources_invalidate_stage_dependencies(self):
        self.complete()
        self.context["master"]["master/resume.tex"] += "\nNew experience"
        write_json(self.project / "inputs.json", self.context)
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            verify_project(self.project, emit=lambda _: None)

    def test_edited_claim_loses_approval(self):
        self.complete()
        self.draft["candidates"][1]["cover_letter"][0]["text"] = "Please consider me for Software Intern at Example Robotics."
        write_json(self.project / "draft.json", self.draft)
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            results = verify_project(self.project, emit=lambda _: None)
        self.assertEqual(results["balanced"], "review")
        self.assertEqual(results["faithful"], "pass")

    def test_invalid_draft_repaired_with_error_feedback(self):
        bad = {"candidates": [self.draft["candidates"][0]]}
        self.backend = FakeBackend([self.plan, self.outline, bad, self.draft])
        self.complete()
        self.assertIn("Expected exactly", self.backend.calls[3][1]["revision_feedback"])
        self.assertEqual(self.backend.calls[3][1]["previous_value"], bad)

    def test_draft_retry_patches_only_failed_claim(self):
        bad = deepcopy(self.draft)
        bullet = bad["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        bullet["text"] = "Built a Python dashboard with 45% fewer errors."
        replacement = self.draft["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        self.backend = FakeBackend([self.plan, self.outline, bad, {"repair_0": replacement}])
        self.complete()
        self.assertEqual(read_json(self.project / "draft.json"), self.draft)
        _, request, shape = self.backend.calls[3]
        self.assertEqual(request["previous_value"], bad)
        self.assertEqual(set(shape["properties"]), {"repair_0"})
        task, = request["failed_claims"]
        self.assertEqual(task["path"], "section/0/entry/0/bullet/0")
        self.assertEqual(task["claim"], bullet)
        self.assertEqual(task["requirements"], [self.plan["requirements"][0]])
        self.assertIn("number absent", task["errors"][0])
        self.assertFalse((self.project / "draft-errors.json").exists())

    def test_local_retry_cannot_modify_valid_claims(self):
        bad = deepcopy(self.draft)
        bullet = bad["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        bullet["text"] = "Built a Python dashboard with 45% fewer errors."
        self.backend = FakeBackend([self.plan, self.outline, bad,
                                    {"repair_0": bullet, "repair_1": bullet}])
        with self.assertRaisesRegex(ValueError, "fields must be"):
            self.complete()
        self.assertEqual(read_json(self.project / "draft.json"), bad)

    def test_exhausted_repairs_retain_reviewable_draft_and_resume(self):
        bad = deepcopy(self.draft)
        bullet = bad["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        bullet["text"] = "Built a Python dashboard with 45% fewer errors."
        self.backend = FakeBackend([self.plan, self.outline, bad,
                                    {"repair_0": bullet}, {"repair_0": bullet}])
        with self.assertRaisesRegex(ValueError, "draft failed validation"):
            self.complete(max_repairs=2)
        self.assertEqual(len(self.backend.calls), 5)
        self.assertEqual(read_json(self.project / "draft.json"), bad)
        self.assertIn(bullet["text"], (self.project / "comparison.md").read_text())
        self.assertTrue(read_json(self.project / "draft-errors.json")["errors"])
        replacement = self.draft["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        self.backend = FakeBackend([{"repair_0": replacement}])
        self.complete()
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(read_json(self.project / "draft.json"), self.draft)
        self.assertFalse((self.project / "draft-errors.json").exists())

    def test_repairs_continue_past_old_limit_with_visible_feedback(self):
        bad = deepcopy(self.draft)
        bullet = bad["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        bullet["text"] += " Improved by 45%."
        replacement = self.draft["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        self.backend = FakeBackend([self.plan, self.outline, bad,
                                    {"repair_0": bullet}, {"repair_0": bullet},
                                    {"repair_0": replacement}])
        messages = []
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            result = run(self.project, self.backend, ask=lambda _: "yes", emit=messages.append)
        self.assertEqual(set(result.values()), {"pass"})
        self.assertEqual(len(self.backend.calls), 6)
        self.assertTrue(any("repairing automatically (3/8)" in m for m in messages))
        self.assertTrue(any("number absent" in m for m in messages))

    def test_unlimited_repairs_continue_until_valid(self):
        bad = deepcopy(self.draft)
        bullet = bad["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        bullet["text"] += " Improved by 45%."
        replacement = self.draft["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]
        self.backend = FakeBackend([self.plan, self.outline, bad] +
                                  [{"repair_0": bullet}] * 9 + [{"repair_0": replacement}])
        self.assertEqual(set(self.complete(max_repairs=0).values()), {"pass"})
        self.assertEqual(read_json(self.project / "draft.json"), self.draft)

    def test_light_faithful_edit_reaches_review_without_model_repair(self):
        self.draft["candidates"][0]["sections"][0]["entries"][0]["bullets"][0]["text"] = "Built a Python dashboard."
        answers = iter(["yes", "yes", "no", "yes", "yes"])
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            result = run(self.project, self.backend, ask=lambda _: next(answers), emit=lambda _: None)
        self.assertEqual(result["faithful"], "review")
        self.assertEqual(len(self.backend.calls), 3)
        self.assertFalse((self.project / "draft-errors.json").exists())

    def test_coverage_failure_uses_full_draft_retry(self):
        bad = deepcopy(self.draft)
        bad["candidates"][0]["sections"][0]["entries"][0]["bullets"].pop()
        self.backend = FakeBackend([self.plan, self.outline, bad, self.draft])
        self.complete()
        request = self.backend.calls[3][1]
        self.assertEqual(request["previous_value"], bad)
        self.assertNotIn("failed_claims", request)
        self.assertIn("Uncovered", request["revision_feedback"])

    def test_path_label_cannot_escape_projects(self):
        self.assertEqual(self.project.parent, Path(self.tmp.name) / "projects")

    def test_lean_missing_never_claims_proof(self):
        self.complete()
        with patch("scrubber.workflow.compile_tex", side_effect=compiled), patch("scrubber.workflow.shutil.which", return_value=None):
            result = verify_project(self.project, lean=True, emit=lambda _: None)
            resumed_result = verify_project(self.project, emit=lambda _: None)
        self.assertEqual(set(result.values()), {"pending"})
        self.assertEqual(set(resumed_result.values()), {"pending"})
        report = read_json(self.project / "faithful/verification.json")
        self.assertNotIn("lean", report)


if __name__ == "__main__":
    unittest.main()
