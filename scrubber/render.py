"""Render plain structured content with a controlled, instrumented LaTeX template."""
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

from .progress import progress
from .text import plain_quote


FILL_TARGET = 0.90


def annotated_text(text, original=None):
    """Colour only inserted/replaced words; provenance comes from the harness."""
    if original is None:
        return escape(text)
    old, new = original.split(), re.findall(r"\S+\s*", text)
    out = []
    for tag, _, _, start, end in SequenceMatcher(
            None, old, [word.strip() for word in new], autojunk=False).get_opcodes():
        value = escape("".join(new[start:end]))
        if value:
            out.append(value if tag == "equal" else r"\textcolor{ScrubberAddition}{" + value + "}")
    return "".join(out)


def claim_tex(claim, original=None):
    rendered = annotated_text(claim["text"], original)
    # Restore source emphasis only for exact, supported phrases still in the text.
    phrases = set()
    for evidence in claim["evidence"]:
        for phrase in re.findall(r"\\textbf\{([^{}]+)\}", evidence["quote"]):
            plain = plain_quote(phrase, evidence["source"])
            if "\\" not in plain:
                phrases.add(escape(" ".join(plain.split())))
    for phrase in sorted(phrases, key=len, reverse=True):
        rendered = rendered.replace(phrase, r"\textbf{" + phrase + "}")
    return rendered


def instrumentation():
    return r"""
\usepackage{xcolor}
\definecolor{ScrubberAddition}{HTML}{185A9D}
\newcount\bulletlines
\newcommand{\ResumeBullet}[2]{%
  \resumeItem{#2\par\global\bulletlines=\prevgraf
  \typeout{SCRUBBER-BULLET-#1:\the\bulletlines}}}
"""


def finish():
    return r"""\par
\typeout{SCRUBBER-LAST-HEIGHT:\the\pagetotal}
\typeout{SCRUBBER-TEXT-HEIGHT:\the\textheight}
\end{document}
"""


def master_template(sources):
    """Keep the user's preamble verbatim. Model output never supplies TeX."""
    templates = [(name, text) for name, text in (sources or {}).items()
                 if name.endswith(".tex") and r"\begin{document}" in text]
    if not templates:
        return None
    if len(templates) != 1:
        raise ValueError("Multiple master LaTeX templates; keep one .tex template in master/")
    name, text = templates[0]
    pre = text.split(r"\begin{document}", 1)[0]
    # Custom entry layout is supported for the Jake Gutierrez template family.
    custom = all("\\newcommand{\\" + macro + "}" in pre for macro in (
        "resumeItem", "resumeSubheading", "resumeSubHeadingListStart",
        "resumeSubHeadingListEnd", "resumeItemListStart", "resumeItemListEnd"))
    return name, pre, custom


def split_detail(text):
    parts = text.rsplit(" | ", 1)
    if len(parts) == 2 and re.search(r"\b(?:19|20)\d{2}\b", parts[1]):
        date, separator, extra = parts[1].partition(";")
        return parts[0] + (separator + extra if separator else ""), date
    return text, ""


def title_tex(claim, sources):
    title = escape(claim["text"])
    for source in sources.values():
        for url, label in re.findall(r"\\href\{(https?://[^{}]+)\}\{([^{}]+)\}", source):
            if " ".join(label.split()) == claim["text"]:
                return r"\href{" + escape(url) + "}{" + title + "}"
    return title


def styled_resume(candidate, template, font, baseline, sources):
    _, pre, custom = template
    if font is not None:
        if font not in (11, 12):
            raise ValueError("Font must be 11 or 12 pt")
        def override(match):
            options = re.sub(r"\b(?:10|11|12)pt\b,?", "", match[1]).strip(",")
            return r"\documentclass[" + f"{font}pt" + ("," + options if options else "") + "]"
        pre = re.sub(r"\\documentclass\[([^]]*)\]", override, pre, count=1)
        if not re.search(r"\\documentclass\[", pre):
            pre = pre.replace(r"\documentclass{", rf"\documentclass[{font}pt]{{", 1)
    if not custom:
        pre += r"""
\newcommand{\resumeItem}[1]{\item #1}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}}
"""
    out = [pre, instrumentation(), r"\begin{document}", r"\begin{center}"]
    out.append(r"\textbf{\Huge\scshape " + escape(candidate["header"][0]["text"]) + r"}\linebreak\linebreak")
    contacts = " | ".join(c["text"] for c in candidate["header"][1:])
    contact_parts = []
    for value in contacts.split(" | "):
        label = escape(value)
        if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            label = r"\href{mailto:" + escape(value) + r"}{\underline{" + label + "}}" if "hyperref" in pre else label
        elif re.fullmatch(r"(?:https?://)?(?:[\w-]+\.)+[\w-]+(?:/[\w./-]*)?", value):
            url = value if value.startswith("http") else "https://" + value
            label = r"\href{" + escape(url) + r"}{\underline{" + label + "}}" if "hyperref" in pre else label
        contact_parts.append(label)
    out += [r"\small " + " $|$ ".join(contact_parts), r"\end{center}"]
    index = 0
    for s, section in enumerate(candidate["sections"]):
        out.append(r"\section{" + escape(section["title"]) + "}")
        out.append(r"\resumeSubHeadingListStart")
        skills = section["title"].casefold() == "skills"
        if skills:
            out.append(r"\small{\item{")
        for e, entry in enumerate(section["entries"]):
            previous = baseline["sections"][s]["entries"][e] if baseline else None
            title = title_tex(entry["title"], sources) if "hyperref" in pre else escape(entry["title"]["text"])
            detail = annotated_text(entry["detail"]["text"], previous["detail"]["text"] if previous else None)
            if skills:
                out.append(r"\textbf{" + title + "}: " + detail +
                           (r"\\" if e < len(section["entries"]) - 1 else ""))
            elif custom:
                # Dates remain right aligned; long descriptive fields may wrap.
                left, right = split_detail(entry["detail"]["text"])
                if right:
                    previous_left, previous_right = split_detail(previous["detail"]["text"]) if previous else (None, None)
                    detail = annotated_text(left, previous_left)
                    date = annotated_text(right, previous_right)
                else:
                    date = ""
                if section["title"].casefold() == "projects":
                    heading = r"\textbf{" + title + r"} \textnormal{$|$ \textit{" + detail + "}}"
                    out.append(r"\resumeSubheading{\parbox[t]{\dimexpr0.95\textwidth-4\tabcolsep\relax}{" + heading + "}}{}{}{}")
                    out.append(r"\vspace{-1.35\baselineskip}")
                else:
                    out.append(r"\resumeSubheading{\parbox[t]{0.70\textwidth}{\textbf{" + title + "}}}{" + date +
                               r"}{\parbox[t]{0.70\textwidth}{" + detail + "}}{}")
            else:
                out.append(r"\item\textbf{" + title + r"}\\ " + detail)
            if entry["bullets"]:
                out.append(r"\resumeItemListStart")
                for b, bullet in enumerate(entry["bullets"]):
                    old = (previous["bullets"][b]["text"] if b < len(previous["bullets"]) else "") if previous else None
                    out.append(r"\ResumeBullet{" + str(index) + "}{" + claim_tex(bullet, old) + "}")
                    index += 1
                out.append(r"\resumeItemListEnd")
        if skills:
            out.append("}}")
        out.append(r"\resumeSubHeadingListEnd")
        if custom and section["title"].casefold() == "skills":
            out.append(r"\vspace{-16pt}")
    out.append(finish())
    return "\n".join(out)


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


def resume_tex(candidate, font=None, sources=None, baseline=None):
    template = master_template(sources)
    if template:
        return styled_resume(candidate, template, font, baseline, sources)
    out = [preamble(font or 12).replace(r"\begin{document}",
           "\\usepackage{xcolor}\n\\definecolor{ScrubberAddition}{HTML}{185A9D}\n\\begin{document}"), r"\begin{center}"]
    for i, claim in enumerate(candidate["header"]):
        line = escape(claim["text"])
        out.append((r"\textbf{" + line + "}" if i == 0 else line) + r"\\")
    out.append(r"\end{center}")
    index = 0
    for s, section in enumerate(candidate["sections"]):
        out.append(r"\section*{" + escape(section["title"]) + "}")
        for e, entry in enumerate(section["entries"]):
            previous = baseline["sections"][s]["entries"][e] if baseline else None
            out.append(r"\textbf{" + escape(entry["title"]["text"]) + "}\\")
            out.append(annotated_text(entry["detail"]["text"], previous["detail"]["text"] if previous else None) + r"\par")
            if entry["bullets"]:
                out.append(r"\begin{itemize}")
                for b, bullet in enumerate(entry["bullets"]):
                    old = (previous["bullets"][b]["text"] if b < len(previous["bullets"]) else "") if previous else None
                    out.append(r"\ResumeBullet{" + str(index) + "}{" + annotated_text(bullet["text"], old) + "}")
                    index += 1
                out.append(r"\end{itemize}")
    out.append(finish())
    return "\n".join(out) + "\n"


def letter_tex(candidate, font=12):
    return preamble(font or 12) + "\n\n".join(escape(p["text"]) + r"\par" for p in candidate["cover_letter"]) + "\n" + r"\end{document}" + "\n"


def compile_tex(path, max_pages):
    path = Path(path).resolve()
    path.with_suffix(".pdf").unlink(missing_ok=True)
    path.with_suffix(".log").unlink(missing_ok=True)
    source = path.read_text()
    requested_engine = re.search(r"^%\s*!TEX program\s*=\s*(pdflatex|lualatex|xelatex)\s*$", source, re.M | re.I)
    engine_name = requested_engine[1].lower() if requested_engine else "pdflatex"
    engine = shutil.which(engine_name)
    if not engine:
        return {"compiled": False, "reason": f"{engine_name} is not installed", "max_pages": max_pages}
    # Local master preamble is trusted; model claims are always escaped.
    with progress(f"Compiling {path.parent.name}/{path.name}"):
        result = subprocess.run([engine, "-no-shell-escape", "-interaction=nonstopmode",
                                 "-halt-on-error", path.name], cwd=path.parent,
                                capture_output=True, text=True, timeout=60)
    log_path = path.with_suffix(".log")
    log = log_path.read_text(errors="replace") if log_path.exists() else result.stdout
    if (result.returncode and engine_name == "lualatex" and "Error in luaotfload" in log
            and r"\ifPDFTeX" in source and shutil.which("pdflatex")):
        # The user's engine-conditional template explicitly supports this branch.
        engine_name = "pdflatex"
        result = subprocess.run([shutil.which(engine_name), "-no-shell-escape",
                                 "-interaction=nonstopmode", "-halt-on-error", path.name],
                                cwd=path.parent, capture_output=True, text=True, timeout=60)
        log = log_path.read_text(errors="replace") if log_path.exists() else result.stdout
    if result.returncode:
        return {"compiled": False, "reason": f"Compilation failed; see {log_path.name}",
                "max_pages": max_pages}
    pages = re.search(r"Output written on .*?\((\d+) pages?", log, re.S)
    result = {"compiled": bool(pages), "engine": engine_name, "pages": int(pages.group(1)) if pages else 0,
            "max_pages": max_pages,
            "bullet_lines": dict((k, int(v)) for k, v in re.findall(r"SCRUBBER-BULLET-(\d+):(\d+)", log)),
            "overflow": bool(re.search(r"Overfull \\[hv]box", log))}
    height = re.search(r"SCRUBBER-LAST-HEIGHT:([\d.]+)pt", log)
    capacity = re.search(r"SCRUBBER-TEXT-HEIGHT:([\d.]+)pt", log)
    if height and capacity and float(capacity[1]) > 0:
        result["last_page_fill"] = round(min(1.0, float(height[1]) / float(capacity[1])), 4)
    return result


def needs_expansion(layout):
    return bool(layout.get("compiled") and "last_page_fill" in layout and
                (layout["pages"] < layout["max_pages"] or layout["last_page_fill"] < FILL_TARGET))
