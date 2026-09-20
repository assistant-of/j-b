import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

MAX_BYTES = 2_000_000


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.hidden, self.password = [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
        if tag == "input" and dict(attrs).get("type", "").lower() == "password":
            self.password = True
        if tag in ("p", "div", "li", "br", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_text(raw):
    parser = PageText()
    parser.feed(raw)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip()), parser.password


def read_document(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path}: exceeds 2 MB input limit")
    if path.suffix.lower() == ".pdf":
        if not shutil.which("pdftotext"):
            raise RuntimeError("Install pdftotext (Poppler), or convert the PDF to Markdown.")
        result = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                                text=True, timeout=30)
        if result.returncode:
            raise ValueError(f"Cannot extract {path}: {result.stderr}")
        content = result.stdout
    elif path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo("word/document.xml").file_size > MAX_BYTES:
                raise ValueError("DOCX uncompressed text exceeds 2 MB")
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        content = "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))
    else:
        content = path.read_text(encoding="utf-8")
        if path.suffix.lower() in (".html", ".htm"):
            content, password = html_text(content)
            if password:
                raise ValueError("Saved HTML is a sign-in page; paste the job description instead.")
    if not content.strip():
        raise ValueError(f"{path}: no text extracted; scanned PDFs need OCR or copy-paste")
    return content


def collect(root, folder):
    result = {}
    base = Path(root) / folder
    for path in sorted(base.rglob("*")):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in (
                ".tex", ".md", ".txt", ".pdf", ".docx", ".html", ".htm"):
            result[path.relative_to(root).as_posix()] = read_document(path)
    if sum(map(len, result.values())) > 250_000:
        raise ValueError(f"{folder}/ has over 250,000 characters. Select fewer source materials.")
    return result


def fetch_job(url):
    if urllib.parse.urlsplit(url).scheme not in ("https", "http"):
        raise ValueError("Job URLs must use http or https")
    request = urllib.request.Request(url, headers={"User-Agent": "ECCScrubber/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
            final_url = response.url
            charset = response.headers.get_content_charset() or "utf-8"
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError("Listing inaccessible. Paste the full job description instead.") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError("Page exceeds 2 MB. Paste just the description.")
    text, password = html_text(raw.decode(charset, errors="replace"))
    if password or len(text) < 200 or re.search(r"signin|sign-in|/login|/sso", final_url, re.I):
        raise ValueError("Portal requires sign-in or returned no listing. Paste the full job description instead.")
    return text
