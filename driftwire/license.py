"""Offline Pro license verification (Ed25519).

A license is a self-contained, tamper-proof string signed by Meridian Digital:

    dw1.<base64url(payload)>.<base64url(signature)>

payload = JSON: {"e": "<email>", "exp": <unix seconds>, "v": 1}
signature = Ed25519 signature over the raw payload bytes.

The CLI verifies the signature with the embedded public key, so a Pro license
works entirely offline (no phone-home) and cannot be forged. `cryptography` is
imported lazily so the free core stays zero-dependency.
"""

import base64
import json
import time
from typing import Dict

# Embedded PUBLIC key (safe to ship in open source). The matching private key is
# held server-side and never distributed.
PUBLIC_KEY_B64 = "ArGFeAJoY26YiarFzylP3sd3T1xJCDgc41j+/RuQ3vM="

_LICENSE_PREFIX = "dw1."


class LicenseError(Exception):
    """Raised when a license is missing, malformed, or invalid."""


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _public_key():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as e:  # pragma: no cover
        raise LicenseError(
            "license verification requires 'cryptography' (pip install driftwire[pro])"
        ) from e
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))


def verify_license(license_str: str, now: float = None) -> Dict[str, object]:
    """Validate a license string and return its payload {"e", "exp", "v"}.

    Raises LicenseError if missing, malformed, invalid, or expired.
    """
    if not license_str:
        raise LicenseError("no license provided")
    license_str = license_str.strip()
    if not license_str.startswith(_LICENSE_PREFIX):
        raise LicenseError("malformed license (expected 'dw1.' prefix)")
    parts = license_str[len(_LICENSE_PREFIX):].split(".")
    if len(parts) != 2:
        raise LicenseError("malformed license (expected payload.signature)")

    payload_raw = _b64url_decode(parts[0])
    signature = _b64url_decode(parts[1])

    try:
        _public_key().verify(signature, payload_raw)
    except Exception as e:
        raise LicenseError("invalid license signature") from e

    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as e:
        raise LicenseError("malformed license payload") from e

    if payload.get("v") != 1:
        raise LicenseError("unsupported license version")
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        raise LicenseError("license missing expiry")
    if (now if now is not None else time.time()) > exp:
        raise LicenseError("license expired")

    return payload


def is_pro(license_str: str) -> bool:
    """True if `license_str` is a currently-valid Pro license."""
    try:
        verify_license(license_str)
        return True
    except LicenseError:
        return False
