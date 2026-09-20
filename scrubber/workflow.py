import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import schema
from .render import compile_tex, letter_tex, resume_tex
from .verify import check_candidate, check_plan, claims, digest, lean_certificate

PLAN_PROMPT = """Extract exact company and role strings from the posting. Propose 4-10 achievable
keyword requirements, each with a unique id, an exact keyword and exact job_quote.
section is 'any' or an exact proposed resume section name. Requirements are mandatory
and checked by literal case-insensitive keyword matching in bullets in that section.
Choose requirements that the master actually supports, including transferable skills.
List all important unsupported job qualifications in gaps, never invent them or hide them.
Faithful source bullets must also be able to meet the approved requirements.
Respect the user's preferences and requested role. Do not include layout rules as keywords."""

OUTLINE_PROMPT = """Propose exactly three candidates named faithful, balanced, targeted, in that order.
Each outline lists sections in order and exact plain-text entry titles that will appear in the resume.
Faithful keeps source section/entry order and selects verbatim source bullet text.
Balanced selectively reorders and rephrases. Targeted emphasizes the strongest supported role fit.
Keep the structure close to the master LaTeX resume. Explain each candidate's tradeoff.
All approved requirements must be achievable. Budget space for the configured pages and font.
Use 'Education', 'Experience', 'Projects', 'Skills' where appropriate, but do not create empty sections."""

DRAFT_PROMPT = """Build exactly the three approved candidates, preserving names, section order,
section titles and entry titles exactly. Every header, entry title, entry detail and bullet is
a claim citing exact master/ source quotes. Copy faithful bullets verbatim from the master
(choose plain-text substrings inside LaTeX markup where possible). Never add unsupported facts.
Each bullet must map to at least one approved requirement id AND include its exact keyword.
All approved requirements must be covered in each candidate. Keep bullets to two rendered lines;
aim for 18-25 words at 12 pt. Prefer action, description, result; quantify only sourced numbers.
Use course names instead of course codes when supported by the master. Education can have zero bullets.
For each candidate, draft a cover letter of 3-4 short paragraphs. Evidence must support every
factual assertion. Use master/ for applicant facts and job.txt for company/role facts. ref/ only
informs tone; do not copy other people's accomplishments. No company history unless in job.txt.
Opening: role, company, supported program/year/university/PEY status, genuine interest and top
2-3 supported skills. Then connect specific projects/experience to the role's transferable skills.
If PEY status or year is absent, omit it rather than assume it. Do not fabricate motivation or metrics.
Only plain text; no TeX. Respect page/font constraints by selecting content, never shrinking type."""


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def new_project(root, label, context):
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:60] or "application"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = Path(root) / "projects" / f"{stamp}-{slug}-{uuid.uuid4().hex[:6]}"
    path.mkdir(parents=True)
    write_json(path / "inputs.json", context)
    (path / "job.txt").write_text(context["job"], encoding="utf-8")
    write_json(path / "state.json", {"version": 1, "dependencies": {}, "approvals": {}})
    return path


def validate_outline(outline):
    if [c["name"] for c in outline["candidates"]] != ["faithful", "balanced", "targeted"]:
        return ["Expected faithful, balanced, targeted in that order"]
    errors = []
    for candidate in outline["candidates"]:
        names = [s["title"] for s in candidate["sections"]]
        if len(set(names)) != len(names):
            errors.append("Section titles must be unique")
    return errors


def plan_preview(plan):
    lines = [f"{plan['role']} — {plan['company']}", "", "Required keywords:"]
    for req in plan["requirements"]:
        lines += [f"- {req['id']}: {req['keyword']} (section: {req['section']})",
                  f"  Job evidence: {req['job_quote']}"]
    lines += ["", "Unsupported qualifications / gaps:"] + [f"- {gap}" for gap in plan["gaps"]]
    return "\n".join(lines)


def outline_preview(outline):
    lines = []
    for candidate in outline["candidates"]:
        lines += [f"\n{candidate['name']}: {candidate['rationale']}"]
        for section in candidate["sections"]:
            lines += [f"  {section['title']}: " + "; ".join(section["entries"])]
    return "\n".join(lines)


def stage(project, state, backend, name, instruction, data, shape, checks,
          preview, ask=input, emit=print):
    path = project / f"{name}.json"
    dependency = digest(data)
    if path.exists() and state["dependencies"].get(name) == dependency:
        value = read_json(path)
        schema.validate(value, shape)
    else:
        value = None
    feedback = ""
    failures = 0
    while True:
        if value is None:
            emit(f"Drafting {name} with {backend.name}…")
            value = backend.ask(instruction, {**data, "revision_feedback": feedback}, shape)
            schema.validate(value, shape)
            history = project / "history"
            history.mkdir(exist_ok=True)
            write_json(history / f"{name}-{uuid.uuid4().hex[:8]}.json", value)
            write_json(path, value)
            state["dependencies"][name] = dependency
            write_json(project / "state.json", state)
        errors = checks(value)
        if errors:
            failures += 1
            if failures > 2:
                raise ValueError(f"{name} failed validation; edit {path} and resume: " + "; ".join(errors))
            feedback = "Correct these deterministic errors: " + json.dumps(errors)
            value = None
            continue
        if preview is None:
            return value
        approval = digest({"value": value, "inputs": dependency})
        if state["approvals"].get(name) == approval:
            return value
        emit(preview(value))
        response = ask(f"Approve {name}? [yes / revision instructions / quit]: ").strip()
        if response.lower() in ("yes", "y"):
            state["approvals"][name] = approval
            write_json(project / "state.json", state)
            return value
        if response.lower() in ("quit", "q", ""):
            raise InterruptedError(f"Saved. Resume with: uv run scrubber resume {project}")
        feedback, value = response, None


def draft_checks(draft, plan, context, outline):
    if [c["name"] for c in draft["candidates"]] != ["faithful", "balanced", "targeted"]:
        return ["Expected exactly faithful, balanced, targeted candidates"]
    errors = []
    for candidate, sketch in zip(draft["candidates"], outline["candidates"]):
        report = check_candidate(candidate, plan, context["master"], context["job"], sketch)
        errors += [f"{candidate['name']}: {e}" for e in report["errors"]]
    return errors


def comparison(draft):
    lines = ["# Candidate comparison", ""]
    for candidate in draft["candidates"]:
        lines += [f"## {candidate['name']}", ""]
        lines += [claim["text"] for claim in candidate["header"]]
        for section in candidate["sections"]:
            lines += ["", f"### {section['title']}", ""]
            for entry in section["entries"]:
                lines += [f"**{entry['title']['text']}** — {entry['detail']['text']}", ""]
                lines += [f"- {b['text']}" for b in entry["bullets"]]
        lines += ["", "### Cover letter", ""]
        for paragraph in candidate["cover_letter"]:
            lines += [paragraph["text"], ""]
    return "\n".join(lines) + "\n"


def claim_approval(candidate, context):
    return digest({"candidate": candidate, "inputs": context})


def run(project, backend, ask=input, emit=print, revise=None, lean=False):
    project = Path(project).resolve()
    context, state = read_json(project / "inputs.json"), read_json(project / "state.json")
    if lean:
        state["require_lean"] = True
        write_json(project / "state.json", state)
    lean = state.get("require_lean", False)
    write_json(project / "results.json", {"status": "in_progress"})
    if revise:
        state["dependencies"].pop(revise, None)
        state["approvals"].pop(revise, None)
        write_json(project / "state.json", state)
    plan = stage(project, state, backend, "plan", PLAN_PROMPT, context, schema.PLAN,
                 lambda p: check_plan(p, context["job"]), plan_preview, ask, emit)
    (project / "keywords.txt").write_text("\n".join(r["keyword"] for r in plan["requirements"]) + "\n", encoding="utf-8")
    outline = stage(project, state, backend, "outline", OUTLINE_PROMPT,
                    {**context, "plan": plan}, schema.OUTLINE, validate_outline, outline_preview, ask, emit)
    draft = stage(project, state, backend, "draft", DRAFT_PROMPT,
                  {**context, "plan": plan, "outline": outline}, schema.DRAFT,
                  lambda d: draft_checks(d, plan, context, outline), None, ask, emit)
    (project / "comparison.md").write_text(comparison(draft), encoding="utf-8")
    for candidate, sketch in zip(draft["candidates"], outline["candidates"]):
        name = candidate["name"]
        approval_key = f"claims:{name}"
        report = check_candidate(candidate, plan, context["master"], context["job"], sketch,
                                 state["approvals"].get(approval_key) == claim_approval(candidate, context))
        folder = project / name
        folder.mkdir(exist_ok=True)
        write_json(folder / "evidence.json", [{"path": p, **c} for p, c, _ in claims(candidate)])
        if report["review"]:
            emit(f"\n{name}: review factual support for these rewritten claims:")
            for item in report["review"]:
                emit(f"\n{item['path']}: {item['text']}")
                for evidence in item["evidence"]:
                    emit(f"  {evidence['source']}: {evidence['quote']}")
            response = ask("Do the cited sources support every rewritten assertion? [yes / no]: ").strip().lower()
            if response in ("yes", "y"):
                state["approvals"][approval_key] = claim_approval(candidate, context)
                write_json(project / "state.json", state)
            else:
                emit(f"{name} remains unapproved. Edit draft.json or resume with --revise draft.")
    return verify_project(project, lean=lean, emit=emit)


def verify_project(project, lean=False, emit=print):
    project = Path(project).resolve()
    context, state = read_json(project / "inputs.json"), read_json(project / "state.json")
    if lean:
        state["require_lean"] = True
        write_json(project / "state.json", state)
    lean = state.get("require_lean", False)
    write_json(project / "results.json", {"status": "in_progress"})
    plan, outline, draft = (read_json(project / f"{s}.json") for s in ("plan", "outline", "draft"))
    for value, shape in ((plan, schema.PLAN), (outline, schema.OUTLINE), (draft, schema.DRAFT)):
        schema.validate(value, shape)
    errors = check_plan(plan, context["job"]) + validate_outline(outline)
    dependencies = {"plan": context, "outline": {**context, "plan": plan},
                    "draft": {**context, "plan": plan, "outline": outline}}
    for name, data in dependencies.items():
        if state["dependencies"].get(name) != digest(data):
            errors.append(f"{name} inputs changed; resume to regenerate/reapprove")
    for name, value in (("plan", plan), ("outline", outline)):
        if state["approvals"].get(name) != digest({"value": value, "inputs": digest(dependencies[name])}):
            errors.append(f"{name} is not approved at its current contents")
    if [c["name"] for c in draft["candidates"]] != ["faithful", "balanced", "targeted"]:
        errors.append("Expected exactly three named candidates")
    if errors:
        raise ValueError("; ".join(errors))
    results = {}
    for candidate, sketch in zip(draft["candidates"], outline["candidates"]):
        folder = project / candidate["name"]
        folder.mkdir(exist_ok=True)
        write_json(folder / "verification.json", {"status": "pending", "reason": "Verification in progress"})
        (folder / "resume.tex").write_text(resume_tex(candidate, context["font_size"]), encoding="utf-8")
        (folder / "cover-letter.tex").write_text(letter_tex(candidate, context["font_size"]), encoding="utf-8")
        (folder / "cover-letter.md").write_text("\n\n".join(p["text"] for p in candidate["cover_letter"]) + "\n", encoding="utf-8")
        write_json(folder / "evidence.json", [{"path": p, **c} for p, c, _ in claims(candidate)])
        layout = compile_tex(folder / "resume.tex", context["max_pages"])
        letter_layout = compile_tex(folder / "cover-letter.tex", 1)
        report = check_candidate(candidate, plan, context["master"], context["job"], sketch,
            state["approvals"].get(f"claims:{candidate['name']}") == claim_approval(candidate, context), layout)
        report["layout"] = layout
        report["cover_letter_layout"] = letter_layout
        if not letter_layout["compiled"]:
            report["pending"].append("Cover letter PDF is unverified")
        elif letter_layout.get("pages", 0) != 1 or letter_layout.get("overflow"):
            report["errors"].append("Cover letter must fit one page without overflow")
        report["status"] = "fail" if report["errors"] else "review" if report["review"] else "pending" if report["pending"] else "pass"
        report["tree"]["status"] = report["status"]
        report["candidate_sha256"] = digest(candidate)
        (folder / "constraints.lean").write_text(lean_certificate(report, candidate, plan, context["job"]), encoding="utf-8")
        if lean:
            if not shutil.which("lean"):
                report["pending"].append("Lean requested but not installed")
                report["status"] = "pending" if report["status"] == "pass" else report["status"]
            else:
                result = subprocess.run(["lean", "constraints.lean"], cwd=folder,
                                        capture_output=True, text=True, timeout=60)
                report["lean"] = {"passed": result.returncode == 0, "output": result.stdout + result.stderr}
                if result.returncode:
                    report["errors"].append("Lean certificate failed")
                    report["status"] = "fail"
        report["tree"]["status"] = report["status"]
        write_json(folder / "verification.json", report)
        results[candidate["name"]] = report["status"]
        emit(f"{candidate['name']}: {report['status']} — {folder}")
        for message in report["errors"] + report["pending"]:
            emit(f"  {message}")
    write_json(project / "results.json", results)
    (project / "comparison.md").write_text(comparison(draft), encoding="utf-8")
    return results
