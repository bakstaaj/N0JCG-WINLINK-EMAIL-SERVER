#!/usr/bin/env python3
"""Safe storage and parsing for the Winlink Standard Forms library.

The official library contains HTML/JavaScript forms.  WES stores those files
for provenance and future sandboxed rendering, but this module only parses the
plain-text .txt descriptors for the first interoperable compose experience.
"""

import datetime as _datetime
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


STANDARD_FORMS_URL = "https://downloads.winlink.org/User%20Programs/Standard_Forms.zip"
TEMPLATES_ROOT = Path(os.environ.get("N0JCG_TEMPLATES_DIR", "/var/lib/n0jcg-winlink-webmail/templates"))
VERSION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+){1,4})\s*$")
VAR_RE = re.compile(r"<var\s+([^>]+?)>", re.I)
SPECIAL_VARS = {"MsgSender", "ProgramVersion", "Templateversion", "Mdate", "mtime"}


def _version(root):
    value = (root / "Standard_Forms_Version.dat").read_text(encoding="utf-8-sig", errors="replace").strip()
    match = VERSION_RE.match(value)
    if not match:
        raise ValueError("Standard Forms archive has no valid version")
    return match.group(1)


def _safe_extract(archive, destination):
    for member in archive.infolist():
        name = member.filename.replace("\\", "/")
        target = (destination / name).resolve()
        if name.startswith("/") or ".." in Path(name).parts or not str(target).startswith(str(destination.resolve()) + os.sep):
            raise ValueError(f"unsafe Standard Forms archive member: {member.filename}")
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def update_library(url=STANDARD_FORMS_URL, root=TEMPLATES_ROOT):
    """Download and atomically install a versioned Standard Forms archive."""
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="n0jcg-standard-forms-") as work:
        archive_path = Path(work) / "Standard_Forms.zip"
        urllib.request.urlretrieve(url, archive_path)
        unpacked = Path(work) / "unpacked"
        unpacked.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract(archive, unpacked)
        candidates = [unpacked, *[p for p in unpacked.iterdir() if p.is_dir()]]
        source = next((candidate for candidate in candidates if (candidate / "Standard_Forms_Version.dat").is_file()), None)
        if source is None:
            raise ValueError("Standard Forms archive is missing Standard_Forms_Version.dat")
        version = _version(source)
        destination = root / "standard" / version
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        metadata = {"version": version, "updated_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(), "source": url}
        (root / "standard" / "current.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        return metadata


def _descriptor_payload(path, version, library_root):
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    form_files, reply_template, subject, recipient, body = [], "", "", "", []
    section = "header"
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("form:"):
            form_files = [item.strip() for item in stripped[5:].split(",") if item.strip()]
        elif stripped.lower().startswith("replytemplate:"):
            reply_template = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("to:"):
            recipient = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("subject:"):
            subject = stripped.split(":", 1)[1].strip()
        elif stripped.lower() == "msg:":
            section = "body"
        elif section == "body":
            body.append(line)
    template_text = "\n".join([subject, recipient, *body])
    fields = []
    for raw in VAR_RE.findall(template_text):
        name = raw.strip()
        if name and name not in fields and name not in SPECIAL_VARS:
            fields.append(name)
    relative = path.relative_to(library_root).as_posix()
    return {
        "id": relative[:-4], "name": path.stem, "category": path.parent.relative_to(library_root).as_posix() if path.parent != library_root else "Standard",
        "descriptor": relative, "version": version, "form_files": form_files, "reply_template": reply_template,
        "recipient_template": recipient, "subject_template": subject, "body_template": "\n".join(body), "fields": fields,
    }


def catalog(root=TEMPLATES_ROOT):
    current = root / "standard" / "current.json"
    if not current.is_file():
        return {"available": False, "templates": []}
    metadata = json.loads(current.read_text(encoding="utf-8"))
    library = root / "standard" / metadata["version"]
    templates = [_descriptor_payload(path, metadata["version"], library) for path in sorted(library.rglob("*.txt")) if path.name.lower() not in {"changelog.txt", "standard_forms_version.dat"}]
    return {**metadata, "available": True, "templates": templates}


def render(template_id, values, callsign, root=TEMPLATES_ROOT):
    data = catalog(root)
    match = next((item for item in data.get("templates", []) if item["id"] == template_id), None)
    if not match:
        raise ValueError("template not found")
    values = values if isinstance(values, dict) else {}
    now = _datetime.datetime.now(_datetime.timezone.utc)
    replacements = {key: str(value or "") for key, value in values.items()}
    replacements.update({"MsgSender": callsign, "ProgramVersion": "N0JCG Winlink Email Server", "Templateversion": data["version"], "Mdate": now.strftime("%Y-%m-%d"), "mtime": now.strftime("%H:%M UTC")})
    def substitute(text):
        return VAR_RE.sub(lambda match: replacements.get(match.group(1).strip(), match.group(0)), text)
    return {"template": match, "recipient": substitute(match["recipient_template"]), "subject": substitute(match["subject_template"]), "body": substitute(match["body_template"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Update the N0JCG Winlink Standard Forms library")
    parser.add_argument("--url", default=STANDARD_FORMS_URL)
    args = parser.parse_args()
    print(json.dumps(update_library(args.url), indent=2))
