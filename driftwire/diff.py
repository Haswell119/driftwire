"""Spec-vs-spec breaking-change detection."""

from typing import Any, Dict, List

from . import jsonschema, spec_loader


def diff_specs(old_spec: Dict[str, Any], new_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compare two OpenAPI specs and return breaking-change findings."""
    old_resolver = spec_loader.make_resolver(old_spec, "<old>")
    new_resolver = spec_loader.make_resolver(new_spec, "<new>")
    findings: List[Dict[str, Any]] = []

    old_paths = old_spec.get("paths", {}) or {}
    new_paths = new_spec.get("paths", {}) or {}

    for path in sorted(set(old_paths) - set(new_paths)):
        findings.append({"path": path, "type": "removed_endpoint", "message": "endpoint removed"})

    for path in sorted(set(old_paths) & set(new_paths)):
        _diff_path(path, old_paths[path], new_paths[path], old_resolver, new_resolver, findings)

    return findings


def _diff_path(path: str, old_item: Dict[str, Any], new_item: Dict[str, Any],
               old_resolver, new_resolver, findings: List[Dict[str, Any]]) -> None:
    old_ops = {k: v for k, v in old_item.items() if k.lower() in _METHODS}
    new_ops = {k: v for k, v in new_item.items() if k.lower() in _METHODS}

    for method in sorted(set(old_ops) - set(new_ops)):
        findings.append({"path": f"{path} [{method.upper()}]", "type": "removed_method",
                         "message": f"{method.upper()} method removed"})

    for method in sorted(set(old_ops) & set(new_ops)):
        _diff_operation(path, method, old_ops[method], new_ops[method],
                        old_resolver, new_resolver, findings)


_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _diff_operation(path: str, method: str, old_op: Dict[str, Any], new_op: Dict[str, Any],
                    old_resolver, new_resolver, findings: List[Dict[str, Any]]) -> None:
    label = f"{method.upper()} {path}"

    # --- parameters: a newly-required request param breaks consumers ---
    old_params = _param_map(old_op.get("parameters", []) or [])
    new_params = _param_map(new_op.get("parameters", []) or [])
    for name in sorted(set(old_params) - set(new_params)):
        findings.append({"path": label, "type": "removed_parameter",
                         "message": f"parameter {name!r} removed"})
    for name in sorted(set(old_params) & set(new_params)):
        old_p, new_p = old_params[name], new_params[name]
        if not old_p.get("required") and new_p.get("required"):
            findings.append({"path": label, "type": "added_required_parameter",
                             "message": f"parameter {name!r} became required"})

    # --- request body schema ---
    _diff_body_schema("request body", old_op, new_op, label, old_resolver, new_resolver, findings)

    # --- response schemas ---
    old_resp = old_op.get("responses", {}) or {}
    new_resp = new_op.get("responses", {}) or {}
    for code in sorted(set(old_resp) - set(new_resp)):
        findings.append({"path": label, "type": "removed_response",
                         "message": f"response {code} removed"})
    for code in sorted(set(old_resp) & set(new_resp)):
        old_schema = _response_schema(old_resp[code])
        new_schema = _response_schema(new_resp[code])
        if old_schema is not None and new_schema is not None:
            jsonschema.diff_schemas(old_schema, new_schema, [label, f"response {code}"],
                                    jsonschema.Schema(old_schema, old_resolver),
                                    jsonschema.Schema(new_schema, new_resolver),
                                    findings)


def _param_map(params: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for p in params:
        name = p.get("name")
        if name:
            out[name] = p
    return out


def _diff_body_schema(kind: str, old_op: Dict[str, Any], new_op: Dict[str, Any], label: str,
                      old_resolver, new_resolver, findings: List[Dict[str, Any]]) -> None:
    old_schema = _body_schema(old_op)
    new_schema = _body_schema(new_op)
    if old_schema is not None and new_schema is not None:
        jsonschema.diff_schemas(old_schema, new_schema, [label, kind],
                                jsonschema.Schema(old_schema, old_resolver),
                                jsonschema.Schema(new_schema, new_resolver),
                                findings)


def _body_schema(op: Dict[str, Any]):
    content = (op.get("requestBody") or {}).get("content", {}) or {}
    for media in ("application/json", "*/*"):
        if media in content:
            return content[media].get("schema")
    if content:
        first = next(iter(content.values()))
        return first.get("schema")
    return None


def _response_schema(resp: Dict[str, Any]):
    content = resp.get("content", {}) or {}
    for media in ("application/json", "*/*"):
        if media in content:
            return content[media].get("schema")
    if content:
        first = next(iter(content.values()))
        return first.get("schema")
    # swagger 2: response schema is direct
    return resp.get("schema")
