#!/usr/bin/env python3
"""DriftWire Pro — license delivery server (stdlib + cryptography).

Routes:
  GET  /                       -> landing page with a "Buy Pro" CTA
  POST /api/checkout           -> create a Stripe Checkout Session (one-time Pro)
  GET  /api/redeem?session_id= -> verify payment, sign an Ed25519 license, show it

The CLI verifies licenses OFFLINE with its embedded public key, so this server
only needs to run at purchase time (no per-run phone-home).

Env: STRIPE_SECRET_KEY, DRIFTWIRE_LICENSE_PRIVKEY_B64, PRICE_ID, APP_URL.
"""

import base64
import html
import json
import os
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SK = os.environ.get("STRIPE_SECRET_KEY", "")
PRIVKEY_B64 = os.environ.get("DRIFTWIRE_LICENSE_PRIVKEY_B64", "")
PRICE_ID = os.environ.get("PRICE_ID", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:8080").rstrip("/")

LICENSE_TTL_DAYS = 365 * 20  # lifetime-ish; exp is checked by the CLI


# ---------- Spec-vs-REALITY demo (the non-commodity wedge) ----------
# The `check` engine probes a LIVE API and validates the responses against the
# OpenAPI spec. This bundled spec describes the mock API below *as it should be*;
# the mock deliberately serves drifted reality so the demo shows real findings.
# No user input reaches the URL: base_url is hardcoded to localhost, so there is
# no SSRF surface and nothing external is ever contacted.

DEMO_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Demo Users API", "version": "1.0.0"},
    "paths": {
        "/demoapi/users/{id}": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "name", "role"],
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                        "role": {"type": "string", "enum": ["user", "admin"]},
                                    },
                                    "additionalProperties": False,
                                }
                            }
                        }
                    }
                }
            }
        },
        "/demoapi/status": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["status", "uptime"],
                                    "properties": {
                                        "status": {"type": "string", "enum": ["ok", "degraded"]},
                                        "uptime": {"type": "integer"},
                                    },
                                }
                            }
                        }
                    }
                }
            }
        },
    },
}


# ---------- Ed25519 signing (cryptography) ----------

def _load_private_key():
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(base64.b64decode(PRIVKEY_B64), password=None)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign_license(email: str, exp: int) -> str:
    payload = json.dumps({"e": email, "exp": int(exp), "v": 1},
                         separators=(",", ":")).encode("utf-8")
    sig = _load_private_key().sign(payload)
    return "dw1." + _b64url(payload) + "." + _b64url(sig)


# ---------- Stripe (stdlib urllib) ----------

def stripe_call(method, path, data=None):
    url = "https://api.stripe.com" + path
    headers = {"Authorization": "Bearer " + SK}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def create_checkout_session(base):
    return stripe_call("POST", "/v1/checkout/sessions", {
        "mode": "payment",
        "customer_creation": "always",
        "line_items[0][price]": PRICE_ID,
        "line_items[0][quantity]": 1,
        "success_url": base + "/api/redeem?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": base + "/",
        "allow_promotion_codes": "true",
        "client_reference_id": "driftwire-pro",
    })


def retrieve_session(session_id):
    return stripe_call("GET", "/v1/checkout/sessions/" + session_id)


# ---------- Pages ----------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DriftWire Pro</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:3rem auto;
      max-width:640px;padding:0 1.5rem;color:#1a1a1a;background:#fafafa;line-height:1.5}}
 h1{{font-size:1.5rem}} .key{{font-family:ui-monospace,Menlo,monospace;font-size:.82rem;
      background:#fff;border:1px solid #ddd;padding:1rem;word-break:break-all;border-radius:6px}}
 code{{background:#f0f0f0;padding:.15rem .35rem;border-radius:4px}}
 .btn{{display:inline-block;background:#1a1a1a;color:#fff;text-decoration:none;padding:.7rem 1.3rem;
       border-radius:6px;font-weight:600}}
 .ok{{color:#2e7d32;font-weight:700}} .muted{{color:#777;font-size:.85rem}}
</style></head><body>
{body}
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj), "application/json; charset=utf-8")

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            return self.handle_index()
        if path == "/api/checkout":
            return self.handle_checkout(redirect=True)
        if path == "/api/redeem":
            return self.handle_redeem()
        if path == "/health":
            return self._json(200, {"ok": True})
        if path.startswith("/demoapi/"):
            return self.handle_mock()
        return self._send(404, "not found", "text/plain")

    def do_OPTIONS(self):
        # CORS preflight for cross-origin fetch() (e.g. the github.io live demo).
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/checkout":
            return self.handle_checkout(redirect=False)
        if path == "/api/demo":
            return self.handle_demo()
        if path == "/api/demo-check":
            return self.handle_demo_check()
        return self._json(404, {"error": "not found"})

    # ---------- Free live demo: spec-vs-spec breaking-change detection ----------
    # `diff` reads two user-pasted specs and performs NO network I/O, so there is no
    # SSRF surface. External $refs are rejected (internal #/… refs only) to keep the
    # server from reading anything the caller didn't paste.

    _MAX_DEMO_BYTES = 200_000

    def handle_demo(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if len(raw) > self._MAX_DEMO_BYTES * 2 + 4096:
            return self._json(413, {"error": "request too large"})
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._json(400, {"error": "invalid JSON body"})
        old_txt = data.get("old") or ""
        new_txt = data.get("new") or ""
        if not old_txt.strip() or not new_txt.strip():
            return self._json(400, {"error": "both 'old' and 'new' spec text are required"})
        if len(old_txt) > self._MAX_DEMO_BYTES or len(new_txt) > self._MAX_DEMO_BYTES:
            return self._json(413, {"error": "spec too large (max 200KB each)"})
        try:
            from driftwire import diff, spec_loader
            old = spec_loader.parse_spec(old_txt, "<old>")
            new = spec_loader.parse_spec(new_txt, "<new>")
        except Exception as e:  # noqa: BLE001
            return self._json(422, {"error": "spec parse error: %s" % e})
        if self._has_external_ref(old) or self._has_external_ref(new):
            return self._json(422, {"error": "external $refs are not allowed in the demo (internal #/… only)"})
        try:
            findings = diff.diff_specs(old, new)
        except Exception as e:  # noqa: BLE001
            return self._json(422, {"error": "diff error: %s" % e})
        return self._json(200, {"count": len(findings), "findings": findings})

    # ---------- Free live demo: spec-vs-REALITY drift (the non-commodity wedge) ----------
    # `check` is what oasdiff/api2spec can't do: probe the DEPLOYED API and validate
    # responses against the spec. Here the "deployed API" is a bundled mock served by
    # THIS process (see /demoapi/*), so there is zero network egress and zero SSRF —
    # base_url is fixed to 127.0.0.1 and the spec/path-params are constants.

    def handle_mock(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/demoapi/users/"):
            # Spec says: id=integer, name=string, role∈{user,admin}, no extra fields.
            body = {"id": "42", "name": 123, "role": "superadmin", "extraField": True}
        elif path == "/demoapi/status":
            # Spec says: status∈{ok,degraded}, uptime=integer.
            body = {"status": "down", "uptime": "two days"}
        else:
            return self._json(404, {"error": "not found"})
        return self._json(200, body)

    def handle_demo_check(self):
        try:
            from driftwire import check
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": "check engine unavailable: %s" % e})
        port = self.server.server_port
        base_url = "http://127.0.0.1:%d" % port
        try:
            result = check.check_spec(DEMO_SPEC, base_url, path_params={"id": "42"},
                                      timeout=5.0)
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": "check error: %s" % e})
        return self._json(200, {
            "count": len(result["findings"]),
            "endpoints": result["endpoints"],
            "probed": result["probed"],
            "skipped": result["skipped"],
            "findings": result["findings"],
        })

    @staticmethod
    def _has_external_ref(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and not value.startswith("#"):
                    return True
                if Handler._has_external_ref(value):
                    return True
        elif isinstance(node, list):
            for item in node:
                if Handler._has_external_ref(item):
                    return True
        return False

    def handle_index(self):
        body = f"""
<h1>DriftWire Pro</h1>
<p>One-time purchase unlocks <b>HTML drift reports</b> and <b>.driftwire.yml</b> waiver
config in the DriftWire CLI — approve known drift while still blocking new drift in CI.</p>
<p><a class="btn" href="/api/checkout">Buy Pro — $29 one-time</a></p>
<p class="muted">License is delivered immediately after payment. Then run
<code>driftwire diff old.yaml new.yaml --format html --license &lt;your-key&gt;</code>.</p>
"""
        return self._send(200, PAGE.replace("{body}", body))

    def handle_checkout(self, redirect=False):
        if not SK or not PRICE_ID:
            return self._json(503, {"error": "payments not configured"})
        host = self.headers.get("Host") or ""
        base = ("https://" if not host.startswith("localhost") else "http://") + host
        try:
            sess = create_checkout_session(base)
        except Exception as e:  # noqa: BLE001
            return self._json(502, {"error": "stripe error: %s" % e})
        url = sess.get("url")
        if redirect and url:
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self._json(200, {"url": url, "id": sess.get("id")})

    def handle_redeem(self):
        session_id = (self._query().get("session_id") or [""])[0]
        if not session_id:
            return self._send(400, "missing session_id", "text/plain")
        try:
            sess = retrieve_session(session_id)
        except Exception as e:  # noqa: BLE001
            return self._send(502, "stripe error: %s" % e, "text/plain")
        status = sess.get("payment_status")
        if status not in ("paid", "no_payment_required"):
            body = f"<h1>Payment not complete</h1><p>Status: {html.escape(str(status))}. "
            body += "If you completed payment, wait a moment and refresh.</p>"
            return self._send(402, PAGE.replace("{body}", body))
        email = (sess.get("customer_details") or {}).get("email") or "your-email"
        try:
            key = sign_license(email, int(time.time()) + LICENSE_TTL_DAYS * 86400)
        except Exception as e:  # noqa: BLE001
            return self._send(500, "signing error: %s" % e, "text/plain")
        body = f"""
<h1><span class="ok">✓ License issued</span></h1>
<p>Your DriftWire Pro license for <b>{html.escape(email)}</b>:</p>
<div class="key">{html.escape(key)}</div>
<p>Copy it, then run:</p>
<code>export DRIFTWIRE_LICENSE="{html.escape(key)}"</code>
<p>…or pass <code>--license</code> on any <code>check</code>/<code>diff</code> command.</p>
"""
        return self._send(200, PAGE.replace("{body}", body))

    def log_message(self, *a):  # keep render logs quiet
        pass


def main():
    if not SK:
        print("WARNING: STRIPE_SECRET_KEY not set — checkout disabled.")
    if not PRIVKEY_B64:
        print("WARNING: DRIFTWIRE_LICENSE_PRIVKEY_B64 not set — cannot sign licenses.")
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
