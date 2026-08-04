#!/usr/bin/env python3
import json, os, socket, threading, hashlib, hmac, base64, secrets, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime

# Serializes writes so concurrent requests can't corrupt or lose each other's
# updates once the server handles requests on multiple threads.
_DATA_LOCK = threading.RLock()

PORT = int(os.environ.get('PORT', 8765))

# DATA_DIR can be overridden via env var — point it at a persistent volume in cloud deployments
_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.environ.get('DATA_DIR', _HERE)
DATA_FILE  = os.path.join(DATA_DIR, 'data.json')
PHOTOS_DIR = os.path.join(DATA_DIR, 'photos')

# ══ Authentication ═════════════════════════════════════════════════════════
# Everything past the sign-in screen is staff data — names, phone numbers,
# hours, sales. None of it should be one URL away once this is on the public
# internet, so the API checks a session on every request instead of trusting
# the browser to have shown a login screen.

SESSION_DAYS = 7
COOKIE_NAME = 'mal_session'

# Set by Railway (and by hand elsewhere) — its presence is how we know this
# instance is reachable from outside the pool's own network.
PUBLIC_HOST = os.environ.get('RAILWAY_PUBLIC_DOMAIN') or os.environ.get('PUBLIC_DOMAIN') or ''
IS_PUBLIC = bool(PUBLIC_HOST)

# Escape hatch for a fresh public deploy, where nobody has a password yet.
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')


def _load_secret():
    """Key that signs session tokens. From the environment when provided,
    otherwise generated once and kept beside the data (gitignored). Rotating
    it just signs everyone out."""
    env = os.environ.get('SECRET_KEY')
    if env:
        return env.encode()
    path = os.path.join(DATA_DIR, '.secret')
    try:
        with open(path, 'rb') as f:
            saved = f.read().strip()
            if saved:
                return saved
    except OSError:
        pass
    generated = base64.urlsafe_b64encode(secrets.token_bytes(32))
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(generated)
        os.chmod(path, 0o600)
    except OSError:
        # Read-only disk: fall back to a per-boot key. Sessions won't survive
        # a restart, which is inconvenient but not insecure.
        pass
    return generated


SECRET = _load_secret()
PBKDF2_ROUNDS = 200_000


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, PBKDF2_ROUNDS)
    return 'pbkdf2${}${}${}'.format(
        PBKDF2_ROUNDS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(pw, stored):
    """Accepts both the hashed form and the plaintext left over from before
    hashing existed, so nobody is locked out by the upgrade."""
    if not stored:
        return False
    if stored.startswith('pbkdf2$'):
        try:
            _, rounds, salt_b64, hash_b64 = stored.split('$')
            dk = hashlib.pbkdf2_hmac('sha256', pw.encode(), base64.b64decode(salt_b64), int(rounds))
            return hmac.compare_digest(dk, base64.b64decode(hash_b64))
        except Exception:
            return False
    return hmac.compare_digest(pw, stored)


def is_legacy_password(stored):
    return bool(stored) and not stored.startswith('pbkdf2$')


def make_token(emp_id):
    exp = int(time.time()) + SESSION_DAYS * 86400
    msg = '{}:{}'.format(emp_id, exp)
    sig = hmac.new(SECRET, msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode('{}:{}'.format(msg, sig).encode()).decode().rstrip('=')


def read_token(token):
    """Return the employee id a token vouches for, or None."""
    try:
        padded = token + '=' * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        emp_id, exp, sig = raw.rsplit(':', 2)
        expect = hmac.new(SECRET, '{}:{}'.format(emp_id, exp).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        if int(exp) < time.time():
            return None
        return emp_id
    except Exception:
        return None


# Must stay in step with MGR_ROLES in worker.html, or the app offers buttons the
# server then refuses. "Pool Owner" is the most senior role staff can be given,
# so leaving it out locked the owner out of their own club's management.
MANAGEMENT_ROLES = ('Pool Owner', 'Owner', 'Manager', 'Staffer')

# Reachable without a session: the sign-in screen needs the name list, and the
# login call itself obviously can't require being logged in.
PUBLIC_API = ('/api/roster', '/api/auth', '/api/logout')

# Routes that change who can get in, or expose the whole club at once.
MANAGEMENT_API = (
    '/api/employee', '/api/employee/delete', '/api/employee/pools',
    '/api/employee/set-password', '/api/pool', '/api/pool/update',
    '/api/pool/delete', '/api/announcement', '/api/announcement/delete',
    '/api/resource', '/api/resource/delete', '/api/shift', '/api/shift/delete',
    '/api/shifts/bulk', '/api/menu', '/api/menu/update', '/api/menu/delete',
    '/api/menu/bulk', '/api/layout', '/api/notification/send',
    '/api/order/void', '/api/order/delete', '/api/order/settle',
)


def get_mdns_host():
    """Return stable .local mDNS hostname — never changes regardless of IP."""
    import subprocess
    try:
        name = subprocess.check_output(['scutil', '--get', 'LocalHostName'], stderr=subprocess.DEVNULL).decode().strip()
        if name:
            return f'{name}.local'
    except Exception:
        pass
    return socket.gethostname()

def get_base_url():
    """Return the public base URL — HTTPS on Railway/cloud, HTTP locally."""
    # Railway sets RAILWAY_PUBLIC_DOMAIN automatically
    domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN') or os.environ.get('PUBLIC_DOMAIN')
    if domain:
        return f'https://{domain.rstrip("/")}'
    return f'http://{get_mdns_host()}:{PORT}'

def get_local_ip():
    import subprocess
    for iface in ('en0', 'en1', 'en2', 'eth0', 'eth1'):
        try:
            out = subprocess.check_output(['ipconfig', 'getifaddr', iface], stderr=subprocess.DEVNULL).decode().strip()
            if out and not out.startswith('169.'):
                return out
        except Exception:
            pass
    try:
        out = subprocess.check_output(['ifconfig'], stderr=subprocess.DEVNULL).decode()
        import re
        for m in re.finditer(r'inet (192\.168\.\d+\.\d+|172\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+).*?netmask (0x\S+)', out):
            ip, mask = m.group(1), m.group(2)
            if mask != '0xffffffff':
                return ip
    except Exception:
        pass
    return '127.0.0.1'

def default_data():
    return {'employees': [], 'chemicals': [], 'shifts': [], 'punches': [], 'announcements': [], 'resources': [], 'pool_status': 'open', 'shift_requests': [], 'notifications': [], 'shift_confirmations': {}, 'pools': [], 'breaks': [],
            'menu': [], 'layouts': [], 'reservations': [], 'orders': [], 'waitlist': []}

# Entities that belong to a single pool and carry a poolId
POOL_SCOPED = ('shifts', 'chemicals', 'punches', 'announcements', 'resources', 'shift_requests', 'notifications', 'breaks',
               'menu', 'layouts', 'reservations', 'orders', 'waitlist')

# Deck items guests can actually be seated at — everything else is decor
BOOKABLE_TYPES = ('lounger', 'chair', 'cabana', 'table', 'daybed')

# ── Kitchen display ─────────────────────────────────────────────────────────
# An order carries two independent states, because money and food move on
# different clocks: `status` is the tab (open → paid, or void), and `kitchen`
# is the food (new → preparing → ready → served). A guest can pay up front and
# still be waiting on a burger; a tab can stay open long after the food landed.
KITCHEN_FLOW = ['new', 'preparing', 'ready', 'served']
KITCHEN_STAMPS = {'preparing': 'startedTs', 'ready': 'readyTs', 'served': 'servedTs'}

def next_ticket_num(data, pool_id):
    today_str = datetime.now().strftime('%Y-%m-%d')
    same_day = [o for o in data.get('orders', [])
                if o.get('date') == today_str and str(o.get('poolId')) == str(pool_id)]
    return max([o.get('num') or 0 for o in same_day], default=0) + 1

def to_cents(v):
    """Money is stored as whole cents everywhere so totals never drift the way
    floating-point dollars do. Accepts 4, '4', '4.50', '$4.50'."""
    if v is None or v == '':
        return 0
    try:
        if isinstance(v, str):
            v = v.replace('$', '').replace(',', '').strip()
        return max(0, int(round(float(v) * 100)))
    except (TypeError, ValueError):
        return 0

def clamp_num(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo

def to_minutes(t):
    """'14:30' → 870. Returns None for blank/garbage."""
    try:
        h, m = str(t).split(':')[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None

def overlaps(a_start, a_end, b_start, b_end):
    """Do two chair bookings collide? A booking with no end time is open-ended and
    runs until checkout, so it blocks anything that starts after it."""
    a1, b1 = to_minutes(a_start), to_minutes(b_start)
    if a1 is None or b1 is None:
        return True  # can't reason about it — treat as a clash and let staff sort it out
    a2, b2 = to_minutes(a_end), to_minutes(b_end)
    if a2 is None:
        a2 = 24 * 60
    if b2 is None:
        b2 = 24 * 60
    return a1 < b2 and b1 < a2

def blank_layout(pool_id):
    """An empty deck for a pool. Coordinates live in this 1000x700 design space and
    are scaled to whatever canvas the client draws on, so a layout looks the same
    on a phone and on a manager's desktop."""
    return {'poolId': pool_id, 'w': 1000, 'h': 700, 'zones': [], 'seats': []}

def migrate(data):
    """Bring older single-pool data forward to the multi-pool model. Idempotent —
    returns True only if something actually changed (so callers can persist once)."""
    changed = False
    pools = data.get('pools')
    if not pools:
        # Create a starter pool, inheriting the old global status if present
        starter = {
            'id': int(datetime.now().timestamp() * 1000),
            'name': 'Maralavitch Pool',
            'address': '',
            'status': data.get('pool_status', 'open'),
            'created': datetime.now().strftime('%Y-%m-%d'),
        }
        data['pools'] = [starter]
        pools = data['pools']
        changed = True
    default_pid = pools[0]['id']
    layouts = data.setdefault('layouts', [])
    for pool in pools:
        if 'status' not in pool:
            pool['status'] = 'open'; changed = True
        if 'address' not in pool:
            pool['address'] = ''; changed = True
        if 'tax_rate' not in pool:
            pool['tax_rate'] = 0.0; changed = True
        # Every pool owns exactly one deck layout
        if not any(l.get('poolId') == pool['id'] for l in layouts):
            layouts.append(blank_layout(pool['id'])); changed = True
    # Employees get a list of assigned pools
    for e in data.get('employees', []):
        if not isinstance(e.get('poolIds'), list) or not e.get('poolIds'):
            e['poolIds'] = [default_pid]; changed = True
    # Tag any untagged pool-scoped records with the default pool
    for key in POOL_SCOPED:
        for item in data.get(key, []):
            if isinstance(item, dict) and not item.get('poolId'):
                item['poolId'] = default_pid; changed = True
    return changed

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            data = json.load(f)
    else:
        data = default_data()
    # Ensure all expected top-level keys exist
    for k, v in default_data().items():
        if k not in data:
            data[k] = v
    if migrate(data):
        save_data(data)
    return data

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    # Write to a temp file then atomically replace, so a concurrent reader never
    # sees a partially written file (which would make the app appear "offline").
    with _DATA_LOCK:
        tmp = DATA_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, DATA_FILE)

MIMES = {
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.ico': 'image/x-icon',
}

def guess_mime(path):
    return MIMES.get(os.path.splitext(path)[1].lower(), 'text/html; charset=utf-8')

def serve_file(handler, path, mime=None):
    mime = mime or guess_mime(path)
    base = os.path.dirname(os.path.abspath(__file__))
    full = os.path.abspath(os.path.join(base, path.lstrip('/')))
    # Keep the catch-all GET route from walking out of the app directory via "../"
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
        handler.send_response(404); handler.end_headers(); return
    with open(full, 'rb') as f:
        body = f.read()
    handler.send_response(200)
    handler.send_header('Content-Type', mime)
    handler.send_header('Content-Length', len(body))
    handler.end_headers()
    handler.wfile.write(body)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    # ── Session helpers ────────────────────────────────────────────────────
    def session_token(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE_NAME and value:
                return value
        auth = self.headers.get('Authorization') or ''
        if auth.startswith('Bearer '):
            return auth[7:].strip()
        return None

    def current_user(self):
        """The signed-in employee, or None. Cached per request."""
        if hasattr(self, '_user_cache'):
            return self._user_cache
        user = None
        token = self.session_token()
        emp_id = read_token(token) if token else None
        if emp_id == 'admin':
            user = {'id': 'admin', 'name': 'Administrator', 'role': 'Owner'}
        elif emp_id:
            user = next((e for e in load_data().get('employees', [])
                         if str(e['id']) == emp_id), None)
        self._user_cache = user
        return user

    def set_session_cookie(self, emp_id):
        token = make_token(emp_id)
        bits = [
            '{}={}'.format(COOKIE_NAME, token),
            'Path=/',
            'Max-Age={}'.format(SESSION_DAYS * 86400),
            'HttpOnly',            # keeps it out of reach of page scripts
            'SameSite=Lax',
        ]
        if IS_PUBLIC:
            bits.append('Secure')  # HTTPS-only once it's on the internet
        self.send_header('Set-Cookie', '; '.join(bits))

    def clear_session_cookie(self):
        self.send_header('Set-Cookie',
                         '{}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'.format(COOKIE_NAME))

    def require_session(self, path):
        """True if the request may proceed. Writes the refusal itself if not."""
        if path in PUBLIC_API:
            return True
        # A fresh install on the LAN has no employees yet, so there is nobody
        # to sign in as and nothing to protect. Letting the first run through
        # is what stops the app bouncing between its own gate and a 401.
        # Never on a public host — there, ADMIN_PASSWORD is the way in.
        if not IS_PUBLIC and not load_data().get('employees'):
            return True
        # A public deploy with no ADMIN_PASSWORD and nobody enrolled has no way
        # in at all, which locks the owner out of their own club. Allow that
        # one case through so the club can be set up. It shuts itself the
        # moment the first employee exists, so finish setup straight away.
        if IS_PUBLIC and not ADMIN_PASSWORD and not load_data().get('employees'):
            return True
        user = self.current_user()
        if not user:
            self.send_json({'ok': False, 'error': 'Sign in required'}, 401)
            return False
        if path in MANAGEMENT_API and user.get('role') not in MANAGEMENT_ROLES:
            self.send_json({'ok': False, 'error': 'Management access required'}, 403)
            return False
        return True

    def send_json(self, data, status=200, session=None, end_session=False):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        if session is not None:
            self.set_session_cookie(session)
        if end_session:
            self.clear_session_cookie()
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200); self.cors(); self.end_headers()

    def do_GET(self):
        try:
            self._handle_get()
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self.send_json({'ok': False, 'error': 'Server error — try again.'}, 500)
            except Exception:
                pass

    def _handle_get(self):
        p = urlparse(self.path).path

        # The pages themselves are just shells — they show a sign-in screen and
        # can't render anything until the API hands them data, so only the API
        # needs guarding here.
        if p.startswith('/api/') and not self.require_session(p):
            return

        if p == '/api/roster':
            # Deliberately thin: enough to draw the "who are you?" picker and
            # nothing more. No phones, no hours, no punches.
            d = load_data()
            self.send_json({
                'employees': [{'id': e['id'], 'name': e.get('name', ''),
                               'role': e.get('role', ''),
                               'has_password': bool(e.get('password'))}
                              for e in d.get('employees', [])],
                'signed_in': bool(self.current_user()),
                'requires_password': IS_PUBLIC,
                # Nobody enrolled and no admin password configured: the club
                # still has to be created, so the dashboard opens unlocked.
                'setup_mode': not d.get('employees') and not ADMIN_PASSWORD,
            })
            return

        if p == '/api/me':
            u = self.current_user()
            self.send_json({'ok': True, 'id': u.get('id'), 'name': u.get('name', ''),
                            'role': u.get('role', '')})
            return

        if p in ('/', '/index.html', '/maralavitchmanagement'):
            serve_file(self, 'pool-manager.html')
        elif p in ('/quicklog', '/quicklog.html'):
            # Redirect to merged staff portal
            self.send_response(302)
            self.send_header('Location', '/maralavitchstaff')
            self.end_headers()
        elif p in ('/worker', '/worker.html', '/maralavitchstaff'):
            serve_file(self, 'worker.html')
        elif p in ('/kds', '/kds.html', '/kitchen'):
            serve_file(self, 'kds.html')
        elif p in ('/print-qr', '/print-qr.html'):
            serve_file(self, 'print-qr.html')
        elif p == '/manifest.json':
            serve_file(self, 'manifest.json', 'application/manifest+json')
        elif p == '/sw.js':
            serve_file(self, 'sw.js', 'application/javascript')
        elif p == '/api/data':
            d = load_data()
            out = dict(d)
            out['employees'] = []
            for e in d.get('employees', []):
                se = {k: v for k, v in e.items() if k != 'password'}
                se['has_password'] = bool(e.get('password'))
                out['employees'].append(se)
            self.send_json(out)
        elif p == '/api/orders':
            # Lightweight feed for the kitchen display and the waiters' status card.
            # Both poll it every few seconds, so it stays far smaller than /api/data.
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            pid = q.get('poolId', [None])[0]
            d = load_data()
            rows = d.get('orders', [])
            if pid not in (None, '', 'all'):
                rows = [o for o in rows if str(o.get('poolId')) == str(pid)]
            today_str = datetime.now().strftime('%Y-%m-%d')
            # Today's tickets, plus anything the kitchen still owes from before midnight
            rows = [o for o in rows
                    if o.get('date') == today_str
                    or (o.get('status') != 'void' and o.get('kitchen') in ('new', 'preparing', 'ready'))]
            self.send_json({
                'orders': sorted(rows, key=lambda o: o.get('createdTs') or 0),
                'pools': [{'id': x['id'], 'name': x.get('name', ''), 'status': x.get('status', 'open')}
                          for x in d.get('pools', [])],
                'now': int(datetime.now().timestamp() * 1000),
            })
        elif p == '/api/ip':
            self.send_json({'ip': get_local_ip(), 'host': get_mdns_host(), 'port': PORT, 'base_url': get_base_url()})
        elif p.startswith('/photos/'):
            fname = p[8:]
            photo_path = os.path.join(PHOTOS_DIR, fname)
            if os.path.exists(photo_path):
                with open(photo_path, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Content-Length', len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()
        else:
            serve_file(self, p)

    def do_POST(self):
        # Hold the data lock across the whole read-modify-write so simultaneous
        # writes from multiple devices can't overwrite each other.
        try:
            with _DATA_LOCK:
                self._handle_post()
        except Exception:
            # Without this, an unexpected error mid-handler just drops the
            # connection: the waiter's button "does nothing", nothing is logged,
            # and the bug is invisible. Answer in JSON and leave a trace.
            import traceback
            traceback.print_exc()
            try:
                self.send_json({'ok': False, 'error': 'Server error — try again.'}, 500)
            except Exception:
                pass  # connection already gone; the log line is what matters

    def _handle_post(self):
        p = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        data = load_data()
        ts = lambda: int(datetime.now().timestamp() * 1000)

        if not self.require_session(p):
            return

        if p == '/api/chemical':
            body['id'] = ts()
            if not body.get('status'):
                cl = float(body.get('cl', 0))
                ph = float(body.get('ph', 0))
                ccl = body.get('ccl')
                cl_ok = 1 <= cl <= 3
                ph_ok = 7.2 <= ph <= 7.8
                ccl_ok = ccl is None or float(ccl) < 0.5
                body['status'] = 'pass' if (cl_ok and ph_ok and ccl_ok) else ('fail' if (cl < 0.5 or ph < 7.0 or ph > 8.0) else 'warn')
            data['chemicals'].insert(0, body)
            save_data(data)
            self.send_json({'ok': True, 'id': body['id']})

        elif p == '/api/employee':
            body['id'] = ts()
            if not isinstance(body.get('poolIds'), list):
                body['poolIds'] = []
            data['employees'].append(body)
            save_data(data)
            self.send_json({'ok': True, 'employee': body})

        elif p == '/api/employee/delete':
            data['employees'] = [e for e in data['employees'] if e['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/employee/pools':
            # Set which pools an employee is staffed at
            emp_id = str(body.get('empId', ''))
            emp = next((e for e in data['employees'] if str(e['id']) == emp_id), None)
            if emp:
                ids = body.get('poolIds', [])
                emp['poolIds'] = ids if isinstance(ids, list) else []
                save_data(data)
                self.send_json({'ok': True})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/pool':
            body['id'] = ts()
            body.setdefault('status', 'open')
            body.setdefault('address', '')
            body.setdefault('created', datetime.now().strftime('%Y-%m-%d'))
            body.setdefault('tax_rate', 0.0)
            data.setdefault('pools', []).append(body)
            data.setdefault('layouts', []).append(blank_layout(body['id']))
            save_data(data)
            self.send_json({'ok': True, 'pool': body})

        elif p == '/api/pool/update':
            pool = next((x for x in data.get('pools', []) if x['id'] == body.get('id')), None)
            if pool:
                for k in ('name', 'address', 'status'):
                    if k in body:
                        pool[k] = body[k]
                # Square's application id is a public identifier, not a secret —
                # it only names the app in the deep link. The credentials that
                # actually move money live inside Square Point of Sale on the
                # waiter's phone, and never come near this server.
                if 'square_app_id' in body:
                    pool['square_app_id'] = (body['square_app_id'] or '').strip()
                if 'tax_rate' in body:
                    try:
                        pool['tax_rate'] = max(0.0, min(30.0, float(body['tax_rate'])))
                    except (TypeError, ValueError):
                        pass
                save_data(data)
                self.send_json({'ok': True, 'pool': pool})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/pool/delete':
            pid = body.get('id')
            pools = data.get('pools', [])
            if len(pools) <= 1:
                self.send_json({'ok': False, 'error': 'Cannot delete the last pool'}, 400)
            else:
                data['pools'] = [x for x in pools if x['id'] != pid]
                # Cascade-delete everything scoped to that pool
                for key in POOL_SCOPED:
                    data[key] = [it for it in data.get(key, []) if it.get('poolId') != pid]
                # Unassign employees from the removed pool
                for e in data.get('employees', []):
                    if isinstance(e.get('poolIds'), list):
                        e['poolIds'] = [x for x in e['poolIds'] if x != pid]
                save_data(data)
                self.send_json({'ok': True})

        elif p == '/api/employee/set-password':
            emp_id = str(body.get('empId', ''))
            emp = next((e for e in data['employees'] if str(e['id']) == emp_id), None)
            if emp:
                if body.get('password'):
                    emp['password'] = hash_password(body['password'])
                elif 'password' in emp:
                    del emp['password']
                save_data(data)
                self.send_json({'ok': True})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/auth':
            emp_id = str(body.get('empId', ''))
            password = body.get('password', '')

            # Works before anyone has a password — the way into a fresh public
            # deploy. Set ADMIN_PASSWORD in the environment to enable it.
            if ADMIN_PASSWORD and emp_id == 'admin':
                if hmac.compare_digest(password, ADMIN_PASSWORD):
                    self.send_json({'ok': True, 'role': 'Owner', 'name': 'Administrator'},
                                   session='admin')
                else:
                    self.send_json({'ok': False, 'error': 'Incorrect password'}, 401)
                return

            emp = next((e for e in data['employees'] if str(e['id']) == emp_id), None)
            if not emp:
                self.send_json({'ok': False, 'error': 'Employee not found'}, 404)
                return

            stored = emp.get('password')
            if not stored:
                # On the LAN this is the long-standing convenience: pick your
                # name and you're in. Once the app is on the public internet a
                # blank password is not a credential, so it's refused there.
                if IS_PUBLIC:
                    self.send_json({'ok': False, 'needs_password': True,
                                    'error': 'A manager must set your password before you can sign in.'}, 403)
                else:
                    self.send_json({'ok': True, 'role': emp.get('role', ''), 'name': emp.get('name', '')},
                                   session=emp_id)
                return

            if verify_password(password, stored):
                # Quietly upgrade the old plaintext entries as people sign in.
                if is_legacy_password(stored):
                    emp['password'] = hash_password(password)
                    save_data(data)
                self.send_json({'ok': True, 'role': emp.get('role', ''), 'name': emp.get('name', '')},
                               session=emp_id)
            else:
                self.send_json({'ok': False, 'error': 'Incorrect password'}, 401)

        elif p == '/api/logout':
            self.send_json({'ok': True}, end_session=True)

        elif p == '/api/shift':
            body['id'] = ts()
            data['shifts'].append(body)
            # Create notification for the scheduled employee
            emp_id = str(body.get('empId', ''))
            shift_date = body.get('date', '')
            shift_start = body.get('start', '')
            shift_end = body.get('end', '')
            shift_role = body.get('role', '')
            if emp_id:
                def _fmt(t):
                    if not t: return ''
                    try:
                        h, m = int(t[:2]), int(t[3:5])
                        return f"{h%12 or 12}:{m:02d} {'AM' if h<12 else 'PM'}"
                    except Exception:
                        return t
                try:
                    from datetime import datetime as _dt
                    date_lbl = _dt.strptime(shift_date, '%Y-%m-%d').strftime('%a %b %-d')
                except Exception:
                    date_lbl = shift_date
                notif = {
                    'id': ts() + 1,
                    'empId': emp_id,
                    'poolId': body.get('poolId'),
                    'title': '📅 New Shift Added',
                    'message': f"You've been scheduled: {date_lbl}, {_fmt(shift_start)} – {_fmt(shift_end)} ({shift_role})",
                    'read': False,
                    'ts': datetime.now().strftime('%Y-%m-%d %H:%M')
                }
                data.setdefault('notifications', []).insert(0, notif)
            save_data(data)
            self.send_json({'ok': True, 'shift': body})

        elif p == '/api/shifts/bulk':
            # Create many shifts at once (one person, multiple days), with a single
            # summary notification instead of one per shift.
            shifts = body.get('shifts', [])
            if not shifts:
                self.send_json({'ok': False, 'error': 'No shifts provided'}, 400)
            else:
                base = ts()
                created = []
                for i, s in enumerate(shifts):
                    s['id'] = base + i
                    data['shifts'].append(s)
                    created.append(s)
                emp_id = str(created[0].get('empId', ''))
                if emp_id:
                    def _fmtd(ds):
                        try:
                            return datetime.strptime(ds, '%Y-%m-%d').strftime('%b %-d')
                        except Exception:
                            return ds
                    dates = sorted(s.get('date', '') for s in created)
                    n = len(created)
                    span = _fmtd(dates[0]) if n == 1 else f"{_fmtd(dates[0])} – {_fmtd(dates[-1])}"
                    data.setdefault('notifications', []).insert(0, {
                        'id': base + len(created) + 1,
                        'empId': emp_id, 'poolId': created[0].get('poolId'),
                        'title': '📅 New Shifts Scheduled',
                        'message': f"You've been scheduled for {n} shift{'s' if n != 1 else ''} ({span}).",
                        'read': False, 'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    })
                save_data(data)
                self.send_json({'ok': True, 'count': len(created)})

        elif p == '/api/shift/delete':
            data['shifts'] = [s for s in data['shifts'] if s['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/punch':
            # Legacy toggle endpoint (used by manager dashboard)
            emp_id = str(body['empId'])
            today_str = datetime.now().strftime('%Y-%m-%d')
            now_time = datetime.now().strftime('%H:%M')
            open_p = next((x for x in data['punches']
                           if str(x['empId']) == emp_id and x['date'] == today_str and not x.get('out')), None)
            if open_p:
                open_p['out'] = now_time
                a = datetime.strptime(open_p['date'] + 'T' + open_p['in'], '%Y-%m-%dT%H:%M')
                b = datetime.strptime(open_p['date'] + 'T' + open_p['out'], '%Y-%m-%dT%H:%M')
                open_p['hours'] = round((b - a).seconds / 3600, 2)
                save_data(data)
                self.send_json({'action': 'out', 'punch': open_p})
            else:
                punch = {'id': ts(), 'empId': emp_id, 'empName': body.get('empName', ''),
                         'poolId': body.get('poolId'),
                         'date': today_str, 'in': now_time, 'out': None, 'hours': None}
                data['punches'].append(punch)
                save_data(data)
                self.send_json({'action': 'in', 'punch': punch})

        elif p == '/api/punch/in':
            emp_id = str(body['empId'])
            today_str = datetime.now().strftime('%Y-%m-%d')
            now_time = datetime.now().strftime('%H:%M')
            already = next((x for x in data['punches']
                            if str(x['empId']) == emp_id and x['date'] == today_str and not x.get('out')), None)
            if already:
                self.send_json({'error': 'Already clocked in', 'punch': already})
            else:
                punch = {
                    'id': ts(), 'empId': emp_id, 'empName': body.get('empName', ''),
                    'poolId': body.get('poolId'),
                    'date': today_str, 'in': now_time, 'out': None, 'hours': None,
                    'checkin_answers': body.get('answers', {}),
                    'checkin_flags': body.get('flags', [])
                }
                data['punches'].append(punch)
                save_data(data)
                self.send_json({'action': 'in', 'punch': punch})

        elif p == '/api/punch/out':
            emp_id = str(body['empId'])
            today_str = datetime.now().strftime('%Y-%m-%d')
            now_time = datetime.now().strftime('%H:%M')
            open_p = next((x for x in data['punches']
                           if str(x['empId']) == emp_id and x['date'] == today_str and not x.get('out')), None)
            if not open_p:
                self.send_json({'error': 'Not clocked in'})
            else:
                open_p['out'] = now_time
                a = datetime.strptime(open_p['date'] + 'T' + open_p['in'], '%Y-%m-%dT%H:%M')
                b = datetime.strptime(open_p['date'] + 'T' + open_p['out'], '%Y-%m-%dT%H:%M')
                open_p['hours'] = round((b - a).seconds / 3600, 2)
                open_p['checkout_answers'] = body.get('answers', {})
                open_p['checkout_photo'] = body.get('photo', None)
                open_p['checkout_flags'] = body.get('flags', [])
                save_data(data)
                self.send_json({'action': 'out', 'punch': open_p})

        elif p == '/api/announcement':
            body['id'] = ts()
            body['posted'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            data.setdefault('announcements', []).insert(0, body)
            save_data(data)
            self.send_json({'ok': True, 'announcement': body})

        elif p == '/api/announcement/delete':
            data['announcements'] = [a for a in data.get('announcements',[]) if a['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/resource':
            body['id'] = ts()
            data.setdefault('resources', []).append(body)
            save_data(data)
            self.send_json({'ok': True, 'resource': body})

        elif p == '/api/resource/delete':
            data['resources'] = [r for r in data.get('resources',[]) if r['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/pool_status':
            # Per-pool status; falls back to the global field if no pool given
            pid = body.get('poolId')
            pool = next((x for x in data.get('pools', []) if x['id'] == pid), None)
            if pool:
                pool['status'] = body.get('status', 'open')
            else:
                data['pool_status'] = body.get('status', 'open')
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/shift-request':
            body['id'] = ts()
            body['submitted'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            body['status'] = 'pending'
            data.setdefault('shift_requests', []).insert(0, body)
            save_data(data)
            self.send_json({'ok': True, 'request': body})

        elif p == '/api/shift-request/update':
            req_id = body.get('id')
            req = next((r for r in data.get('shift_requests', []) if r['id'] == req_id), None)
            if req:
                new_status = body.get('status', req['status'])
                req['status'] = new_status
                # Notify the employee when a request is approved or denied
                emp_id = str(req.get('empId', ''))
                if emp_id and new_status in ('approved', 'denied'):
                    req_type = req.get('type', 'shift')
                    req_date = req.get('date', '')
                    try:
                        date_lbl = datetime.strptime(req_date, '%Y-%m-%d').strftime('%a %b %-d')
                    except Exception:
                        date_lbl = req_date
                    if new_status == 'approved':
                        title = '✅ Shift Request Approved' if req_type == 'shift' else '✅ Day Off Approved'
                        msg   = f"Your shift request for {date_lbl} has been approved." if req_type == 'shift' else f"Your day off request for {date_lbl} was approved."
                    else:
                        title = '❌ Shift Request Denied' if req_type == 'shift' else '❌ Day Off Denied'
                        msg   = f"Your shift request for {date_lbl} was not approved." if req_type == 'shift' else f"Your day off request for {date_lbl} was not approved."
                    notif = {
                        'id': int(datetime.now().timestamp() * 1000) + 2,
                        'empId': emp_id,
                        'poolId': req.get('poolId'),
                        'title': title,
                        'message': msg,
                        'read': False,
                        'ts': datetime.now().strftime('%Y-%m-%d %H:%M')
                    }
                    data.setdefault('notifications', []).insert(0, notif)
                save_data(data)
                self.send_json({'ok': True})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/shift-request/delete':
            data['shift_requests'] = [r for r in data.get('shift_requests', []) if r['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/notification/read':
            emp_id = str(body.get('empId', ''))
            for n in data.get('notifications', []):
                if str(n.get('empId', '')) == emp_id:
                    n['read'] = True
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/notification/send':
            # Management → staff. target = 'all' (everyone staffed at the pool) or an employee id
            pool_id = body.get('poolId')
            target = str(body.get('target', 'all'))
            title = (body.get('title') or '📢 Message from Management').strip()
            message = (body.get('message') or '').strip()
            if not message:
                self.send_json({'ok': False, 'error': 'Message is empty'}, 400)
            else:
                if target == 'all':
                    recipients = [e for e in data.get('employees', [])
                                  if pool_id is None or pool_id in (e.get('poolIds') or [])]
                else:
                    recipients = [e for e in data.get('employees', []) if str(e['id']) == target]
                base = int(datetime.now().timestamp() * 1000)
                stamp = datetime.now().strftime('%Y-%m-%d %H:%M')
                for i, e in enumerate(recipients):
                    data.setdefault('notifications', []).insert(0, {
                        'id': base + i,
                        'empId': str(e['id']),
                        'poolId': pool_id,
                        'title': title,
                        'message': message,
                        'read': False,
                        'ts': stamp,
                        'from_mgmt': True,
                    })
                save_data(data)
                self.send_json({'ok': True, 'sent': len(recipients)})

        elif p == '/api/shift/confirm':
            emp_id = str(body.get('empId', ''))
            shift_id = body.get('shiftId')
            note = body.get('note', '')
            key = f"{emp_id}_{shift_id}"
            data.setdefault('shift_confirmations', {})[key] = {
                'confirmed': True,
                'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'note': note
            }
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/break':
            # Only managers and staffers may assign breaks
            assigner = next((e for e in data['employees'] if str(e['id']) == str(body.get('assignedBy', ''))), None)
            if not assigner or assigner.get('role') not in ('Manager', 'Staffer'):
                self.send_json({'ok': False, 'error': 'Only managers and staffers can assign breaks.'}, 403)
            else:
                body['id'] = ts()
                body['assignedByName'] = assigner.get('name', '')
                body.setdefault('status', 'scheduled')
                body.setdefault('startedAt', None)
                body.setdefault('endedAt', None)
                body.setdefault('actualMinutes', None)
                data.setdefault('breaks', []).append(body)
                # Notify the employee
                emp_id = str(body.get('empId', ''))
                if emp_id:
                    def _fmt(t):
                        if not t: return ''
                        try:
                            h, m = int(t[:2]), int(t[3:5])
                            return f"{h%12 or 12}:{m:02d} {'AM' if h<12 else 'PM'}"
                        except Exception:
                            return t
                    dur = body.get('duration', '')
                    when = body.get('start')
                    msg = f"You have a {dur}-minute break" + (f" at {_fmt(when)}" if when else "") + " today."
                    data.setdefault('notifications', []).insert(0, {
                        'id': ts() + 1, 'empId': emp_id, 'poolId': body.get('poolId'),
                        'title': '☕ Break Scheduled', 'message': msg, 'read': False,
                        'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    })
                save_data(data)
                self.send_json({'ok': True, 'break': body})

        elif p == '/api/break/start':
            br = next((b for b in data.get('breaks', []) if b['id'] == body.get('id')), None)
            if br:
                br['status'] = 'active'
                br['startedAt'] = datetime.now().strftime('%H:%M')
                save_data(data)
                self.send_json({'ok': True, 'break': br})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/break/end':
            br = next((b for b in data.get('breaks', []) if b['id'] == body.get('id')), None)
            if br:
                br['status'] = 'completed'
                br['endedAt'] = datetime.now().strftime('%H:%M')
                if br.get('startedAt'):
                    try:
                        a = datetime.strptime(br['startedAt'], '%H:%M')
                        z = datetime.strptime(br['endedAt'], '%H:%M')
                        br['actualMinutes'] = max(0, round((z - a).seconds / 60))
                    except Exception:
                        pass
                save_data(data)
                self.send_json({'ok': True, 'break': br})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/break/delete':
            data['breaks'] = [b for b in data.get('breaks', []) if b['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        # ── Menu ────────────────────────────────────────────────────────────
        elif p == '/api/menu':
            body['id'] = ts()
            body['name'] = (body.get('name') or '').strip()
            if not body['name']:
                self.send_json({'ok': False, 'error': 'Item needs a name'}, 400); return
            body['price'] = to_cents(body.get('price'))
            body.setdefault('category', 'General')
            body.setdefault('emoji', '')
            body.setdefault('desc', '')
            body.setdefault('taxable', True)
            body.setdefault('active', True)
            data.setdefault('menu', []).append(body)
            save_data(data)
            self.send_json({'ok': True, 'item': body})

        elif p == '/api/menu/update':
            item = next((m for m in data.get('menu', []) if m['id'] == body.get('id')), None)
            if not item:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            for k in ('name', 'category', 'emoji', 'desc'):
                if k in body:
                    item[k] = (body[k] or '').strip()
            if 'price' in body:
                item['price'] = to_cents(body['price'])
            for k in ('taxable', 'active'):
                if k in body:
                    item[k] = bool(body[k])
            save_data(data)
            self.send_json({'ok': True, 'item': item})

        elif p == '/api/menu/delete':
            data['menu'] = [m for m in data.get('menu', []) if m['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/menu/bulk':
            # Seed a starter menu in one shot (used by the "load starter menu" button)
            pool_id = body.get('poolId')
            base = ts()
            added = []
            for i, it in enumerate(body.get('items', [])):
                name = (it.get('name') or '').strip()
                if not name:
                    continue
                added.append({
                    'id': base + i, 'poolId': pool_id, 'name': name,
                    'price': to_cents(it.get('price')), 'category': it.get('category') or 'General',
                    'emoji': it.get('emoji', ''), 'desc': it.get('desc', ''),
                    'taxable': bool(it.get('taxable', True)), 'active': True,
                })
            data.setdefault('menu', []).extend(added)
            save_data(data)
            self.send_json({'ok': True, 'count': len(added)})

        # ── Deck layout ─────────────────────────────────────────────────────
        elif p == '/api/layout':
            pool_id = body.get('poolId')
            if pool_id is None:
                self.send_json({'ok': False, 'error': 'Missing poolId'}, 400); return
            layouts = data.setdefault('layouts', [])
            layout = next((l for l in layouts if l.get('poolId') == pool_id), None)
            if not layout:
                layout = blank_layout(pool_id)
                layouts.append(layout)
            seats = []
            for s in body.get('seats', []):
                stype = s.get('type', 'lounger')
                seats.append({
                    'id': s.get('id') or ts() + len(seats),
                    'label': (s.get('label') or '').strip(),
                    'type': stype,
                    'x': clamp_num(s.get('x'), 0, layout.get('w', 1000)),
                    'y': clamp_num(s.get('y'), 0, layout.get('h', 700)),
                    'rot': clamp_num(s.get('rot'), 0, 359),
                    'cap': max(1, int(s.get('cap') or 1)),
                    'bookable': bool(s.get('bookable', stype in BOOKABLE_TYPES)),
                })
            zones = []
            for z in body.get('zones', []):
                zones.append({
                    'id': z.get('id') or ts() + 500 + len(zones),
                    'name': (z.get('name') or '').strip(),
                    'x': clamp_num(z.get('x'), 0, layout.get('w', 1000)),
                    'y': clamp_num(z.get('y'), 0, layout.get('h', 700)),
                    'w': clamp_num(z.get('w'), 20, layout.get('w', 1000)),
                    'h': clamp_num(z.get('h'), 20, layout.get('h', 700)),
                    'color': z.get('color') or '#38bdf8',
                    'shape': z.get('shape') or 'rect',
                })
            layout['seats'] = seats
            layout['zones'] = zones
            save_data(data)
            self.send_json({'ok': True, 'layout': layout})

        # ── Chair reservations ──────────────────────────────────────────────
        elif p == '/api/reservation':
            seat_id = body.get('seatId')
            date_str = body.get('date') or datetime.now().strftime('%Y-%m-%d')
            # One live booking per chair per day — reject a double-book outright
            clash = next((r for r in data.get('reservations', [])
                          if r.get('seatId') == seat_id and r.get('date') == date_str
                          and r.get('status') in ('reserved', 'active')
                          and overlaps(r.get('start'), r.get('end'), body.get('start'), body.get('end'))), None)
            if clash:
                self.send_json({'ok': False, 'error': f"That chair is already booked for {clash.get('guestName') or 'a guest'} at that time."}, 409)
                return
            res = {
                'id': ts(),
                'poolId': body.get('poolId'),
                'seatId': seat_id,
                'seatLabel': body.get('seatLabel', ''),
                'guestName': (body.get('guestName') or '').strip() or 'Guest',
                'party': max(1, int(body.get('party') or 1)),
                'phone': (body.get('phone') or '').strip(),
                'note': (body.get('note') or '').strip(),
                'date': date_str,
                'start': body.get('start') or datetime.now().strftime('%H:%M'),
                'end': body.get('end') or '',
                'status': 'active' if body.get('seatNow') else 'reserved',
                'empId': str(body.get('empId', '')),
                'empName': body.get('empName', ''),
                'created': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'checkedInAt': datetime.now().strftime('%H:%M') if body.get('seatNow') else None,
                'checkedOutAt': None,
            }
            data.setdefault('reservations', []).insert(0, res)
            save_data(data)
            self.send_json({'ok': True, 'reservation': res})

        elif p == '/api/reservation/update':
            res = next((r for r in data.get('reservations', []) if r['id'] == body.get('id')), None)
            if not res:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            action = body.get('action')
            if action == 'checkin':
                res['status'] = 'active'
                res['checkedInAt'] = datetime.now().strftime('%H:%M')
            elif action == 'checkout':
                res['status'] = 'done'
                res['checkedOutAt'] = datetime.now().strftime('%H:%M')
            elif action == 'cancel':
                res['status'] = 'cancelled'
            for k in ('guestName', 'party', 'phone', 'note', 'start', 'end'):
                if k in body:
                    res[k] = body[k]
            if 'party' in body:
                res['party'] = max(1, int(body.get('party') or 1))
            save_data(data)
            self.send_json({'ok': True, 'reservation': res})

        elif p == '/api/reservation/delete':
            data['reservations'] = [r for r in data.get('reservations', []) if r['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        # ── Waitlist ────────────────────────────────────────────────────────
        elif p == '/api/waitlist':
            entry = {
                'id': ts(),
                'poolId': body.get('poolId'),
                'name': (body.get('name') or '').strip() or 'Guest',
                'party': max(1, int(body.get('party') or 1)),
                'phone': (body.get('phone') or '').strip(),
                'note': (body.get('note') or '').strip(),
                'date': body.get('date') or datetime.now().strftime('%Y-%m-%d'),
                'added': datetime.now().strftime('%H:%M'),
                'status': 'waiting',
                'seatedSeatId': None,
            }
            data.setdefault('waitlist', []).append(entry)
            save_data(data)
            self.send_json({'ok': True, 'entry': entry})

        elif p == '/api/waitlist/update':
            entry = next((w for w in data.get('waitlist', []) if w['id'] == body.get('id')), None)
            if not entry:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            action = body.get('action')
            if action == 'seat':
                # Seating a waiting party creates their reservation in the same write
                seat_id = body.get('seatId')
                date_str = entry['date']
                clash = next((r for r in data.get('reservations', [])
                              if r.get('seatId') == seat_id and r.get('date') == date_str
                              and r.get('status') in ('reserved', 'active')), None)
                if clash:
                    self.send_json({'ok': False, 'error': 'That chair is already taken.'}, 409); return
                res = {
                    'id': ts(), 'poolId': entry['poolId'], 'seatId': seat_id,
                    'seatLabel': body.get('seatLabel', ''), 'guestName': entry['name'],
                    'party': entry['party'], 'phone': entry.get('phone', ''), 'note': entry.get('note', ''),
                    'date': date_str, 'start': datetime.now().strftime('%H:%M'), 'end': body.get('end') or '',
                    'status': 'active', 'empId': str(body.get('empId', '')), 'empName': body.get('empName', ''),
                    'created': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    'checkedInAt': datetime.now().strftime('%H:%M'), 'checkedOutAt': None,
                }
                data.setdefault('reservations', []).insert(0, res)
                entry['status'] = 'seated'
                entry['seatedSeatId'] = seat_id
                save_data(data)
                self.send_json({'ok': True, 'reservation': res}); return
            elif action == 'remove':
                entry['status'] = 'left'
            for k in ('name', 'party', 'phone', 'note'):
                if k in body:
                    entry[k] = body[k]
            save_data(data)
            self.send_json({'ok': True, 'entry': entry})

        elif p == '/api/waitlist/delete':
            data['waitlist'] = [w for w in data.get('waitlist', []) if w['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        # ── Point of sale ───────────────────────────────────────────────────
        elif p == '/api/order':
            pool = next((x for x in data.get('pools', []) if x['id'] == body.get('poolId')), None)
            tax_rate = float((pool or {}).get('tax_rate') or 0.0)
            menu_by_id = {m['id']: m for m in data.get('menu', [])}
            lines, subtotal, taxable_base = [], 0, 0
            for li in body.get('items', []):
                qty = max(1, int(li.get('qty') or 1))
                src = menu_by_id.get(li.get('menuId'))
                # Trust the menu's own price whenever the item still exists, so a
                # stale tablet can't ring up yesterday's prices.
                if src:
                    name, price, taxable = src['name'], src['price'], bool(src.get('taxable', True))
                else:
                    name = (li.get('name') or 'Item').strip()
                    price = to_cents(li.get('price'))
                    taxable = bool(li.get('taxable', True))
                line_total = price * qty
                subtotal += line_total
                if taxable:
                    taxable_base += line_total
                # Tap-selected customisations ("No ice", "Well done"). Kept as a
                # list rather than folded into the note so the kitchen can show
                # them as chips and sales can count them later.
                mods = [str(m).strip()[:40] for m in (li.get('mods') or []) if str(m).strip()][:8]
                lines.append({'menuId': li.get('menuId'), 'name': name, 'price': price,
                              'qty': qty, 'note': (li.get('note') or '').strip(),
                              'mods': mods, 'total': line_total})
            if not lines:
                self.send_json({'ok': False, 'error': 'Order is empty'}, 400); return
            tax = int(round(taxable_base * tax_rate / 100.0))
            tip = to_cents(body.get('tip'))
            payment = body.get('payment') or 'cash'
            comped = payment == 'comp'
            total = 0 if comped else subtotal + tax + tip
            status = 'open' if body.get('holdOpen') else 'paid'
            tendered = to_cents(body.get('cashTendered')) if payment == 'cash' else 0
            order = {
                'id': ts(),
                'poolId': body.get('poolId'),
                'seatId': body.get('seatId'),
                'seatLabel': body.get('seatLabel', ''),
                'guestName': (body.get('guestName') or '').strip(),
                'items': lines,
                'subtotal': subtotal, 'taxRate': tax_rate, 'tax': tax, 'tip': tip, 'total': total,
                'payment': payment,
                'account': (body.get('account') or '').strip() if payment == 'account' else '',
                'compReason': (body.get('compReason') or '').strip() if comped else '',
                'cashTendered': tendered,
                'changeDue': max(0, tendered - total) if payment == 'cash' and status == 'paid' else 0,
                # Account charges are collected later — everything else settles on the spot
                'settled': not (payment == 'account' and status == 'paid'),
                'status': status,
                'empId': str(body.get('empId', '')),
                'empName': body.get('empName', ''),
                'created': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'date': datetime.now().strftime('%Y-%m-%d'),
                'paidAt': datetime.now().strftime('%H:%M') if status == 'paid' else None,
                # ── Kitchen side ──
                # Ticket numbers restart at 1 each day, per pool: "order 12" has to
                # mean one thing on the pass at any given moment.
                'num': next_ticket_num(data, body.get('poolId')),
                'kitchen': 'new',
                'createdTs': int(datetime.now().timestamp() * 1000),
                'startedTs': None, 'readyTs': None, 'servedTs': None,
                'kitchenNote': (body.get('kitchenNote') or '').strip(),
            }
            data.setdefault('orders', []).insert(0, order)
            save_data(data)
            self.send_json({'ok': True, 'order': order})

        elif p == '/api/order/pay':
            # Close out a tab that was left open
            order = next((o for o in data.get('orders', []) if o['id'] == body.get('id')), None)
            if not order:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            payment = body.get('payment') or 'cash'
            comped = payment == 'comp'
            tip = to_cents(body.get('tip'))
            total = 0 if comped else order['subtotal'] + order['tax'] + tip
            tendered = to_cents(body.get('cashTendered')) if payment == 'cash' else 0
            order.update({
                'payment': payment, 'tip': tip, 'total': total,
                'account': (body.get('account') or '').strip() if payment == 'account' else '',
                'compReason': (body.get('compReason') or '').strip() if comped else '',
                'cashTendered': tendered,
                'changeDue': max(0, tendered - total) if payment == 'cash' else 0,
                'settled': payment != 'account',
                'status': 'paid',
                'paidAt': datetime.now().strftime('%H:%M'),
            })
            # Receipt trail back to Square. Stored so a disputed charge can be
            # matched to the tab that produced it without guessing by timestamp.
            for src, dest in (('squareTransactionId', 'squareTransactionId'),
                              ('squareClientTransactionId', 'squareClientTransactionId')):
                if body.get(src):
                    order[dest] = str(body[src])[:80]
            save_data(data)
            self.send_json({'ok': True, 'order': order})

        elif p == '/api/order/kitchen':
            # The cook bumping a ticket along. Deliberately separate from
            # /api/order/pay — the food and the money settle independently.
            order = next((o for o in data.get('orders', []) if o['id'] == body.get('id')), None)
            state = body.get('kitchen')
            if not order:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            if state not in KITCHEN_FLOW:
                self.send_json({'ok': False, 'error': f'Unknown kitchen state: {state}'}, 400); return
            prev = order.get('kitchen')
            order['kitchen'] = state
            stamp = KITCHEN_STAMPS.get(state)
            if stamp:
                order[stamp] = int(datetime.now().timestamp() * 1000)
            order['bumpedBy'] = body.get('byName', '')
            # Tell the waiter their food is up — they're out on the deck, not
            # standing at the pass watching for it.
            if state == 'ready' and prev != 'ready' and order.get('empId'):
                where = order.get('seatLabel') or order.get('guestName') or 'the deck'
                data.setdefault('notifications', []).insert(0, {
                    'id': ts() + 1, 'empId': str(order['empId']), 'poolId': order.get('poolId'),
                    'title': f"🍔 Order #{order.get('num')} is ready",
                    'message': f"Pick up for {where} — {sum(i['qty'] for i in order.get('items', []))} item(s) on the pass.",
                    'read': False, 'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
                })
            save_data(data)
            self.send_json({'ok': True, 'order': order})

        elif p == '/api/order/settle':
            # Mark an outstanding house-account charge as collected
            order = next((o for o in data.get('orders', []) if o['id'] == body.get('id')), None)
            if not order:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            order['settled'] = bool(body.get('settled', True))
            order['settledAt'] = datetime.now().strftime('%Y-%m-%d %H:%M') if order['settled'] else None
            save_data(data)
            self.send_json({'ok': True, 'order': order})

        elif p == '/api/order/void':
            order = next((o for o in data.get('orders', []) if o['id'] == body.get('id')), None)
            if not order:
                self.send_json({'ok': False, 'error': 'Not found'}, 404); return
            order['status'] = 'void'
            order['voidReason'] = (body.get('reason') or '').strip()
            order['settled'] = True
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/order/delete':
            data['orders'] = [o for o in data.get('orders', []) if o['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        elif p == '/api/photo':
            import base64
            photo_dir = PHOTOS_DIR
            os.makedirs(photo_dir, exist_ok=True)
            data_url = body.get('data', '')
            if ',' in data_url:
                img_bytes = base64.b64decode(data_url.split(',')[1])
                fname = f"photo_{ts()}.jpg"
                with open(os.path.join(photo_dir, fname), 'wb') as f:
                    f.write(img_bytes)
                self.send_json({'ok': True, 'filename': fname})
            else:
                self.send_json({'error': 'Invalid photo data'}, 400)

        else:
            self.send_json({'error': 'not found'}, 404)

if __name__ == '__main__':
    ip = get_local_ip()
    print(f'Pool Manager → http://localhost:{PORT}')
    print(f'Guard QR URL → http://{ip}:{PORT}/quicklog')
    # Threaded server: handle many devices at once so the app never appears
    # "offline" just because another request is in flight.
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
