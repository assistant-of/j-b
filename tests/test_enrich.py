import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scrubber.enrich import baseline_for, expand_project
from scrubber.render import annotated_text, needs_expansion, resume_tex
from scrubber.workflow import new_project, read_json, run, verify_project, write_json
from .fixtures import FakeBackend, compiled, fixture


def measured(fill=0.5, **kwargs):
    return {**compiled(), "last_page_fill": fill, **kwargs}


class ExpansionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.context, self.plan, self.outline, self.draft = fixture()
        self.project = new_project(tmp.name, "expansion", self.context)
        for candidate in self.draft["candidates"]:
            folder = self.project / candidate["name"]
            folder.mkdir()
            write_json(folder / "verification.json", {"errors": [], "layout": measured()})
        self.proposals = deepcopy(self.draft["candidates"])
        for candidate in self.proposals:
            bullet = candidate["sections"][0]["entries"][0]["bullets"][0]
            bullet["text"] += " Used Python for data analysis."

    def expand(self, backend, **kwargs):
        return expand_project(self.project, backend, self.draft, self.plan, self.outline,
                              self.context, emit=lambda _: None, **kwargs)

    def test_underfilled_one_page_triggers_and_resume_preserves_colour(self):
        backend = FakeBackend(self.proposals)
        with patch("scrubber.enrich.compile_tex", return_value=measured(.94)):
            result = self.expand(backend)
            self.expand(backend)
        self.assertEqual(len(backend.calls), 3)
        self.assertEqual(result["candidates"], self.proposals)
        base = baseline_for(self.project, result["candidates"][0])
        tex = resume_tex(result["candidates"][0], sources=self.context["master"], baseline=base)
        self.assertIn(r"\textcolor{ScrubberAddition}{Used Python for data analysis.}", tex)
        self.assertEqual(read_json(self.project / "expansion.json")["faithful"]["status"], "filled")

    def test_overflow_retry_keeps_last_valid_draft_and_receives_feedback(self):
        backend = FakeBackend([self.proposals[0], self.proposals[0], *self.proposals[1:]])
        with patch("scrubber.enrich.compile_tex", side_effect=[measured(.1, pages=2),
                measured(.94), measured(.94), measured(.94)]):
            self.expand(backend)
        self.assertIn("limit is 1", " ".join(backend.calls[1][1]["revision_feedback"]))
        attempts = read_json(self.project / "expansion.json")["faithful"]["attempts"]
        self.assertFalse(attempts[0]["accepted"])
        self.assertTrue(attempts[1]["accepted"])

    def test_unsupported_number_is_rejected_even_when_it_fills_page(self):
        bad = deepcopy(self.proposals[0])
        bad["sections"][0]["entries"][0]["bullets"][0]["text"] += " Saved 90%."
        backend = FakeBackend([bad, *self.draft["candidates"]])
        with patch("scrubber.enrich.compile_tex", return_value=measured(.94)):
            result = self.expand(backend)
        self.assertEqual(result, fixture()[3])
        self.assertIn("number absent", " ".join(backend.calls[1][1]["revision_feedback"]))

    def test_expansion_cannot_change_cover_letter_or_outline(self):
        bad = deepcopy(self.proposals[0])
        bad["cover_letter"][0]["text"] = "Changed letter"
        backend = FakeBackend([bad, *self.draft["candidates"]])
        with patch("scrubber.enrich.compile_tex") as compile_call:
            self.expand(backend)
        compile_call.assert_not_called()
        self.assertIn("Expansion must preserve cover_letter", backend.calls[1][1]["revision_feedback"])

    def test_insufficient_evidence_is_saved_without_endless_model_calls(self):
        backend = FakeBackend(self.draft["candidates"])
        self.expand(backend)
        self.expand(backend)
        self.assertEqual(len(backend.calls), 3)
        self.assertEqual(read_json(self.project / "expansion.json")["targeted"]["status"],
                         "insufficient_supported_detail")

    def test_full_page_and_unmeasured_layout_do_not_trigger(self):
        for layout in (measured(.95), {"compiled": False}):
            for candidate in self.draft["candidates"]:
                write_json(self.project / candidate["name"] / "verification.json",
                           {"errors": [], "layout": layout})
            self.expand(FakeBackend([]))
        self.assertTrue(needs_expansion(measured(.95, max_pages=2)))

    def test_workflow_verifies_before_expansion_and_requires_new_claim_review(self):
        class CheckingBackend(FakeBackend):
            def ask(inner, instruction, data, shape):
                if "baseline" in data:
                    self.assertTrue((self.project / "faithful/constraints.lean").exists())
                    self.assertTrue((self.project / "faithful/resume.tex").exists())
                return super().ask(instruction, data, shape)

        backend = CheckingBackend([self.plan, self.outline, self.draft, *self.proposals])

        def current_layout(path, *_):
            return measured(.94 if "Used Python" in path.read_text() or "cover-letter" in path.name else .5)

        with patch("scrubber.workflow.compile_tex", side_effect=current_layout), patch(
                "scrubber.enrich.compile_tex", return_value=measured(.94)):
            answers = iter(["yes", "yes", "no", "no", "no"])
            results = run(self.project, backend, ask=lambda _: next(answers), emit=lambda _: None)
            self.assertEqual(set(results.values()), {"review"})
            # verify is read-only with respect to model calls and keeps added-word colour.
            verify_project(self.project, emit=lambda _: None)
        self.assertEqual(len(backend.calls), 6)
        tex = (self.project / "faithful/resume.tex").read_text()
        self.assertIn(r"\textcolor{ScrubberAddition}{Used Python", tex)

    def test_unfilled_resume_cannot_pass_after_claim_approval(self):
        backend = FakeBackend([self.plan, self.outline, self.draft, *self.draft["candidates"]])
        with patch("scrubber.workflow.compile_tex", return_value=measured()):
            results = run(self.project, backend, ask=lambda _: "yes", emit=lambda _: None)
        self.assertEqual(set(results.values()), {"pending"})


class AnnotationTests(unittest.TestCase):
    def test_colours_only_additions_and_escapes_tex(self):
        result = annotated_text("Built Python tools and C++ tools.", "Built Python tools.")
        self.assertTrue(result.startswith("Built Python "))
        self.assertIn(r"\textcolor{ScrubberAddition}", result)
        self.assertEqual(annotated_text("Python", "Python"), "Python")
        self.assertIn(r"\textbackslash{}input", annotated_text(r"\input{secret}", ""))


if __name__ == "__main__":
    unittest.main()
