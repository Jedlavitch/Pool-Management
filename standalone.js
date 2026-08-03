/* ══════════════════════════════════════════════════════════════════════════
   Mar-A-Lavitch — browser-hosted mode.

   On GitHub Pages there is no server.py, so every fetch('/api/...') the apps
   make would come back as a Pages 404 page and the UI would boot empty. This
   file patches window.fetch and answers those calls out of an in-browser copy
   of the data, so pool-manager.html and worker.html run unmodified.

   It is deliberately inert anywhere a real server is answering: on localhost
   or the deployed Railway app this script returns immediately and the apps
   talk to server.py exactly as before.

   Writes land in localStorage, so a visitor can ring up an order or clock
   someone in and it stays put. Storage is per-browser: this mode has no
   server, so two devices do not see each other's changes.
   ══════════════════════════════════════════════════════════════════════════ */
(function (global) {
'use strict';

if (typeof global.STARTER_DATA !== 'function') {
  console.warn('[standalone] starter-data.js did not load — leaving fetch alone.');
  return;
}

/* ── Should we take over? ────────────────────────────────────────────────
   Two ways in. The fast path is for hosts we know have no API — Pages,
   file://, or an explicit ?standalone=1 — decided synchronously so the page
   never fires a request it knows will 404.

   Otherwise we start out undecided and let the first /api/ call settle it:
   the real request goes out, and only if it comes back looking like "nothing
   is serving an API here" (connection refused, or a 404 that isn't JSON) do
   we switch over. A live server answers normally and this file gets out
   of the way for the rest of the session — which is what keeps it safe to
   ship inside the production apps. */
const params = new URLSearchParams(global.location.search);
const STATIC_HOST =
  params.get('standalone') === '1' ||
  global.location.protocol === 'file:' ||
  /\.github\.io$/i.test(global.location.hostname);

let mode = STATIC_HOST ? 'standalone' : 'unknown';   // 'standalone' | 'live' | 'unknown'

/* worker.html reads this to skip service-worker registration on a static
   host. It is the synchronous signal on purpose: by the time `mode` settles,
   registration has already run. */
global.MAL_STATIC_HOST = STATIC_HOST;

const STORE_KEY = 'mal-store-v1';
const realFetch = global.fetch.bind(global);

/* ── State ───────────────────────────────────────────────────────────────*/
function loadState() {
  try {
    const raw = global.localStorage.getItem(STORE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* private mode, corrupt JSON — fall through to a fresh seed */ }
  return global.STARTER_DATA();
}
let data = loadState();

function save() {
  try { global.localStorage.setItem(STORE_KEY, JSON.stringify(data)); }
  catch (e) { /* storage full or blocked: the app still works for this page */ }
}
function resetStore() {
  try { global.localStorage.removeItem(STORE_KEY); } catch (e) {}
  global.location.reload();
}

/* ── Small helpers mirroring server.py ───────────────────────────────────*/
let seq = 0;
const nid = () => Date.now() * 1000 + (seq++ % 1000);   // unique across a burst
const pad = n => String(n).padStart(2, '0');
const ymd = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hm  = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const today = () => ymd(new Date());
const now   = () => hm(new Date());
const cents = v => {
  if (v === null || v === undefined || v === '') return 0;
  const n = parseFloat(String(v).replace(/[$,\s]/g, ''));
  return isNaN(n) ? 0 : Math.max(0, Math.round(n * 100));
};
const list = k => (data[k] = data[k] || []);
const byId = (k, id) => list(k).find(x => String(x.id) === String(id));

function reply(body, status) {
  return new Response(JSON.stringify(body), {
    status: status || 200,
    headers: { 'Content-Type': 'application/json' },
  });
}
const ok    = extra => reply(Object.assign({ ok: true }, extra || {}));
const fail  = (msg, status) => reply({ ok: false, error: msg }, status || 400);

/* ── GET /api/data — passwords never leave the store, same as the server ─*/
function snapshot() {
  const out = Object.assign({}, data);
  out.employees = list('employees').map(e => {
    const safe = Object.assign({}, e);
    delete safe.password;
    safe.has_password = !!e.password;
    return safe;
  });
  return out;
}

/* ── Generic create / update / delete ────────────────────────────────────*/
function create(key, body, extra) {
  const row = Object.assign({}, body, { id: nid() }, extra || {});
  list(key).push(row);
  save();
  return row;
}
function removeById(key, id) {
  data[key] = list(key).filter(x => String(x.id) !== String(id));
  save();
}
function patch(key, id, fields) {
  const row = byId(key, id);
  if (!row) return null;
  Object.assign(row, fields);
  save();
  return row;
}

/* ── Order maths — priced off the live menu, like the server ─────────────*/
function priceOrder(body) {
  const pool = list('pools').find(p => p.id === body.poolId) || {};
  const taxRate = Number(pool.tax_rate) || 0;
  const menu = {};
  list('menu').forEach(m => { menu[m.id] = m; });

  let subtotal = 0, taxableBase = 0;
  const lines = (body.items || []).map(li => {
    const qty = Math.max(1, parseInt(li.qty, 10) || 1);
    const src = menu[li.menuId];
    const name = src ? src.name : (li.name || 'Item').trim();
    const price = src ? src.price : cents(li.price);
    const taxable = src ? src.taxable !== false : li.taxable !== false;
    const total = price * qty;
    subtotal += total;
    if (taxable) taxableBase += total;
    return { menuId: li.menuId, name, price, qty, note: (li.note || '').trim(), total };
  });
  const tax = Math.round(taxableBase * taxRate / 100);
  return { lines, subtotal, taxRate, tax };
}

/* ── Routes ──────────────────────────────────────────────────────────────
   Keys are the paths the two apps POST to. Anything not listed falls through
   to a generic {ok:true} so an unmapped corner of the UI degrades quietly
   instead of throwing. */
const routes = {

  '/api/auth': b => {
    const emp = byId('employees', b.empId);
    if (!emp) return fail('Employee not found', 404);
    if (!emp.password) return ok();                    // nobody has set one yet
    return emp.password === b.password ? ok() : fail('Incorrect password');
  },

  '/api/punch/in': b => {
    const open = list('punches').find(p =>
      String(p.empId) === String(b.empId) && p.date === today() && !p.out);
    if (open) return reply({ error: 'Already clocked in', punch: open });
    const punch = create('punches', {
      empId: String(b.empId), empName: b.empName || '', poolId: b.poolId,
      date: today(), in: now(), out: null, hours: null,
      checkin_answers: b.answers || {}, checkin_flags: b.flags || [],
    });
    return reply({ action: 'in', punch });
  },

  '/api/punch/out': b => {
    const open = list('punches').find(p =>
      String(p.empId) === String(b.empId) && p.date === today() && !p.out);
    if (!open) return reply({ error: 'Not clocked in' });
    open.out = now();
    const [h1, m1] = open.in.split(':').map(Number);
    const [h2, m2] = open.out.split(':').map(Number);
    open.hours = Math.round(((h2 * 60 + m2) - (h1 * 60 + m1)) / 60 * 100) / 100;
    open.checkout_answers = b.answers || {};
    open.checkout_flags = b.flags || [];
    open.checkout_photo = null;                       // no filesystem in the browser
    save();
    return reply({ action: 'out', punch: open });
  },

  '/api/punch': b => reply({ ok: true, punch: create('punches', b) }),

  '/api/pool_status': b => {
    const pool = list('pools').find(p => p.id === b.poolId);
    if (pool) pool.status = b.status || 'open';
    else data.pool_status = b.status || 'open';
    save();
    return ok();
  },

  '/api/layout': b => {
    if (b.poolId === undefined || b.poolId === null) return fail('Missing poolId');
    let layout = list('layouts').find(l => l.poolId === b.poolId);
    if (!layout) { layout = { poolId: b.poolId, w: 1000, h: 700, seats: [], zones: [] }; list('layouts').push(layout); }
    const clamp = (v, hi) => Math.min(hi, Math.max(0, Number(v) || 0));
    layout.seats = (b.seats || []).map(s => ({
      id: s.id || nid(), label: (s.label || '').trim(), type: s.type || 'lounger',
      x: clamp(s.x, layout.w), y: clamp(s.y, layout.h), rot: clamp(s.rot, 359),
      cap: Math.max(1, parseInt(s.cap, 10) || 1), bookable: s.bookable !== false,
    }));
    layout.zones = (b.zones || []).map(z => ({
      id: z.id || nid(), name: (z.name || '').trim(),
      x: clamp(z.x, layout.w), y: clamp(z.y, layout.h),
      w: clamp(z.w, layout.w), h: clamp(z.h, layout.h),
      color: z.color || '#38bdf8', shape: z.shape || 'rect',
    }));
    save();
    return ok({ layout });
  },

  '/api/order': b => {
    const { lines, subtotal, taxRate, tax } = priceOrder(b);
    if (!lines.length) return fail('Order is empty');
    const payment = b.payment || 'cash';
    const comped = payment === 'comp';
    const tip = cents(b.tip);
    const status = b.holdOpen ? 'open' : 'paid';
    const total = comped ? 0 : subtotal + tax + tip;
    const tendered = payment === 'cash' ? cents(b.cashTendered) : 0;
    const order = {
      id: nid(), poolId: b.poolId, seatId: b.seatId, seatLabel: b.seatLabel || '',
      guestName: (b.guestName || '').trim(), items: lines,
      subtotal, taxRate, tax, tip, total, payment,
      account: payment === 'account' ? (b.account || '').trim() : '',
      compReason: comped ? (b.compReason || '').trim() : '',
      cashTendered: tendered,
      changeDue: payment === 'cash' && status === 'paid' ? Math.max(0, tendered - total) : 0,
      settled: !(payment === 'account' && status === 'paid'),
      status, empId: String(b.empId || ''), empName: b.empName || '',
      created: `${today()} ${now()}`, date: today(),
      paidAt: status === 'paid' ? now() : null,
    };
    list('orders').unshift(order);
    save();
    return ok({ order });
  },

  '/api/order/pay': b => {
    const order = byId('orders', b.id);
    if (!order) return fail('Not found', 404);
    const payment = b.payment || 'cash';
    const comped = payment === 'comp';
    const tip = cents(b.tip);
    const total = comped ? 0 : order.subtotal + order.tax + tip;
    const tendered = payment === 'cash' ? cents(b.cashTendered) : 0;
    Object.assign(order, {
      payment, tip, total,
      account: payment === 'account' ? (b.account || '').trim() : '',
      compReason: comped ? (b.compReason || '').trim() : '',
      cashTendered: tendered,
      changeDue: payment === 'cash' ? Math.max(0, tendered - total) : 0,
      settled: payment !== 'account', status: 'paid', paidAt: now(),
    });
    save();
    return ok({ order });
  },

  '/api/order/settle': b => ok({ order: patch('orders', b.id, { settled: true }) }),
  '/api/order/void':   b => ok({ order: patch('orders', b.id, { status: 'void', voidReason: (b.reason || '').trim() }) }),
  '/api/order/delete': b => { removeById('orders', b.id); return ok(); },

  '/api/reservation': b => {
    const date = b.date || today();
    const clash = list('reservations').find(r =>
      r.seatId === b.seatId && r.date === date &&
      (r.status === 'reserved' || r.status === 'active'));
    if (clash) return reply({ ok: false, error: `That chair is already booked for ${clash.guestName || 'a guest'} at that time.` }, 409);
    const res = create('reservations', {
      poolId: b.poolId, seatId: b.seatId, seatLabel: b.seatLabel || '',
      guestName: (b.guestName || '').trim() || 'Guest',
      party: Math.max(1, parseInt(b.party, 10) || 1),
      phone: (b.phone || '').trim(), note: (b.note || '').trim(),
      date, start: b.start || now(), end: b.end || '',
      status: b.seatNow ? 'active' : 'reserved',
      empId: String(b.empId || ''), empName: b.empName || '',
      created: `${today()} ${now()}`, checkedInAt: b.seatNow ? now() : null,
    });
    return ok({ reservation: res });
  },

  '/api/reservation/update': b => {
    const fields = {};
    if (b.status) fields.status = b.status;
    if (b.status === 'active') fields.checkedInAt = now();
    ['guestName', 'party', 'phone', 'note', 'start', 'end', 'seatId', 'seatLabel']
      .forEach(k => { if (b[k] !== undefined) fields[k] = b[k]; });
    const row = patch('reservations', b.id, fields);
    return row ? ok({ reservation: row }) : fail('Not found', 404);
  },

  '/api/reservation/delete': b => { removeById('reservations', b.id); return ok(); },

  '/api/waitlist': b => ok({ entry: create('waitlist', {
    poolId: b.poolId, name: (b.name || '').trim() || 'Guest',
    party: Math.max(1, parseInt(b.party, 10) || 1),
    phone: (b.phone || '').trim(), note: (b.note || '').trim(),
    date: b.date || today(), added: now(), status: 'waiting', seatedSeatId: null,
  }) }),

  '/api/waitlist/update': b => {
    const entry = byId('waitlist', b.id);
    if (!entry) return fail('Not found', 404);
    if (b.action === 'seat') {
      entry.status = 'seated';
      entry.seatedSeatId = b.seatId || null;
    } else if (b.action === 'remove') {
      entry.status = 'left';
    } else {
      Object.assign(entry, b);
    }
    save();
    return ok({ entry });
  },

  '/api/waitlist/delete': b => { removeById('waitlist', b.id); return ok(); },

  // Prices arrive as dollars off the form and are stored as cents, like to_cents().
  '/api/menu': b => {
    const name = (b.name || '').trim();
    if (!name) return fail('Item needs a name');
    return ok({ item: create('menu', Object.assign({}, b, {
      name, price: cents(b.price),
      category: b.category || 'General', emoji: b.emoji || '', desc: b.desc || '',
      taxable: b.taxable !== false, active: b.active !== false,
    })) });
  },
  '/api/menu/update': b => {
    const item = byId('menu', b.id);
    if (!item) return fail('Not found', 404);
    ['name', 'category', 'emoji', 'desc'].forEach(k => {
      if (k in b) item[k] = (b[k] || '').trim();
    });
    if ('price' in b) item.price = cents(b.price);
    ['taxable', 'active'].forEach(k => { if (k in b) item[k] = !!b[k]; });
    save();
    return ok({ item });
  },
  '/api/menu/delete': b => { removeById('menu', b.id); return ok(); },
  '/api/menu/bulk': b => {
    let added = 0;
    (b.items || []).forEach(m => {
      const name = (m.name || '').trim();
      if (!name) return;
      create('menu', Object.assign({}, m, { poolId: b.poolId, name, price: cents(m.price) }));
      added++;
    });
    return ok({ count: added });
  },

  '/api/employee':      b => ok({ employee: create('employees', Object.assign({ poolIds: [] }, b)) }),
  '/api/employee/delete': b => { removeById('employees', b.id); return ok(); },
  '/api/employee/pools':  b => {
    const emp = byId('employees', b.empId);
    if (!emp) return fail('Not found', 404);
    emp.poolIds = Array.isArray(b.poolIds) ? b.poolIds : [];
    save();
    return ok();
  },
  '/api/employee/set-password': b => {
    const emp = byId('employees', b.empId);
    if (!emp) return fail('Not found', 404);
    emp.password = b.password || '';
    save();
    return ok();
  },

  '/api/shift':        b => ok({ shift: create('shifts', b) }),
  '/api/shift/delete': b => { removeById('shifts', b.id); return ok(); },
  '/api/shifts/bulk':  b => { (b.shifts || []).forEach(s => create('shifts', s)); return ok({ count: (b.shifts || []).length }); },
  '/api/shift/confirm': b => {
    data.shift_confirmations = data.shift_confirmations || {};
    data.shift_confirmations[`${b.empId}_${b.shiftId}`] =
      { confirmed: true, ts: `${today()} ${now()}`, note: b.note || '' };
    save();
    return ok();
  },

  '/api/shift-request':        b => ok({ request: create('shift_requests', Object.assign({ status: 'pending' }, b)) }),
  '/api/shift-request/update': b => ok({ request: patch('shift_requests', b.id, { status: b.status }) }),
  '/api/shift-request/delete': b => { removeById('shift_requests', b.id); return ok(); },

  '/api/break': b => {
    const assigner = byId('employees', b.assignedBy);
    if (!assigner || ['Manager', 'Staffer'].indexOf(assigner.role) === -1) {
      return fail('Only managers and staffers can assign breaks.', 403);
    }
    return ok({ break: create('breaks', Object.assign({
      status: 'scheduled', startedAt: null, endedAt: null, actualMinutes: null,
      assignedByName: assigner.name || '',
    }, b)) });
  },
  '/api/break/start': b => ok({ break: patch('breaks', b.id, { status: 'active', startedAt: now() }) }),
  '/api/break/end': b => {
    const br = byId('breaks', b.id);
    if (!br) return fail('Not found', 404);
    br.status = 'completed';
    br.endedAt = now();
    if (br.startedAt) {
      const [h1, m1] = br.startedAt.split(':').map(Number);
      const [h2, m2] = br.endedAt.split(':').map(Number);
      br.actualMinutes = Math.max(0, (h2 * 60 + m2) - (h1 * 60 + m1));
    }
    save();
    return ok({ break: br });
  },
  '/api/break/delete': b => { removeById('breaks', b.id); return ok(); },

  '/api/chemical': b => {
    const cl = parseFloat(b.cl) || 0, ph = parseFloat(b.ph) || 0;
    const ccl = b.ccl === null || b.ccl === undefined || b.ccl === '' ? null : parseFloat(b.ccl);
    let status = b.status;
    if (!status) {
      const good = cl >= 1 && cl <= 3 && ph >= 7.2 && ph <= 7.8 && (ccl === null || ccl < 0.5);
      const bad = cl < 0.5 || ph < 7.0 || ph > 8.0;
      status = good ? 'pass' : (bad ? 'fail' : 'warn');
    }
    const row = create('chemicals', Object.assign({}, b, { status }));
    list('chemicals').pop();
    list('chemicals').unshift(row);           // newest first, like the server
    save();
    return ok({ id: row.id });
  },

  '/api/announcement':        b => ok({ announcement: create('announcements', Object.assign({ posted: `${today()} ${now()}` }, b)) }),
  '/api/announcement/delete': b => { removeById('announcements', b.id); return ok(); },

  '/api/resource':        b => ok({ resource: create('resources', b) }),
  '/api/resource/delete': b => { removeById('resources', b.id); return ok(); },

  '/api/pool': b => {
    const pool = create('pools', Object.assign(
      { status: 'open', address: '', created: today(), tax_rate: 0 }, b));
    list('layouts').push({ poolId: pool.id, w: 1000, h: 700, seats: [], zones: [] });
    save();
    return ok({ pool });
  },
  '/api/pool/update': b => ok({ pool: patch('pools', b.id, b) }),
  '/api/pool/delete': b => { removeById('pools', b.id); return ok(); },

  '/api/notification/send': b => ok({ notification: create('notifications', Object.assign(
    { read: false, ts: `${today()} ${now()}` }, b)) }),
  '/api/notification/read': b => {
    list('notifications').forEach(n => {
      if (b.id ? String(n.id) === String(b.id) : String(n.empId) === String(b.empId)) n.read = true;
    });
    save();
    return ok();
  },

  '/api/logout': () => ok(),

  // No filesystem to write to — accept the capture so check-in still completes.
  '/api/photo': () => ok({ filename: '', url: '' }),
};

/* ── Answering out of the browser store ──────────────────────────────────*/
function serveLocally(path, method, init) {
  if (method === 'GET') {
    if (path === '/api/data') return reply(snapshot());
    if (path === '/api/roster') return reply({
      employees: list('employees').map(e => ({
        id: e.id, name: e.name, role: e.role, has_password: !!e.password,
      })),
      // No cookies without a server: the pages fall back to their own
      // remembered sign-in, and a password is never demanded here.
      signed_in: false,
      requires_password: false,
    });
    if (path === '/api/me') return reply({ ok: true, id: null, name: '', role: '' });
    if (path === '/api/ip') return reply({
      ip: '', host: global.location.host, port: 0,
      base_url: global.location.origin + global.location.pathname.replace(/\/[^/]*$/, ''),
    });
    return fail('Unknown route', 404);
  }

  let body = {};
  try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) {}

  const handler = routes[path];
  try {
    return handler ? handler(body) : ok();
  } catch (err) {
    console.error('[standalone] handler failed for', path, err);
    return fail('Request failed');
  }
}

/* Does this response mean "no API is serving this path"? A static host
   answers /api/data with its own 404 page; a real server never does. */
function looksServerless(res) {
  if (res.status !== 404) return false;
  const type = res.headers.get('Content-Type') || '';
  return !/json/i.test(type);
}

function activateStandalone(why) {
  mode = 'standalone';
  console.info('[standalone] no API found (' + why + ') — serving the built-in dataset.');
}

/* ── The patch ───────────────────────────────────────────────────────────*/
global.fetch = function (input, init) {
  const url = typeof input === 'string' ? input : (input && input.url) || '';
  const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0];

  if (mode === 'live' || !path.startsWith('/api/')) return realFetch(input, init);

  const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();

  if (mode === 'standalone') return Promise.resolve(serveLocally(path, method, init));

  // Undecided: let the real request answer the question.
  return realFetch(input, init).then(res => {
    if (!looksServerless(res)) { mode = 'live'; return res; }
    activateStandalone('404 from ' + path);
    return serveLocally(path, method, init);
  }, err => {
    activateStandalone(err && err.message ? err.message : 'request failed');
    return serveLocally(path, method, init);
  });
};

global.MAL_STORE = { reset: resetStore, state: () => data, mode: () => mode };

})(window);
