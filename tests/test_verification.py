import unittest
from copy import deepcopy

from scrubber import schema
from scrubber.render import escape, resume_tex
from scrubber.verify import check_candidate, check_plan, contains, lean_certificate
from .fixtures import compiled, fixture


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.context, self.plan, self.outline, self.draft = fixture()
        self.candidate = self.draft["candidates"][0]

    def check(self, approved=True, layout=None):
        return check_candidate(self.candidate, self.plan, self.context["master"], self.context["job"],
                               self.outline["candidates"][0], approved, layout or compiled())

    def test_supported_candidate_passes_after_review(self):
        self.assertEqual(self.check()["status"], "pass")
        self.assertEqual(check_plan(self.plan, self.context["job"]), [])

    def test_rewording_needs_human_review(self):
        self.assertEqual(self.check(approved=False)["status"], "review")

    def test_missing_compiler_never_passes(self):
        report = self.check(layout={"compiled": False})
        self.assertEqual(report["status"], "pending")
        self.assertIn("false", lean_certificate(report, self.candidate, self.plan, self.context["job"]))

    def test_forged_quote_fails_even_with_approval(self):
        self.candidate["header"][0]["evidence"][0]["quote"] = "Fake achievement"
        self.assertEqual(self.check()["status"], "fail")

    def test_reference_letter_cannot_establish_applicant_facts(self):
        self.context["master"]["ref/letter.md"] = "Example Student"
        self.candidate["header"][0]["evidence"][0]["source"] = "ref/letter.md"
        self.assertEqual(self.check()["status"], "fail")

    def test_job_cannot_establish_resume_fact(self):
        self.candidate["header"][0] = {"text": "Python", "evidence": [{"source": "job.txt", "quote": "Python"}]}
        self.assertEqual(self.check()["status"], "fail")

    def test_invented_metric_rejected(self):
        self.candidate["cover_letter"][1]["text"] += " Improved performance by 45%."
        self.assertTrue(any("number absent" in e for e in self.check()["errors"]))

    def test_keyword_mapping_is_checked(self):
        self.candidate["sections"][0]["entries"][0]["bullets"][0]["requirements"] = ["R2"]
        self.assertEqual(self.check()["status"], "fail")

    def test_each_bullet_has_explicit_keyword_evidence_in_report(self):
        nodes = self.check()["tree"]["sections"][0]["children"]
        bullets = [node for node in nodes if "keyword_checks" in node]
        self.assertEqual(len(bullets), 2)
        self.assertTrue(all(node["keyword_checks"][0]["in_bullet"] and
                            node["keyword_checks"][0]["in_job"] for node in bullets))

    def test_empty_keyword_mapping_fails_independently_of_schema(self):
        self.candidate["sections"][0]["entries"][0]["bullets"][0]["requirements"] = []
        self.assertTrue(any("at least one" in e for e in self.check()["errors"]))

    def test_missing_requirement_rejected(self):
        self.candidate["sections"][0]["entries"][0]["bullets"].pop()
        self.assertTrue(any("Uncovered" in e for e in self.check()["errors"]))

    def test_section_requirement_is_enforced(self):
        self.plan["requirements"][0]["section"] = "Experience"
        report = self.check()
        self.assertEqual(report["tree"]["sections"][0]["status"], "fail")

    def test_faithful_bullet_cannot_be_rewritten(self):
        self.candidate["sections"][0]["entries"][0]["bullets"][0]["text"] = "Built a Python dashboard."
        self.assertTrue(any("Faithful" in e for e in self.check()["errors"]))

    def test_three_line_bullet_rejected(self):
        layout = compiled()
        layout["bullet_lines"]["0"] = 3
        self.assertTrue(any("maximum 2" in e for e in self.check(layout=layout)["errors"]))

    def test_missing_bullet_measurement_rejected(self):
        layout = compiled()
        layout["bullet_lines"] = {}
        self.assertEqual(self.check(layout=layout)["status"], "fail")

    def test_page_limit_and_overflow_rejected(self):
        for field, value in (("pages", 2), ("overflow", True)):
            layout = compiled()
            layout[field] = value
            self.assertEqual(self.check(layout=layout)["status"], "fail")

    def test_approved_outline_cannot_drift(self):
        self.candidate["sections"][0]["entries"][0]["title"]["text"] = "Another Project"
        self.assertEqual(self.check()["status"], "fail")

    def test_plan_rejects_invented_company_and_quotes(self):
        self.plan["company"] = "Imaginary Corporation"
        self.plan["requirements"][0]["job_quote"] = "Imagined Python job"
        self.assertEqual(len(check_plan(self.plan, self.context["job"])), 2)

    def test_keyword_word_boundaries(self):
        self.assertFalse(contains("JavaScript", "Java"))
        self.assertTrue(contains("Experience with C++.", "C++"))
        self.assertTrue(contains("Unit   Tests", "unit tests"))

    def test_schema_rejects_missing_mapping_and_extra_fields(self):
        for change in (lambda b: b.update(requirements=[]), lambda b: b.update(latex="bad")):
            draft = deepcopy(self.draft)
            change(draft["candidates"][0]["sections"][0]["entries"][0]["bullets"][0])
            with self.assertRaises(ValueError):
                schema.validate(draft, schema.DRAFT)

    def test_tex_injection_is_escaped(self):
        value = r"\input{/etc/passwd} 50% & #_ $"
        escaped = escape(value)
        self.assertNotIn(r"\input{", escaped)
        self.assertIn(r"\textbackslash{}input\{", escaped)
        self.candidate["header"][0]["text"] = value
        text = resume_tex(self.candidate)
        self.assertIn(escaped, text)
        self.assertEqual(text.count(r"\ResumeBullet{"), 2)


if __name__ == "__main__":
    unittest.main()
