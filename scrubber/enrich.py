"""A measured editorial pass after initial verification, with saved provenance."""
from copy import deepcopy

from . import schema
from .render import FILL_TARGET, compile_tex, needs_expansion, resume_tex
from .verify import check_candidate, digest


EXPAND_PROMPT = """Improve this resume after its initial formal verification and layout measurement.
Fill the configured page budget with substantive, role-relevant detail from master/ evidence.
Aim for at least 90% of the last page, without exceeding max_pages or two rendered lines per
bullet. Use the measured layout and previous attempt feedback to budget your changes.
Prioritize the most relevant research/projects: methods, implementation, validation, and
supported outcomes. Turn bare technology lists into meaningful accomplishment bullets.
Expand existing bullets or append new bullets to approved entries; preserve the order and
meaning of existing bullets. Keep header, cover_letter, section order/titles, and entry titles
exactly unchanged. You may expand entry details. Do not delete existing bullets or repeat
content just to fill space. If an attempt overflows, shorten the additions before trying again.
Every added or rewritten claim must cite exact master/ substrings and every bullet must
contain its mapped approved job keywords. Never invent metrics, responsibilities, skills,
eligibility, motivation, or facts, and never complete placeholders such as [[microcontroller]].
For faithful, preserve source wording closely. Other candidates may emphasize transferable
skills with supported rephrasing. Return plain text only. The renderer colours changes;
do not supply colours or LaTeX. If no supported improvement is possible, return the current
candidate unchanged; the harness will report the remaining space as unresolved.
"""


def structure_errors(candidate, baseline):
    errors = []
    for key in ("name", "header", "cover_letter"):
        if candidate[key] != baseline[key]:
            errors.append(f"Expansion must preserve {key}")
    if [s["title"] for s in candidate["sections"]] != [s["title"] for s in baseline["sections"]]:
        return errors + ["Expansion must preserve sections"]
    for section, original in zip(candidate["sections"], baseline["sections"]):
        if [e["title"] for e in section["entries"]] != [e["title"] for e in original["entries"]]:
            errors.append("Expansion must preserve entry titles and order")
            continue
        for entry, old in zip(section["entries"], original["entries"]):
            if len(entry["bullets"]) < len(old["bullets"]):
                errors.append("Expansion must not delete existing bullets")
    return errors


def baseline_for(project, candidate):
    # Import here to avoid a workflow/enrichment import cycle.
    from .workflow import read_json
    path = project / "expansion.json"
    if path.exists():
        saved = read_json(path).get(candidate["name"], {})
        baseline = saved.get("baseline")
        if baseline and not structure_errors(candidate, baseline):
            return baseline
    return None


def expand_project(project, backend, draft, plan, outline, context, emit=print, attempts=4):
    from .workflow import read_json, write_json
    path = project / "expansion.json"
    records = read_json(path) if path.exists() else {}
    inputs_hash = digest({"context": context, "plan": plan, "outline": outline})
    for i, (candidate, sketch) in enumerate(zip(draft["candidates"], outline["candidates"])):
        name = candidate["name"]
        report = read_json(project / name / "verification.json")
        layout = report["layout"]
        previous = records.get(name, {})
        if (previous.get("inputs_sha256") == inputs_hash and
                previous.get("candidate_sha256") == digest(candidate) and
                previous.get("status") != "in_progress"):
            continue
        if not needs_expansion(layout):
            continue
        baseline = (deepcopy(previous["baseline"]) if
                    previous.get("inputs_sha256") == inputs_hash and
                    previous.get("candidate_sha256") == digest(candidate) else deepcopy(candidate))
        records[name] = {"baseline": baseline, "inputs_sha256": inputs_hash,
                         "initial_layout": layout, "attempts": [], "status": "in_progress"}
        write_json(path, records)
        emit(f"{name}: expanding role-relevant detail to fill {context['max_pages']} page(s) "
             f"(last page {layout['last_page_fill']:.0%}; target {FILL_TARGET:.0%})…")
        feedback, last_attempt = [], None
        for attempt in range(attempts):
            data = {**context, "plan": plan, "outline": sketch, "baseline": baseline,
                    "candidate": candidate, "layout": layout, "target_fill": FILL_TARGET,
                    "revision_feedback": feedback, "previous_attempt": last_attempt}
            proposed = backend.ask(EXPAND_PROMPT, data, schema.CANDIDATE)
            schema.validate(proposed, schema.CANDIDATE)
            if proposed == candidate:
                records[name]["status"] = "insufficient_supported_detail"
                break
            history = project / "history" / f"expansion-{name}-{digest(proposed)[:12]}"
            history.mkdir(parents=True, exist_ok=True)
            write_json(history / "candidate.json", proposed)
            errors = structure_errors(proposed, baseline)
            proposed_layout = None
            if not errors:
                (history / "resume.tex").write_text(resume_tex(
                    proposed, context["font_size"], context["master"], baseline), encoding="utf-8")
                proposed_layout = compile_tex(history / "resume.tex", context["max_pages"])
                checked = check_candidate(proposed, plan, context["master"], context["job"],
                                          sketch, layout=proposed_layout)
                errors += checked["errors"]
                if not proposed_layout.get("compiled") or "last_page_fill" not in proposed_layout:
                    errors.append("Expansion layout could not be measured")
                if not errors and not report["errors"]:
                    old_size = layout["pages"] - 1 + layout["last_page_fill"]
                    new_size = proposed_layout["pages"] - 1 + proposed_layout["last_page_fill"]
                    if new_size <= old_size:
                        errors.append("Expansion must add useful detail and increase occupied space")
            entry = {"attempt": attempt + 1, "candidate_sha256": digest(proposed),
                     "layout": proposed_layout, "errors": errors, "accepted": not errors,
                     "artifact": str(history.relative_to(project))}
            records[name]["attempts"].append(entry)
            write_json(history / "validation.json", entry)
            if not errors:
                candidate, layout = proposed, proposed_layout
                draft["candidates"][i] = candidate
                report = {"errors": []}
                # Save after each accepted pass so interruption never loses progress.
                records[name]["candidate_sha256"] = digest(candidate)
                write_json(path, records)
                write_json(project / "draft.json", draft)
                if not needs_expansion(layout):
                    records[name]["status"] = "filled"
                    break
                feedback = ["The resume still has space. Add further supported, relevant detail."]
            else:
                feedback = errors
                emit(f"{name}: expansion attempt {attempt + 1} needs revision: " + "; ".join(errors))
            last_attempt = proposed
            write_json(path, records)
        if records[name]["status"] == "in_progress":
            records[name]["status"] = "target_unmet"
        records[name]["candidate_sha256"] = digest(candidate)
        records[name]["final_layout"] = layout
        write_json(path, records)
        if records[name]["status"] != "filled":
            emit(f"{name}: page-fill target remains unresolved; see expansion.json.")
    return draft
