#!/usr/bin/env python3
"""What Lumen's setup added to this computer, so uninstall removes exactly that and nothing else.

The record lives in ~/.config/lumen/installed.json (private). Every change to a file the
user owns goes through here:

  record.py add-block FILE ID TEXT   append TEXT (a fenced block) to FILE, remember its sha256
  record.py has-block ID             exit 0 if that block is recorded and still in its file
  record.py remove-blocks            take out every recorded block that is still exactly as
                                     added; a block the user has since edited is kept (and listed)
  record.py link NAME TARGET         ~/.local/bin/NAME -> TARGET, never replacing something
                                     that isn't Lumen's (it's skipped and named instead)
  record.py unlink                   remove the links Lumen made (if they still point at Lumen)
  record.py own-dir PATH             mark a directory as created by Lumen (a random id inside)
  record.py owned-dir PATH           exit 0 if PATH carries the mark recorded here
  record.py set KEY VALUE / get KEY  small facts (e.g. model_downloaded_by_lumen)
  record.py remove-legacy-shortcut FILE   the 1.0.0 shortcut (marker line + its exact bind line)
  record.py show                     the record, as JSON

Writes to user files resolve symlinks first (dotfiles stay links), keep the file's mode, and
go through a private temp file in the same folder that is renamed over the original.
"""

import hashlib
import json
import os
import secrets
import sys
import tempfile

HOME = os.path.expanduser("~")
RECORD = os.path.join(HOME, ".config/lumen/installed.json")
BIN = os.path.join(HOME, ".local/bin")
MARK = ".lumen-owned"


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def load():
    try:
        with open(RECORD) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"blocks": [], "links": [], "dirs": [], "facts": {}}


def write_private(path, data, mode=0o600):
    """Atomic write that never exposes the content: mkstemp (0600, same folder), fsync,
    then the final mode, then rename over the target."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save(rec):
    write_private(RECORD, json.dumps(rec, indent=2) + "\n")


def add_block(path, bid, text):
    rec = load()
    real = os.path.realpath(path)
    with open(real) as f:
        cur = f.read()
    if any(b["id"] == bid and b["file"] == real for b in rec["blocks"]) and text in cur:
        return 0
    block = ("" if cur.endswith("\n") or not cur else "\n") + "\n" + text.rstrip("\n") + "\n"
    write_private(real, cur + block, os.stat(real).st_mode & 0o7777)
    rec["blocks"].append({"id": bid, "file": real, "text": block, "sha256": sha(block)})
    save(rec)
    return 0


def has_block(bid):
    for b in load()["blocks"]:
        try:
            with open(b["file"]) as f:
                if b["id"] == bid and b["text"] in f.read():
                    return 0
        except OSError:
            pass
    return 1


def remove_blocks():
    rec = load()
    keep = []
    for b in rec["blocks"]:
        try:
            with open(b["file"]) as f:
                cur = f.read()
        except OSError:
            continue                                      # the file is gone: nothing to remove
        if sha(b["text"]) == b["sha256"] and cur.count(b["text"]) == 1:
            write_private(b["file"], cur.replace(b["text"], "", 1), os.stat(b["file"]).st_mode & 0o7777)
            print(f"removed: Lumen's lines in {b['file']}")
        else:
            keep.append(b)
            print(f"kept: {b['file']} — Lumen's block there was changed, so it's left for you:\n{b['text'].strip()}")
    rec["blocks"] = keep
    save(rec)
    return 0


def link(name, target):
    rec = load()
    path = os.path.join(BIN, name)
    os.makedirs(BIN, exist_ok=True)
    if os.path.lexists(path):
        if os.path.islink(path) and os.path.realpath(path) == os.path.realpath(target):
            if not any(l["path"] == path for l in rec["links"]):
                rec["links"].append({"path": path, "target": target})
                save(rec)
            return 0
        print(f"skipped: {path} already exists and isn't Lumen's, so it was left alone")
        return 0
    os.symlink(target, path)
    rec["links"].append({"path": path, "target": target})
    save(rec)
    return 0


def unlink():
    rec = load()
    left = []
    for l in rec["links"]:
        p = l["path"]
        if os.path.islink(p) and os.readlink(p) == l["target"]:
            os.unlink(p)
            print(f"removed: {p}")
        elif os.path.lexists(p):
            left.append(l)
            print(f"kept: {p} (it now points somewhere else)")
    rec["links"] = left
    save(rec)
    return 0


def own_dir(path):
    rec = load()
    os.makedirs(path, mode=0o700, exist_ok=True)
    mark = os.path.join(path, MARK)
    if os.path.exists(mark):
        with open(mark) as f:
            mid = f.read().strip()
    else:
        mid = secrets.token_hex(16)
        write_private(mark, mid + "\n")
    rec["dirs"] = [d for d in rec["dirs"] if d["path"] != path] + [{"path": path, "id": mid}]
    save(rec)
    return 0


def owned_dir(path):
    for d in load()["dirs"]:
        if d["path"] == path:
            try:
                with open(os.path.join(path, MARK)) as f:
                    return 0 if f.read().strip() == d["id"] else 1
            except OSError:
                return 1
    return 1


def remove_legacy_shortcut(path):
    """Lumen 1.0.0 added a marker comment and one bind line, unfenced. Remove that pair only
    when the line after the marker is still exactly the bind Lumen wrote."""
    import re
    real = os.path.realpath(path)
    try:
        with open(real) as f:
            lines = f.read().split("\n")
    except OSError:
        return 0
    bind = re.compile(r'^o\.bind\("SHIFT \+ ALT \+ PRINT", "Lumen: start / stop recording", "[^"]*/lumen"\)$')
    out, i, removed = [], 0, False
    while i < len(lines):
        if (lines[i].startswith("-- lumen-shortcut (added by lumen setup") and i + 1 < len(lines)
                and bind.match(lines[i + 1])):
            if out and out[-1] == "":
                out.pop()                       # the blank line setup put in front of it
            i += 2
            removed = True
            continue
        out.append(lines[i])
        i += 1
    if removed:
        write_private(real, "\n".join(out), os.stat(real).st_mode & 0o7777)
        print(f"removed: Lumen's shortcut in {real}")
    elif any(l.startswith("-- lumen-shortcut") for l in lines):
        print(f"kept: {real} — Lumen's old shortcut there was changed, so it's left for you")
    return 0


def main(a):
    cmd = a[0] if a else ""
    if cmd == "add-block" and len(a) == 4:
        return add_block(a[1], a[2], a[3])
    if cmd == "has-block" and len(a) == 2:
        return has_block(a[1])
    if cmd == "remove-blocks":
        return remove_blocks()
    if cmd == "link" and len(a) == 3:
        return link(a[1], a[2])
    if cmd == "unlink":
        return unlink()
    if cmd == "own-dir" and len(a) == 2:
        return own_dir(a[1])
    if cmd == "owned-dir" and len(a) == 2:
        return owned_dir(a[1])
    if cmd == "set" and len(a) == 3:
        rec = load()
        rec.setdefault("facts", {})[a[1]] = a[2]
        save(rec)
        return 0
    if cmd == "get" and len(a) == 2:
        v = load().get("facts", {}).get(a[1])
        if v is None:
            return 1
        print(v)
        return 0
    if cmd == "remove-legacy-shortcut" and len(a) == 2:
        return remove_legacy_shortcut(a[1])
    if cmd == "show":
        print(json.dumps(load(), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
