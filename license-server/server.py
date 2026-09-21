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
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/checkout":
            return self.handle_checkout(redirect=False)
        return self._json(404, {"error": "not found"})

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
