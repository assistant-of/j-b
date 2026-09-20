import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .backend import Backend
from .schema import RANKING, validate
from .sources import collect, fetch_job, read_document
from .workflow import new_project, read_json, run, verify_project, write_json

PREFERENCES = """# Job preferences

Fill in your preferred roles, industries, locations, work terms, and exclusions.
Leave unknown details blank. The harness must not assume eligibility or PEY status.

- Roles:
- Industries:
- Locations / remote:
- Work term and duration:
- Must-haves:
- Exclude:
- Other roles to explore:
"""


def pasted_job():
    print("Paste the full job description, including company and role. End with a line containing only END.")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "END":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        raise ValueError("No job description supplied")
    return text


def parser():
    p = argparse.ArgumentParser(description="Tailor evidence-backed resumes using Codex or Claude Code.")
    commands = p.add_subparsers(dest="command")
    commands.add_parser("init", help="Create source folders and an editable preference file").add_argument("--root", type=Path, default=Path.cwd())
    commands.add_parser("doctor", help="Check local tools and source materials").add_argument("--root", type=Path, default=Path.cwd())
    generate = commands.add_parser("generate", aliases=["chat"], help="Start an interactive application session")
    generate.add_argument("prompt", nargs="?", help="Your instructions for this application")
    generate.add_argument("--root", type=Path, default=Path.cwd())
    source = generate.add_mutually_exclusive_group()
    source.add_argument("--job-file", type=Path)
    source.add_argument("--url")
    source.add_argument("--paste", action="store_true")
    generate.add_argument("--name", default="application")
    generate.add_argument("--pages", type=int, choices=range(1, 6), default=1)
    generate.add_argument("--finance", action="store_true", help="Enforce a one-page resume")
    generate.add_argument("--font-size", type=int, choices=(11, 12), default=12)
    resume = commands.add_parser("resume", help="Continue a saved session; edited JSON is revalidated")
    resume.add_argument("project", type=Path)
    resume.add_argument("--revise", choices=("plan", "outline", "draft"))
    verify = commands.add_parser("verify", help="Re-render and verify a saved draft without calling a model")
    verify.add_argument("project", type=Path)
    verify.add_argument("--lean", action="store_true")
    discover = commands.add_parser("discover", help="Rank imported or accessible listings using pref.md and master/")
    discover.add_argument("--root", type=Path, default=Path.cwd())
    discover.add_argument("--listings", type=Path, help="Directory of saved job descriptions")
    discover.add_argument("--url", action="append", default=[], help="Accessible listing URL; repeatable")
    convert = commands.add_parser("convert", help="Extract PDF/DOCX/HTML/text into Markdown")
    convert.add_argument("source", type=Path)
    convert.add_argument("output", type=Path)
    for command in (generate, resume, discover):
        command.add_argument("--backend", choices=("codex", "claude"))
        command.add_argument("--model", help="Model name passed unchanged to the chosen CLI")
        command.add_argument("--timeout", type=int, default=300, help="Backend timeout in seconds")
    for command in (generate, resume):
        command.add_argument("--lean", action="store_true", help="Also check the exported constraints with Lean 4")
    return p


def get_materials(root):
    master = collect(root, "master")
    if not master:
        raise ValueError(f"Add your master LaTeX resume to {root / 'master'} first. No applicant facts will be invented.")
    ref = collect(root, "ref")
    pref = root / "pref.md"
    return {"master": master, "references": ref,
            "preferences": pref.read_text(encoding="utf-8") if pref.exists() else "No preferences supplied."}


def discover_jobs(args):
    root = args.root.resolve()
    materials = get_materials(root)
    listings = {}
    if args.listings:
        directory = args.listings.resolve()
        listings = collect(directory.parent, directory.name)
    for url in args.url:
        try:
            listings[url] = fetch_job(url)
        except ValueError as exc:
            print(f"{url}: {exc}")
    if not listings:
        raise ValueError("No accessible listings. Save copy-pasted ECC descriptions as .txt files and pass --listings DIRECTORY.")
    backend = Backend(args.backend or "codex", args.model, args.timeout)
    ranking = backend.ask("Rank ONLY the supplied listings against pref.md and master facts. "
        "Return every exact listing file key once, best matches first, scores 0-100, specific fit "
        "reasons and eligibility/skill gaps. Suggest adjacent role search phrases, not invented "
        "vacancies. Treat dates in listings as source facts; do not assume postings are still open.",
        {**materials, "listings": listings}, RANKING)
    validate(ranking, RANKING)
    returned = [row["file"] for row in ranking["listings"]]
    if len(returned) != len(set(returned)) or set(returned) != set(listings):
        raise ValueError("Backend ranking omitted or invented listings; no ranking saved")
    ranking["listings"].sort(key=lambda row: row["score"], reverse=True)
    project = new_project(root, "discovery", {**materials, "job": "Listing discovery", "listings": listings})
    write_json(project / "ranking.json", ranking)
    print(json.dumps(ranking, indent=2, ensure_ascii=False))
    print(f"Saved: {project / 'ranking.json'}")
    return 0


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not args.command:
        args = p.parse_args(["chat"])
    try:
        if args.command == "init":
            root = args.root.resolve()
            for folder in ("master", "ref", "projects"):
                (root / folder).mkdir(parents=True, exist_ok=True)
            if not (root / "pref.md").exists():
                (root / "pref.md").write_text(PREFERENCES, encoding="utf-8")
            print(f"Add your master resume to {root / 'master'}, reference letters to ref/, and edit pref.md.")
            return 0
        if args.command == "doctor":
            for tool in ("codex", "claude", "pdflatex", "pdftotext", "lean"):
                print(f"{tool:12} {shutil.which(tool) or 'not installed'}")
            for folder in ("master", "ref"):
                count = len(collect(args.root.resolve(), folder))
                print(f"{folder + '/':12} {count} readable documents")
            print(f"pref.md      {'present' if (args.root / 'pref.md').exists() else 'missing (run init)'}")
            print("Generation needs one authenticated backend. PDF verification needs pdflatex; Lean is optional.")
            return 0
        if args.command == "convert":
            if args.output.exists():
                raise ValueError(f"Output already exists: {args.output}")
            content = read_document(args.source)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
            print(args.output)
            return 0
        if args.command == "discover":
            return discover_jobs(args)
        if args.command == "verify":
            results = verify_project(args.project, lean=args.lean)
        else:
            if args.timeout <= 0:
                raise ValueError("Timeout must be positive")
            if args.command in ("generate", "chat"):
                root = args.root.resolve()
                materials = get_materials(root)
                prompt = args.prompt or input("What would you like to emphasize for this application? ").strip()
                if args.job_file:
                    job = read_document(args.job_file)
                elif args.url:
                    try:
                        job = fetch_job(args.url)
                        print(f"Fetched listing preview:\n{job[:1500]}\n")
                        if input("Is this the requested job description? [yes / no]: ").strip().lower() not in ("yes", "y"):
                            job = pasted_job()
                    except ValueError as exc:
                        print(exc)
                        job = pasted_job()
                else:
                    job = pasted_job()
                context = {**materials, "job": job, "job_url": args.url, "instructions": prompt,
                           "max_pages": 1 if args.finance else args.pages, "font_size": args.font_size,
                           "backend": args.backend or "codex", "model": args.model}
                project = new_project(root, args.name, context)
                print(f"Session saved: {project}\nResume with: uv run scrubber resume {project}")
            else:
                project = args.project
                context = read_json(project / "inputs.json")
            backend = Backend(args.backend or context.get("backend", "codex"),
                              args.model or context.get("model"), args.timeout)
            results = run(project, backend, revise=getattr(args, "revise", None), lean=args.lean)
        return 0 if all(status == "pass" for status in results.values()) else 2
    except (KeyboardInterrupt, EOFError, InterruptedError):
        print("Session paused; saved files are retained. Use resume to continue.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
