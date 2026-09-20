"""Render plain structured content with a controlled, instrumented LaTeX template."""
import re
import shutil
import subprocess
from pathlib import Path


def escape(text):
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
                    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
                    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(c, c) for c in text)


def preamble(font):
    if font not in (11, 12):
        raise ValueError("Font must be 11 or 12 pt")
    return rf"""\documentclass[{font}pt,letterpaper]{{article}}
\usepackage[margin=0.65in]{{geometry}}
\usepackage[T1]{{fontenc}}
\usepackage[utf8]{{inputenc}}
\usepackage{{enumitem}}
\usepackage{{titlesec}}
\pagestyle{{empty}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{3pt}}
\titleformat{{\section}}{{\bfseries\normalsize}}{{}}{{0pt}}{{}}[\titlerule]
\titlespacing*{{\section}}{{0pt}}{{7pt}}{{4pt}}
\setlist[itemize]{{leftmargin=*,nosep,topsep=2pt}}
\newcount\bulletlines
\newcommand{{\ResumeBullet}}[2]{{%
  \item\setbox0=\vbox{{\hsize=\linewidth\noindent #2\par\global\bulletlines=\prevgraf}}%
  \typeout{{SCRUBBER-BULLET-#1:\the\bulletlines}}\box0}}
\begin{{document}}
"""


def resume_tex(candidate, font=12):
    out = [preamble(font), r"\begin{center}"]
    for i, claim in enumerate(candidate["header"]):
        line = escape(claim["text"])
        out.append((r"\textbf{" + line + "}" if i == 0 else line) + r"\\")
    out.append(r"\end{center}")
    index = 0
    for section in candidate["sections"]:
        out.append(r"\section*{" + escape(section["title"]) + "}")
        for entry in section["entries"]:
            out.append(r"\textbf{" + escape(entry["title"]["text"]) + "}\\")
            out.append(escape(entry["detail"]["text"]) + r"\par")
            if entry["bullets"]:
                out.append(r"\begin{itemize}")
                for bullet in entry["bullets"]:
                    out.append(r"\ResumeBullet{" + str(index) + "}{" + escape(bullet["text"]) + "}")
                    index += 1
                out.append(r"\end{itemize}")
    out.append(r"\end{document}")
    return "\n".join(out) + "\n"


def letter_tex(candidate, font=12):
    return preamble(font) + "\n\n".join(escape(p["text"]) + r"\par" for p in candidate["cover_letter"]) + "\n" + r"\end{document}" + "\n"


def compile_tex(path, max_pages):
    path = Path(path).resolve()
    path.with_suffix(".pdf").unlink(missing_ok=True)
    path.with_suffix(".log").unlink(missing_ok=True)
    engine = shutil.which("pdflatex")
    if not engine:
        return {"compiled": False, "reason": "pdflatex is not installed", "max_pages": max_pages}
    # Only the harness-generated template is compiled by callers, never model-supplied TeX.
    result = subprocess.run([engine, "-no-shell-escape", "-interaction=nonstopmode",
                             "-halt-on-error", path.name], cwd=path.parent,
                            capture_output=True, text=True, timeout=60)
    log_path = path.with_suffix(".log")
    log = log_path.read_text(errors="replace") if log_path.exists() else result.stdout
    if result.returncode:
        return {"compiled": False, "reason": f"Compilation failed; see {log_path.name}",
                "max_pages": max_pages}
    pages = re.search(r"Output written on .*?\((\d+) pages?", log, re.S)
    return {"compiled": bool(pages), "pages": int(pages.group(1)) if pages else 0,
            "max_pages": max_pages,
            "bullet_lines": dict((k, int(v)) for k, v in re.findall(r"SCRUBBER-BULLET-(\d+):(\d+)", log)),
            "overflow": bool(re.search(r"Overfull \\[hv]box", log))}
