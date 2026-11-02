#!/usr/bin/env python3
"""Typed JSON contracts between agents.

  validate(obj, schema) -> list of problems (empty = valid). A small, stdlib-only subset of JSON Schema: type,
                           required, properties, additionalProperties, items, enum, minimum, maximum, minItems,
                           maxItems. Several workflows install no packages, so this must stay dependency-free.
  schema(name)          -> pipeline/schemas/<name>.json
"""
import json, pathlib

HERE = pathlib.Path(__file__).parent
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int, "number": (int, float)}
_cache = {}

def schema(name):
    if name not in _cache:
        _cache[name] = json.loads((HERE / "schemas" / f"{name}.json").read_text())
    return _cache[name]

def validate(obj, sch, path="$"):
    out, want = [], sch.get("type")
    if want:
        ok = isinstance(obj, TYPES[want]) and not (want in ("integer", "number") and isinstance(obj, bool))
        if not ok:
            return [f"{path}: expected {want}"]
    if "enum" in sch and obj not in sch["enum"]:
        out.append(f"{path}: {obj!r} is not one of {sch['enum']}")
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in sch and obj < sch["minimum"]:
            out.append(f"{path}: below {sch['minimum']}")
        if "maximum" in sch and obj > sch["maximum"]:
            out.append(f"{path}: above {sch['maximum']}")
    if isinstance(obj, dict):
        props = sch.get("properties", {})
        out += [f"{path}.{k}: required" for k in sch.get("required", []) if k not in obj]
        for k, v in obj.items():
            if k in props:
                out += validate(v, props[k], f"{path}.{k}")
            elif sch.get("additionalProperties") is False:
                out.append(f"{path}.{k}: not allowed")
    if isinstance(obj, list):
        if "minItems" in sch and len(obj) < sch["minItems"]:
            out.append(f"{path}: needs at least {sch['minItems']} items")
        if "maxItems" in sch and len(obj) > sch["maxItems"]:
            out.append(f"{path}: allows at most {sch['maxItems']} items")
        if "items" in sch:
            for i, v in enumerate(obj):
                out += validate(v, sch["items"], f"{path}[{i}]")
    return out
