#!/usr/bin/env python3
import json, os, socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from datetime import datetime

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

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {'employees': [], 'chemicals': [], 'shifts': [], 'punches': [], 'announcements': [], 'resources': [], 'pool_status': 'open', 'shift_requests': []}

def save_data(data):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

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
        elif p == '/api/data':
            d = load_data()
            out = dict(d)
            out['employees'] = []
            for e in d.get('employees', []):
                se = {k: v for k, v in e.items() if k != 'password'}
                se['has_password'] = bool(e.get('password'))
                out['employees'].append(se)
            self.send_json(out)
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
            data['employees'].append(body)
            save_data(data)
            self.send_json({'ok': True, 'employee': body})

        elif p == '/api/employee/delete':
            data['employees'] = [e for e in data['employees'] if e['id'] != body['id']]
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
            save_data(data)
            self.send_json({'ok': True, 'shift': body})

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
                req['status'] = body.get('status', req['status'])
                save_data(data)
                self.send_json({'ok': True})
            else:
                self.send_json({'ok': False, 'error': 'Not found'}, 404)

        elif p == '/api/shift-request/delete':
            data['shift_requests'] = [r for r in data.get('shift_requests', []) if r['id'] != body['id']]
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
    HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
