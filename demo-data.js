/* ══════════════════════════════════════════════════════════════════════════
   Mar-A-Lavitch — demo dataset for the static (GitHub Pages) build.

   Every person, punch, and shift in here is invented. The club's real
   data.json is gitignored and never leaves the server; this file exists so a
   stranger with a link can click through a fully populated app without an
   account, a server, or a single byte of real staff data.

   Dates are computed at load time so the demo always looks like "today",
   however long from now someone opens the link.
   ══════════════════════════════════════════════════════════════════════════ */
(function (global) {
'use strict';

const POOL_ID = 900000000001;

/* ── Dates, relative to whenever this is opened ──────────────────────────*/
const NOW = new Date();
function dayOffset(n) {
  const d = new Date(NOW);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
const TODAY = dayOffset(0);
function stamp(dayShift, hhmm) { return `${dayOffset(dayShift)} ${hhmm}`; }

/* ── People (invented) ───────────────────────────────────────────────────
   No passwords: /api/auth waves through any employee without one, so a
   visitor can pick a name and land straight in the staff app. */
const DEMO_EMPLOYEES = [
  { id: 4001, name: 'Dana Whitfield',  role: 'Manager',     phone: '555-0142', poolIds: [POOL_ID] },
  { id: 4002, name: 'Marcus Ortiz',    role: 'Lifeguard',   phone: '555-0168', poolIds: [POOL_ID] },
  { id: 4003, name: 'Priya Raman',     role: 'Lifeguard',   phone: '555-0193', poolIds: [POOL_ID] },
  { id: 4004, name: 'Jonah Feldman',   role: 'Lifeguard',   phone: '555-0117', poolIds: [POOL_ID] },
  { id: 4005, name: 'Sofia Klein',     role: 'Maintenance', phone: '555-0125', poolIds: [POOL_ID] },
];

/* ── Chemical log ────────────────────────────────────────────────────────
   Graded the same way the server grades: cl 1–3 and ph 7.2–7.8 pass, a
   drifting reading warns, out-of-band fails. */
const DEMO_CHEMICALS = [
  { id: 5001, poolId: POOL_ID, date: TODAY, time: '14:00', type: 'hourly', cl: 2.1, ph: 7.4, ccl: 0.1, by: 'Priya Raman',   status: 'pass', notes: '' },
  { id: 5002, poolId: POOL_ID, date: TODAY, time: '12:00', type: 'hourly', cl: 1.8, ph: 7.5, ccl: 0.2, by: 'Marcus Ortiz',  status: 'pass', notes: '' },
  { id: 5003, poolId: POOL_ID, date: TODAY, time: '10:00', type: 'hourly', cl: 0.9, ph: 7.9, ccl: 0.3, by: 'Marcus Ortiz',  status: 'warn', notes: 'Chlorine low at open — added tabs, rechecking at noon.' },
  { id: 5004, poolId: POOL_ID, date: dayOffset(-1), time: '16:00', type: 'hourly', cl: 2.4, ph: 7.3, ccl: 0.1, by: 'Jonah Feldman', status: 'pass', notes: '' },
  { id: 5005, poolId: POOL_ID, date: dayOffset(-1), time: '11:00', type: 'hourly', cl: 2.0, ph: 7.4, ccl: 0.0, by: 'Priya Raman',   status: 'pass', notes: '' },
];

/* ── Schedule: yesterday through the next three days ─────────────────────*/
const DEMO_SHIFTS = [
  { id: 6001, poolId: POOL_ID, empId: '4002', empName: 'Marcus Ortiz',  date: TODAY, start: '09:00', end: '17:00', role: 'Lifeguard' },
  { id: 6002, poolId: POOL_ID, empId: '4003', empName: 'Priya Raman',   date: TODAY, start: '11:00', end: '19:00', role: 'Lifeguard' },
  { id: 6003, poolId: POOL_ID, empId: '4001', empName: 'Dana Whitfield', date: TODAY, start: '08:00', end: '16:00', role: 'Manager' },
  { id: 6004, poolId: POOL_ID, empId: '4005', empName: 'Sofia Klein',   date: TODAY, start: '07:00', end: '11:00', role: 'Maintenance' },
  { id: 6005, poolId: POOL_ID, empId: '4004', empName: 'Jonah Feldman', date: dayOffset(1), start: '09:00', end: '17:00', role: 'Lifeguard' },
  { id: 6006, poolId: POOL_ID, empId: '4003', empName: 'Priya Raman',   date: dayOffset(1), start: '12:00', end: '20:00', role: 'Lifeguard' },
  { id: 6007, poolId: POOL_ID, empId: '4001', empName: 'Dana Whitfield', date: dayOffset(1), start: '08:00', end: '16:00', role: 'Manager' },
  { id: 6008, poolId: POOL_ID, empId: '4002', empName: 'Marcus Ortiz',  date: dayOffset(2), start: '09:00', end: '17:00', role: 'Lifeguard' },
  { id: 6009, poolId: POOL_ID, empId: '4004', empName: 'Jonah Feldman', date: dayOffset(2), start: '11:00', end: '19:00', role: 'Lifeguard' },
  { id: 6010, poolId: POOL_ID, empId: '4005', empName: 'Sofia Klein',   date: dayOffset(3), start: '07:00', end: '11:00', role: 'Maintenance' },
  { id: 6011, poolId: POOL_ID, empId: '4002', empName: 'Marcus Ortiz',  date: dayOffset(-1), start: '09:00', end: '17:00', role: 'Lifeguard' },
];

/* ── Punches: two guards on the clock right now, one closed shift ────────*/
const DEMO_PUNCHES = [
  { id: 7001, poolId: POOL_ID, empId: '4002', empName: 'Marcus Ortiz',  date: TODAY, in: '08:56', out: '', hours: 0,
    checkin_answers: { uniform: 'yes', rescue_tube: 'yes', first_aid: 'yes' }, checkin_flags: [] },
  { id: 7002, poolId: POOL_ID, empId: '4003', empName: 'Priya Raman',   date: TODAY, in: '10:58', out: '', hours: 0,
    checkin_answers: { uniform: 'yes', rescue_tube: 'yes', first_aid: 'no' }, checkin_flags: ['First aid kit missing gauze — restocked from storage.'] },
  { id: 7003, poolId: POOL_ID, empId: '4005', empName: 'Sofia Klein',   date: TODAY, in: '07:02', out: '11:04', hours: 4.03,
    checkin_answers: { uniform: 'yes' }, checkin_flags: [] },
  { id: 7004, poolId: POOL_ID, empId: '4002', empName: 'Marcus Ortiz',  date: dayOffset(-1), in: '08:58', out: '17:06', hours: 8.13,
    checkin_answers: { uniform: 'yes', rescue_tube: 'yes', first_aid: 'yes' }, checkin_flags: [] },
];

const DEMO_BREAKS = [
  { id: 7101, poolId: POOL_ID, empId: '4002', empName: 'Marcus Ortiz', date: TODAY, start: '13:00',
    duration: 30, status: 'scheduled', startedAt: null, endedAt: null, actualMinutes: null,
    assignedBy: 4001, assignedByName: 'Dana Whitfield' },
  { id: 7102, poolId: POOL_ID, empId: '4003', empName: 'Priya Raman', date: TODAY, start: '11:30',
    duration: 30, status: 'completed', startedAt: '11:32', endedAt: '12:00', actualMinutes: 28,
    assignedBy: 4001, assignedByName: 'Dana Whitfield' },
];

const DEMO_ANNOUNCEMENTS = [
  { id: 8001, poolId: POOL_ID, title: 'Swim meet Saturday — lanes 1–4 closed', priority: 'high',
    body: 'Setup starts 6:30am. Lap swim moves to the far end until noon. Extra guard on the deck for the warm-up block.',
    posted: stamp(-1, '18:20') },
  { id: 8002, poolId: POOL_ID, title: 'New rescue tubes in the guard shack', priority: 'normal',
    body: 'Old ones are retired — please pull a new tube at the start of your rotation.',
    posted: stamp(-3, '09:05') },
];

const DEMO_RESOURCES = [
  { id: 8101, poolId: POOL_ID, title: 'Chemical testing procedure', type: 'procedure',
    desc: 'How to take a reading, what passes, and when to close the pool.', url: '' },
  { id: 8102, poolId: POOL_ID, title: 'Emergency action plan', type: 'procedure',
    desc: 'Whistle codes, clear-the-pool steps, and who to call first.', url: '' },
];

const DEMO_NOTIFICATIONS = [
  { id: 8201, poolId: POOL_ID, empId: '4003', title: 'Break assigned', message: 'Dana assigned you a 30 min break at 3:00 PM.', read: false, ts: stamp(0, '12:40') },
  { id: 8202, poolId: POOL_ID, empId: '4002', title: 'Shift confirmed',  message: 'You are on 9:00 AM – 5:00 PM today.',        read: true,  ts: stamp(-1, '20:10') },
];

/* ── Deck in use: two seated parties, one reservation, one waiting ───────*/
const DEMO_RESERVATIONS = [
  { id: 8301, poolId: POOL_ID, seatId: 1016, seatLabel: 'C1', guestName: 'Alvarez', party: 4,
    phone: '555-0177', note: 'Birthday — bringing a cake.', date: TODAY, start: '15:00', end: '18:00',
    status: 'reserved', empId: '4001', empName: 'Dana Whitfield', created: stamp(0, '09:12'), checkedInAt: null },
  { id: 8302, poolId: POOL_ID, seatId: 1000, seatLabel: 'A1', guestName: 'Brenner', party: 1,
    phone: '555-0104', note: '', date: TODAY, start: '13:30', end: '',
    status: 'active', empId: '4003', empName: 'Priya Raman', created: stamp(0, '13:28'), checkedInAt: '13:30' },
  { id: 8303, poolId: POOL_ID, seatId: 1009, seatLabel: 'A10', guestName: 'Okafor', party: 2,
    phone: '555-0188', note: '', date: TODAY, start: '12:15', end: '',
    status: 'active', empId: '4002', empName: 'Marcus Ortiz', created: stamp(0, '12:14'), checkedInAt: '12:15' },
];

const DEMO_WAITLIST = [
  { id: 8401, poolId: POOL_ID, name: 'Sullivan', party: 3, phone: '555-0151',
    note: 'Wants shade if anything opens.', date: TODAY, added: '14:22', status: 'waiting', seatedSeatId: null },
  { id: 8402, poolId: POOL_ID, name: 'Chen', party: 2, phone: '555-0136',
    note: '', date: TODAY, added: '14:41', status: 'waiting', seatedSeatId: null },
];

/* Tax is 6% of the taxable lines only — water and passes ring up untaxed,
   same as the server computes it. */
const DEMO_ORDERS = [
  { id: 8501, poolId: POOL_ID, seatId: 1000, seatLabel: 'A1', guestName: 'Brenner',
    items: [ { menuId: 3000, name: 'Cheeseburger', price: 850, qty: 1, note: '', total: 850 },
             { menuId: 3008, name: 'Lemonade',     price: 300, qty: 2, note: 'no ice', total: 600 } ],
    subtotal: 1450, taxRate: 6.0, tax: 87, tip: 0, total: 1537,
    payment: 'cash', account: '', compReason: '', cashTendered: 0, changeDue: 0,
    settled: true, status: 'open', empId: '4003', empName: 'Priya Raman',
    created: stamp(0, '13:44'), date: TODAY, paidAt: null },
  { id: 8502, poolId: POOL_ID, seatId: 1009, seatLabel: 'A10', guestName: 'Okafor',
    items: [ { menuId: 3002, name: 'Chicken Tenders', price: 800, qty: 2, note: '', total: 1600 },
             { menuId: 3006, name: 'Bottled Water',   price: 200, qty: 2, note: '', total: 400 } ],
    subtotal: 2000, taxRate: 6.0, tax: 96, tip: 300, total: 2396,
    payment: 'cash', account: '', compReason: '', cashTendered: 2500, changeDue: 104,
    settled: true, status: 'paid', empId: '4002', empName: 'Marcus Ortiz',
    created: stamp(0, '12:31'), date: TODAY, paidAt: '13:05' },
];
/* Deck geometry and snack-bar menu — configuration, not personal data. */
const DEMO_LAYOUT = {
  poolId: POOL_ID, w: 1000, h: 700,
  zones: [
    {"id":2000,"name":"Main Deck","x":80.0,"y":60.0,"w":840.0,"h":250.0,"color":"#38bdf8","shape":"rect"}
  ],
  seats: [
    {"id":1000,"label":"A1","type":"lounger","x":330.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1001,"label":"A2","type":"lounger","x":380.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1002,"label":"A3","type":"lounger","x":430.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1003,"label":"A4","type":"lounger","x":480.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1004,"label":"A5","type":"lounger","x":520.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1005,"label":"A6","type":"lounger","x":570.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1006,"label":"A7","type":"lounger","x":620.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1007,"label":"A8","type":"lounger","x":670.0,"y":170.0,"rot":0,"cap":1,"bookable":true},
    {"id":1008,"label":"A9","type":"lounger","x":330.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1009,"label":"A10","type":"lounger","x":380.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1010,"label":"A11","type":"lounger","x":430.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1011,"label":"A12","type":"lounger","x":480.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1012,"label":"A13","type":"lounger","x":520.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1013,"label":"A14","type":"lounger","x":570.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1014,"label":"A15","type":"lounger","x":620.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1015,"label":"A16","type":"lounger","x":670.0,"y":260.0,"rot":0,"cap":1,"bookable":true},
    {"id":1016,"label":"C1","type":"cabana","x":340.0,"y":520.0,"rot":0,"cap":6,"bookable":true},
    {"id":1017,"label":"C2","type":"cabana","x":500.0,"y":520.0,"rot":0,"cap":6,"bookable":true},
    {"id":1018,"label":"C3","type":"cabana","x":660.0,"y":520.0,"rot":0,"cap":6,"bookable":true},
    {"id":1019,"label":"","type":"umbrella","x":500.0,"y":400.0,"rot":0,"cap":1,"bookable":false}
  ]
};

const DEMO_MENU = [
  {"id":3000,"poolId":900000000001,"name":"Cheeseburger","price":850,"category":"Grill","emoji":"🍔","desc":"","taxable":true,"active":true},
  {"id":3001,"poolId":900000000001,"name":"Hot Dog","price":500,"category":"Grill","emoji":"🌭","desc":"","taxable":true,"active":true},
  {"id":3002,"poolId":900000000001,"name":"Chicken Tenders","price":800,"category":"Grill","emoji":"🍗","desc":"","taxable":true,"active":true},
  {"id":3003,"poolId":900000000001,"name":"French Fries","price":400,"category":"Grill","emoji":"🍟","desc":"","taxable":true,"active":true},
  {"id":3004,"poolId":900000000001,"name":"Caesar Salad","price":900,"category":"Grill","emoji":"🥗","desc":"","taxable":true,"active":true},
  {"id":3005,"poolId":900000000001,"name":"Cheese Pizza","price":350,"category":"Grill","emoji":"🍕","desc":"","taxable":true,"active":true},
  {"id":3006,"poolId":900000000001,"name":"Bottled Water","price":200,"category":"Drinks","emoji":"💧","desc":"","taxable":false,"active":true},
  {"id":3007,"poolId":900000000001,"name":"Soda","price":250,"category":"Drinks","emoji":"🥤","desc":"","taxable":true,"active":true},
  {"id":3008,"poolId":900000000001,"name":"Lemonade","price":300,"category":"Drinks","emoji":"🍋","desc":"","taxable":true,"active":true},
  {"id":3009,"poolId":900000000001,"name":"Iced Coffee","price":400,"category":"Drinks","emoji":"☕","desc":"","taxable":true,"active":true},
  {"id":3010,"poolId":900000000001,"name":"Gatorade","price":300,"category":"Drinks","emoji":"🧃","desc":"","taxable":true,"active":true},
  {"id":3011,"poolId":900000000001,"name":"Ice Cream Bar","price":350,"category":"Snacks","emoji":"🍦","desc":"","taxable":true,"active":true},
  {"id":3012,"poolId":900000000001,"name":"Popsicle","price":200,"category":"Snacks","emoji":"🧊","desc":"","taxable":true,"active":true},
  {"id":3013,"poolId":900000000001,"name":"Chips","price":200,"category":"Snacks","emoji":"🥨","desc":"","taxable":true,"active":true},
  {"id":3014,"poolId":900000000001,"name":"Candy","price":150,"category":"Snacks","emoji":"🍬","desc":"","taxable":true,"active":true},
  {"id":3015,"poolId":900000000001,"name":"Sunscreen SPF 50","price":900,"category":"Shop","emoji":"🧴","desc":"","taxable":true,"active":true},
  {"id":3016,"poolId":900000000001,"name":"Goggles","price":1200,"category":"Shop","emoji":"🥽","desc":"","taxable":true,"active":true},
  {"id":3017,"poolId":900000000001,"name":"Pool Towel","price":600,"category":"Shop","emoji":"🧻","desc":"","taxable":true,"active":true},
  {"id":3018,"poolId":900000000001,"name":"Day Pass","price":1500,"category":"Passes","emoji":"🎟️","desc":"","taxable":false,"active":true},
  {"id":3019,"poolId":900000000001,"name":"Guest Pass","price":1000,"category":"Passes","emoji":"🎫","desc":"","taxable":false,"active":true}
];

/* ── The seed the static build boots from ────────────────────────────────*/
global.DEMO_SEED = function () {
  return JSON.parse(JSON.stringify({
    pools: [{ id: POOL_ID, name: 'Mar-A-Lavitch Pool', status: 'open',
              address: '1 Poolside Ln, Bethesda, MD', created: dayOffset(-60), tax_rate: 6.0 }],
    pool_status: 'open',
    employees: DEMO_EMPLOYEES,
    chemicals: DEMO_CHEMICALS,
    shifts: DEMO_SHIFTS,
    punches: DEMO_PUNCHES,
    breaks: DEMO_BREAKS,
    announcements: DEMO_ANNOUNCEMENTS,
    resources: DEMO_RESOURCES,
    notifications: DEMO_NOTIFICATIONS,
    shift_requests: [],
    shift_confirmations: {},
    layouts: [DEMO_LAYOUT],
    menu: DEMO_MENU,
    reservations: DEMO_RESERVATIONS,
    waitlist: DEMO_WAITLIST,
    orders: DEMO_ORDERS,
  }));
};

})(window);
