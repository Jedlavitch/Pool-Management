# Mar-A-Lavitch Pool Management 🏊

Everything it takes to run a swim club, in one small Python server and two web apps —
a desktop console for management and a phone-first PWA for the guards on deck.

No frameworks, no build step, no database. `server.py` is pure Python standard library,
the apps are single HTML files, and all state lives in one `data.json`.

```sh
python3 server.py
```

Then open **http://localhost:8765**.

| App | URL | Who it's for |
|---|---|---|
| **Management** | `/` or `/maralavitchmanagement` | Managers — staffing, compliance, reporting |
| **Mar-A-Lavitch Staff** | `/maralavitchstaff` | Lifeguards on their phones (installable PWA) |
| **Scan-to-log QR** | `/print-qr` | A printable poster guards scan to clock in |

Guards on the same Wi-Fi reach it at the address the management app displays — the server
resolves its own LAN IP and `.local` hostname so you don't have to look either one up.

## What it does

**Pool operations**
- Chemical logs with automatic pass / warn / fail grading against free-chlorine, pH, and
  combined-chlorine ranges — plus CSV export for the health department
- Multiple pools, each with its own status, address, tax rate, and deck layout
- Resources and announcements pushed from management to every staff phone

**Staffing**
- Employees, per-employee passwords, and which pools each one is staffed at
- Shift scheduling, including bulk creation, shift requests, and confirmations
- Clock in/out with an optional photo, a live "currently on site" board, and hours summaries
- Breaks: management assigns them, staff start and end them from their own phone

**Deck & point of sale**
- A drag-to-arrange deck map — loungers, cabanas, daybeds, tables, umbrellas — rendered
  identically on a manager's desktop and a guard's phone by the shared `pos-shared.js`
- Reservations and a waitlist that seat straight onto the map
- A snack-bar POS: menu management, order tickets, payment, settle, and void

## How it's put together

| File | Role |
|---|---|
| `server.py` | Threaded stdlib HTTP server; the whole JSON API |
| `pool-manager.html` | Management console (single file, ~70 KB) |
| `worker.html` | Staff PWA (single file) |
| `pos-shared.js` | Deck-map renderer + money/seat helpers shared by both apps |
| `sw.js`, `manifest.json` | Service worker and manifest that make the staff app installable |
| `data.json` | All state. Gitignored — it's live data, not source |
| `index.html`, `app-url.js` | Front-door page for GitHub Pages; points at wherever the server runs |

Seat coordinates live in a fixed 1000×700 design space and render as percentages, so one
saved layout scales to any screen without re-saving. Money is stored in whole cents
everywhere; dollars only exist for display.

## Deploying

The `Procfile` runs it as-is on Railway or anything Procfile-shaped.

| Variable | Why |
|---|---|
| `PORT` | Defaults to `8765`. Railway sets this itself. |
| `DATA_DIR` | Point at a persistent volume or `data.json`, `photos/`, and the signing key vanish on redeploy. |
| `SECRET_KEY` | Signs session cookies. Generated automatically if unset, but then it lives on the volume — set it explicitly to keep sessions alive across volume changes. |
| `ADMIN_PASSWORD` | The way into a fresh deploy, before any employee has a password. Sign in as **Administrator**. |

`RAILWAY_PUBLIC_DOMAIN` (set automatically) is also how the app knows it's public: it starts
refusing blank passwords and marks the session cookie `Secure`.

### Deploying to Railway

1. **railway.app** → New Project → Deploy from GitHub repo → pick this one. It detects Python
   from `requirements.txt` and runs the `Procfile`.
2. Add a **Volume**, mount path `/data`.
3. Under **Variables**, set `DATA_DIR=/data`, an `ADMIN_PASSWORD`, and a `SECRET_KEY` from:
   ```sh
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
4. Settings → Networking → **Generate Domain**.
5. Open the domain, sign in as **Administrator** with your `ADMIN_PASSWORD`, then add your
   pool and staff and give each person a password.

A fresh deploy starts empty — `data.json` is gitignored and never leaves your own server, so
nothing is carried up with the code. To bring the club's real data across, upload your local
`data.json` into the volume; otherwise set it up fresh from the dashboard.

Once real managers have passwords, remove `ADMIN_PASSWORD` to close the bootstrap door.

## Also in here

[`pour-decisions/`](pour-decisions/) — a separate side project: a voice-driven cocktail
coach that watches the glass through the camera and counts your pour. It has
[its own README](pour-decisions/README.md).
