"""Waiver / exception config (Pro feature).

A `.driftwire.yml` lets a team record reviewed-and-justified exceptions so CI
stays green on known drift while still failing on new drift:

    waivers:
      - type: type_mismatch            # optional: exact finding type
        path: "GET /todos/1 -> /userId"  # optional: glob on the finding path
        message: "expected type string"  # optional: substring of the message
        reason: "legacy field, JIRA-123" # REQUIRED: why this is waived

A finding is waived when it matches EVERY field present on a waiver entry.
"""

import fnmatch
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def load_config(path: Optional[str]) -> Dict[str, Any]:
    """Load a waiver config file (YAML preferred, JSON accepted). Returns {} if absent."""
    if not path:
        return {}
    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith(".json"):
        return json.loads(text)
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except Exception as e:
        raise ValueError(f"could not parse {path} (YAML requires PyYAML): {e}") from e
    return data if isinstance(data, dict) else {}


def _matches(finding: Dict[str, Any], waiver: Dict[str, Any]) -> bool:
    typ = waiver.get("type")
    if typ is not None and finding.get("type") != typ:
        return False
    path_glob = waiver.get("path")
    if path_glob is not None and not fnmatch.fnmatch(str(finding.get("path", "")), str(path_glob)):
        return False
    msg_sub = waiver.get("message")
    if msg_sub is not None and str(msg_sub) not in str(finding.get("message", "")):
        return False
    return True


def apply_waivers(findings: List[Dict[str, Any]], config: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split findings into (active, waived).

    A waiver entry without a `reason` is invalid and silently skipped (it cannot
    justify an exception, so it cannot waive anything).
    """
    waivers = config.get("waivers", []) or []
    valid = [w for w in waivers if isinstance(w, dict) and w.get("reason")]
    active: List[Dict[str, Any]] = []
    waived: List[Dict[str, Any]] = []
    for finding in findings:
        if any(_matches(finding, w) for w in valid):
            waived.append(finding)
        else:
            active.append(finding)
    return active, waived
