"""Result upload: validate one record and park it in the inbox. Never touches git or the repo copy.

The inbox is a folder on the host that only this app writes. A record sits there, shown in the UI as
"pending", until Josh commits it into results/ (scripts/import-results.py) and refreshes the bundle,
at which point the committed copy wins. Everything a caller sends is untrusted: it is checked
against the schema and the catalogue, its id becomes a file name only after a strict pattern check,
and an existing id is never overwritten.
"""
import json
import os
import re
from pathlib import Path

import jsonschema

MAX_BYTES = 64 * 1024
# the id becomes a file name: letters, digits and . _ + - only, no leading dot, no path separators
ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")


class Rejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def parse(body: bytes, schema: dict, known_models: set[str], existing_ids: set[str]) -> dict:
    """Return the record, or raise Rejected(status, reason). The reason never echoes the body."""
    if len(body) > MAX_BYTES:
        raise Rejected(413, f"record larger than {MAX_BYTES} bytes")
    try:
        rec = json.loads(body)
    except ValueError:
        raise Rejected(422, "body is not valid JSON")
    if not isinstance(rec, dict):
        raise Rejected(422, "body must be one JSON object")
    try:
        jsonschema.validate(rec, schema)
    except jsonschema.ValidationError as e:
        raise Rejected(422, "does not match results.schema.json: " + e.message.splitlines()[0][:200])
    if not ID_RE.match(rec["id"]):
        raise Rejected(422, "id may only contain letters, digits and . _ + - (and must not start with a dot)")
    if rec["model"] not in known_models:
        raise Rejected(422, f"model {rec['model']!r} is not in library.yaml")
    if rec["id"] in existing_ids:
        raise Rejected(409, f"a result with id {rec['id']!r} already exists")
    return rec


def store(inbox: Path, rec: dict) -> Path:
    """Write <inbox>/<id>.json, refusing to replace anything (O_EXCL)."""
    final = inbox / f"{rec['id']}.json"
    if final.parent != inbox:                       # belt and braces on top of ID_RE
        raise Rejected(422, "bad id")
    try:
        fd = os.open(final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    except FileExistsError:
        raise Rejected(409, f"a result with id {rec['id']!r} already exists")
    except OSError as e:
        raise Rejected(503, f"inbox is not writable ({e.strerror})")
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    return final
