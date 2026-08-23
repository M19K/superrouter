"""Small file helpers, so a handle is never left to the garbage collector.

`json.load(open(p))` reads fine and closes the file only when CPython's
refcount happens to drop it. That is an implementation detail, not a promise —
and this project argues everywhere else that relying on an unstated assumption
is how quiet failures start. 36 of them had accumulated.
"""
import json


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def write_json(path, obj, indent=1):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent)


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_lines(path):
    """Non-empty lines, for the .jsonl records this project writes."""
    with open(path, encoding="utf-8") as f:
        return [ln for ln in (line.strip() for line in f) if ln]
