"""Minimal JSON Schema / OpenAPI schema validator.

Covers the subset of JSON Schema that OpenAPI response schemas actually use:
type, properties, required, additionalProperties, items, enum, $ref,
allOf/oneOf/anyOf, and OpenAPI 3.0 `nullable`. Enough to detect real drift:
undocumented fields, missing required fields, and type mismatches.

A validator returns a list of findings (each a dict), never raises on bad data.
"""

import re
from typing import Any, Callable, Dict, List, Optional

# JSON pointer token escaping per RFC 6901
_unescape = lambda tok: tok.replace("~1", "/").replace("~0", "~")


def _pointer_str(path: List[str]) -> str:
    if not path:
        return "/"
    return "/" + "/".join(
        p.replace("~", "~0").replace("/", "~1") for p in path
    )


def _human_path(path: List[str]) -> str:
    """Human-readable location for diff findings (no JSON-pointer escaping)."""
    return "/".join(path)


class Schema:
    """A schema with a resolver for $ref."""

    def __init__(self, root: Dict[str, Any], resolver: Optional[Callable[[str], Dict[str, Any]]] = None):
        self.root = root
        # internal refs: '#/components/schemas/Foo'
        self._resolver = resolver
        self._refs_seen: Dict[str, Any] = {}

    def resolve(self, ref: str) -> Dict[str, Any]:
        if ref in self._refs_seen:
            return self._refs_seen[ref]
        if self._resolver is not None:
            node = self._resolver(ref)
        else:
            node = self._resolve_local(ref)
        self._refs_seen[ref] = node
        return node

    def _resolve_local(self, ref: str) -> Dict[str, Any]:
        if not ref.startswith("#/"):
            raise ValueError(f"Unresolvable $ref: {ref!r} (no external resolver)")
        node: Any = self.root
        for tok in ref[2:].split("/"):
            tok = _unescape(tok)
            if isinstance(node, dict) and tok in node:
                node = node[tok]
            elif isinstance(node, list) and tok.isdigit() and int(tok) < len(node):
                node = node[int(tok)]
            else:
                raise ValueError(f"$ref {ref!r} not found in schema")
        return node


def _unwrap_ref(schema: Dict[str, Any], sch: Schema) -> Dict[str, Any]:
    """Resolve a node that is a bare {$ref: ...}."""
    if isinstance(schema, dict) and "$ref" in schema:
        return sch.resolve(schema["$ref"])
    return schema


def _type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _matches_type(value: Any, typ: str) -> bool:
    v = _type_of(value)
    if typ == "number":
        return v in ("integer", "number")
    return v == typ


def validate(schema: Dict[str, Any], data: Any, resolver: Optional[Callable[[str], Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Validate `data` against `schema` (an OpenAPI/JSON Schema dict).

    Returns a list of findings. Empty list == valid.
    Each finding: {"path": "/a/b", "message": str, "type": "type_mismatch"|"missing_required"|"undocumented_field"|...}
    """
    sch = Schema(schema, resolver)
    findings: List[Dict[str, Any]] = []
    _validate(schema, data, [], sch, findings)
    return findings


def _validate(schema: Dict[str, Any], data: Any, path: List[str], sch: Schema, findings: List[Dict[str, Any]]) -> None:
    if not isinstance(schema, dict):
        return

    schema = _unwrap_ref(schema, sch)

    # OpenAPI 3.0 nullable
    if data is None and schema.get("nullable") is True:
        return

    # combinators
    if "allOf" in schema:
        for sub in schema["allOf"]:
            _validate(sub, data, path, sch, findings)
    if "oneOf" in schema or "anyOf" in schema:
        key = "oneOf" if "oneOf" in schema else "anyOf"
        subs = schema[key]
        matched = False
        for sub in subs:
            sub_findings: List[Dict[str, Any]] = []
            _validate(sub, data, path, sch, sub_findings)
            if not sub_findings:
                matched = True
                break
        if not matched:
            findings.append({
                "path": _pointer_str(path),
                "type": "one_of",
                "message": f"value does not match any schema in {key}",
            })

    typ = schema.get("type")
    if typ is not None:
        if isinstance(typ, list):
            ok = any(_matches_type(data, t) for t in typ)
            expected = "|".join(typ)
        else:
            ok = _matches_type(data, typ)
            expected = typ
        if not ok:
            findings.append({
                "path": _pointer_str(path),
                "type": "type_mismatch",
                "message": f"expected type {expected}, got {_type_of(data)}",
            })
            # still recurse into properties? No — type already wrong, stop here to avoid noise.
            return

    if "enum" in schema:
        if not any(data == e for e in schema["enum"]):
            findings.append({
                "path": _pointer_str(path),
                "type": "enum_violation",
                "message": f"value {data!r} not in enum {schema['enum']}",
            })

    if typ == "object" or (isinstance(data, dict) and ("properties" in schema or "required" in schema)):
        _validate_object(schema, data, path, sch, findings)

    if typ == "array" or (isinstance(data, list) and "items" in schema):
        _validate_array(schema, data, path, sch, findings)


def _validate_object(schema: Dict[str, Any], data: Any, path: List[str], sch: Schema, findings: List[Dict[str, Any]]) -> None:
    if not isinstance(data, dict):
        # type already reported (or type omitted) — report once here
        return

    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])

    for name in required:
        if name not in data:
            findings.append({
                "path": _pointer_str(path + [name]),
                "type": "missing_required",
                "message": f"missing required field {name!r}",
            })

    additional = schema.get("additionalProperties", True)

    for key, value in data.items():
        if key in props:
            _validate(props[key], value, path + [key], sch, findings)
        elif additional is False:
            findings.append({
                "path": _pointer_str(path + [key]),
                "type": "undocumented_field",
                "message": f"field {key!r} not declared in schema (additionalProperties=false)",
            })
        elif isinstance(additional, dict):
            _validate(additional, value, path + [key], sch, findings)
        # additionalProperties: true -> any extra field allowed, no finding


def _validate_array(schema: Dict[str, Any], data: Any, path: List[str], sch: Schema, findings: List[Dict[str, Any]]) -> None:
    if not isinstance(data, list):
        return
    items = schema.get("items")
    if isinstance(items, dict):
        for i, item in enumerate(data):
            _validate(items, item, path + [str(i)], sch, findings)


# ---------------------------------------------------------------------------
# Schema diffing (breaking-change detection between two versions)
# ---------------------------------------------------------------------------

def _schema_kind(schema: Dict[str, Any], sch: Schema) -> str:
    schema = _unwrap_ref(schema, sch)
    return str(schema.get("type", "object"))


def diff_schemas(old: Dict[str, Any], new: Dict[str, Any], path: List[str], sch_old: Schema, sch_new: Schema, findings: List[Dict[str, Any]]) -> None:
    """Compare two schemas and append breaking-change findings."""
    old = _unwrap_ref(old, sch_old)
    new = _unwrap_ref(new, sch_new)
    if not isinstance(old, dict) or not isinstance(new, dict):
        if old != new:
            findings.append({
                "path": _human_path(path), "type": "breaking_change",
                "message": f"schema changed from {old!r} to {new!r}",
            })
        return

    old_type = old.get("type")
    new_type = new.get("type")
    if old_type is not None and new_type is not None and old_type != new_type:
        findings.append({
            "path": _human_path(path), "type": "type_change",
            "message": f"type changed from {old_type!r} to {new_type!r}",
        })
        return  # a type change is the biggest signal; don't recurse further

    old_required = set(old.get("required", []) or [])
    new_required = set(new.get("required", []) or [])
    for name in sorted(new_required - old_required):
        findings.append({
            "path": _human_path(path + [name]), "type": "added_required",
            "message": f"field {name!r} became required",
        })

    old_props = old.get("properties", {}) or {}
    new_props = new.get("properties", {}) or {}
    for name in sorted(set(old_props) - set(new_props)):
        findings.append({
            "path": _human_path(path + [name]), "type": "removed_field",
            "message": f"field {name!r} removed",
        })
    for name in sorted(set(old_props) & set(new_props)):
        diff_schemas(old_props[name], new_props[name], path + [name], sch_old, sch_new, findings)

    old_items = old.get("items")
    new_items = new.get("items")
    if isinstance(old_items, dict) and isinstance(new_items, dict):
        diff_schemas(old_items, new_items, path + ["[]"], sch_old, sch_new, findings)
    elif isinstance(old_items, dict) != isinstance(new_items, dict):
        findings.append({
            "path": _human_path(path), "type": "breaking_change",
            "message": "array items schema changed shape",
        })

    old_enum = old.get("enum")
    new_enum = new.get("enum")
    if old_enum is not None and new_enum is not None:
        removed = [e for e in old_enum if e not in new_enum]
        if removed:
            findings.append({
                "path": _human_path(path), "type": "removed_enum",
                "message": f"enum values removed: {removed!r}",
            })
