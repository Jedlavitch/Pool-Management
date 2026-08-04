/* ══════════════════════════════════════════════════════════════════════════
   Mar-A-Lavitch — shared deck-map + POS helpers
   Loaded by BOTH the staff app (worker.html) and the management app
   (pool-manager.html) so the seating layout looks and behaves identically on a
   lifeguard's phone and on the manager's desktop.

   Coordinates live in a fixed 1000×700 "design space" (matching the server's
   blank_layout) and are rendered as percentages, so one saved layout scales to
   any canvas size without a re-save.
   ══════════════════════════════════════════════════════════════════════════ */
(function (global) {
'use strict';

const DW = 1000, DH = 700;   // design-space canvas

/* ── Money ────────────────────────────────────────────────────────────────
   Everything is whole cents end to end; dollars only exist for display. */
function money(cents) {
  const n = Number(cents) || 0;
  return (n < 0 ? '-$' : '$') + (Math.abs(n) / 100).toFixed(2);
}
function parseMoney(str) {
  if (str === null || str === undefined || str === '') return 0;
  const n = parseFloat(String(str).replace(/[$,\s]/g, ''));
  return isNaN(n) ? 0 : Math.max(0, Math.round(n * 100));
}

/* ── Seat catalogue ───────────────────────────────────────────────────────
   w/h are the footprint in design units. `bookable:false` means it's deck
   furniture guests aren't seated at (umbrellas, planters, signs). */
const SEAT_META = {
  lounger:  { w: 40, h: 62, r: 8,   emoji: '🛋️', label: 'Lounger',  bookable: true,  cap: 1 },
  chair:    { w: 36, h: 36, r: 7,   emoji: '🪑', label: 'Chair',    bookable: true,  cap: 1 },
  daybed:   { w: 70, h: 58, r: 10,  emoji: '🛏️', label: 'Daybed',   bookable: true,  cap: 2 },
  table:    { w: 46, h: 46, r: 999, emoji: '🍽️', label: 'Table',    bookable: true,  cap: 4 },
  cabana:   { w: 78, h: 74, r: 10,  emoji: '⛱️', label: 'Cabana',   bookable: true,  cap: 6 },
  umbrella: { w: 32, h: 32, r: 999, emoji: '☂️', label: 'Umbrella', bookable: false, cap: 1 },
  planter:  { w: 30, h: 30, r: 8,   emoji: '🪴', label: 'Planter',  bookable: false, cap: 1 },
  ladder:   { w: 26, h: 40, r: 5,   emoji: '🪜', label: 'Ladder',   bookable: false, cap: 1 },
};
const SEAT_ORDER = ['lounger', 'chair', 'daybed', 'table', 'cabana', 'umbrella', 'planter', 'ladder'];
function meta(type) { return SEAT_META[type] || SEAT_META.lounger; }

/* ── Zone presets ─────────────────────────────────────────────────────────*/
const ZONE_COLORS = ['#38bdf8', '#34d399', '#fbbf24', '#f472b6', '#a78bfa', '#fb923c'];

/* ── Seat status → colour ─────────────────────────────────────────────────
   Green reads "go here" for a staffer hunting a free chair; blue is a seated
   guest; amber is booked-but-not-yet-arrived. */
const STATUS_STYLE = {
  open:     { fill: 'var(--deck-open,#ecfdf5)',  edge: 'var(--deck-open-edge,#34d399)',  ink: '#065f46' },
  reserved: { fill: 'var(--deck-res,#fffbeb)',   edge: 'var(--deck-res-edge,#f59e0b)',   ink: '#92400e' },
  active:   { fill: 'var(--deck-act,#dbeafe)',   edge: 'var(--deck-act-edge,#2563eb)',   ink: '#1e3a8a' },
  decor:    { fill: 'var(--deck-decor,#f1f5f9)', edge: 'var(--deck-decor-edge,#cbd5e1)', ink: '#64748b' },
};

/* ── One-time stylesheet injection ───────────────────────────────────────*/
const CSS = `
.pd-wrap{position:relative;width:100%;aspect-ratio:${DW}/${DH};container-type:inline-size;background:var(--deck-bg,#f8fafc);
  border:1px solid var(--deck-border,#e2e8f0);border-radius:12px;overflow:hidden;touch-action:none;
  background-image:radial-gradient(circle at 1px 1px,var(--deck-dot,#e2e8f0) 1px,transparent 0);
  background-size:calc(100%/20) calc(100%/14);user-select:none;-webkit-user-select:none;}
.pd-zone{position:absolute;border-radius:12px;border:2px dashed;opacity:.55;pointer-events:none;
  display:flex;align-items:flex-start;justify-content:flex-start;}
.pd-zone.pool{border-style:solid;border-radius:16px;}
.pd-zone-name{font-size:.62rem;font-weight:800;text-transform:uppercase;letter-spacing:.06em;
  padding:3px 7px;white-space:nowrap;opacity:.9;}
.pd-seat{position:absolute;transform-origin:center;display:flex;flex-direction:column;
  align-items:center;justify-content:center;border:2px solid;box-sizing:border-box;
  font-weight:800;line-height:1;overflow:hidden;transition:box-shadow .12s,filter .12s;}
.pd-seat.tappable{cursor:pointer;}
.pd-seat.tappable:hover{filter:brightness(.96);}
/* Label/icon sizing tracks the canvas width (cqw) so a 3-character chair number
   like "A16" fits on a phone-width map and still reads on a desktop one. */
.pd-seat-lbl{font-size:clamp(5px,1.7cqw,12px);letter-spacing:-.04em;max-width:98%;
  overflow:hidden;text-overflow:clip;white-space:nowrap;}
.pd-seat-ico{font-size:clamp(7px,2cqw,15px);line-height:1;}
.pd-seat-sub{font-size:clamp(4px,1.35cqw,9px);font-weight:700;opacity:.85;max-width:96%;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.pd-seat-dot{position:absolute;top:2px;right:2px;width:9px;height:9px;border-radius:50%;
  background:#f5333f;border:1.5px solid #fff;box-shadow:0 0 0 1px rgba(0,0,0,.12);}
.pd-seat.sel{box-shadow:0 0 0 3px rgba(37,99,235,.5);z-index:5;}
.pd-seat.dragging{z-index:9;filter:brightness(1.04);box-shadow:0 6px 18px rgba(0,0,0,.25);}
.pd-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:6px;color:var(--muted,#64748b);text-align:center;padding:20px;}
.pd-empty b{font-size:.95rem;color:var(--text,#0f172a);}
.pd-empty span{font-size:.78rem;max-width:280px;line-height:1.45;}
/* editor-only */
.pd-wrap.editing .pd-zone{pointer-events:auto;cursor:move;opacity:.75;}
.pd-wrap.editing .pd-seat{cursor:move;}
.pd-zone.sel{opacity:1;box-shadow:0 0 0 3px rgba(37,99,235,.45);}
.pd-grip{position:absolute;right:-7px;bottom:-7px;width:15px;height:15px;border-radius:4px;
  background:#2563eb;border:2px solid #fff;cursor:nwse-resize;box-shadow:0 1px 4px rgba(0,0,0,.3);}
`;
let cssInjected = false;
function injectCSS() {
  if (cssInjected) return;
  cssInjected = true;
  const s = document.createElement('style');
  s.textContent = CSS;
  document.head.appendChild(s);
}

/* ── Geometry helpers ────────────────────────────────────────────────────*/
const pct = (v, total) => (v / total) * 100 + '%';
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
function snap(v, step) { return step ? Math.round(v / step) * step : v; }

/* Convert a pointer event to design-space coords inside the canvas. */
function toDesign(wrap, ev) {
  const r = wrap.getBoundingClientRect();
  return {
    x: ((ev.clientX - r.left) / r.width) * DW,
    y: ((ev.clientY - r.top) / r.height) * DH,
  };
}

/* ── Build one seat element ──────────────────────────────────────────────*/
function seatEl(seat, state, opts) {
  const m = meta(seat.type);
  const st = STATUS_STYLE[state.status] || STATUS_STYLE.open;
  const el = document.createElement('div');
  el.className = 'pd-seat';
  el.dataset.seatId = seat.id;
  el.style.left = pct(seat.x - m.w / 2, DW);
  el.style.top = pct(seat.y - m.h / 2, DH);
  el.style.width = pct(m.w, DW);
  el.style.height = pct(m.h, DH);
  el.style.borderRadius = m.r >= 999 ? '50%' : m.r + 'px';
  el.style.background = st.fill;
  el.style.borderColor = st.edge;
  el.style.color = st.ink;
  if (seat.rot) el.style.transform = `rotate(${seat.rot}deg)`;

  const showText = m.w >= 30;
  if (state.status === 'decor' || !m.bookable) {
    el.innerHTML = `<span class="pd-seat-ico">${m.emoji}</span>`;
  } else {
    el.innerHTML =
      `<span class="pd-seat-lbl">${esc(seat.label || '')}</span>` +
      (showText && state.sub ? `<span class="pd-seat-sub">${esc(state.sub)}</span>` : '');
  }
  if (state.dot) {
    const d = document.createElement('span');
    d.className = 'pd-seat-dot';
    if (state.dotColor) d.style.background = state.dotColor;
    el.appendChild(d);
  }
  if (opts.onSeatTap && (m.bookable || opts.tapDecor)) el.classList.add('tappable');
  return el;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function zoneEl(zone) {
  const el = document.createElement('div');
  el.className = 'pd-zone' + (zone.shape === 'pool' ? ' pool' : '');
  el.dataset.zoneId = zone.id;
  el.style.left = pct(zone.x, DW);
  el.style.top = pct(zone.y, DH);
  el.style.width = pct(zone.w, DW);
  el.style.height = pct(zone.h, DH);
  el.style.borderColor = zone.color;
  el.style.background = zone.color + '22';
  el.innerHTML = `<span class="pd-zone-name" style="color:${esc(zone.color)}">${esc(zone.name || '')}</span>`;
  return el;
}

/* ══ Read-only / tappable map ═════════════════════════════════════════════
   container : element to fill
   layout    : {seats:[], zones:[]}
   opts.seatState(seat) -> {status:'open'|'reserved'|'active'|'decor', sub, dot, dotColor}
   opts.onSeatTap(seat, ev)
   opts.emptyHTML : shown when the layout has no seats
   ════════════════════════════════════════════════════════════════════════*/
function renderMap(container, layout, opts) {
  injectCSS();
  opts = opts || {};
  const seats = (layout && layout.seats) || [];
  const zones = (layout && layout.zones) || [];

  const wrap = document.createElement('div');
  wrap.className = 'pd-wrap';
  zones.forEach(z => wrap.appendChild(zoneEl(z)));

  seats.forEach(seat => {
    const m = meta(seat.type);
    const bookable = seat.bookable !== undefined ? seat.bookable : m.bookable;
    const state = bookable && opts.seatState ? (opts.seatState(seat) || { status: 'open' })
                                             : { status: 'decor' };
    wrap.appendChild(seatEl(seat, state, opts));
  });

  if (!seats.length && !zones.length) {
    const e = document.createElement('div');
    e.className = 'pd-empty';
    e.innerHTML = opts.emptyHTML ||
      '<b>No deck layout yet</b><span>A manager can lay out the chairs, umbrellas and cabanas in the layout editor.</span>';
    wrap.appendChild(e);
  }

  if (opts.onSeatTap) {
    wrap.addEventListener('click', ev => {
      const el = ev.target.closest('.pd-seat.tappable');
      if (!el) return;
      const seat = seats.find(s => String(s.id) === el.dataset.seatId);
      if (seat) opts.onSeatTap(seat, ev);
    });
  }

  container.innerHTML = '';
  container.appendChild(wrap);
  return wrap;
}

/* ══ Drag-and-drop layout editor ══════════════════════════════════════════
   Pointer events (not mouse/touch) so one code path drives both a manager's
   mouse and a lifeguard's thumb.

   new LayoutEditor(container, layout, {
     onSelect(sel)  // sel = {kind:'seat'|'zone', item} or null
     onDirty()      // fired whenever geometry changes
   })
   ════════════════════════════════════════════════════════════════════════*/
class LayoutEditor {
  constructor(container, layout, opts) {
    injectCSS();
    this.container = container;
    this.opts = opts || {};
    this.grid = 10;
    this.sel = null;                     // {kind, id}
    this.setLayout(layout);
  }

  setLayout(layout) {
    this.layout = {
      w: DW, h: DH,
      seats: JSON.parse(JSON.stringify((layout && layout.seats) || [])),
      zones: JSON.parse(JSON.stringify((layout && layout.zones) || [])),
    };
    this.sel = null;
    this.render();
  }

  /* Serialise for POST /api/layout */
  toJSON() { return { seats: this.layout.seats, zones: this.layout.zones }; }

  selected() {
    if (!this.sel) return null;
    const list = this.sel.kind === 'seat' ? this.layout.seats : this.layout.zones;
    const item = list.find(x => String(x.id) === String(this.sel.id));
    return item ? { kind: this.sel.kind, item } : null;
  }

  /* Selection repaints classes in place rather than rebuilding the canvas —
     a full re-render here would detach the very element a pointerdown is about
     to start dragging. */
  select(kind, id) {
    this.sel = kind ? { kind, id } : null;
    this._paintSelection();
    if (this.opts.onSelect) this.opts.onSelect(this.selected());
  }

  _paintSelection() {
    if (!this.wrap) return;
    this.wrap.querySelectorAll('.sel').forEach(el => el.classList.remove('sel'));
    this.wrap.querySelectorAll('.pd-grip').forEach(el => el.remove());
    if (!this.sel) return;
    const el = this.wrap.querySelector(
      this.sel.kind === 'seat' ? `.pd-seat[data-seat-id="${this.sel.id}"]`
                               : `.pd-zone[data-zone-id="${this.sel.id}"]`);
    if (!el) return;
    el.classList.add('sel');
    if (this.sel.kind === 'zone') {
      const grip = document.createElement('div');
      grip.className = 'pd-grip';
      el.appendChild(grip);
    }
  }

  dirty() { if (this.opts.onDirty) this.opts.onDirty(); }

  /* ── Mutations ─────────────────────────────────────────────────────── */
  nextLabel(type) {
    // Loungers/chairs get A1, A2 … ; cabanas get C1 ; tables T1 — so a staffer
    // can call out a chair number that matches the sticker on the frame.
    const prefix = { lounger: 'A', chair: 'A', daybed: 'D', table: 'T', cabana: 'C' }[type] || 'X';
    const used = this.layout.seats
      .map(s => (s.label || '').match(new RegExp('^' + prefix + '(\\d+)$')))
      .filter(Boolean).map(m => Number(m[1]));
    const n = used.length ? Math.max(...used) + 1 : 1;
    return prefix + n;
  }

  addSeat(type, at) {
    const m = meta(type);
    const p = at || { x: DW / 2, y: DH / 2 };
    const seat = {
      id: Date.now() + Math.floor(Math.random() * 1000),
      label: m.bookable ? this.nextLabel(type) : '',
      type, x: snap(clamp(p.x, 0, DW), this.grid), y: snap(clamp(p.y, 0, DH), this.grid),
      rot: 0, cap: m.cap, bookable: m.bookable,
    };
    this.layout.seats.push(seat);
    this.render();
    this.select('seat', seat.id);
    this.dirty();
    return seat;
  }

  /* Lay down a run of chairs in one gesture — the single biggest time-saver
     when setting up a real deck of 40+ loungers. */
  addRow(type, count, opts) {
    opts = opts || {};
    const m = meta(type);
    const gap = opts.gap != null ? opts.gap : 8;
    const step = (opts.vertical ? m.h : m.w) + gap;
    const total = step * (count - 1);
    let x = opts.x != null ? opts.x : (opts.vertical ? DW / 2 : (DW - total) / 2);
    let y = opts.y != null ? opts.y : (opts.vertical ? (DH - total) / 2 : DH / 2);
    const made = [];
    for (let i = 0; i < count; i++) {
      const seat = {
        id: Date.now() + i * 7 + Math.floor(Math.random() * 100),
        label: m.bookable ? this.nextLabel(type) : '',
        type,
        x: snap(clamp(opts.vertical ? x : x + step * i, 0, DW), this.grid),
        y: snap(clamp(opts.vertical ? y + step * i : y, 0, DH), this.grid),
        rot: 0, cap: m.cap, bookable: m.bookable,
      };
      this.layout.seats.push(seat);
      made.push(seat);
    }
    this.render();
    this.dirty();
    return made;
  }

  addZone(name, shape) {
    const zone = {
      id: Date.now() + Math.floor(Math.random() * 1000),
      name: name || 'New zone',
      x: 120, y: 120, w: 300, h: 180,
      color: ZONE_COLORS[this.layout.zones.length % ZONE_COLORS.length],
      shape: shape || 'rect',
    };
    this.layout.zones.push(zone);
    this.render();
    this.select('zone', zone.id);
    this.dirty();
    return zone;
  }

  update(kind, id, patch) {
    const list = kind === 'seat' ? this.layout.seats : this.layout.zones;
    const item = list.find(x => String(x.id) === String(id));
    if (!item) return;
    Object.assign(item, patch);
    this.render();
    this.dirty();
  }

  remove(kind, id) {
    if (kind === 'seat') this.layout.seats = this.layout.seats.filter(x => String(x.id) !== String(id));
    else this.layout.zones = this.layout.zones.filter(x => String(x.id) !== String(id));
    this.render();
    this.select(null);
    this.dirty();
  }

  duplicate(kind, id) {
    const list = kind === 'seat' ? this.layout.seats : this.layout.zones;
    const src = list.find(x => String(x.id) === String(id));
    if (!src) return;
    const copy = JSON.parse(JSON.stringify(src));
    copy.id = Date.now() + Math.floor(Math.random() * 1000);
    copy.x = clamp(copy.x + 40, 0, DW);
    copy.y = clamp(copy.y + 30, 0, DH);
    if (kind === 'seat' && copy.label) copy.label = this.nextLabel(copy.type);
    list.push(copy);
    this.render();
    this.select(kind, copy.id);
    this.dirty();
  }

  clearAll() {
    this.layout.seats = [];
    this.layout.zones = [];
    this.render();
    this.select(null);
    this.dirty();
  }

  counts() {
    const bookable = this.layout.seats.filter(s => s.bookable !== false && meta(s.type).bookable);
    return { total: this.layout.seats.length, bookable: bookable.length, zones: this.layout.zones.length };
  }

  /* ── Render + drag wiring ──────────────────────────────────────────── */
  render() {
    const wrap = renderMap(this.container, this.layout, {
      seatState: seat => {
        const m = meta(seat.type);
        const bookable = seat.bookable !== undefined ? seat.bookable : m.bookable;
        return { status: bookable ? 'open' : 'decor', sub: '' };
      },
    });
    wrap.classList.add('editing');
    this.wrap = wrap;
    this._paintSelection();
    this._wireDrag(wrap);
  }

  _wireDrag(wrap) {
    let drag = null;

    wrap.addEventListener('pointerdown', ev => {
      const grip = ev.target.closest('.pd-grip');
      const seat = ev.target.closest('.pd-seat');
      const zone = ev.target.closest('.pd-zone');
      const p = toDesign(wrap, ev);

      if (grip) {
        const z = this.layout.zones.find(x => String(x.id) === String(this.sel.id));
        if (!z) return;
        drag = { mode: 'resize', item: z, startW: z.w, startH: z.h, ox: p.x, oy: p.y, moved: false };
      } else if (seat) {
        const s = this.layout.seats.find(x => String(x.id) === seat.dataset.seatId);
        if (!s) return;
        if (!this.sel || this.sel.kind !== 'seat' || String(this.sel.id) !== String(s.id)) {
          this.select('seat', s.id);
        }
        drag = { mode: 'seat', item: s, ox: p.x - s.x, oy: p.y - s.y, moved: false };
      } else if (zone) {
        const z = this.layout.zones.find(x => String(x.id) === zone.dataset.zoneId);
        if (!z) return;
        if (!this.sel || this.sel.kind !== 'zone' || String(this.sel.id) !== String(z.id)) {
          this.select('zone', z.id);
        }
        drag = { mode: 'zone', item: z, ox: p.x - z.x, oy: p.y - z.y, moved: false };
      } else {
        this.select(null);
        return;
      }
      ev.preventDefault();
      this.wrap.setPointerCapture(ev.pointerId);
    });

    wrap.addEventListener('pointermove', ev => {
      if (!drag) return;
      const p = toDesign(this.wrap, ev);
      drag.moved = true;
      if (drag.mode === 'seat') {
        drag.item.x = snap(clamp(p.x - drag.ox, 0, DW), this.grid);
        drag.item.y = snap(clamp(p.y - drag.oy, 0, DH), this.grid);
        this._moveEl(`.pd-seat[data-seat-id="${drag.item.id}"]`, drag.item, meta(drag.item.type));
      } else if (drag.mode === 'zone') {
        drag.item.x = snap(clamp(p.x - drag.ox, 0, DW - drag.item.w), this.grid);
        drag.item.y = snap(clamp(p.y - drag.oy, 0, DH - drag.item.h), this.grid);
        const el = this.wrap.querySelector(`.pd-zone[data-zone-id="${drag.item.id}"]`);
        if (el) { el.style.left = pct(drag.item.x, DW); el.style.top = pct(drag.item.y, DH); }
      } else if (drag.mode === 'resize') {
        drag.item.w = snap(clamp(drag.startW + (p.x - drag.ox), 40, DW - drag.item.x), this.grid);
        drag.item.h = snap(clamp(drag.startH + (p.y - drag.oy), 40, DH - drag.item.y), this.grid);
        const el = this.wrap.querySelector(`.pd-zone[data-zone-id="${drag.item.id}"]`);
        if (el) { el.style.width = pct(drag.item.w, DW); el.style.height = pct(drag.item.h, DH); }
      }
    });

    const end = ev => {
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      try { this.wrap.releasePointerCapture(ev.pointerId); } catch (e) {}
      if (moved) this.dirty();
    };
    wrap.addEventListener('pointerup', end);
    wrap.addEventListener('pointercancel', end);
  }

  /* Move without a full re-render so dragging stays smooth on a phone. */
  _moveEl(sel, item, m) {
    const el = this.wrap.querySelector(sel);
    if (!el) return;
    el.style.left = pct(item.x - m.w / 2, DW);
    el.style.top = pct(item.y - m.h / 2, DH);
    el.classList.add('dragging');
    clearTimeout(this._dragCls);
    this._dragCls = setTimeout(() => el.classList.remove('dragging'), 180);
  }
}

/* ══ Starter menu ═════════════════════════════════════════════════════════
   Offered as a one-tap seed so a new pool's POS isn't a blank screen. */
const STARTER_MENU = [
  { name: 'Cheeseburger',    price: 8.50, category: 'Grill',   emoji: '🍔' },
  { name: 'Hot Dog',         price: 5.00, category: 'Grill',   emoji: '🌭' },
  { name: 'Chicken Tenders', price: 8.00, category: 'Grill',   emoji: '🍗' },
  { name: 'French Fries',    price: 4.00, category: 'Grill',   emoji: '🍟' },
  { name: 'Caesar Salad',    price: 9.00, category: 'Grill',   emoji: '🥗' },
  { name: 'Cheese Pizza',    price: 3.50, category: 'Grill',   emoji: '🍕' },
  { name: 'Bottled Water',   price: 2.00, category: 'Drinks',  emoji: '💧', taxable: false },
  { name: 'Soda',            price: 2.50, category: 'Drinks',  emoji: '🥤' },
  { name: 'Lemonade',        price: 3.00, category: 'Drinks',  emoji: '🍋' },
  { name: 'Iced Coffee',     price: 4.00, category: 'Drinks',  emoji: '☕' },
  { name: 'Gatorade',        price: 3.00, category: 'Drinks',  emoji: '🧃' },
  { name: 'Ice Cream Bar',   price: 3.50, category: 'Snacks',  emoji: '🍦' },
  { name: 'Popsicle',        price: 2.00, category: 'Snacks',  emoji: '🧊' },
  { name: 'Chips',           price: 2.00, category: 'Snacks',  emoji: '🥨' },
  { name: 'Candy',           price: 1.50, category: 'Snacks',  emoji: '🍬' },
  { name: 'Sunscreen SPF 50', price: 9.00, category: 'Shop',   emoji: '🧴' },
  { name: 'Goggles',         price: 12.00, category: 'Shop',   emoji: '🥽' },
  { name: 'Pool Towel',      price: 6.00, category: 'Shop',    emoji: '🧻' },
  { name: 'Day Pass',        price: 15.00, category: 'Passes', emoji: '🎟️', taxable: false },
  { name: 'Guest Pass',      price: 10.00, category: 'Passes', emoji: '🎫', taxable: false },
];

/* ══ Shared POS/deck domain logic ════════════════════════════════════════*/

/* Reservations that matter for "is this chair free right now": today only,
   not cancelled, not checked out. */
function liveReservations(reservations, poolId, dateStr) {
  return (reservations || []).filter(r =>
    Number(r.poolId) === Number(poolId) &&
    r.date === dateStr &&
    (r.status === 'reserved' || r.status === 'active'));
}

/* seatId -> reservation, for fast map colouring. */
function seatReservationMap(reservations, poolId, dateStr) {
  const map = {};
  liveReservations(reservations, poolId, dateStr).forEach(r => {
    const cur = map[r.seatId];
    // An active guest always wins over a later booking on the same chair
    if (!cur || (cur.status !== 'active' && r.status === 'active')) map[r.seatId] = r;
  });
  return map;
}

/* seatId -> open (unpaid) order total, so a chair can show its running tab. */
function seatTabMap(orders, poolId) {
  const map = {};
  (orders || []).forEach(o => {
    if (Number(o.poolId) !== Number(poolId) || o.status !== 'open' || o.seatId == null) return;
    map[o.seatId] = (map[o.seatId] || 0) + (o.subtotal + o.tax);
  });
  return map;
}

function occupancy(layout, resMap) {
  const seats = ((layout && layout.seats) || []).filter(s =>
    s.bookable !== undefined ? s.bookable : meta(s.type).bookable);
  let active = 0, reserved = 0;
  seats.forEach(s => {
    const r = resMap[s.id];
    if (!r) return;
    if (r.status === 'active') active++; else reserved++;
  });
  return { total: seats.length, active, reserved, open: seats.length - active - reserved };
}

/* Cart → the shape POST /api/order expects, plus a local preview of the
   totals the server will authoritatively recompute. */
function cartTotals(cart, menuById, taxRate) {
  let subtotal = 0, taxable = 0;
  cart.forEach(li => {
    const src = menuById[li.menuId];
    const price = src ? src.price : (li.price || 0);
    const isTaxable = src ? src.taxable !== false : li.taxable !== false;
    const t = price * li.qty;
    subtotal += t;
    if (isTaxable) taxable += t;
  });
  const tax = Math.round(taxable * (Number(taxRate) || 0) / 100);
  return { subtotal, tax, total: subtotal + tax };
}

function todayStr(d) {
  d = d || new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function nowHM() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function fmtHM(t) {
  if (!t) return '';
  const [h, m] = String(t).split(':').map(Number);
  if (isNaN(h)) return t;
  return `${h % 12 || 12}:${String(m || 0).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}
/* Minutes until a booking's end time — negative means it has run over. */
function minutesLeft(endHM) {
  if (!endHM) return null;
  const [h, m] = String(endHM).split(':').map(Number);
  if (isNaN(h)) return null;
  const now = new Date();
  return (h * 60 + (m || 0)) - (now.getHours() * 60 + now.getMinutes());
}

const PAYMENTS = {
  cash:    { label: 'Cash',    emoji: '💵' },
  card:    { label: 'Card',    emoji: '💳' },
  account: { label: 'Account', emoji: '🏠' },
  comp:    { label: 'Comp',    emoji: '🎁' },
};

/* ══ Square Point of Sale hand-off ════════════════════════════════════════
   Square's Reader SDK is native-only — a web page can't talk to card hardware.
   What a web page *can* do is hand the charge to the Square Point of Sale app
   already installed on the waiter's phone, which owns the card reader (or Tap
   to Pay), take the payment there, and come back with a transaction id.

   Worth knowing: `client_id` here is the Square *application* id. It is public
   by design — it only names the app being launched. Nothing that can move
   money passes through this code or this server; the merchant credentials live
   inside Square Point of Sale, signed in on the phone.  */
const SQUARE = {
  isAndroid: () => /android/i.test(navigator.userAgent),
  isIOS: () => /iphone|ipad|ipod/i.test(navigator.userAgent) ||
               (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1),
};

// amountCents: integer. callbackUrl: where Square returns the browser afterwards.
function squareChargeUrl({ appId, amountCents, callbackUrl, note, currency = 'USD' }) {
  if (SQUARE.isAndroid()) {
    return 'intent:#Intent;action=com.squareup.pos.action.CHARGE;package=com.squareup;'
      + `S.com.squareup.pos.WEB_CALLBACK_URI=${callbackUrl};`
      + `S.com.squareup.pos.CLIENT_ID=${appId};`
      + 'S.com.squareup.pos.API_VERSION=v2.0;'
      + `i.com.squareup.pos.TOTAL_AMOUNT=${amountCents};`
      + `S.com.squareup.pos.CURRENCY_CODE=${currency};`
      + 'S.com.squareup.pos.TENDER_TYPES=com.squareup.pos.TENDER_CARD,com.squareup.pos.TENDER_CARD_ON_FILE;'
      + 'end';
  }
  const data = {
    amount_money: { amount: String(amountCents), currency_code: currency },
    callback_url: callbackUrl,
    client_id: appId,
    version: '1.3',
    notes: (note || '').slice(0, 500),
    options: { supported_tender_types: ['CREDIT_CARD', 'CARD_ON_FILE'] },
  };
  return 'square-commerce-v1://payment/create?data=' + encodeURIComponent(JSON.stringify(data));
}

// Square answers on the callback URL with different parameter names per platform.
function squareReturn(search) {
  const q = new URLSearchParams(search);
  const err = q.get('error_code') || q.get('com.squareup.pos.ERROR_CODE');
  const txn = q.get('transaction_id') || q.get('com.squareup.pos.SERVER_TRANSACTION_ID');
  const client = q.get('client_transaction_id') || q.get('com.squareup.pos.CLIENT_TRANSACTION_ID');
  if (!err && !txn && !client) return null;      // not a Square return at all
  return { error: err, transactionId: txn, clientTransactionId: client };
}

// Square's codes are terse; a waiter mid-shift needs the plain version.
const SQUARE_ERRORS = {
  payment_canceled: 'Payment cancelled in Square.',
  'com.squareup.pos.ERROR_TRANSACTION_CANCELED': 'Payment cancelled in Square.',
  no_network_connection: 'Square had no connection — try again.',
  'com.squareup.pos.ERROR_NO_NETWORK': 'Square had no connection — try again.',
  not_logged_in: 'Square Point of Sale is not signed in on this phone.',
  'com.squareup.pos.ERROR_NOT_AUTHORIZED': 'This phone is not authorised to take Square payments.',
  unsupported_api_version: 'Square Point of Sale needs updating on this phone.',
  invalid_request: "Square rejected the request — check the Application ID on the Pools page.",
};
function squareErrorText(code) {
  return SQUARE_ERRORS[code] || `Square could not take the payment (${code || 'unknown error'}).`;
}

/* ══ Item modifiers ═══════════════════════════════════════════════════════
   One tap instead of typing. A waiter standing at a lounger in the sun is not
   going to spell out "light ice" on a phone keyboard, and free text arrives at
   the kitchen as "No Ice", "no ice", "w/o ice" — three things to read instead
   of one. These are the customisations that come up over and over; anything
   unusual still goes in the note field beside them. */
const MODIFIERS = {
  Drinks: ['No ice', 'Light ice', 'Extra ice', 'No straw', 'With lid',
           'Extra lemon', 'Diet', 'Unsweetened', 'Large cup'],
  Grill:  ['No cheese', 'No onion', 'No pickles', 'Well done', 'Extra crispy',
           'Plain', 'Add cheese', 'Gluten-free bun', 'Sauce on side'],
  Snacks: ['Cup, not cone', 'Extra napkins', 'Cut in half'],
  Shop:   [],
  Passes: [],
};
// Falls back to drink modifiers for a custom category the manager invented,
// since drinks are what get customised most at a pool.
function modsFor(category) {
  return MODIFIERS[category] || MODIFIERS.Drinks;
}

global.PoolDeck = {
  DW, DH, SEAT_META, SEAT_ORDER, ZONE_COLORS, STATUS_STYLE, STARTER_MENU, PAYMENTS,
  MODIFIERS, modsFor,
  SQUARE, squareChargeUrl, squareReturn, squareErrorText,
  meta, money, parseMoney, esc, renderMap, LayoutEditor,
  liveReservations, seatReservationMap, seatTabMap, occupancy, cartTotals,
  todayStr, nowHM, fmtHM, minutesLeft,
};

})(window);
