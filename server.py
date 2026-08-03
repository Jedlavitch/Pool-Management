#!/usr/bin/env python3
import json, os, socket, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime, timedelta

# Serializes writes so concurrent requests can't corrupt or lose each other's
# updates once the server handles requests on multiple threads.
_DATA_LOCK = threading.RLock()

PORT = int(os.environ.get('PORT', 8765))

# DATA_DIR can be overridden via env var — point it at a persistent volume in cloud deployments
_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.environ.get('DATA_DIR', _HERE)
DATA_FILE  = os.path.join(DATA_DIR, 'data.json')
PHOTOS_DIR = os.path.join(DATA_DIR, 'photos')

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
    return {'employees': [], 'chemicals': [], 'shifts': [], 'punches': [], 'announcements': [], 'resources': [], 'pool_status': 'open', 'shift_requests': [], 'notifications': [], 'shift_confirmations': {}, 'pools': [], 'breaks': [], 'menu': [], 'orders': []}

# Entities that belong to a single pool and carry a poolId
POOL_SCOPED = ('shifts', 'chemicals', 'punches', 'announcements', 'resources', 'shift_requests', 'notifications', 'breaks', 'menu', 'orders')

# ── Snack bar / kitchen display ─────────────────────────────────────────────
# A ticket walks this line: a waiter fires it, the kitchen starts it, the kitchen
# calls it up, the waiter runs it out. 'void' is off to the side (cancelled).
ORDER_FLOW = ['new', 'preparing', 'ready', 'served']
ORDER_STATUSES = ORDER_FLOW + ['void']
# Which timestamp each status stamps when a ticket lands on it
ORDER_STAMPS = {'preparing': 'startedTs', 'ready': 'readyTs', 'served': 'servedTs', 'void': 'voidedTs'}

# Tickets older than this are dropped on the next write so data.json can't grow forever
ORDER_RETENTION_DAYS = 14

# Seeded once per pool so a brand-new pool has a working snack bar on day one.
STARTER_MENU = [
    ('Grill',     'Cheeseburger',       8.50),
    ('Grill',     'Hamburger',          7.50),
    ('Grill',     'Hot Dog',            5.00),
    ('Grill',     'Chicken Tenders',    8.00),
    ('Grill',     'Grilled Cheese',     6.00),
    ('Grill',     'French Fries',       4.00),
    ('Grill',     'Mozzarella Sticks',  6.50),
    ('Snacks',    'Nachos',             5.50),
    ('Snacks',    'Soft Pretzel',       4.00),
    ('Snacks',    'Chips',              2.00),
    ('Snacks',    'Fruit Cup',          4.50),
    ('Drinks',    'Fountain Soda',      2.50),
    ('Drinks',    'Bottled Water',      2.00),
    ('Drinks',    'Lemonade',           3.00),
    ('Drinks',    'Iced Tea',           3.00),
    ('Drinks',    'Gatorade',           3.50),
    ('Ice Cream', 'Ice Cream Sandwich', 3.50),
    ('Ice Cream', 'Popsicle',           2.50),
    ('Ice Cream', 'Soft Serve Cone',    4.00),
]

def seed_menu(pool_id, base_ts):
    return [{'id': base_ts + i, 'poolId': pool_id, 'category': cat,
             'name': name, 'price': price, 'active': True}
            for i, (cat, name, price) in enumerate(STARTER_MENU)]

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
    for idx, pool in enumerate(pools):
        if 'status' not in pool:
            pool['status'] = 'open'; changed = True
        if 'address' not in pool:
            pool['address'] = ''; changed = True
        # Give each pool a starter snack-bar menu exactly once. The flag matters:
        # without it, a manager who deliberately clears the menu would watch it
        # grow back on the very next request.
        if not pool.get('menu_seeded'):
            base = int(datetime.now().timestamp() * 1000) + idx * 1000
            data.setdefault('menu', []).extend(seed_menu(pool['id'], base))
            pool['menu_seeded'] = True; changed = True
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

def serve_file(handler, path, mime='text/html; charset=utf-8'):
    base = os.path.dirname(__file__)
    full = os.path.join(base, path.lstrip('/'))
    if not os.path.exists(full):
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

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200); self.cors(); self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ('/', '/index.html', '/maralavitchmanagement'):
            serve_file(self, 'pool-manager.html')
        elif p in ('/quicklog', '/quicklog.html'):
            # Redirect to merged staff portal
            self.send_response(302)
            self.send_header('Location', '/maralavitchstaff')
            self.end_headers()
        elif p in ('/worker', '/worker.html', '/maralavitchstaff'):
            serve_file(self, 'worker.html')
        elif p in ('/print-qr', '/print-qr.html'):
            serve_file(self, 'print-qr.html')
        elif p in ('/kds', '/kds.html', '/kitchen'):
            serve_file(self, 'kds.html')
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
            # Lightweight feed for the kitchen display and the waiters' order tab —
            # both poll this every few seconds, so it must stay small.
            from urllib.parse import parse_qs
            q = parse_qs(urlparse(self.path).query)
            pid = q.get('poolId', [None])[0]
            d = load_data()
            orders = d.get('orders', [])
            if pid not in (None, '', 'all'):
                orders = [o for o in orders if str(o.get('poolId')) == str(pid)]
            today_str = datetime.now().strftime('%Y-%m-%d')
            # Today's tickets plus anything still open from before midnight
            orders = [o for o in orders
                      if o.get('date') == today_str or o.get('status') in ('new', 'preparing', 'ready')]
            menu = [m for m in d.get('menu', [])
                    if pid in (None, '', 'all') or str(m.get('poolId')) == str(pid)]
            self.send_json({
                'orders': sorted(orders, key=lambda o: o.get('createdTs', 0)),
                'menu': menu,
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
        with _DATA_LOCK:
            self._handle_post()

    def _handle_post(self):
        p = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        data = load_data()
        ts = lambda: int(datetime.now().timestamp() * 1000)

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
            data.setdefault('pools', []).append(body)
            save_data(data)
            self.send_json({'ok': True, 'pool': body})

        elif p == '/api/pool/update':
            pool = next((x for x in data.get('pools', []) if x['id'] == body.get('id')), None)
            if pool:
                for k in ('name', 'address', 'status'):
                    if k in body:
                        pool[k] = body[k]
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
                    emp['password'] = body['password']
                elif 'password' in emp:
                    del emp['password']
                save_data(data)
                self.send_json({'ok': True})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/auth':
            emp_id = str(body.get('empId', ''))
            password = body.get('password', '')
            emp = next((e for e in data['employees'] if str(e['id']) == emp_id), None)
            if not emp:
                self.send_json({'ok': False, 'error': 'Employee not found'}, 404)
            elif not emp.get('password'):
                self.send_json({'ok': True})  # No password set — allow through
            elif emp['password'] == password:
                self.send_json({'ok': True})
            else:
                self.send_json({'ok': False, 'error': 'Incorrect password'})

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

        # ── Snack bar: menu ────────────────────────────────────────────────
        elif p == '/api/menu':
            body['id'] = ts()
            body['name'] = (body.get('name') or '').strip()
            body['category'] = (body.get('category') or 'Snacks').strip()
            try:
                body['price'] = round(float(body.get('price') or 0), 2)
            except (TypeError, ValueError):
                body['price'] = 0.0
            body.setdefault('active', True)
            if not body['name']:
                self.send_json({'ok': False, 'error': 'Item needs a name'}, 400)
            else:
                data.setdefault('menu', []).append(body)
                save_data(data)
                self.send_json({'ok': True, 'item': body})

        elif p == '/api/menu/update':
            item = next((m for m in data.get('menu', []) if m['id'] == body.get('id')), None)
            if not item:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)
            else:
                for k in ('name', 'category', 'active'):
                    if k in body:
                        item[k] = body[k]
                if 'price' in body:
                    try:
                        item['price'] = round(float(body['price']), 2)
                    except (TypeError, ValueError):
                        pass
                save_data(data)
                self.send_json({'ok': True, 'item': item})

        elif p == '/api/menu/delete':
            data['menu'] = [m for m in data.get('menu', []) if m['id'] != body['id']]
            save_data(data)
            self.send_json({'ok': True})

        # ── Snack bar: orders (KDS) ────────────────────────────────────────
        elif p == '/api/order':
            items = [i for i in (body.get('items') or []) if (i.get('name') or '').strip()]
            if not items:
                self.send_json({'ok': False, 'error': 'Order has no items'}, 400)
                return
            now = datetime.now()
            today_str = now.strftime('%Y-%m-%d')
            pid = body.get('poolId')
            # Ticket numbers restart at 1 each day, per pool — "order 12" has to
            # mean one thing on the pass at any given moment.
            same_day = [o for o in data.get('orders', [])
                        if o.get('date') == today_str and str(o.get('poolId')) == str(pid)]
            num = max([o.get('num', 0) for o in same_day], default=0) + 1
            clean = []
            for i in items:
                try:
                    qty = max(1, int(i.get('qty', 1)))
                except (TypeError, ValueError):
                    qty = 1
                try:
                    price = round(float(i.get('price') or 0), 2)
                except (TypeError, ValueError):
                    price = 0.0
                clean.append({'name': str(i.get('name')).strip(), 'qty': qty,
                              'price': price, 'note': (i.get('note') or '').strip()})
            order = {
                'id': ts(), 'num': num, 'poolId': pid, 'date': today_str,
                'table': (body.get('table') or '').strip() or 'Walk-up',
                'items': clean,
                'note': (body.get('note') or '').strip(),
                'empId': str(body.get('empId', '')), 'empName': body.get('empName', ''),
                'status': 'new',
                'total': round(sum(i['price'] * i['qty'] for i in clean), 2),
                'placed': now.strftime('%H:%M'),
                'createdTs': int(now.timestamp() * 1000),
                'startedTs': None, 'readyTs': None, 'servedTs': None,
            }
            data.setdefault('orders', []).append(order)
            # Keep the file from growing without bound
            cutoff = (now - timedelta(days=ORDER_RETENTION_DAYS)).strftime('%Y-%m-%d')
            data['orders'] = [o for o in data['orders'] if o.get('date', '') >= cutoff]
            save_data(data)
            self.send_json({'ok': True, 'order': order})

        elif p == '/api/order/status':
            order = next((o for o in data.get('orders', []) if o['id'] == body.get('id')), None)
            status = body.get('status')
            if not order:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)
            elif status not in ORDER_STATUSES:
                self.send_json({'ok': False, 'error': f'Unknown status: {status}'}, 400)
            else:
                prev = order.get('status')
                order['status'] = status
                stamp = ORDER_STAMPS.get(status)
                if stamp:
                    order[stamp] = int(datetime.now().timestamp() * 1000)
                order['bumpedBy'] = body.get('byName', '')
                # Tell the waiter their food is up — they're out on the deck, not
                # staring at the pass.
                if status == 'ready' and prev != 'ready' and order.get('empId'):
                    tbl = order.get('table', '')
                    data.setdefault('notifications', []).insert(0, {
                        'id': ts() + 1, 'empId': str(order['empId']), 'poolId': order.get('poolId'),
                        'title': f"🍔 Order #{order.get('num')} is ready",
                        'message': f"Pick up for {tbl} — {sum(i['qty'] for i in order.get('items', []))} item(s) on the pass.",
                        'read': False, 'ts': datetime.now().strftime('%Y-%m-%d %H:%M'),
                    })
                save_data(data)
                self.send_json({'ok': True, 'order': order})

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
