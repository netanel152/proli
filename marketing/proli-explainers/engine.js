// Proli explainers — Pictogram Motion engine (window.G), vertical 1080×1920.
// Easing, rng, colour, the Proli five-hue palette, the grid pattern with whole-row offsets, the motif library,
// this film's pictograms (tap, phone, bubble, pin, wrench, padlock, bell, funnel, cup, toggle, calendar…) and type.
(function () {
  const W = 1080, H = 1920;
  const G = (window.G = { W, H });

  // ───────── maths / easing ─────────
  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const lerp = (a, b, t) => a + (b - a) * t;
  const inv = (a, b, x) => clamp((x - a) / (b - a));
  const E = {
    lin: (t) => clamp(t),
    io: (t) => 0.5 - 0.5 * Math.cos(Math.PI * clamp(t)),
    o3: (t) => 1 - Math.pow(1 - clamp(t), 3),
    i3: (t) => Math.pow(clamp(t), 3),
    io3: (t) => (t = clamp(t), t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
    oExp: (t) => (t = clamp(t), t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)),
    iExp: (t) => (t = clamp(t), t <= 0 ? 0 : Math.pow(2, 10 * t - 10)),
    ioExp: (t) => (t = clamp(t), t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? Math.pow(2, 20 * t - 10) / 2 : (2 - Math.pow(2, -20 * t + 10)) / 2),
    oBack: (t, s = 1.7) => (t = clamp(t) - 1, t * t * ((s + 1) * t + s) + 1),
  };
  const rng = (seed) => {
    let a = seed >>> 0;
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  };
  const hash = (n) => rng(n * 9301 + 49297)();
  Object.assign(G, { clamp, lerp, inv, E, rng, hash });

  // ───────── colour ─────────
  const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const toHex = (c) => '#' + c.map((v) => Math.round(clamp(v, 0, 255)).toString(16).padStart(2, '0')).join('');
  const mix = (a, b, t) => { const A = hex(a), B = hex(b); return toHex(A.map((v, i) => lerp(v, B[i], t))); };
  const rgba = (h, a) => { const c = hex(h); return `rgba(${c[0]},${c[1]},${c[2]},${a})`; };
  G.mix = mix; G.rgba = rgba;

  // Five hues + one dark neutral, five tones each (t0 darkest → t4 lightest). Names are ours.
  const CREAM = '#f7f8f6', INK = '#10243e';
  const tones = (b) => [mix(b, '#000000', 0.34), mix(b, '#000000', 0.17), b, mix(b, '#ffffff', 0.2), mix(b, '#ffffff', 0.4)];
  const PAL = {
    sun:   { name: 'Proli Sun',  t: tones('#f6c31c'), fg: INK },
    water: { name: 'Pipe Water', t: tones('#2b78bf'), fg: CREAM },
    mint:  { name: 'Fixed Mint', t: tones('#1d8864'), fg: CREAM },
    coral: { name: 'Call Coral', t: tones('#d4583a'), fg: CREAM },
    lilac: { name: 'Quiet Lilac', t: tones('#6650b5'), fg: CREAM },
    night: { name: 'Proli Night', t: ['#081422', '#0c1b2f', '#10243e', '#1b3352', '#2a4567'], fg: CREAM, accent: '#f6c31c' },
  };
  for (const k in PAL) { PAL[k].key = k; PAL[k].base = PAL[k].t[2]; PAL[k].far = mix(PAL[k].fg, PAL[k].t[2], 0.42); }
  G.PAL = PAL; G.CREAM = CREAM; G.INK = INK; G.SUN = PAL.sun.t[2]; G.SUN0 = PAL.sun.t[0];

  // ───────── canvas helpers ─────────
  G.canvas = (w = W, h = H) => { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; };
  G.disc = (ctx, x, y, r, col) => { if (r <= 0) return; ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill(); };
  G.seg = (ctx, a, b, w, col) => { ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
  G.pline = (ctx, pts, w, col) => { ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.stroke(); };
  G.ring = (ctx, x, y, r, w, col, a0 = 0, a1 = Math.PI * 2) => { if (r <= 0) return; ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineCap = 'round'; ctx.beginPath(); ctx.arc(x, y, r, a0, a1); ctx.stroke(); };
  G.poly = (ctx, pts, col) => { ctx.fillStyle = col; ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); ctx.closePath(); ctx.fill(); };
  G.rrect = (ctx, x, y, w, h, r, col) => { ctx.fillStyle = col; ctx.beginPath(); ctx.roundRect(x, y, w, h, r); ctx.fill(); };

  // ───────── pictograms ─────────
  // Drop: disc + pointed top. Origin = disc centre, r = disc radius.
  G.drop = (ctx, x, y, r, col) => {
    if (r <= 0) return;
    ctx.fillStyle = col; ctx.beginPath();
    ctx.moveTo(x, y - r * 2.4);
    ctx.quadraticCurveTo(x + r * 0.95, y - r * 0.9, x + r, y);
    ctx.arc(x, y, r, 0, Math.PI);
    ctx.quadraticCurveTo(x - r * 0.95, y - r * 0.9, x, y - r * 2.4);
    ctx.fill();
  };
  // Map pin: an upside-down drop with a hole. (x, y) = the point on the ground, s = head radius.
  G.pin = (ctx, x, y, s, col, hole) => {
    ctx.save(); ctx.translate(x, y - s * 2.4); ctx.rotate(Math.PI); G.drop(ctx, 0, 0, s, col); ctx.restore();
    G.disc(ctx, x, y - s * 2.4, s * 0.42, hole);
  };
  // Sun: disc + 12 rounded rays.
  G.sunIcon = (ctx, x, y, r, col, spin = 0, rays = 1) => {
    G.disc(ctx, x, y, r, col);
    if (rays <= 0) return;
    for (let i = 0; i < 12; i++) {
      const a = spin + (i / 12) * Math.PI * 2;
      G.seg(ctx, [x + Math.cos(a) * r * 1.3, y + Math.sin(a) * r * 1.3], [x + Math.cos(a) * r * lerp(1.3, 1.7, rays), y + Math.sin(a) * r * lerp(1.3, 1.7, rays)], r * 0.18, col);
    }
  };
  G.moon = (ctx, x, y, r, col) => {
    ctx.save(); ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.clip();
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.arc(x + r * 0.5, y - r * 0.3, r * 0.86, 0, Math.PI * 2);
    ctx.fillStyle = col; ctx.fill('evenodd'); ctx.restore();
  };
  // Tap (faucet) on a wall pipe. (x, y) = spout mouth, s = scale. open: 0 closed → 1 open (handle turns).
  G.tap = (ctx, x, y, s, col, col2, open = 1) => {
    const w = s * 0.24;
    G.seg(ctx, [x + s * 1.6, y - s * 0.95], [x + s * 0.55, y - s * 0.95], w, col);            // pipe from the wall
    G.rrect(ctx, x + s * 1.55, y - s * 1.35, s * 0.22, s * 0.8, s * 0.06, col2);               // wall flange
    ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.arc(x + s * 0.18, y - s * 0.58, s * 0.37, -Math.PI / 2, -Math.PI, true); ctx.stroke(); // spout bend
    G.seg(ctx, [x - s * 0.19, y - s * 0.58], [x - s * 0.19, y - s * 0.12], w, col);
    G.rrect(ctx, x - s * 0.33, y - s * 0.16, s * 0.28, s * 0.16, s * 0.05, col);               // mouth
    // handle: a bar on a stem; open turns it 90°
    const hx = x + s * 0.95, hy = y - s * 1.32, a = lerp(0, Math.PI / 2, open);
    G.seg(ctx, [hx, y - s * 0.95], [hx, hy], w * 0.7, col);
    G.seg(ctx, [hx - Math.cos(a) * s * 0.42, hy - Math.sin(a) * s * 0.12], [hx + Math.cos(a) * s * 0.42, hy + Math.sin(a) * s * 0.12], w * 0.8, col2);
    G.disc(ctx, hx, hy, w * 0.55, col);
  };
  // Phone: a rounded slab with a screen. (x, y) = centre, s = height/2. Returns the screen rect.
  G.phone = (ctx, x, y, s, body, screen) => {
    const w = s * 1.0, h = s * 2;
    G.rrect(ctx, x - w / 2, y - s, w, h, s * 0.16, body);
    const sx = x - w / 2 + s * 0.08, sy = y - s + s * 0.2, sw = w - s * 0.16, sh = h - s * 0.36;
    G.rrect(ctx, sx, sy, sw, sh, s * 0.06, screen);
    G.disc(ctx, x, y + s - s * 0.08, s * 0.035, screen);
    return [sx, sy, sw, sh];
  };
  // Speech bubble with a tail. (x, y) = top-left, tail on 'r' (right, sent) or 'l'.
  G.bubble = (ctx, x, y, w, h, col, side = 'r') => {
    G.rrect(ctx, x, y, w, h, Math.min(h * 0.38, 34), col);
    const tx = side === 'r' ? x + w - h * 0.25 : x + h * 0.25;
    G.poly(ctx, [[tx - h * 0.18, y + h - 2], [tx + h * 0.18, y + h - 2], [side === 'r' ? tx + h * 0.32 : tx - h * 0.32, y + h + h * 0.28]], col);
  };
  // Check mark, p = draw progress.
  G.tick = (ctx, x, y, s, w, col, p = 1) => {
    const a = [x - s * 0.5, y], b = [x - s * 0.12, y + s * 0.38], c = [x + s * 0.55, y - s * 0.42];
    const L1 = Math.hypot(b[0] - a[0], b[1] - a[1]), L2 = Math.hypot(c[0] - b[0], c[1] - b[1]), d = clamp(p) * (L1 + L2);
    if (d <= 0) return;
    const pts = [a];
    if (d <= L1) pts.push([lerp(a[0], b[0], d / L1), lerp(a[1], b[1], d / L1)]);
    else pts.push(b, [lerp(b[0], c[0], (d - L1) / L2), lerp(b[1], c[1], (d - L1) / L2)]);
    G.pline(ctx, pts, w, col);
  };
  G.cross = (ctx, x, y, s, w, col) => { G.seg(ctx, [x - s / 2, y - s / 2], [x + s / 2, y + s / 2], w, col); G.seg(ctx, [x + s / 2, y - s / 2], [x - s / 2, y + s / 2], w, col); };
  // Wrench: a bar with an open ring head. (x, y) = centre, s = length/2, ang = rotation.
  G.wrench = (ctx, x, y, s, ang, col, bg) => {
    ctx.save(); ctx.translate(x, y); ctx.rotate(ang);
    G.seg(ctx, [0, s * 0.62], [0, -s * 0.55], s * 0.26, col);
    G.disc(ctx, 0, -s * 0.72, s * 0.34, col);
    G.rrect(ctx, -s * 0.11, -s * 1.12, s * 0.22, s * 0.42, s * 0.04, bg);      // jaw gap
    G.disc(ctx, 0, s * 0.68, s * 0.2, col); G.disc(ctx, 0, s * 0.68, s * 0.08, bg);
    ctx.restore();
  };
  // Padlock. (x, y) = body centre, s = body half width. open: shackle lifts and swings.
  G.padlock = (ctx, x, y, s, open, col, hole) => {
    const lift = s * 0.42 * E.oExp(open), sw = s * 0.62;
    ctx.save(); ctx.translate(x + sw, y - s * 0.55 - lift); ctx.rotate(-0.55 * E.oBack(clamp((open - 0.25) / 0.75), 2.2)); ctx.translate(-x - sw, -(y - s * 0.55 - lift));
    G.ring(ctx, x, y - s * 0.55 - lift, sw, s * 0.24, col, Math.PI, 0);
    G.seg(ctx, [x + sw, y - s * 0.55 - lift], [x + sw, y - s * 0.2 - lift], s * 0.24, col);
    G.seg(ctx, [x - sw, y - s * 0.55 - lift], [x - sw, y - s * 0.3], s * 0.24, col);
    ctx.restore();
    G.rrect(ctx, x - s, y - s * 0.55, s * 2, s * 1.55, s * 0.24, col);
    G.disc(ctx, x, y + s * 0.1, s * 0.2, hole); G.seg(ctx, [x, y + s * 0.15], [x, y + s * 0.5], s * 0.14, hole);
  };
  // Bell. (x, y) = top, s = height. swing in radians.
  G.bell = (ctx, x, y, s, col, swing = 0) => {
    ctx.save(); ctx.translate(x, y); ctx.rotate(swing);
    G.disc(ctx, 0, s * 0.02, s * 0.08, col);
    ctx.fillStyle = col; ctx.beginPath();
    ctx.moveTo(-s * 0.1, s * 0.1); ctx.quadraticCurveTo(-s * 0.4, s * 0.12, -s * 0.4, s * 0.55); ctx.lineTo(-s * 0.52, s * 0.8);
    ctx.lineTo(s * 0.52, s * 0.8); ctx.lineTo(s * 0.4, s * 0.55); ctx.quadraticCurveTo(s * 0.4, s * 0.12, s * 0.1, s * 0.1); ctx.closePath(); ctx.fill();
    G.disc(ctx, Math.sin(swing * 2) * s * 0.12, s * 0.9, s * 0.11, col);
    ctx.restore();
  };
  // Funnel. (x, y) = top centre, s = top half width.
  G.funnel = (ctx, x, y, s, col) => {
    G.poly(ctx, [[x - s, y], [x + s, y], [x + s * 0.16, y + s * 0.95], [x + s * 0.16, y + s * 1.45], [x - s * 0.16, y + s * 1.45], [x - s * 0.16, y + s * 0.95]], col);
  };
  // Magnifier. (x, y) = lens centre.
  G.magnifier = (ctx, x, y, s, col) => { G.ring(ctx, x, y, s, s * 0.26, col); G.seg(ctx, [x + s * 0.75, y + s * 0.75], [x + s * 1.55, y + s * 1.55], s * 0.34, col); };
  // Cup with steam. (x, y) = cup centre.
  G.cup = (ctx, x, y, s, col, steam = 1, tt = 0) => {
    G.rrect(ctx, x - s * 0.6, y - s * 0.55, s * 1.2, s * 1.1, [s * 0.06, s * 0.06, s * 0.42, s * 0.42], col);
    G.ring(ctx, x + s * 0.72, y - s * 0.08, s * 0.25, s * 0.15, col, -Math.PI / 2, Math.PI / 2);
    G.seg(ctx, [x - s * 0.8, y + s * 0.72], [x + s * 0.8, y + s * 0.72], s * 0.14, col);
    if (steam > 0) for (let i = 0; i < 3; i++) {
      const sx = x - s * 0.32 + i * s * 0.32, ph = tt * 2.4 + i;
      ctx.save(); ctx.globalAlpha = steam;
      G.pline(ctx, [[sx, y - s * 0.75], [sx + Math.sin(ph) * s * 0.08, y - s * 0.95], [sx - Math.sin(ph) * s * 0.08, y - s * 1.15]], s * 0.1, col);
      ctx.restore();
    }
  };
  // Toggle switch. on 0..1.
  G.toggle = (ctx, x, y, s, on, track, knob, trackOn) => {
    G.rrect(ctx, x - s, y - s * 0.5, s * 2, s, s * 0.5, mix(track, trackOn, on));
    G.disc(ctx, lerp(x - s * 0.5, x + s * 0.5, on), y, s * 0.38, knob);
  };
  // Calendar. (x, y) = centre, s = half width.
  G.calendar = (ctx, x, y, s, col, cell, lit = -1, litCol) => {
    G.rrect(ctx, x - s, y - s * 0.8, s * 2, s * 1.75, s * 0.18, col);
    G.rrect(ctx, x - s, y - s * 0.8, s * 2, s * 0.42, [s * 0.18, s * 0.18, 0, 0], mix(col, '#000000', 0.25));
    G.seg(ctx, [x - s * 0.5, y - s * 0.98], [x - s * 0.5, y - s * 0.62], s * 0.14, col); G.seg(ctx, [x + s * 0.5, y - s * 0.98], [x + s * 0.5, y - s * 0.62], s * 0.14, col);
    for (let j = 0; j < 3; j++) for (let i = 0; i < 4; i++) {
      const k = j * 4 + i, cx = x - s * 0.66 + i * s * 0.44, cy = y - s * 0.12 + j * s * 0.36;
      G.rrect(ctx, cx - s * 0.15, cy - s * 0.12, s * 0.3, s * 0.24, s * 0.05, k === lit ? litCol : cell);
    }
  };
  // Menu (three bars) and a list sheet with rows.
  G.menu = (ctx, x, y, s, col) => { for (let i = -1; i <= 1; i++) G.seg(ctx, [x - s, y + i * s * 0.62], [x + s, y + i * s * 0.62], s * 0.32, col); };
  // Toolbox (the pros' sign).
  G.toolbox = (ctx, x, y, s, col, bg) => {
    G.ring(ctx, x, y - s * 0.55, s * 0.36, s * 0.14, col, Math.PI, 0);
    G.rrect(ctx, x - s, y - s * 0.55, s * 2, s * 1.2, s * 0.14, col);
    G.rrect(ctx, x - s * 0.22, y - s * 0.12, s * 0.44, s * 0.22, s * 0.06, bg);
  };
  // Media icons for "text / photo / voice / video".
  G.icoText = (ctx, x, y, s, col) => { for (let i = 0; i < 3; i++) G.seg(ctx, [x + s * 0.6, y - s * 0.45 + i * s * 0.45], [x - s * (i === 2 ? 0.1 : 0.6), y - s * 0.45 + i * s * 0.45], s * 0.22, col); };
  G.icoCamera = (ctx, x, y, s, col, bg) => { G.rrect(ctx, x - s * 0.7, y - s * 0.42, s * 1.4, s * 0.95, s * 0.16, col); G.rrect(ctx, x - s * 0.24, y - s * 0.6, s * 0.48, s * 0.3, s * 0.06, col); G.disc(ctx, x, y + s * 0.05, s * 0.3, bg); G.disc(ctx, x, y + s * 0.05, s * 0.16, col); };
  G.icoMic = (ctx, x, y, s, col) => { G.rrect(ctx, x - s * 0.22, y - s * 0.7, s * 0.44, s * 0.85, s * 0.22, col); G.ring(ctx, x, y - s * 0.1, s * 0.4, s * 0.12, col, 0, Math.PI); G.seg(ctx, [x, y + s * 0.3], [x, y + s * 0.6], s * 0.12, col); G.seg(ctx, [x - s * 0.25, y + s * 0.62], [x + s * 0.25, y + s * 0.62], s * 0.12, col); };
  G.icoPlay = (ctx, x, y, s, col, bg) => { G.disc(ctx, x, y, s * 0.68, col); G.poly(ctx, [[x - s * 0.18, y - s * 0.3], [x - s * 0.18, y + s * 0.3], [x + s * 0.32, y]], bg); };

  // ───────── grid pattern ─────────
  const CELL = 180; G.CELL = CELL;
  function motif(ctx, kind, x, y, s, c, r) {
    const [a, b, cc] = c;
    ctx.save();
    ctx.beginPath(); ctx.rect(x, y, s, s); ctx.clip();
    ctx.fillStyle = a; ctx.fillRect(x, y, s, s);
    const rot = Math.floor(r() * 4);
    const upright = kind >= 14;
    ctx.translate(x + s / 2, y + s / 2); if (!upright) ctx.rotate(rot * Math.PI / 2); ctx.translate(-s / 2, -s / 2);
    switch (kind) {
      case 0: G.disc(ctx, 0, 0, s, b); break;
      case 1: G.disc(ctx, s / 2, s, s / 2, b); break;
      case 2: G.disc(ctx, s / 2, s / 2, s / 2, b); G.disc(ctx, s * 0.62, s * 0.42, s * 0.13, cc); break;
      case 3: { ctx.save(); ctx.beginPath(); ctx.arc(s / 2, s, s / 2, Math.PI, 0); ctx.clip();
        for (let i = 0; i < 7; i++) { ctx.fillStyle = i % 2 ? b : cc; ctx.fillRect(0, s / 2 + i * s / 14, s, s / 14); }
        ctx.restore(); break; }
      case 4: { const k = s / 4;
        for (let row = 0; row < 5; row++) for (let i = -1; i < 5; i++) {
          const cx = i * k + (row % 2 ? k / 2 : 0), cy = row * k * 0.5 + k * 0.5;
          G.disc(ctx, cx, cy, k * 0.5, row % 2 ? b : cc); G.disc(ctx, cx, cy, k * 0.3, a);
        } break; }
      case 5: { G.disc(ctx, s / 2, s / 2, s / 2, b);
        ctx.save(); ctx.beginPath(); ctx.arc(s / 2, s / 2, s / 2, 0, Math.PI * 2); ctx.clip();
        const k = s / 6; for (let j = 0; j < 7; j++) for (let i = 0; i < 7; i++) G.disc(ctx, i * k + (j % 2 ? k / 2 : 0), j * k, k * 0.36, cc);
        ctx.restore(); break; }
      case 7: for (let i = 5; i >= 1; i--) G.disc(ctx, 0, s, (s * i) / 5, i % 2 ? b : a); break;
      case 8: { const R = s * 0.3; ctx.fillStyle = b; ctx.beginPath();
        for (let i = 0; i < 16; i++) { const t0 = (i / 16) * Math.PI * 2; ctx.moveTo(s / 2, s / 2); ctx.arc(s / 2, s / 2, R * 1.35, t0, t0 + Math.PI / 32); }
        ctx.fill(); G.disc(ctx, s / 2, s / 2, R, b); G.disc(ctx, s / 2, s / 2, R * 0.55, cc); break; }
      case 9: { G.disc(ctx, s, s, s * 0.9, b); const k = s / 7;
        for (let j = 0; j < 7; j++) for (let i = 0; i < 7; i++) if ((i + j) % 2 === 0) { ctx.fillStyle = cc; ctx.fillRect(i * k + k * 0.2, j * k + k * 0.2, k * 0.6, k * 0.6); }
        break; }
      case 10: G.disc(ctx, s / 2, s / 2, s * 0.46, b); G.disc(ctx, s * 0.66, s * 0.38, s * 0.4, a); break;
      case 11: G.disc(ctx, 0, s / 2, s / 2, b); G.disc(ctx, s, s / 2, s / 2, cc); break;
      case 13: for (let i = 0; i < 9; i++) { ctx.globalAlpha = 1 - i / 10; ctx.fillStyle = b; ctx.fillRect(0, s * 0.1 + i * s * 0.09, s * (1 - i * 0.06), s * 0.045); } ctx.globalAlpha = 1; break;
      // ── this film's motifs ──
      case 14: { const k = s / 4;                                  // drop scales
        for (let j = 0; j < 4; j++) for (let i = 0; i < 4; i++) G.drop(ctx, k * (i + 0.5) + (j % 2 ? k / 2 : 0) - k / 4, k * (j + 0.72), k * 0.17, (i + j) % 2 ? b : cc);
        break; }
      case 15: G.bubble(ctx, s * 0.16, s * 0.24, s * 0.68, s * 0.42, b, 'r'); break;    // a speech bubble
      case 17: G.sunIcon(ctx, s / 2, s / 2, s * 0.2, b); break;
      case 19: G.disc(ctx, s / 2, s / 2, s * 0.4, b); G.disc(ctx, s / 2, s / 2, s * 0.24, a); G.disc(ctx, s / 2, s / 2, s * 0.08, cc); break; // nut
      case 20: { ctx.lineJoin = 'round'; ctx.strokeStyle = b; ctx.lineWidth = s * 0.1; ctx.lineCap = 'round';  // chevrons (pointing right-to-left)
        for (let i = 0; i < 2; i++) { ctx.beginPath(); ctx.moveTo(s * (0.72 - i * 0.24), s * 0.25); ctx.lineTo(s * (0.52 - i * 0.24), s * 0.5); ctx.lineTo(s * (0.72 - i * 0.24), s * 0.75); ctx.stroke(); }
        break; }
      case 21: G.tick(ctx, s / 2, s / 2, s * 0.5, s * 0.1, b); break;                   // a tick
    }
    ctx.restore();
  }
  G.motif = motif;
  G.MOTIFS = {
    home: [0, 1, 2, 3, 4, 5, 7, 10, 11, 14, 14, 15, 19, 13],
    day: [0, 1, 2, 3, 8, 9, 11, 13, 17, 17, 19, 20, 21, 15],
  };

  const patCache = new Map();
  G.pattern = function (pk, seed = 1, o = {}) {
    const spread = o.spread ?? 1, density = o.density ?? 0.5, set = o.set || 'home';
    const key = [pk, seed, spread, density, set].join('|');
    if (patCache.has(key)) { const v = patCache.get(key); patCache.delete(key); patCache.set(key, v); return v; }
    if (patCache.size > 10) patCache.delete(patCache.keys().next().value);
    const S = CELL, P = PAL[pk], base = P.t[2];
    const T = P.t.map((c) => mix(base, c, spread));
    const cols = Math.ceil(W / S) + 3, rows = Math.ceil(H / S) + 1;
    const cv = G.canvas(cols * S, rows * S), ctx = cv.getContext('2d');
    const r = rng(seed * 7919 + 13);
    ctx.fillStyle = T[1]; ctx.fillRect(0, 0, cv.width, cv.height);
    for (let j = -1; j < rows; j++) for (let i = -1; i < cols; i++) {
      const k = r(), cx = i * S, cy = j * S;
      if (k < 0.34) G.disc(ctx, cx + S, cy + S, S, T[Math.floor(r() * 4)]);
      else if (k < 0.5) { G.disc(ctx, cx + S, cy + S, S, T[3]); G.disc(ctx, cx + S, cy + S, S * 0.5, T[0]); }
    }
    const lib = G.MOTIFS[set];
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      if (r() > density) continue;
      const pick = () => T[Math.floor(r() * 5)];
      let a = pick(), b = pick(); while (b === a) b = pick();
      let cc = pick(); while (cc === b) cc = pick();
      motif(ctx, lib[Math.floor(r() * lib.length)], i * S, j * S, S, [a, b, cc], r);
    }
    patCache.set(key, cv);
    return cv;
  };
  G.drawPattern = function (ctx, pat, offs) {
    const S = CELL, rows = Math.ceil(H / S);
    for (let j = 0; j < rows + 1; j++) {
      let dx = offs ? offs(j) : 0;
      dx = ((dx % S) + S) % S - S;
      ctx.drawImage(pat, 0, j * S, pat.width, S, dx, j * S, pat.width, S + 0.5);
    }
  };

  // ───────── type (Hebrew right-to-left; mono left-to-right) ─────────
  G.F = {
    he: (w, px) => `${w} ${px}px "Heebo"`,
    mono: (px, w = 500) => `${w} ${px}px "DM Mono"`,
  };
  // Mask slide-up. align 'right' anchors the right edge (Hebrew); dir defaults to rtl.
  G.maskText = function (ctx, text, x, y, font, col, p, o = {}) {
    if (p <= 0) return 0;
    ctx.save();
    ctx.font = font; ctx.letterSpacing = o.ls || '0px'; ctx.direction = o.dir || 'rtl'; ctx.textAlign = o.align || 'right'; ctx.textBaseline = 'alphabetic';
    const m = ctx.measureText(text);
    const asc = Math.max(m.actualBoundingBoxAscent, m.fontBoundingBoxAscent * 0.8), desc = m.actualBoundingBoxDescent, h = asc + desc;
    const w = m.width + 60;
    const al = ctx.textAlign;
    const x0 = al === 'center' ? x - w / 2 : al === 'right' ? x - w + 30 : x - 30;
    ctx.beginPath(); ctx.rect(x0, y - asc - 10, w, h + 22); ctx.clip();
    const dy = (1 - E.oExp(p)) * (h + 24);
    ctx.fillStyle = col; ctx.fillText(text, x, y + dy);
    ctx.restore();
    return m.width;
  };
  G.measure = (ctx, text, font, ls = '0px') => { ctx.save(); ctx.font = font; ctx.letterSpacing = ls; const w = ctx.measureText(text).width; ctx.restore(); return w; };
  G.typeText = function (ctx, text, x, y, font, col, p, o = {}) {
    const n = Math.floor(text.length * clamp(p) + 0.001);
    if (n <= 0) return;
    ctx.save(); ctx.font = font; ctx.letterSpacing = o.ls || '0px'; ctx.fillStyle = col; ctx.direction = o.dir || 'rtl'; ctx.textAlign = o.align || 'right';
    ctx.fillText(text.slice(0, n), x, y);
    ctx.restore();
  };
  G.hair = (ctx, x, y, w, col, p = 1, lw = 2) => { ctx.fillStyle = col; ctx.fillRect(x - w * clamp(p), y, w * clamp(p), lw); };
})();
