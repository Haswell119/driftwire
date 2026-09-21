"""Spec-vs-reality check: hit live endpoints and validate responses against the spec."""

import json
import re
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError

from . import jsonschema, spec_loader

_PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")


def check_spec(spec: Dict[str, Any], base_url: str, *,
               methods: Optional[List[str]] = None,
               headers: Optional[Dict[str, str]] = None,
               timeout: float = 10.0,
               path_params: Optional[Dict[str, str]] = None,
               skip_requires_params: bool = True) -> Dict[str, Any]:
    """Probe live endpoints and validate responses against the spec.

    Returns {"findings": [...], "endpoints": N, "probed": N, "skipped": N, "errors": N}.
    """
    if methods is None:
        methods = ["get"]
    methods = [m.lower() for m in methods]
    path_params = path_params or {}
    resolver = spec_loader.make_resolver(spec, "<spec>")

    findings: List[Dict[str, Any]] = []
    paths = spec.get("paths", {}) or {}

    endpoints = 0
    probed = 0
    skipped = 0
    errors = 0

    base = base_url.rstrip("/")

    for path, item in paths.items():
        for method, op in item.items():
            if method.lower() not in methods:
                continue
            endpoints += 1

            # fill path params
            missing = [p for p in _PATH_PARAM_RE.findall(path) if p not in path_params]
            if skip_requires_params and missing:
                skipped += 1
                continue

            url = path
            for name in path_params:
                url = url.replace("{" + name + "}", path_params[name])
            url = base + url

            result = _probe(url, op, method, headers, timeout)
            if result.get("error"):
                errors += 1
                findings.append({"path": f"{method.upper()} {path}", "type": "probe_error",
                                 "message": result["error"]})
                continue
            probed += 1

            status = str(result["status"])
            content = result["body"]
            response_schema = _response_schema_for_status(op, status, content)
            if response_schema is None:
                continue

            f = jsonschema.validate(response_schema, content, resolver)
            for finding in f:
                finding["path"] = f"{method.upper()} {path} -> {finding['path']}"
                finding["message"] = f"[HTTP {status}] {finding['message']}"
                findings.append(finding)

    return {"findings": findings, "endpoints": endpoints, "probed": probed,
            "skipped": skipped, "errors": errors}


def _probe(url: str, op: Dict[str, Any], method: str, headers: Optional[Dict[str, str]],
           timeout: float) -> Dict[str, Any]:
    req_headers = {"Accept": "application/json", "User-Agent": "driftwire/0.1"}
    if headers:
        req_headers.update(headers)
    try:
        req = Request(url, headers=req_headers, method=method.upper())
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return {"status": resp.status,
                    "body": _parse_json(raw),
                    "content_type": resp.headers.get("Content-Type", "")}
    except HTTPError as e:
        # non-2xx is still a response to validate (e.g. 404 defined in spec)
        raw = e.read()
        return {"status": e.code, "body": _parse_json(raw),
                "content_type": e.headers.get("Content-Type", "")}
    except (URLError, TimeoutError, OSError) as e:
        return {"error": f"request failed: {e}"}


def _parse_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _response_schema_for_status(op: Dict[str, Any], status: str, content):
    responses = op.get("responses", {}) or {}
    resp = responses.get(status) or responses.get("default")
    if resp is None:
        return None
    if content is None:
        return None
    c = resp.get("content", {}) or {}
    if c:
        for media in ("application/json", "*/*"):
            if media in c:
                return c[media].get("schema")
        first = next(iter(c.values()))
        return first.get("schema")
    # swagger 2
    return resp.get("schema")
