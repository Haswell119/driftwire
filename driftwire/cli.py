"""Command-line interface for DriftWire."""

import argparse
import json
import os
import sys
from typing import Optional

from . import __version__, check, diff, license as lic, report, spec_loader, waivers

PRO_FEATURE_NOTICE = (
    "DriftWire Pro required for this feature (HTML reports and .driftwire.yml waivers).\n"
    "Get a Pro license at https://haswell119.github.io/driftwire/ or pass it with\n"
    "--license <key> / the DRIFTWIRE_LICENSE environment variable."
)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="driftwire",
        description="Detect API contract drift: spec-vs-reality and spec-vs-spec.",
    )
    parser.add_argument("--version", action="version", version=f"driftwire {__version__}")
    parser.add_argument("--license", default=os.environ.get("DRIFTWIRE_LICENSE", ""),
                        help="Pro license key (or set DRIFTWIRE_LICENSE)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Validate a live API against its OpenAPI spec")
    p_check.add_argument("--license", default=argparse.SUPPRESS,
                         help="Pro license key (or set DRIFTWIRE_LICENSE)")
    p_check.add_argument("spec", help="path or URL to the OpenAPI spec")
    p_check.add_argument("--url", required=True, help="base URL of the live API")
    p_check.add_argument("--method", action="append", default=[],
                         help="HTTP method to probe (repeatable; default GET)")
    p_check.add_argument("--header", action="append", default=[],
                         help="extra header, e.g. 'Authorization: Bearer ***'")
    p_check.add_argument("--path-param", action="append", default=[],
                         help="path parameter, e.g. 'id=42'")
    p_check.add_argument("--timeout", type=float, default=10.0)
    p_check.add_argument("--format", choices=["text", "json", "html"], default="text")
    p_check.add_argument("--config", help="path to .driftwire.yml waiver config (Pro)")
    p_check.add_argument("--list-waived", action="store_true",
                         help="also print waived findings (Pro)")

    p_diff = sub.add_parser("diff", help="Detect breaking changes between two spec versions")
    p_diff.add_argument("--license", default=argparse.SUPPRESS,
                        help="Pro license key (or set DRIFTWIRE_LICENSE)")
    p_diff.add_argument("old", help="path or URL to the old spec")
    p_diff.add_argument("new", help="path or URL to the new spec")
    p_diff.add_argument("--format", choices=["text", "json", "html"], default="text")
    p_diff.add_argument("--config", help="path to .driftwire.yml waiver config (Pro)")
    p_diff.add_argument("--list-waived", action="store_true",
                         help="also print waived findings (Pro)")

    args = parser.parse_args(argv)

    wants_pro = (args.format == "html") or bool(args.config) or args.list_waived
    if wants_pro:
        try:
            lic.verify_license(args.license)
        except lic.LicenseError as e:
            print(f"driftwire: Pro required: {e}", file=sys.stderr)
            print(PRO_FEATURE_NOTICE, file=sys.stderr)
            return 3

    try:
        if args.command == "check":
            result = _run_check(args)
        else:
            result = _run_diff(args)
    except Exception as e:  # noqa: BLE001
        print(f"driftwire: error: {e}", file=sys.stderr)
        return 2

    waived: list = []
    if args.config:
        config = waivers.load_config(args.config)
        result["findings"], waived = waivers.apply_waivers(result["findings"], config)
        result["waived"] = len(waived)

    _emit(args.command, args.format, result, waived, args.list_waived)

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


def _emit(command: str, fmt: str, result: dict, waived: list, list_waived: bool) -> None:
    if fmt == "json":
        print(json.dumps(result, indent=2))
        return
    if fmt == "html":
        print(report.render_html(result, command))
        return

    findings = result.get("findings", [])
    if command == "check":
        print(f"probed {result.get('probed', 0)} endpoint(s), "
              f"skipped {result.get('skipped', 0)} (missing path params), "
              f"errors {result.get('errors', 0)}")
    if not findings and not (waived and list_waived):
        print("✓ no drift detected")
        return
    if findings:
        print(f"✗ {len(findings)} drift finding(s):")
        for f in findings:
            print(f"  - [{f.get('type')}] {f.get('path')}: {f.get('message')}")
    if waived:
        print(f"  ({len(waived)} finding(s) waived by .driftwire.yml)")
        if list_waived:
            for f in waived:
                print(f"  - [waived] [{f.get('type')}] {f.get('path')}: {f.get('message')}")


if __name__ == "__main__":
    raise SystemExit(main())
