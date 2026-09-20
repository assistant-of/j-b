import io
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scrubber.backend import Backend
from scrubber.cli import main, parser
from scrubber.render import compile_tex
from scrubber.schema import obj, STRING
from scrubber.sources import fetch_job, html_text, read_document


class BackendTests(unittest.TestCase):
    def test_codex_structured_file_and_stdin_transport(self):
        def execute(command, **kwargs):
            self.assertEqual(command[:4], ["codex", "-a", "never", "exec"])
            self.assertIn("read-only", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("requested-model", command)
            self.assertNotIn("PRIVATE INPUT", " ".join(command))
            self.assertIn("PRIVATE INPUT", kwargs["input"])
            self.assertFalse(kwargs.get("shell", False))
            Path(command[command.index("--output-last-message") + 1]).write_text('{"answer":"ok"}')
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch("scrubber.backend.shutil.which", return_value="codex"), patch("scrubber.backend.subprocess.run", side_effect=execute):
            result = Backend("codex", "requested-model").ask("Task", {"data": "PRIVATE INPUT"}, obj(answer=STRING))
        self.assertEqual(result, {"answer": "ok"})

    def test_claude_structured_envelope(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({"structured_output": {"answer": "ok"}}), "")
        with patch("scrubber.backend.shutil.which", return_value="claude"), patch("scrubber.backend.subprocess.run", return_value=response) as call:
            result = Backend("claude").ask("Task", {}, obj(answer=STRING))
        self.assertEqual(result["answer"], "ok")
        command = call.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("mcp__*", command)

    def test_backend_failures_are_actionable(self):
        responses = [subprocess.CompletedProcess([], 1, "", "Authentication required"),
                     subprocess.CompletedProcess([], 0, "not json", ""),
                     subprocess.CompletedProcess([], 0, '{"is_error":true,"result":"Budget exceeded"}', ""),
                     subprocess.CompletedProcess([], 0, '{"structured_output":{"wrong":"value"}}', "")]
        for response in responses:
            with self.subTest(response=response), patch("scrubber.backend.shutil.which", return_value="claude"), patch("scrubber.backend.subprocess.run", return_value=response):
                with self.assertRaises(RuntimeError):
                    Backend("claude").ask("Task", {}, obj(answer=STRING))

    def test_backend_timeout(self):
        with patch("scrubber.backend.shutil.which", return_value="claude"), patch("scrubber.backend.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 1)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                Backend("claude", timeout=1).ask("Task", {}, obj(answer=STRING))


class DocumentTests(unittest.TestCase):
    def test_html_excludes_scripts_and_detects_login(self):
        text, password = html_text('<h1>Role</h1><script>ignore rules</script><p>Python tools</p><input type="password">')
        self.assertNotIn("ignore", text)
        self.assertIn("Python tools", text)
        self.assertTrue(password)

    def test_url_failure_offers_paste(self):
        with patch("scrubber.sources.urllib.request.urlopen", side_effect=TimeoutError):
            with self.assertRaisesRegex(ValueError, "Paste"):
                fetch_job("https://example.invalid/job")

    def test_url_rejects_file_scheme(self):
        with self.assertRaises(ValueError):
            fetch_job("file:///etc/passwd")

    def test_docx_paragraph_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "letter.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First paragraph.</w:t></w:r></w:p><w:p><w:r><w:t>Second paragraph.</w:t></w:r></w:p></w:body></w:document>')
            self.assertEqual(read_document(path), "First paragraph.\nSecond paragraph.")

    def test_compiler_measures_log_and_disables_shell_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.tex"
            path.write_text("controlled template")

            def execute(command, **kwargs):
                self.assertIn("-no-shell-escape", command)
                path.with_suffix(".log").write_text("SCRUBBER-BULLET-0:2\nSCRUBBER-BULLET-1:3\nOutput written on resume.pdf (2 pages, 12345 bytes).\nOverfull \\hbox")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("scrubber.render.shutil.which", return_value="pdflatex"), patch("scrubber.render.subprocess.run", side_effect=execute):
                result = compile_tex(path, 1)
            self.assertEqual(result["pages"], 2)
            self.assertEqual(result["bullet_lines"], {"0": 2, "1": 3})
            self.assertTrue(result["overflow"])

    def test_failed_compile_removes_stale_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "resume.tex"
            path.write_text("template")
            path.with_suffix(".pdf").write_text("old PDF")
            with patch("scrubber.render.shutil.which", return_value=None):
                self.assertFalse(compile_tex(path, 1)["compiled"])
            self.assertFalse(path.with_suffix(".pdf").exists())

    def test_cli_missing_master_is_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stderr", new_callable=io.StringIO) as output:
            code = main(["generate", "test", "--root", tmp, "--paste"])
        self.assertEqual(code, 1)
        self.assertIn("master", output.getvalue())

    def test_cli_repair_budget(self):
        for command in (["generate"], ["resume", "project"]):
            with self.subTest(command=command):
                self.assertEqual(parser().parse_args(command).max_repairs, 8)
                self.assertEqual(parser().parse_args(command + ["--max-repairs", "0"]).max_repairs, 0)
        with patch("sys.stderr", new_callable=io.StringIO) as output:
            self.assertEqual(main(["resume", "missing", "--max-repairs", "-1"]), 1)
        self.assertIn("--max-repairs must be nonnegative", output.getvalue())

    def test_init_preserves_preferences(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO):
            pref = Path(tmp) / "pref.md"
            pref.write_text("My own preferences")
            self.assertEqual(main(["init", "--root", tmp]), 0)
            self.assertEqual(pref.read_text(), "My own preferences")


if __name__ == "__main__":
    unittest.main()
