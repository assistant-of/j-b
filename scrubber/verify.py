"""Deterministic evidence/coverage checks; no semantic truth claims."""
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize(text):
    return " ".join(text.split()).casefold()


def contains(text, phrase):
    return re.search(r"(?<!\w)" + re.escape(normalize(phrase)) + r"(?!\w)", normalize(text)) is not None


def check_plan(plan, job):
    errors, ids = [], set()
    for req in plan["requirements"]:
        if req["id"] in ids:
            errors.append(f"Duplicate requirement {req['id']}")
        ids.add(req["id"])
        if req["job_quote"] not in job:
            errors.append(f"{req['id']}: job quote is not an exact source substring")
        if not contains(req["job_quote"], req["keyword"]):
            errors.append(f"{req['id']}: keyword is absent from its job quote")
    for key in ("company", "role"):
        if not contains(job, plan[key]):
            errors.append(f"{key} must appear verbatim in the job description")
    return errors


def claims(candidate):
    for i, claim in enumerate(candidate["header"]):
        yield f"header/{i}", claim, False
    for s, section in enumerate(candidate["sections"]):
        for e, entry in enumerate(section["entries"]):
            for field in ("title", "detail"):
                yield f"section/{s}/entry/{e}/{field}", entry[field], False
            for b, bullet in enumerate(entry["bullets"]):
                yield f"section/{s}/entry/{e}/bullet/{b}", bullet, True
    for p, paragraph in enumerate(candidate["cover_letter"]):
        yield f"cover/{p}", paragraph, False


def check_candidate(candidate, plan, sources, job, outline, approved=False, layout=None):
    requirements = {r["id"]: r for r in plan["requirements"]}
    errors, review, nodes, covered = [], [], [], set()
    expected_sections = [s["title"] for s in outline["sections"]]
    if [s["title"] for s in candidate["sections"]] != expected_sections:
        errors.append("Section order/titles differ from the approved outline")
    if candidate["name"] != outline["name"]:
        errors.append("Candidate name differs from the approved outline")
    for section, expected in zip(candidate["sections"], outline["sections"]):
        if [e["title"]["text"] for e in section["entries"]] != expected["entries"]:
            errors.append(f"{section['title']}: entries differ from the approved outline")
    bullet_index = 0
    for path, claim, bullet in claims(candidate):
        node_errors, node_review = [], []
        quotations = []
        for evidence in claim["evidence"]:
            source, quote = evidence["source"], evidence["quote"]
            corpus = job if source == "job.txt" and path.startswith("cover/") else sources.get(source)
            if corpus is None or (not source.startswith("master/") and source != "job.txt"):
                node_errors.append(f"Source {source} cannot establish facts here")
            elif quote not in corpus:
                node_errors.append(f"Quote not found verbatim in {source}")
            else:
                quotations.append(quote)
        if normalize(claim["text"]) not in [normalize(q) for q in quotations]:
            node_review.append("Reworded/composed claim: confirm the cited evidence supports every assertion")
        numbers = re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?:%|\+)?", claim["text"])
        evidence_numbers = re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?:%|\+)?", " ".join(quotations))
        if any(number not in evidence_numbers for number in numbers):
            node_errors.append("Contains a number absent from its cited evidence")
        if bullet:
            if not claim["requirements"]:
                node_errors.append("Every bullet must map to at least one job keyword")
            if candidate["name"] == "faithful" and normalize(claim["text"]) not in [normalize(q) for q in quotations]:
                node_errors.append("Faithful bullets must copy a source quote verbatim")
            for rid in claim["requirements"]:
                req = requirements.get(rid)
                if req is None:
                    node_errors.append(f"Unknown requirement {rid}")
                elif not contains(claim["text"], req["keyword"]):
                    node_errors.append(f"Missing mapped keyword: {req['keyword']}")
                else:
                    section_title = candidate["sections"][int(path.split('/')[1])]["title"]
                    if req["section"] != "any" and normalize(req["section"]) != normalize(section_title):
                        node_errors.append(f"{rid} must appear in section {req['section']}")
                    else:
                        covered.add(rid)
            if layout and layout.get("compiled"):
                lines = layout.get("bullet_lines", {}).get(str(bullet_index))
                if lines is None or lines < 1 or lines > 2:
                    node_errors.append(f"Rendered bullet has {lines} lines (maximum 2)")
            bullet_index += 1
        errors.extend(f"{path}: {e}" for e in node_errors)
        if node_review and not approved:
            review.append({"path": path, "text": claim["text"], "evidence": claim["evidence"],
                           "reason": node_review[0]})
        node = {"path": path, "errors": node_errors,
                "status": "fail" if node_errors else "review" if node_review and not approved else "pass"}
        if bullet:
            node["keyword_checks"] = [{"requirement": rid,
                "keyword": requirements[rid]["keyword"] if rid in requirements else None,
                "in_bullet": rid in requirements and contains(claim["text"], requirements[rid]["keyword"]),
                "in_job": rid in requirements and contains(job, requirements[rid]["keyword"])}
                for rid in claim["requirements"]]
        nodes.append(node)
    missing = sorted(set(requirements) - covered)
    if missing:
        errors.append(f"Uncovered required job keywords: {', '.join(missing)}")
    if not bullet_index:
        errors.append("Resume must contain at least one bullet")
    pending = []
    if not layout or not layout.get("compiled"):
        pending.append("LaTeX page count and rendered bullet line counts are unverified")
    else:
        if layout.get("pages", 0) < 1 or layout["pages"] > layout["max_pages"]:
            errors.append(f"Resume has {layout.get('pages')} pages; limit is {layout['max_pages']}")
        if layout.get("overflow"):
            errors.append("LaTeX reports overflowing content")
    section_nodes = []
    for s, section in enumerate(candidate["sections"]):
        children = [n for n in nodes if n["path"].startswith(f"section/{s}/")]
        status = "fail" if any(n["status"] == "fail" for n in children) else "review" if any(n["status"] == "review" for n in children) else "pass"
        section_nodes.append({"title": section["title"], "status": status, "children": children})
    status = "fail" if errors else "review" if review else "pending" if pending else "pass"
    return {"status": status, "errors": errors, "review": review, "pending": pending,
            "covered_requirements": sorted(covered), "tree": {"objective": "Build a supported, tailored resume and letter",
            "status": status, "sections": section_nodes,
            "other_claims": [n for n in nodes if not n["path"].startswith("section/")]}}


def lean_certificate(report, candidate, plan, job):
    """Recompute literal keyword coverage in Lean; other checks remain measurements.

    Text is encoded as normalized Unicode codepoints, avoiding executable string
    interpolation. The emitted word-character table reproduces Python's Unicode
    regex boundaries for the finite input alphabet.
    """
    bits = [not report["errors"], not report["review"], not report["pending"]]
    terms = ", ".join("true" if bit else "false" for bit in bits)
    requirements = {r["id"]: normalize(r["keyword"]) for r in plan["requirements"]}
    bullets = [(normalize(claim["text"]), [requirements.get(rid, "") for rid in claim["requirements"]])
               for _, claim, is_bullet in claims(candidate) if is_bullet]
    job = normalize(job)
    alphabet = set(job + "".join(requirements.values()) + "".join(text for text, _ in bullets))
    word_chars = sorted(ord(c) for c in alphabet if re.fullmatch(r"\w", c))

    def codes(text):
        return "[" + ", ".join(str(ord(c)) for c in text) + "]"

    out = ["""-- Exact keyword membership is recomputed below, not imported as a Boolean.
-- Trust boundary: Python normalization, Unicode word classification and input serialization.
-- Other layout/evidence outcomes and human reviews are recorded measurements.
-- This certificate does not establish real-world facts or semantic entailment.
set_option maxRecDepth 100000
set_option maxHeartbeats 4000000

def prefixAtBoundary (wordChars : List Nat) : List Nat → List Nat → Bool
  | [], [] => true
  | [], c :: _ => !(wordChars.contains c)
  | _ :: _, [] => false
  | k :: ks, c :: cs => (k == c) && prefixAtBoundary wordChars ks cs

def scanKeyword (wordChars keyword : List Nat) (previousWord : Bool) : List Nat → Bool
  | [] => false
  | c :: cs =>
      ((!previousWord) && prefixAtBoundary wordChars keyword (c :: cs)) ||
      scanKeyword wordChars keyword (wordChars.contains c) cs

def hasKeyword (wordChars text keyword : List Nat) : Bool :=
  (!keyword.isEmpty) && scanKeyword wordChars keyword false text

structure Bullet where
  text : List Nat
  keywords : List (List Nat)

def bulletMatches (wordChars job : List Nat) (approved : List (List Nat)) (b : Bullet) : Bool :=
  (!b.keywords.isEmpty) && b.keywords.all (fun k =>
    approved.contains k && hasKeyword wordChars b.text k && hasKeyword wordChars job k)
"""]
    out += [f"def wordChars : List Nat := {word_chars}", f"def jobText : List Nat := {codes(job)}",
            "def approvedKeywords : List (List Nat) := [" + ", ".join(codes(k) for k in requirements.values()) + "]"]
    names = []
    for i, (text, keywords) in enumerate(bullets):
        name = f"bullet_{i}"
        names.append(name)
        out += [f"def {name} : Bullet := ⟨{codes(text)}, [" + ", ".join(codes(k) for k in keywords) + "]⟩",
                f"theorem {name}_has_job_keyword : bulletMatches wordChars jobText approvedKeywords {name} = true := by decide"]
    out += ["def bullets : List Bullet := [" + ", ".join(names) + "]",
            "theorem every_bullet_has_job_keyword :",
            "    ((!bullets.isEmpty) && bullets.all (bulletMatches wordChars jobText approvedKeywords)) = true := by decide",
            f"def checks : List Bool := [{terms}]",
            "theorem recorded_constraints_hold : checks.all id = true := by decide"]
    return "\n".join(out) + "\n"
