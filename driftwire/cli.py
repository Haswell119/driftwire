"""Command-line interface for DriftWire."""

import argparse
import json
import sys
from typing import Optional

from . import __version__, check, diff, spec_loader


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="driftwire",
        description="Detect API contract drift: spec-vs-reality and spec-vs-spec.",
    )
    parser.add_argument("--version", action="version", version=f"driftwire {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Validate a live API against its OpenAPI spec")
    p_check.add_argument("spec", help="path or URL to the OpenAPI spec")
    p_check.add_argument("--url", required=True, help="base URL of the live API")
    p_check.add_argument("--method", action="append", default=[],
                         help="HTTP method to probe (repeatable; default GET)")
    p_check.add_argument("--header", action="append", default=[],
                         help="extra header, e.g. 'Authorization: Bearer x'")
    p_check.add_argument("--path-param", action="append", default=[],
                         help="path parameter, e.g. 'id=42'")
    p_check.add_argument("--timeout", type=float, default=10.0)
    p_check.add_argument("--json", action="store_true", dest="as_json", help="JSON output")

    p_diff = sub.add_parser("diff", help="Detect breaking changes between two spec versions")
    p_diff.add_argument("old", help="path or URL to the old spec")
    p_diff.add_argument("new", help="path or URL to the new spec")
    p_diff.add_argument("--json", action="store_true", dest="as_json", help="JSON output")

    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            result = _run_check(args)
        else:
            result = _run_diff(args)
    except Exception as e:  # noqa: BLE001
        print(f"driftwire: error: {e}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(args.command, result)

    findings = result.get("findings", [])
    return 1 if findings else 0


def _run_check(args) -> dict:
    spec = spec_loader.load_spec(args.spec)
    headers = {}
    for h in args.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()
    path_params = {}
    for p in args.path_param:
        k, _, v = p.partition("=")
        path_params[k.strip()] = v.strip()
    methods = args.method or None
    return check.check_spec(spec, args.url, methods=methods, headers=headers,
                            timeout=args.timeout, path_params=path_params)


def _run_diff(args) -> dict:
    old = spec_loader.load_spec(args.old)
    new = spec_loader.load_spec(args.new)
    findings = diff.diff_specs(old, new)
    return {"findings": findings}


def _print_human(command: str, result: dict) -> None:
    findings = result.get("findings", [])
    if command == "check":
        print(f"probed {result.get('probed', 0)} endpoint(s), "
              f"skipped {result.get('skipped', 0)} (missing path params), "
              f"errors {result.get('errors', 0)}")
    if not findings:
        print("✓ no drift detected")
        return
    print(f"✗ {len(findings)} drift finding(s):")
    for f in findings:
        print(f"  - [{f.get('type')}] {f.get('path')}: {f.get('message')}")


if __name__ == "__main__":
    raise SystemExit(main())
