import shutil
import tempfile
import unittest
from pathlib import Path

from scrubber.render import compile_tex, resume_tex
from .fixtures import fixture


MASTER = r"""\documentclass[letterpaper,11pt]{article}
\usepackage[margin=0.5in]{geometry}
\usepackage{enumitem}
\usepackage{xcolor}
\usepackage{hyperref}
\pagestyle{empty}
\newcommand{\resumeItem}[1]{\item\small{#1\vspace{-2pt}}}
\newcommand{\resumeSubheading}[4]{\item
\begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
\textbf{#1} & #2 \\
\textit{\small#3} & \textit{\small#4} \\
\end{tabular*}\vspace{-7pt}}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in,label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
\begin{document}
Example Student
\end{document}
"""


class MasterRendererTests(unittest.TestCase):
    def setUp(self):
        self.candidate = fixture()[3]["candidates"][0]
        self.sources = {"master/resume.tex": MASTER}

    def test_original_preamble_macros_and_font_preserved(self):
        tex = resume_tex(self.candidate, sources=self.sources)
        self.assertTrue(tex.startswith(MASTER.split(r"\begin{document}")[0]))
        self.assertNotIn("0.65in", tex)
        self.assertIn(r"\resumeSubheading{", tex)
        self.assertIn(r"\ResumeBullet{0}", tex)
        self.assertIn(r"\textbf{\Huge\scshape Example Student}", tex)
        self.assertIn(r"\href{mailto:example@example.invalid}", tex)

    def test_only_explicit_font_override_changes_class(self):
        tex = resume_tex(self.candidate, 12, self.sources)
        self.assertIn(r"\documentclass[12pt,letterpaper]{article}", tex)
        self.assertIn(r"\small{#1\vspace{-2pt}}", tex)

    def test_ambiguous_templates_are_not_silently_chosen(self):
        with self.assertRaisesRegex(ValueError, "Multiple master"):
            resume_tex(self.candidate, sources={**self.sources, "master/second.tex": MASTER})

    @unittest.skipUnless(shutil.which("pdflatex"), "pdflatex required")
    def test_actual_pdf_measures_sparse_page_and_long_bullets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.tex"
            path.write_text(resume_tex(self.candidate, sources=self.sources))
            layout = compile_tex(path, 1)
            self.assertTrue(layout["compiled"], layout)
            self.assertEqual(layout["pages"], 1)
            self.assertFalse(layout["overflow"])
            self.assertLess(layout["last_page_fill"], .9)
            self.assertEqual(set(layout["bullet_lines"]), {"0", "1"})
            self.assertTrue(all(1 <= n <= 2 for n in layout["bullet_lines"].values()))
            self.candidate["sections"][0]["entries"][0]["bullets"][0]["text"] *= 8
            path.write_text(resume_tex(self.candidate, sources=self.sources))
            self.assertGreater(compile_tex(path, 1)["bullet_lines"]["0"], 2)


if __name__ == "__main__":
    unittest.main()
