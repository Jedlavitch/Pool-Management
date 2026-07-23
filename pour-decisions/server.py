#!/usr/bin/env python3
"""Pour Decisions server.

Serves the app and exposes POST /api/order, which asks Claude to turn any
drink request into a structured recipe the pour-tracker can follow.

  HTTP  : http://localhost:8791          (Mac / dev)
  HTTPS : https://<mac-ip>:8792          (iPad — camera needs a secure context;
          a self-signed cert is generated on first run if openssl is available)
"""
import json
import os
import ssl
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT = 8791
HTTPS_PORT = 8792
CERT = os.path.join(APP_DIR, "cert.pem")
KEY = os.path.join(APP_DIR, "key.pem")

try:
    import anthropic
    _client = anthropic.Anthropic()
except Exception:
    _client = None

MODEL = "claude-opus-4-8"

SYSTEM = """You are the AI brain of "Pour Decisions", a bar assistant whose camera watches a
clear glass and measures pours in real time. Turn whatever the bartender asks for into ONE
cocktail recipe. If they name a classic, give the accurate classic spec. If the request is
freeform ("something smoky", "surprise me", an ingredient list, a mood), invent something
genuinely good and drinkable. If the request isn't obviously a drink, interpret it playfully
as inspiration for one.

Complex cocktails are welcome — multi-spirit tiki builds, layered or flaming drinks, egg-white
sours, fat-washed or split-base specs (Ramos Gin Fizz, Zombie, Jungle Bird, Pisco Sour,
Sazerac...). Never dumb a drink down: give the full professional spec, with each technique
(dry shake, double strain, absinthe rinse, float, swizzle, flame) as its own step with real
timing and technique detail in the label.

Rules for the steps array:
- 3 to 14 steps total, in the order the bartender performs them.
- "pour" steps are liquids measured by the camera: include "oz" (US fl oz, quarter-ounce
  increments, 0.25 to 6). Put measurable liquid pours BEFORE ice/shaking whenever reasonable
  so the camera can read the level in the empty vessel.
- "action" steps are everything else (dashes of bitters, add ice, shake, stir, muddle,
  strain, top with soda "to taste"). No oz on these.
- "garnish" is the final step.
- spoken_intro: one short, charismatic sentence the app says aloud when the recipe starts.
- tagline: one witty line shown under the drink name."""

RECIPE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "glass", "tagline", "spoken_intro", "steps"],
    "properties": {
        "name": {"type": "string"},
        "glass": {"type": "string", "description": "serving glass, short (e.g. 'rocks, big cube')"},
        "tagline": {"type": "string"},
        "spoken_intro": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "label"],
                "properties": {
                    "type": {"type": "string", "enum": ["pour", "action", "garnish"]},
                    "label": {"type": "string"},
                    "oz": {"type": "number", "description": "US fl oz; pour steps only; 0.25-oz increments"},
                },
            },
        },
    },
}

_cache = {}
_cache_lock = threading.Lock()


def generate_recipe(order: str) -> dict:
    key = " ".join(order.lower().split())
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    resp = _client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM,
        messages=[{"role": "user", "content": order}],
        output_config={"format": {"type": "json_schema", "schema": RECIPE_SCHEMA}},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("The AI bartender declined that order.")
    text = next(b.text for b in resp.content if b.type == "text")
    recipe = json.loads(text)
    with _cache_lock:
        _cache[key] = recipe
    return recipe


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=APP_DIR, **kwargs)

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/api/order":
            return self._send_json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            order = json.loads(self.rfile.read(length)).get("order", "").strip()
            if not order:
                return self._send_json(400, {"error": "empty order"})
            if _client is None:
                return self._send_json(503, {"error": "AI offline: anthropic SDK unavailable"})
            recipe = generate_recipe(order)
            self._send_json(200, {"recipe": recipe})
        except anthropic.AuthenticationError:
            self._send_json(503, {"error": "AI offline: no API credentials (set ANTHROPIC_API_KEY)"})
        except anthropic.RateLimitError:
            self._send_json(503, {"error": "AI busy: rate limited, try again in a moment"})
        except anthropic.APIConnectionError:
            self._send_json(503, {"error": "AI offline: no network to the API"})
        except Exception as e:
            msg = str(e)
            if "authentication" in msg.lower() or "api_key" in msg.lower():
                msg = "AI offline: no API credentials — export ANTHROPIC_API_KEY before starting server.py"
                return self._send_json(503, {"error": msg})
            self._send_json(500, {"error": msg})

    def log_message(self, fmt, *args):
        pass  # keep the console quiet; errors surface as JSON


def ensure_cert() -> bool:
    if os.path.exists(CERT) and os.path.exists(KEY):
        return True
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", KEY, "-out", CERT, "-days", "825",
             "-subj", "/CN=pour-decisions.local"],
            check=True, capture_output=True,
        )
        return True
    except Exception:
        return False


def main():
    http_srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"Pour Decisions  http://localhost:{HTTP_PORT}")

    if ensure_cert():
        https_srv = ThreadingHTTPServer(("0.0.0.0", HTTPS_PORT), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)
        https_srv.socket = ctx.wrap_socket(https_srv.socket, server_side=True)
        threading.Thread(target=https_srv.serve_forever, daemon=True).start()
        print(f"iPad (camera needs HTTPS): https://<this-mac's-ip>:{HTTPS_PORT}")

    print("AI bartender:", "ready" if _client else "offline (pip install anthropic)")
    http_srv.serve_forever()


if __name__ == "__main__":
    main()
