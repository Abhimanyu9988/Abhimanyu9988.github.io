#!/usr/bin/env python3
"""Unpacks a chat lab for the free check. Only safe file names are written, and a lab with no check.sh is refused."""
import json, pathlib, re, sys

SAFE = re.compile(r"(?:k8s/)?[a-z0-9][a-z0-9_.-]{0,60}\.(?:tf|sh|ya?ml|json|tpl|md)", re.I)

def unpack(lab, folder):
    """The file names written. A bad name stops the check, because a path must never leave the folder."""
    files = lab.get("files") or []
    bad = [str(f.get("path")) for f in files if not SAFE.fullmatch(str(f.get("path", "")))]
    if bad:
        raise SystemExit(f"file name not allowed: {bad[0][:80]}")
    if "check.sh" not in {f["path"] for f in files}:
        raise SystemExit("this lab has no check.sh, so nothing can be tested for free")
    for f in files:
        path = pathlib.Path(folder, f["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(f.get("content", "")))
    return sorted(f["path"] for f in files)

if __name__ == "__main__":
    with open(sys.argv[1]) as handle:
        print("\n".join(unpack(json.load(handle), sys.argv[2])))
