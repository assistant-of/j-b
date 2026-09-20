import json
import re
import shutil
import subprocess
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from . import schema
from .enrich import baseline_for, expand_project
from .progress import progress
from .render import FILL_TARGET, compile_tex, letter_tex, needs_expansion, resume_tex
from .text import plain_quote
from .verify import check_candidate, check_plan, claims, digest, lean_certificate

PLAN_PROMPT = """Extract exact company and role strings from the posting. Propose 4-10 achievable
keyword requirements, each with a unique id, an exact keyword and exact job_quote.
section is 'any' or an exact proposed resume section name. Requirements are mandatory
and checked by literal case-insensitive keyword matching in bullets in that section.
Choose requirements that the master actually supports, including transferable skills.
List all important unsupported job qualifications in gaps, never invent them or hide them.
Faithful source bullets must also support the approved requirements with only light editing.
Respect the user's preferences and requested role. Do not include layout rules as keywords."""

OUTLINE_PROMPT = """Propose exactly three candidates named faithful, balanced, targeted, in that order.
Each outline lists sections in order and exact plain-text entry titles that will appear in the resume.
Faithful keeps source section/entry order and stays close to source bullet wording, allowing light editing.
Balanced selectively reorders and rephrases. Targeted emphasizes the strongest supported role fit.
Keep the structure close to the master LaTeX resume. Explain each candidate's tradeoff.
All approved requirements must be achievable. Budget space for the configured pages and font.
Select enough substantive experience and project entries to fill the page budget. Plan for
2-4 useful bullets in the strongest entries; avoid empty experience entries and token lists.
Use 'Education', 'Experience', 'Projects', 'Skills' where appropriate, but do not create empty sections."""

DRAFT_PROMPT = """Build exactly the three approved candidates, preserving names, section order,
section titles and entry titles exactly. Every header, entry title, entry detail and bullet is
a claim citing exact master/ source quotes. Keep faithful wording close to the source: common LaTeX
formatting such as \\textbf{...} and \\emph{...} may be removed from bullet text, but evidence.quote
must retain the exact raw source, including markup. Light edits for clarity, length or supported
job terminology are allowed in faithful bullets; preserve meaning and do not add assertions.
Reworded claims require evidence review. For custom macros or math, use plain-text wording.
Never add unsupported facts.
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

DRAFT_PROMPT += """\nProduce a complete resume, not a minimal keyword checklist. Use the available
page budget: typically 2-4 substantive bullets per relevant experience/project. Each should
explain an action, method or implementation, and a supported result where available. Avoid
bare technology lists as bullets and unexplained experience entries. A measured editorial
pass will add supported detail after the first verification if the pages remain sparse."""

REPAIR_PROMPT = """Repair only the claims listed in failed_claims. Return a replacement claim
for each repair id in the requested schema. All other draft content will be preserved.
Use previous_value and the approved outline for context. Each task includes its errors,
current evidence, plain-text quote previews and mapped keyword requirements.
Evidence quotes must remain exact raw master/ source substrings (job.txt is allowed only
for cover-letter company/role facts). Faithful bullets should stay close to source wording;
light edits for clarity, length or supported job terminology are allowed without changing
meaning or adding assertions. Choose a different supported quote if needed. Keep required
keywords and section coverage, approved entry titles, and the original drafting constraints.
"""


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


DEFAULT_MAX_REPAIRS = 8


def stage(project, state, backend, name, instruction, data, shape, checks,
          preview, ask=input, emit=print, repair=None, max_repairs=DEFAULT_MAX_REPAIRS):
    if max_repairs < 0:
        raise ValueError("max_repairs must be nonnegative (0 means retry until valid)")
    path = project / f"{name}.json"
    dependency = digest(data)
    if path.exists() and state["dependencies"].get(name) == dependency:
        value = read_json(path)
        schema.validate(value, shape)
    else:
        value = None
    feedback = ""
    previous_value = None
    failures = 0
    while True:
        if value is None:
            emit(f"Drafting {name} with {backend.name}…")
            request = {**data, "revision_feedback": feedback}
            if previous_value is not None:
                request["previous_value"] = previous_value
            if repair and previous_value is not None:
                value = repair(backend, instruction, request)
            else:
                value = backend.ask(instruction, request, shape)
            schema.validate(value, shape)
            history = project / "history"
            history.mkdir(exist_ok=True)
            write_json(history / f"{name}-{uuid.uuid4().hex[:8]}.json", value)
            write_json(path, value)
            state["dependencies"][name] = dependency
            write_json(project / "state.json", state)
        errors = checks(value)
        if errors:
            write_json(project / f"{name}-errors.json", {"errors": errors})
            if name == "draft":
                (project / "comparison.md").write_text(comparison(value), encoding="utf-8")
            failures += 1
            if max_repairs and failures > max_repairs:
                raise ValueError(f"{name} failed validation after {max_repairs} repair attempts; "
                                 f"saved {path}. Resume to retry automatically, optionally with "
                                 "--max-repairs 0 to retry until valid: " + "; ".join(errors))
            limit = str(max_repairs) if max_repairs else "unlimited"
            emit(f"{name} validation failed; repairing automatically ({failures}/{limit}):")
            for error in errors:
                emit(f"  {error}")
            feedback = "Correct these deterministic errors: " + json.dumps(errors)
            previous_value = value
            value = None
            continue
        (project / f"{name}-errors.json").unlink(missing_ok=True)
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
        feedback, previous_value, value = response, value, None
        failures = 0


def repair_draft(backend, instruction, data):
    """Use a closed patch schema for local errors; regenerate for global errors."""
    draft = data["previous_value"]
    tasks, targets, fields = [], [], {}
    if [c["name"] for c in draft["candidates"]] != ["faithful", "balanced", "targeted"]:
        return backend.ask(instruction, data, schema.DRAFT)
    repaired = deepcopy(draft)
    requirements = {r["id"]: r for r in data["plan"]["requirements"]}
    for candidate, sketch in zip(repaired["candidates"], data["outline"]["candidates"]):
        report = check_candidate(candidate, data["plan"], data["master"], data["job"], sketch)
        nodes = report["tree"]["other_claims"] + [
            node for section in report["tree"]["sections"] for node in section["children"]]
        local_errors = {f"{node['path']}: {error}" for node in nodes for error in node["errors"]}
        if set(report["errors"]) - local_errors:
            # Coverage and outline errors can require changing more than one claim.
            return backend.ask(instruction, data, schema.DRAFT)
        by_path = {node["path"]: node["errors"] for node in nodes if node["errors"]}
        for path, claim, bullet in claims(candidate):
            if path not in by_path:
                continue
            key = f"repair_{len(tasks)}"
            tasks.append({"id": key, "candidate": candidate["name"], "path": path,
                          "claim": deepcopy(claim), "errors": by_path[path],
                          "requirements": [requirements.get(rid, {"id": rid})
                                           for rid in claim.get("requirements", [])],
                          "source_quotes": [{**e, "plain_text": plain_quote(e["quote"], e["source"])}
                                            for e in claim["evidence"]]})
            fields[key] = schema.BULLET if bullet else schema.CLAIM
            targets.append((key, claim))
    if not tasks:
        return backend.ask(instruction, data, schema.DRAFT)
    shape = schema.obj(**fields)
    patches = backend.ask(instruction + "\n" + REPAIR_PROMPT,
                          {**data, "failed_claims": tasks}, shape)
    schema.validate(patches, shape)
    for key, claim in targets:
        claim.clear()
        claim.update(deepcopy(patches[key]))
    return repaired


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


def run(project, backend, ask=input, emit=print, revise=None, lean=False,
        max_repairs=DEFAULT_MAX_REPAIRS):
    if max_repairs < 0:
        raise ValueError("max_repairs must be nonnegative (0 means retry until valid)")
    project = Path(project).resolve()
    context, state = read_json(project / "inputs.json"), read_json(project / "state.json")
    if lean:
        state["require_lean"] = True
        write_json(project / "state.json", state)
    lean = state.get("require_lean", False)
    write_json(project / "results.json", {"status": "in_progress"})
    if revise:
        if (project / "expansion.json").exists():
            history = project / "history"
            history.mkdir(exist_ok=True)
            shutil.copyfile(project / "expansion.json", history / f"expansion-{uuid.uuid4().hex[:8]}.json")
            (project / "expansion.json").unlink()
        state["dependencies"].pop(revise, None)
        state["approvals"].pop(revise, None)
        write_json(project / "state.json", state)
    plan = stage(project, state, backend, "plan", PLAN_PROMPT, context, schema.PLAN,
                 lambda p: check_plan(p, context["job"]), plan_preview, ask, emit,
                 max_repairs=max_repairs)
    (project / "keywords.txt").write_text("\n".join(r["keyword"] for r in plan["requirements"]) + "\n", encoding="utf-8")
    outline = stage(project, state, backend, "outline", OUTLINE_PROMPT,
                    {**context, "plan": plan}, schema.OUTLINE, validate_outline, outline_preview, ask, emit,
                    max_repairs=max_repairs)
    draft = stage(project, state, backend, "draft", DRAFT_PROMPT,
                  {**context, "plan": plan, "outline": outline}, schema.DRAFT,
                  lambda d: draft_checks(d, plan, context, outline), None, ask, emit, repair=repair_draft,
                  max_repairs=max_repairs)
    (project / "comparison.md").write_text(comparison(draft), encoding="utf-8")

    # Materialize an inspectable draft before asking for claim-by-claim review.
    # This keeps resume.tex available if the user pauses or closes the session
    # at that review prompt. The final verification pass below updates reports
    # after approvals are recorded.
    verify_project(project, lean=lean, emit=emit)

    draft = expand_project(project, backend, draft, plan, outline, context, emit)
    # Re-render and verify expanded claims before the user reviews the concrete PDF.
    if (project / "expansion.json").exists():
        verify_project(project, lean=lean, emit=emit)

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
        (folder / "resume.tex").write_text(resume_tex(candidate, context["font_size"],
            context["master"], baseline_for(project, candidate)), encoding="utf-8")
        (folder / "cover-letter.tex").write_text(letter_tex(candidate, context["font_size"]), encoding="utf-8")
        (folder / "cover-letter.md").write_text("\n\n".join(p["text"] for p in candidate["cover_letter"]) + "\n", encoding="utf-8")
        write_json(folder / "evidence.json", [{"path": p, **c} for p, c, _ in claims(candidate)])
        layout = compile_tex(folder / "resume.tex", context["max_pages"])
        letter_layout = compile_tex(folder / "cover-letter.tex", 1)
        report = check_candidate(candidate, plan, context["master"], context["job"], sketch,
            state["approvals"].get(f"claims:{candidate['name']}") == claim_approval(candidate, context), layout)
        report["layout"] = layout
        report["page_fill"] = {"target": FILL_TARGET, "needs_expansion": needs_expansion(layout)}
        if needs_expansion(layout):
            report["pending"].append(f"Resume page-fill target unmet: {layout['pages']}/{layout['max_pages']} "
                f"pages, last page {layout['last_page_fill']:.0%}; target {FILL_TARGET:.0%}. "
                "Run resume for expansion; if already attempted, inspect expansion.json or use --revise draft.")
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
                with progress(f"Checking Lean certificate for {candidate['name']}"):
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
