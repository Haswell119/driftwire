#!/usr/bin/env python3
"""Issue a DriftWire Pro license (server-side tool; private key from env).

Usage:
    DRIFTWIRE_LICENSE_PRIVKEY_B64=<b64> python3 sign_license.py EMAIL --days 365

Prints the license string. The private key is read from the environment and is
never printed. Reused by the license delivery server.
"""

import argparse
import base64
import json
import os
import time


def _load_private_key():
    from cryptography.hazmat.primitives import serialization
    b64 = os.environ.get("DRIFTWIRE_LICENSE_PRIVKEY_B64", "")
    if not b64:
        raise SystemExit("DRIFTWIRE_LICENSE_PRIVKEY_B64 not set")
    return serialization.load_pem_private_key(base64.b64decode(b64), password=None)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign_license(email: str, exp: int) -> str:
    payload = json.dumps({"e": email, "exp": int(exp), "v": 1},
                         separators=(",", ":")).encode("utf-8")
    sig = _load_private_key().sign(payload)
    return f"dw1.{_b64url(payload)}.{_b64url(sig)}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Issue a DriftWire Pro license")
    ap.add_argument("email")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--exp", type=int, default=0, help="explicit unix expiry (overrides --days)")
    args = ap.parse_args()
    exp = args.exp or int(time.time()) + args.days * 86400
    print(sign_license(args.email, exp))


if __name__ == "__main__":
    main()
