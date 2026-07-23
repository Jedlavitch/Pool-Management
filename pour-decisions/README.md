# Pour Decisions 🍸

Voice-driven cocktail coach with camera-based pour tracking. Say "make me a margarita"
(or invent one — "something smoky with mezcal") and the app shows the recipe, watches the
glass through the camera, counts the ounces as you pour, and yells STOP at the right moment.

## Run it

```sh
python3 server.py
```

- Mac / dev: http://localhost:8791
- iPad on the same Wi-Fi: **https**://\<your-mac-ip\>:8792 — the camera requires a secure
  context on iOS, so use the HTTPS port and accept the self-signed certificate warning.

No camera handy? **Sim mode** renders a virtual glass that pours ~0.75 oz/sec through the
exact same detection pipeline.

## The AI bartender

`POST /api/order` uses the Claude API (`claude-opus-4-8`, structured outputs) to turn any
free-form order into a recipe the pour tracker can follow. It needs `pip3 install anthropic`
plus an API key — easiest is a `.env` file next to the server (gitignored):

```sh
# pour-decisions/.env
ANTHROPIC_API_KEY=sk-ant-...
```

Get a key at console.anthropic.com. The file is re-read on demand — drop it in and the very
next order uses it, no restart. (`export ANTHROPIC_API_KEY=...` before launching works too.)

Without it the app still works — the 12 built-in classics match locally and the header
shows "AI offline — classics only".

## GitHub Pages

`index.html` is fully static: hosted on Pages you get free HTTPS (so the iPad camera works
with zero cert fuss), voice, sim mode, and the built-in classics. The AI endpoint is the
only part that needs `server.py` running somewhere.

## How pour tracking works

Calibrate once on the empty vessel (per-row color baseline inside the guide box). Each
frame, rows whose color departs from the baseline are counted as liquid from the bottom
up; the fill fraction maps to ounces via the selected vessel capacity, and each pour step
measures the *delta* from when the step started — so ice, straws, and prior liquid don't
break the math. Colored liquid, a steady camera, decent light, and a clear vessel matter;
metal shaker tins are invisible to cameras.
