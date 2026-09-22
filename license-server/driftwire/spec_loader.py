"""Load OpenAPI specs (JSON or YAML) from a path or URL, with $ref resolution."""

import json
import os
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover
    _HAS_YAML = False


def load_spec(source: str) -> Dict[str, Any]:
    """Load an OpenAPI document from a file path or http(s) URL.

    Returns the parsed dict (or list, but OpenAPI is a dict).
    """
    if source.startswith(("http://", "https://")):
        text = _http_get(source)
    else:
        if not os.path.exists(source):
            raise FileNotFoundError(f"spec file not found: {source}")
        with open(source, "r", encoding="utf-8") as fh:
            text = fh.read()
    return parse_spec(text, source)


def parse_spec(text: str, origin: str = "<string>") -> Dict[str, Any]:
    text = text.lstrip("\ufeff")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if _HAS_YAML:
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            return data
        raise ValueError(f"spec at {origin} did not parse to an object")
    raise RuntimeError("spec is YAML but PyYAML is not installed (pip install pyyaml)")


def _http_get(url: str) -> str:
    req = urlopen(url, timeout=20)
    charset = req.headers.get_content_charset() or "utf-8"
    return req.read().decode(charset, errors="replace")


def make_resolver(root: Dict[str, Any], base_uri: str) -> Callable[[str], Dict[str, Any]]:
    """Build a $ref resolver. Resolves internal refs (#/components/...) against `root`,
    and external refs (file or URL) relative to the spec's location."""
    cache: Dict[str, Any] = {}

    def resolve(ref: str) -> Any:
        if ref in cache:
            return cache[ref]
        if ref.startswith("#"):
            node = _resolve_pointer(root, ref[1:])
        else:
            ext_spec, frag = ref.split("#", 1) if "#" in ref else (ref, "")
            ext_uri = _join_uri(base_uri, ext_spec)
            ext_root = load_spec(ext_uri)
            node = _resolve_pointer(ext_root, frag) if frag else ext_root
        cache[ref] = node
        return node

    return resolve


def _resolve_pointer(doc: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return doc
    if not pointer.startswith("/"):
        raise ValueError(f"bad JSON pointer: {pointer!r}")
    node = doc
    for tok in pointer[1:].split("/"):
        tok = tok.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and tok in node:
            node = node[tok]
        elif isinstance(node, list) and tok.isdigit() and int(tok) < len(node):
            node = node[int(tok)]
        else:
            raise ValueError(f"pointer {pointer!r} not found")
    return node


def _join_uri(base: str, rel: str) -> str:
    if rel.startswith(("http://", "https://", "file://")):
        return rel
    p = urlparse(base)
    if p.scheme in ("http", "https"):
        from urllib.parse import urljoin
        return urljoin(base, rel)
    # local file
    base_dir = os.path.dirname(os.path.abspath(base))
    return os.path.join(base_dir, rel)


def servers(spec: Dict[str, Any]) -> list:
    """Return the list of base server URLs (OpenAPI 3) or schemes+host (Swagger 2)."""
    out = []
    for s in spec.get("servers", []):
        url = s.get("url", "")
        # substitute default server variables
        for var, var_spec in (s.get("variables") or {}).items():
            default = var_spec.get("default", "")
            url = url.replace("{" + var + "}", str(default))
        out.append(url)
    if not out and "host" in spec:  # Swagger 2
        scheme = (spec.get("schemes") or ["https"])[0]
        out.append(f"{scheme}://{spec['host']}{spec.get('basePath', '')}")
    return out
