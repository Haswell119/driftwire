"""Tests for DriftWire Pro features: license, HTML report, waivers."""

import base64
import json
import time

import pytest

from driftwire import license as lic
from driftwire import report, waivers


# --- license verification -------------------------------------------------

def _fresh_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw,
                                       serialization.PublicFormat.Raw)
    return sk, base64.b64encode(pub).decode()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sign(sk, email, exp):
    payload = json.dumps({"e": email, "exp": int(exp), "v": 1},
                         separators=(",", ":")).encode("utf-8")
    sig = sk.sign(payload)
    return f"dw1.{_b64url(payload)}.{_b64url(sig)}"


def test_valid_license_verifies(monkeypatch):
    sk, pub_b64 = _fresh_keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub_b64)
    key = _sign(sk, "dev@example.com", time.time() + 86400)
    payload = lic.verify_license(key)
    assert payload["e"] == "dev@example.com"
    assert lic.is_pro(key)


def test_tampered_license_rejected(monkeypatch):
    sk, pub_b64 = _fresh_keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub_b64)
    key = _sign(sk, "dev@example.com", time.time() + 86400)
    # flip a byte in the signature
    parts = key.split(".")
    sig = base64.urlsafe_b64decode(parts[2] + "==")
    tampered = _b64url(bytes([sig[0] ^ 0xFF]) + sig[1:])
    with pytest.raises(lic.LicenseError):
        lic.verify_license(f"dw1.{parts[1]}.{tampered}")


def test_expired_license_rejected(monkeypatch):
    sk, pub_b64 = _fresh_keypair()
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", pub_b64)
    key = _sign(sk, "dev@example.com", time.time() - 10)
    with pytest.raises(lic.LicenseError):
        lic.verify_license(key)


def test_malformed_license_rejected():
    for bad in ("", "not-a-license", "dw1.", "dw1.abc"):
        with pytest.raises(lic.LicenseError):
            lic.verify_license(bad)


# --- HTML report ----------------------------------------------------------

def test_report_html_contains_findings():
    result = {"findings": [
        {"type": "type_mismatch", "path": "GET /todos/1 -> /userId",
         "message": "expected type string, got integer"},
    ], "probed": 1, "skipped": 0, "errors": 0}
    html_out = report.render_html(result, "check")
    assert "<!doctype html>" in html_out
    assert "type_mismatch" in html_out
    assert "GET /todos/1" in html_out


def test_report_html_clean():
    html_out = report.render_html({"findings": []}, "diff")
    assert "no drift detected" in html_out


# --- waivers --------------------------------------------------------------

FINDINGS = [
    {"type": "type_mismatch", "path": "GET /todos/1 -> /userId",
     "message": "expected type string, got integer"},
    {"type": "removed_field", "path": "GET /users/response 200/name",
     "message": "field 'name' removed"},
]


def test_waiver_matches_type_only():
    cfg = {"waivers": [{"type": "type_mismatch", "reason": "known legacy"}]}
    active, waived = waivers.apply_waivers(FINDINGS, cfg)
    assert [f["type"] for f in active] == ["removed_field"]
    assert len(waived) == 1


def test_waiver_matches_path_glob():
    cfg = {"waivers": [{"path": "GET /users/*", "reason": "v2 migration"}]}
    active, waived = waivers.apply_waivers(FINDINGS, cfg)
    assert [f["type"] for f in active] == ["type_mismatch"]
    assert waived[0]["type"] == "removed_field"


def test_waiver_requires_reason():
    cfg = {"waivers": [{"type": "type_mismatch"}]}  # no reason -> invalid, skips
    active, waived = waivers.apply_waivers(FINDINGS, cfg)
    assert active == FINDINGS and waived == []


def test_waiver_all_fields_must_match():
    cfg = {"waivers": [{"type": "type_mismatch", "message": "no such text",
                        "reason": "x"}]}
    active, waived = waivers.apply_waivers(FINDINGS, cfg)
    assert active == FINDINGS and waived == []
