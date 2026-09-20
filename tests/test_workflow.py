import tempfile
import unittest
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

    def complete(self):
        with patch("scrubber.workflow.compile_tex", side_effect=compiled):
            return run(self.project, self.backend, ask=lambda _: "yes", emit=lambda _: None)

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
