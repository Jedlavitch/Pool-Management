#!/usr/bin/env python3
"""Pour Decisions server.

Serves the app and exposes POST /api/order, which asks Claude to turn any
drink request into a structured recipe the pour-tracker can follow.

  HTTP  : http://localhost:8791          (Mac / dev)
  HTTPS : https://<mac-ip>:8792          (iPad — camera needs a secure context;
          a self-signed cert is generated on first run if openssl is available)

Credentials: put ANTHROPIC_API_KEY=sk-ant-... in pour-decisions/.env
(gitignored), or export it before launching. The .env file is re-read on
demand, so adding the key does not require a server restart.
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
ENV_FILE = os.path.join(APP_DIR, ".env")

try:
    import anthropic
except Exception:
    anthropic = None

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

NO_KEY_MSG = ("AI offline: add ANTHROPIC_API_KEY=sk-ant-... to pour-decisions/.env "
              "(get a key at console.anthropic.com), then just order again")

_client = None
_client_lock = threading.Lock()
_cache = {}
_cache_lock = threading.Lock()


def _read_env_file() -> dict:
    cfg = {}
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return cfg


def get_client():
    """Build the client lazily so a key dropped into .env works without a restart."""
    global _client
    if anthropic is None:
        return None
    with _client_lock:
        if _client is not None:
            return _client
        cfg = _read_env_file()
        key = cfg.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        try:
            if key:
                # An explicit key targets the real API even if a stray
                # ANTHROPIC_BASE_URL leaked into this process's environment.
                _client = anthropic.Anthropic(
                    api_key=key,
                    base_url=cfg.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
                )
            else:
                _client = anthropic.Anthropic()  # env / profile resolution
        except Exception:
            _client = None
        return _client


def reset_client():
    global _client
    with _client_lock:
        _client = None


def save_key(key: str):
    lines = []
    try:
        with open(ENV_FILE) as f:
            lines = [l.rstrip("\n") for l in f]
    except OSError:
        pass
    lines = [l for l in lines if not l.strip().startswith("ANTHROPIC_API_KEY")]
    lines.append("ANTHROPIC_API_KEY=" + key)
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)


def generate_recipe(order: str) -> dict:
    key = " ".join(order.lower().split())
    with _cache_lock:
        if key in _cache:
            return _cache[key]

    client = get_client()
    if client is None:
        raise PermissionError(NO_KEY_MSG)

    resp = client.messages.create(
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
        if self.path == "/api/key":
            return self._handle_key()
        if self.path != "/api/order":
            return self._send_json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            order = json.loads(self.rfile.read(length)).get("order", "").strip()
            if not order:
                return self._send_json(400, {"error": "empty order"})
            if anthropic is None:
                return self._send_json(503, {"error": "AI offline: pip3 install anthropic, then restart"})
            recipe = generate_recipe(order)
            self._send_json(200, {"recipe": recipe})
        except PermissionError as e:
            self._send_json(503, {"error": str(e)})
        except Exception as e:
            msg = str(e)
            if anthropic and isinstance(e, anthropic.AuthenticationError):
                reset_client()
                return self._send_json(503, {"error": "AI offline: that API key was rejected — check pour-decisions/.env"})
            if anthropic and isinstance(e, anthropic.RateLimitError):
                return self._send_json(503, {"error": "AI busy: rate limited, try again in a moment"})
            if anthropic and isinstance(e, anthropic.APIConnectionError):
                return self._send_json(503, {"error": "AI offline: no network to the API"})
            if "authentication" in msg.lower() or "api_key" in msg.lower():
                reset_client()
                return self._send_json(503, {"error": NO_KEY_MSG})
            self._send_json(500, {"error": msg})

    def _handle_key(self):
        """One-time in-app setup: the bartender pastes their own key, we verify
        it with a free token-count call, then persist it to the gitignored .env."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            key = json.loads(self.rfile.read(length)).get("key", "")
        except Exception:
            return self._send_json(400, {"error": "bad request"})
        # pasted keys often carry line-wraps / invisible whitespace (Mail, Notes) — scrub, don't reject
        key = "".join(key.split())
        if not key.isascii():
            return self._send_json(400, {"error": "the key has odd characters in it (smart punctuation?) — copy it fresh from console.anthropic.com and paste again"})
        if len(key) < 20:
            return self._send_json(400, {"error": "that doesn't look like an API key — paste the whole thing"})
        if anthropic is None:
            return self._send_json(503, {"error": "pip3 install anthropic first, then restart"})
        try:
            probe = anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com")
            probe.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": "hi"}])
        except anthropic.AuthenticationError:
            return self._send_json(401, {"error": "the API rejected that key — make sure it's the CURRENT key from console.anthropic.com (regenerating there kills the old one)"})
        except anthropic.APIConnectionError:
            return self._send_json(503, {"error": "no network to the API right now"})
        except anthropic.NotFoundError:
            return self._send_json(403, {"error": "the key works, but this account can't use " + MODEL + " — check your plan in the console"})
        except Exception as e:
            # never save a key we couldn't verify — that leaves a silent landmine
            print("key validation error:", repr(e), flush=True)
            return self._send_json(500, {"error": "couldn't verify the key: " + str(e)})
        save_key(key)
        reset_client()
        self._send_json(200, {"ok": True})

    def do_GET(self):
        if self.path == "/api/status":
            has_key = bool(_read_env_file().get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
            return self._send_json(200, {"ai_ready": anthropic is not None and has_key})
        return super().do_GET()

    def log_message(self, fmt, *args):
        # static files stay quiet; API traffic is logged for debugging
        if "/api/" in (self.path or ""):
            print(self.address_string(), fmt % args, flush=True)


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

    has_key = bool(_read_env_file().get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    print("AI bartender:", "ready" if (anthropic and has_key) else
          "offline — add ANTHROPIC_API_KEY to pour-decisions/.env")
    http_srv.serve_forever()


if __name__ == "__main__":
    main()
