// Proli explainers — one page, two films (?film=customer | ?film=pro), 42 s each, 1080×1920, 24 fps.
// 120 BPM: beat 0.5 s, bar 2 s. render(t) keeps no state between frames.
(function () {
  const { W, H, E, PAL, clamp, lerp, inv, rgba, mix, CELL } = G;
  const F = G.F;
  const Q = new URLSearchParams(location.search);
  const FILM = Q.get('film') === 'pro' ? 'pro' : 'customer';
  const FULL = 42;

  // ───────── layout (vertical) ─────────
  const SX = 540, SY = 760, SR = 390;      // stage disc
  const TX = W - 96;                       // text column: right edge (Hebrew reads from the right)
  const TY = 1400;                         // first title baseline
  const MAXW = W - 192;

  // ───────── helpers ─────────
  const rowDrift = (t, seed, sp = 22, amp = 22) => (j) => (j % 2 ? 1 : -1) * t * sp + Math.sin(j * 1.7 + seed) * amp;
  const jumpRows = (t, beats, seed) => (j) => rowDrift(t, seed)(j) + (j % 2 ? 1 : -1) * CELL * 0.25 * beats.reduce((a, b) => a + E.oExp(inv(b, b + 0.2, t)), 0);
  const pop = (lt, t0, d = 0.32) => E.oBack(inv(t0, t0 + d, lt), 2.2);
  function bg(ctx, P, seed, lt, o = {}) {
    ctx.fillStyle = P.t[2]; ctx.fillRect(0, 0, W, H);
    G.drawPattern(ctx, G.pattern(P.key, seed, { spread: o.spread ?? 0.34, density: o.density ?? 0.45, set: o.set || 'home' }), o.offs || rowDrift(lt, seed));
  }
  function stageDisc(ctx, P, lt, o = {}) {
    const col = o.col || (P.fg === G.INK ? P.t[4] : P.t[3]), r = (o.r || SR) * (o.scale ?? 1);
    G.disc(ctx, SX, SY, r, col);
    ctx.save(); ctx.beginPath(); ctx.arc(SX, SY, r, 0, Math.PI * 2); ctx.clip();
    const sx = SX + r * 1.6 - E.o3(inv(0.1, 2.6, lt)) * r * 3.2;   // the one light sweep, right to left
    const g = ctx.createLinearGradient(sx - r * 0.5, SY - r, sx + r * 0.5, SY + r);
    g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.5, 'rgba(255,255,255,0.1)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = g; ctx.fillRect(SX - r, SY - r, r * 2, r * 2);
    ctx.restore();
    return col;
  }
  // Bands assembly: draw fn into a buffer, then slide horizontal bands into register from alternating sides.
  const bandBuf = G.canvas();
  function assemble(ctx, lt, fn, d = 0.45) {
    const p = inv(0, d, lt);
    if (p >= 1) { fn(ctx); return; }
    const b = bandBuf.getContext('2d'); b.setTransform(1, 0, 0, 1, 0, 0); b.clearRect(0, 0, W, H); fn(b);
    const bh = 60;
    for (let j = 0; j < H / bh; j++) {
      const y = j * bh; if (y < SY - SR - 80 || y > SY + SR + 80) continue;
      const d2 = (j % 2 ? 1 : -1) * (120 + 220 * G.hash(j + 5)) * (1 - E.oExp(clamp(p * 1.25 - (j % 5) * 0.04)));
      ctx.drawImage(bandBuf, 0, y, W, bh, d2, y, W, bh);
    }
  }
  function chip(ctx, xr, y, text, fg, bg2, p, o = {}) {
    if (p <= 0) return 0;
    const px = o.px || 34, font = o.mono ? F.mono(px) : F.he(700, px);
    ctx.save(); ctx.font = font; ctx.letterSpacing = o.mono ? '2px' : '0px';
    const tw = ctx.measureText(text).width + 44, h = Math.round(px * 1.75);
    const x = xr - tw;
    ctx.fillStyle = bg2; ctx.beginPath(); ctx.rect(xr - tw * p, y, tw * p, h); ctx.fill();
    ctx.beginPath(); ctx.rect(xr - tw * p, y, tw * p, h); ctx.clip();
    ctx.fillStyle = fg; ctx.direction = o.mono ? 'ltr' : 'rtl'; ctx.textAlign = 'center';
    ctx.fillText(text, x + tw / 2, y + px * 1.22);
    ctx.restore(); return tw;
  }
  // Text block: meta (mono, ltr), title lines (Heebo 900, mask slide-up), second line (Heebo 700), chip.
  function titlePx(lines, px) {
    const c = bandBuf.getContext('2d');
    const wmax = Math.max(...lines.map((l) => G.measure(c, l, F.he(900, px), (-0.01 * px) + 'px')));
    return wmax > MAXW ? Math.floor(px * MAXW / wmax) : px;
  }
  function textBlock(ctx, lt, T, fg) {
    if (!T) return;
    const d0 = T.d0 ?? 0.25, lines = [].concat(T.title), px = titlePx(lines, T.px || 200), lh = px * 0.98;
    const y0 = T.y ?? TY;
    if (T.meta) G.typeText(ctx, T.meta, TX, y0 - px * 0.82 - 30, F.mono(28), rgba(fg, 0.78), inv(d0, d0 + 0.3, lt), { ls: '4px', dir: 'ltr', align: 'right' });
    lines.forEach((ln, i) => G.maskText(ctx, ln, TX + px * 0.02, y0 + i * lh, F.he(900, px), T.titleCol || fg, inv(d0 + 0.08 * i, d0 + 0.5 + 0.08 * i, lt), { ls: (-0.01 * px) + 'px' }));
    let yy = y0 + (lines.length - 1) * lh;
    if (T.sub) { const sp = T.spx || 58; yy += sp + 34; G.maskText(ctx, T.sub, TX, yy, F.he(700, sp), fg, inv(d0 + 0.16, d0 + 0.66, lt)); }
    if (T.chip) chip(ctx, TX, yy + 34, T.chip, T.chipFg || PAL.night.t[2], T.chipBg || G.SUN, E.oExp(inv(d0 + 0.3, d0 + 0.65, lt)), { mono: T.chipMono, px: 34 });
  }
  const textReady = (T) => (T.d0 ?? 0.25) + 0.75;
  function textBox(T) {
    const lines = [].concat(T.title), px = titlePx(lines, T.px || 200), y0 = T.y ?? TY;
    const y1 = y0 + (lines.length - 1) * px * 0.98 + (T.sub ? (T.spx || 58) + 50 : 20) + (T.chip ? 100 : 0);
    return [96, y0 - px * 0.9 - (T.meta ? 60 : 0), W - 60, y1];
  }

  // ───────── the HUD ─────────
  function hud(ctx, t, P, info) {
    if (!info) return;
    const fg = P.fg;
    ctx.save();
    ctx.font = F.he(500, 30); ctx.direction = 'rtl'; ctx.textAlign = 'right'; ctx.fillStyle = rgba(fg, 0.86);
    ctx.fillText(info.right, W - 64, 96);
    ctx.font = F.mono(26); ctx.direction = 'ltr'; ctx.textAlign = 'left'; ctx.letterSpacing = '4px'; ctx.fillStyle = rgba(fg, 0.86);
    ctx.fillText(info.left, 64, 96);
    // progress: N ticks right to left (Hebrew order)
    const N = info.n, x0 = W - 64, x1 = 64, y = H - 84;
    for (let i = 1; i <= N; i++) {
      const x = lerp(x0, x1, (i - 1) / (N - 1));
      const now = i === info.cur, done = i < info.cur;
      ctx.fillStyle = rgba(fg, now ? 1 : done ? 0.75 : 0.28);
      ctx.fillRect(Math.round(x) - (now ? 3 : 1.5), y - (now ? 18 : 8), now ? 6 : 3, now ? 26 : 16);
    }
    ctx.restore();
  }

  // ───────── shared pictogram scenes ─────────
  // A phone on the stage; returns its screen rect. Bubbles drawn by the caller in screen coordinates.
  function phoneOn(ctx, P, s = 300) { return G.phone(ctx, SX, SY + 10, s, G.INK, G.CREAM); }
  function bars(ctx, x, y, w, n, col, lw = 16) { for (let i = 0; i < n; i++) G.seg(ctx, [x + w, y + i * 32], [x + w * (i === n - 1 ? 0.45 : 0.05), y + i * 32], lw, col); }
  // A word inside a bubble (Hebrew), typed or shown.
  function bubbleWord(ctx, x, y, w, h, col, word, fg, p = 1, side = 'r', px = 64) {
    G.bubble(ctx, x, y, w, h, col, side);
    G.typeText(ctx, word, x + w / 2, y + h / 2 + px * 0.36, F.he(800, px), fg, p, { align: 'center' });
  }

  // ═════════════════════════ FILM A — customers ═════════════════════════
  const A = {};
  // Shot events are in shot time (lt); the transition into a shot is added at its t0 automatically.
  const HA = (left, cur) => ({ right: 'פרולי · ללקוחות', left, n: 5, cur });
  A.FULL = 45;
  A.SHOTS = [
    { t0: 0, kind: 'hook', P: 'night', hud: HA('00 / 05', 0),
      T: { title: 'נזילה?', sub: 'מי יתקן את זה?', d0: 0.4, px: 230 },
      ev: [[0.5, 'drip'], [1.5, 'drip'], [2.5, 'drip'], [3.5, 'drip'], [0.5, 'title']] },
    { t0: 4, kind: 'brand', P: 'sun', T: { title: 'פרולי', sub: 'בעל מקצוע, דרך וואטסאפ', px: 300, y: 1470, d0: 0.1 },
      ev: [[0.5, 'rowjump'], [1.5, 'rowjump'], [2.5, 'rowjump'], [3.5, 'rowjump'], [0.1, 'brand']] },
    { t0: 8.5, kind: 'write', P: 'water', hud: HA('01 / 05', 1), T: { meta: '01 / 05', title: 'כותבים', sub: 'טקסט, תמונה, קול או וידאו', d0: 0.1 },
      ev: [[0.35, 'morph'], [1.0, 'popnote', 0], [1.5, 'popnote', 1], [2.0, 'popnote', 2], [2.5, 'popnote', 3]] },
    { t0: 13, kind: 'consent', P: 'water', hud: HA('02 / 05', 2), T: { meta: '02 / 05', title: 'מאשרים', sub: 'פעם אחת, בפנייה הראשונה', d0: 0.1 },
      ev: [[0.5, 'bubble'], [1.5, 'tick']] },
    { t0: 17.5, kind: 'find', P: 'mint', hud: HA('03 / 05', 3), T: { meta: '03 / 05', title: 'מאתרים', sub: 'בעל מקצוע פנוי, קרוב אליך', d0: 0.1 },
      ev: [[0.5, 'slam'], [1.0, 'ping'], [1.5, 'ping'], [2.0, 'ping'], [2.5, 'ping'], [3.0, 'found'], [4.0, 'line']] },
    { t0: 23.5, kind: 'approve', P: 'lilac', hud: HA('04 / 05', 4), T: { meta: '04 / 05', title: 'הוא מאשר', sub: 'המספר שלך נחשף רק אחרי שאישר', spx: 52, d0: 0.0 },
      ev: [[0.25, 'bubble'], [1.0, 'unlock'], [1.5, 'reveal']] },
    { t0: 28, kind: 'status', P: 'sun', hud: HA('05 / 05', 5), T: { meta: '05 / 05', title: 'תמיד בתמונה', sub: 'כתבו "סטטוס" או "נציג"', d0: 0.1 },
      ev: [[0.5, 'key'], [0.625, 'key'], [0.75, 'key'], [0.875, 'key'], [1.0, 'key'], [1.5, 'bubble'], [2.0, 'key'], [2.125, 'key'], [2.25, 'key'], [2.375, 'key']] },
    { t0: 32.5, kind: 'fixed', P: 'mint', hud: HA('05 / 05', 6), T: { title: 'תוקן.', sub: 'ופרולי מוודאת שהכול בסדר', px: 230, d0: 0.1 },
      ev: [[0.5, 'turn'], [1.0, 'tickbig']] },
    { t0: 37, kind: 'end', P: 'night',
      T: { title: 'פרולי', sub: 'בלי אפליקציה · ללא עלות ללקוחות', spx: 52, chip: 'proli.me', chipMono: true, px: 300, y: 1330, d0: 2.6, titleCol: G.SUN },
      ev: [...Array.from({ length: 12 }, (_, i) => [0.25 + i * 0.125, 'tile', i]), [2.0, 'collapse'], [2.5, 'end']] },
  ];
  A.TRANS = { 4: 'rows', 8.5: 'quarter', 13: 'slide', 17.5: 'cols', 23.5: 'rows', 28: 'flip', 32.5: 'quarter', 37: 'rows' };
  A.WORDS = [[5, 'אשר', 0.6], [6, 'סטטוס', 1.05], [6, 'נציג', 2.45]];

  // drops in the hook: splash on the beat
  const DRIPS = [0.5, 1.5, 2.5, 3.5], FALL = 0.3;
  const TAP = { x: SX - 70, y: SY - 140, s: 230 }, FLOOR = SY + 250;
  function dripScene(ctx, lt, P, dry) {
    const fg = G.CREAM;
    // the floor line and the puddle
    G.seg(ctx, [SX - 300, FLOOR + 22], [SX + 300, FLOOR + 22], 6, rgba(fg, 0.35));
    const n = dry ? 0 : DRIPS.filter((s) => lt >= s).length;
    const pw = dry ? 150 * (1 - E.io3(inv(0.5, 1.5, lt))) : 40 + n * 38 * E.oExp(inv(0, 0.3, lt - (DRIPS[n - 1] || 0)));
    if (pw > 2) { ctx.fillStyle = PAL.water.t[3]; ctx.beginPath(); ctx.ellipse(TAP.x, FLOOR + 10, pw, pw * 0.13, 0, 0, Math.PI * 2); ctx.fill(); }
    G.tap(ctx, TAP.x, TAP.y, TAP.s, fg, P.t[4], dry ? 1 - E.io3(inv(0.5, 1.0, lt)) : 1);
    if (dry) return;
    for (const st of DRIPS) {
      const rel = st - FALL, grow = inv(rel - 0.6, rel, lt), fall = inv(rel, st, lt);
      if (lt < rel - 0.6 || lt > st + 0.6) continue;
      if (lt < st) {
        const y = lerp(TAP.y + 22, FLOOR - 4, fall * fall);
        G.drop(ctx, TAP.x - 10, y, 22 * (lt < rel ? E.o3(grow) : 1), PAL.water.t[4]);
      } else {
        const p = inv(st, st + 0.45, lt), a = 1 - p;
        for (let k = 0; k < 5; k++) {
          const ang = Math.PI * (0.15 + 0.7 * (k / 4)), d = 90 * E.o3(p);
          G.disc(ctx, TAP.x - 10 - Math.cos(ang) * d, FLOOR - Math.sin(ang) * d * 0.9 + 80 * p * p, 9 * a, rgba(PAL.water.t[4], a));
        }
        ctx.strokeStyle = rgba(PAL.water.t[4], a); ctx.lineWidth = 6; ctx.beginPath(); ctx.ellipse(TAP.x - 10, FLOOR + 10, 30 + 120 * p, (30 + 120 * p) * 0.13, 0, 0, Math.PI * 2); ctx.stroke();
      }
    }
  }
  A.draw = {
    hook(ctx, lt, P) {
      bg(ctx, P, 3, lt, { spread: 0.45 });
      stageDisc(ctx, P, lt);
      dripScene(ctx, lt, P, false);
    },
    brand(ctx, lt, P) {
      ctx.fillStyle = P.t[2]; ctx.fillRect(0, 0, W, H);
      G.drawPattern(ctx, G.pattern('sun', 11, { spread: 1, density: 0.55 }), jumpRows(lt, [0.5, 1.5, 2.5], 4));
      // the text band: solid, on the grid (rows 7–10)
      const by = CELL * 6.5, p = E.ioExp(inv(0, 0.4, lt));
      ctx.fillStyle = P.t[2]; ctx.fillRect(W * (1 - p), by, W * p, H - by);
      G.hair(ctx, TX, by + 20, MAXW, rgba(G.INK, 0.4), inv(0.2, 0.8, lt), 3);
    },
    write(ctx, lt, P) {
      bg(ctx, P, 21, lt);
      stageDisc(ctx, P, lt);
      assemble(ctx, lt, (c) => phoneOn(c, P));
      const [sx, sy, sw] = [SX - 134, SY - 230, 268];
      // the drop falls into the screen and becomes a message
      const fall = inv(0, 0.35, lt), morph = inv(0.35, 0.7, lt);
      if (lt < 0.55) G.drop(ctx, SX + 30, lerp(SY - 640, sy + 80, fall * fall), 26 * (1 - E.i3(morph)), PAL.water.t[4]);
      const bs = E.oBack(morph, 2);
      if (bs > 0) {
        ctx.save(); ctx.translate(sx + sw - 20, sy + 150); ctx.scale(bs, bs); ctx.translate(-(sx + sw - 20), -(sy + 150));
        G.bubble(ctx, sx + 30, sy + 40, sw - 50, 110, PAL.water.t[4], 'r'); bars(ctx, sx + 60, sy + 78, sw - 120, 2, G.INK, 14);
        ctx.restore();
      }
      // text, photo, voice, video: four chips pop round the rim on the beats
      const ic = [[SX + 300, SY - 220, 'text'], [SX - 300, SY - 220, 'cam'], [SX + 300, SY + 230, 'mic'], [SX - 300, SY + 230, 'play']];
      ic.forEach(([x, y, k], i) => {
        const s = pop(lt, 1.0 + i * 0.5);
        if (s <= 0) return;
        ctx.save(); ctx.translate(x, y); ctx.scale(s, s);
        G.disc(ctx, 0, 0, 82, G.SUN);
        if (k === 'text') G.icoText(ctx, 0, 0, 44, G.INK); else if (k === 'cam') G.icoCamera(ctx, 0, 0, 44, G.INK, G.SUN);
        else if (k === 'mic') G.icoMic(ctx, 0, 0, 52, G.INK); else G.icoPlay(ctx, 0, 0, 56, G.INK, G.SUN);
        ctx.restore();
      });
    },
    consent(ctx, lt, P) {
      bg(ctx, P, 33, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx, P);
      const [sx, sy, sw] = [SX - 134, SY - 230, 268];
      G.bubble(ctx, sx + 30, sy + 40, sw - 50, 110, PAL.water.t[4], 'r'); bars(ctx, sx + 60, sy + 78, sw - 120, 2, G.INK, 14);
      // Proli asks once (yellow, from the left), the reply is a tick
      const b1 = pop(lt, 0.5);
      if (b1 > 0) { ctx.save(); ctx.translate(sx + 20, sy + 260); ctx.scale(b1, b1); ctx.translate(-(sx + 20), -(sy + 260));
        G.bubble(ctx, sx + 20, sy + 190, sw - 60, 120, G.SUN, 'l'); bars(ctx, sx + 46, sy + 232, sw - 120, 2, G.INK, 14);
        G.padlock(ctx, sx + sw - 70, sy + 252, 18, 0, G.INK, G.SUN); ctx.restore(); }
      const b2 = pop(lt, 1.5);
      if (b2 > 0) { ctx.save(); ctx.translate(sx + sw - 30, sy + 430); ctx.scale(b2, b2); ctx.translate(-(sx + sw - 30), -(sy + 430));
        G.bubble(ctx, sx + 110, sy + 350, sw - 140, 110, PAL.mint.t[3], 'r'); G.tick(ctx, sx + 110 + (sw - 140) / 2, sy + 405, 54, 14, G.CREAM, inv(1.55, 1.85, lt)); ctx.restore(); }
    },
    find(ctx, lt, P) {
      bg(ctx, P, 41, lt);
      const k = lerp(1, 1.1, E.io(inv(0, 6, lt)));    // a slow push into the map
      ctx.save(); ctx.translate(SX, SY); ctx.scale(k, k); ctx.translate(-SX, -SY);
      const col = stageDisc(ctx, P, lt, { col: P.t[3] });
      ctx.save(); ctx.beginPath(); ctx.arc(SX, SY, SR, 0, Math.PI * 2); ctx.clip();
      // streets: a grid, two diagonals
      ctx.globalAlpha = E.oExp(inv(0, 0.4, lt));
      for (let i = -3; i <= 3; i++) { G.seg(ctx, [SX + i * 120, SY - SR], [SX + i * 120, SY + SR], 14, P.t[4]); G.seg(ctx, [SX - SR, SY + i * 120], [SX + SR, SY + i * 120], 14, P.t[4]); }
      G.seg(ctx, [SX - SR, SY + SR * 0.7], [SX + SR, SY - SR * 0.5], 30, P.t[4]);
      ctx.globalAlpha = 1;
      // rings from the pin, on the beats
      for (const b of [1.0, 1.5, 2.0, 2.5]) { const p = inv(b, b + 1.1, lt); if (p > 0 && p < 1) G.ring(ctx, SX, SY + 30, 40 + 360 * E.o3(p), 10 * (1 - p), rgba(G.CREAM, 1 - p)); }
      // pros as small toolbox discs; the nearest one lights
      const pros = [[SX - 250, SY - 150], [SX + 210, SY - 230], [SX + 90, SY + 160], [SX - 170, SY + 250], [SX + 290, SY + 60], [SX - 60, SY - 290]];
      const near = 2;
      pros.forEach(([x, y], i) => {
        const s = pop(lt, 0.7 + i * 0.125), lit = i === near ? E.oExp(inv(3.0, 3.25, lt)) : 0, dim = i === near ? 0 : 0.55 * inv(4.0, 4.4, lt);
        if (s <= 0) return;
        ctx.save(); ctx.globalAlpha = 1 - dim; ctx.translate(x, y); ctx.scale(s * (1 + 0.25 * lit), s * (1 + 0.25 * lit));
        G.disc(ctx, 0, 0, 44, mix(G.CREAM, G.SUN, lit)); G.toolbox(ctx, 0, 8, 22, P.t[0], mix(G.CREAM, G.SUN, lit)); ctx.restore();
      });
      // the route from the pro to the pin
      const lp = E.io3(inv(4.0, 4.6, lt));
      if (lp > 0) { ctx.setLineDash([4, 26]); G.pline(ctx, [[pros[near][0], pros[near][1]], [lerp(pros[near][0], SX, lp), lerp(pros[near][1], SY + 30, lp)]], 14, G.SUN); ctx.setLineDash([]); }
      ctx.restore();
      // the pin slams in on 16.5
      const d = inv(0.2, 0.5, lt);
      if (d > 0) { const y = lerp(SY - 700, SY + 30, E.i3(d)), sq = lt > 0.5 ? 1 - 0.18 * Math.exp(-(lt - 0.5) * 10) * Math.cos((lt - 0.5) * 30) : 1;
        ctx.save(); ctx.translate(SX, y); ctx.scale(1 / sq, sq); G.pin(ctx, 0, 0, 52, G.CREAM, col); ctx.restore(); }
      ctx.restore();
    },
    approve(ctx, lt, P) {
      bg(ctx, P, 52, lt);
      stageDisc(ctx, P, lt);
      // the pro's reply comes in, then the lock opens on 23.0 and the number is revealed
      const b = pop(lt, 0.25);
      if (b > 0) { ctx.save(); ctx.translate(SX + 150, SY - 230); ctx.scale(b, b); ctx.translate(-(SX + 150), -(SY - 230));
        bubbleWord(ctx, SX - 10, SY - 330, 300, 130, G.CREAM, 'אשר', G.INK, 1, 'r', 76);
        G.wrench(ctx, SX - 110, SY - 265, 70, -0.6, G.SUN, P.t[3]); ctx.restore(); }
      const open = inv(1.0, 1.35, lt), shake = lt > 1.0 ? Math.sin((lt - 1.0) * 40) * 6 * Math.exp(-(lt - 1.0) * 8) : 0;
      G.padlock(ctx, SX + shake, SY + 120, 120, open, G.CREAM, P.t[3]);
      const r = pop(lt, 1.5);
      if (r > 0) { ctx.save(); ctx.translate(SX + 250, SY + 130); ctx.scale(r, r);
        G.disc(ctx, 0, 0, 74, G.SUN); G.phone(ctx, 0, 0, 40, G.INK, G.SUN); ctx.restore(); }
    },
    status(ctx, lt, P) {
      bg(ctx, P, 61, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx, P);
      const [sx, sy, sw] = [SX - 134, SY - 230, 268];
      if (lt >= 0.4) bubbleWord(ctx, sx + 40, sy + 40, sw - 60, 100, PAL.water.t[3], 'סטטוס', G.CREAM, inv(0.5, 1.0, lt), 'r', 52);
      const b = pop(lt, 1.5);
      if (b > 0) { ctx.save(); ctx.translate(sx + 20, sy + 270); ctx.scale(b, b); ctx.translate(-(sx + 20), -(sy + 270));
        G.bubble(ctx, sx + 20, sy + 180, sw - 50, 120, G.SUN, 'l'); bars(ctx, sx + 46, sy + 222, sw - 110, 2, G.INK, 14); ctx.restore(); }
      if (lt >= 1.9) bubbleWord(ctx, sx + 100, sy + 350, sw - 120, 100, PAL.water.t[3], 'נציג', G.CREAM, inv(2.0, 2.4, lt), 'r', 52);
    },
    fixed(ctx, lt, P) {
      bg(ctx, P, 71, lt);
      stageDisc(ctx, P, lt);
      dripScene(ctx, lt, P, true);
      const s = pop(lt, 1.0, 0.3);
      if (s > 0) { ctx.save(); ctx.translate(SX + 220, SY - 200); ctx.scale(s, s); G.disc(ctx, 0, 0, 110, G.SUN); G.tick(ctx, 0, 6, 110, 26, G.INK, inv(1.05, 1.3, lt)); ctx.restore(); }
    },
    end(ctx, lt, P) { endScene(ctx, lt, P, 'customer'); },
  };

  // ═════════════════════════ FILM B — professionals ═════════════════════════
  const B = {};
  const HB = (left, cur) => ({ right: 'פרולי · לבעלי מקצוע', left, n: 6, cur });
  const CMDS = [
    { w: 'אשר', P: 'mint', ico: 'tick' }, { w: 'דחה', P: 'coral', ico: 'cross' }, { w: 'חפש', P: 'water', ico: 'mag' },
    { w: 'הפסקה', P: 'lilac', ico: 'cup' }, { w: 'זמין', P: 'sun', ico: 'toggle' }, { w: 'תפריט', P: 'mint', ico: 'menu' },
    { w: 'עבודות', P: 'water', ico: 'cal' },
  ];
  const CMD_DT = 0.875;   // one command every 7 sixteenths: a run that outpaces the bar
  B.FULL = 47;
  B.SHOTS = [
    { t0: 0, kind: 'dawn', P: 'night', hud: HB('08:00', 0), T: { title: 'בוקר טוב', sub: 'יום חדש מתחיל', d0: 0.6 },
      ev: [[0.0, 'rise'], [1.0, 'rays'], [0.6, 'title']] },
    { t0: 4.5, kind: 'summary', P: 'sun', hud: HB('08:00', 1), T: { meta: '01 / 06', title: 'סיכום בוקר', sub: 'העבודות של היום, כל בוקר', d0: 0.1 },
      ev: [[1.0, 'tick'], [1.5, 'tick'], [2.0, 'tick']] },
    { t0: 9, kind: 'lead', P: 'coral', hud: HB('09:12', 2), T: { meta: '02 / 06', title: 'יש עבודה', sub: 'תיאור התקלה והכתובת', d0: 0.1 },
      ev: [[-0.25, 'bell'], [0.5, 'bell'], [1.0, 'bell'], [1.5, 'bell'], [2.0, 'bubble']] },
    { t0: 13.5, kind: 'filter', P: 'water', hud: HB('09:12', 3), T: { meta: '03 / 06', title: 'רק מה שמתאים', sub: 'לפי תחום, אזור ועומס', d0: 0.1 },
      ev: [...Array.from({ length: 12 }, (_, i) => [0.25 + i * 0.0625 + 0.4, 'dot', i]), [0.5, 'chipon'], [0.75, 'chipon'], [1.0, 'chipon'], [2.5, 'through'], [3.0, 'tick']] },
    { t0: 18, kind: 'take', P: 'mint', hud: HB('09:13', 4), T: { meta: '04 / 06', title: 'אשר.', sub: 'מילה אחת, והעבודה שלך', px: 230, d0: 0.1 },
      ev: [[0.5, 'key'], [0.67, 'key'], [0.83, 'key'], [2.0, 'tickbig']] },
    { t0: 22.5, kind: 'commands', P: 'night', hud: HB('12:30', 5),
      ev: [[0.0, 'brand'], ...Array.from({ length: 7 }, (_, i) => [i * CMD_DT, 'cmd', i])] },
    { t0: 30.5, kind: 'break', P: 'lilac', hud: HB('13:00', 5), T: { title: 'צריך הפסקה?', sub: 'מתי שתרצו, בהודעה אחת', d0: 0.1 },
      ev: [[0.5, 'clink'], [1.0, 'switch'], [2.5, 'switch']] },
    { t0: 35, kind: 'dusk', P: 'coral', hud: HB('19:00', 6), T: { meta: '06 / 06', title: 'סוף יום', sub: 'כל העבודות, לפי מועד', d0: 0.1 },
      ev: [[0.5, 'set'], [2.0, 'tick'], [2.25, 'tick'], [2.5, 'tick']] },
    { t0: 39.5, kind: 'end', P: 'night',
      T: { title: 'פרולי', sub: 'לבעלי מקצוע · פיילוט ללא עלות', spx: 52, chip: 'info@proli.me', chipMono: true, px: 300, y: 1330, d0: 1.0, titleCol: G.SUN },
      ev: [...Array.from({ length: 12 }, (_, i) => [i * 0.0625, 'tile', i]), [0.75, 'collapse'], [1.0, 'end']] },
  ];
  B.TRANS = { 4.5: 'rows', 9: 'cols', 13.5: 'quarter', 18: 'slide', 22.5: 'rows', 30.5: 'flip', 35: 'quarter', 39.5: 'rows' };
  B.WORDS = [[4, 'אשר', 1.0], [6, 'זמין · הפסקה', 0.3], [3, 'תחום · אזור · עומס', 1.3]];
  B.draw = {
    dawn(ctx, lt, P) {
      // the frame warms from night to sunrise as the sun comes up
      bg(ctx, P, 5, lt, { spread: 0.4 });
      ctx.fillStyle = rgba(PAL.coral.t[1], 0.35 * E.io(inv(0.0, 2.0, lt))); ctx.fillRect(0, 0, W, H);
      const col = stageDisc(ctx, P, lt, { col: mix(P.t[3], PAL.coral.t[3], E.io(inv(0, 2, lt))) });
      ctx.save(); ctx.beginPath(); ctx.arc(SX, SY, SR, 0, Math.PI * 2); ctx.clip();
      const rise = E.oBack(inv(0.0, 1.0, lt), 1.4), y = lerp(SY + 380, SY - 20, rise);
      G.sunIcon(ctx, SX, y, 130, G.SUN, lt * 0.15, E.oExp(inv(1.0, 1.4, lt)));
      G.rrect(ctx, SX - SR, SY + 140, SR * 2, SR, 0, P.t[1]);   // the horizon
      for (let i = 0; i < 4; i++) G.seg(ctx, [SX - 240 + i * 40, SY + 200 + i * 44], [SX + 240 - i * 40, SY + 200 + i * 44], 12, rgba(G.CREAM, 0.12));
      ctx.restore();
      void col;
    },
    summary(ctx, lt, P) {
      bg(ctx, P, 15, lt, { set: 'day' });
      stageDisc(ctx, P, lt);
      assemble(ctx, lt, (c) => phoneOn(c, P));
      const [sx, sy, sw] = [SX - 134, SY - 230, 268];
      G.bubble(ctx, sx + 16, sy + 30, sw - 32, 380, PAL.sun.t[4], 'l');
      G.sunIcon(ctx, sx + sw - 60, sy + 80, 18, G.SUN0);
      for (let i = 0; i < 3; i++) {
        const y = sy + 150 + i * 88;
        G.rrect(ctx, sx + sw - 120, y - 22, 80, 44, 10, G.INK);                  // the time slot
        G.seg(ctx, [sx + sw - 150, y], [sx + 90, y], 14, rgba(G.INK, 0.55));     // the job
        const p = pop(lt, 1.0 + i * 0.5, 0.25);
        if (p > 0) { G.disc(ctx, sx + 56, y, 26 * p, PAL.mint.t[2]); G.tick(ctx, sx + 56, y + 2, 24, 7, G.CREAM, inv(1.0 + i * 0.5, 1.2 + i * 0.5, lt)); }
      }
    },
    lead(ctx, lt, P) {
      bg(ctx, P, 25, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx, P);
      const [sx, sy, sw] = [SX - 134, SY - 230, 268];
      // the bell rings above the phone on the beats
      const sw0 = [0.0, 0.5, 1.0, 1.5].reduce((a, b) => a + (lt > b ? Math.sin((lt - b) * 26) * 0.4 * Math.exp(-(lt - b) * 6) : 0), 0);
      G.bell(ctx, SX + 200, SY - 360, 130, G.SUN, sw0);
      for (const b of [0.0, 0.5, 1.0, 1.5]) { const p = inv(b, b + 0.45, lt); if (p > 0 && p < 1) { G.ring(ctx, SX + 200, SY - 280, 90 + 70 * p, 8 * (1 - p), rgba(G.SUN, 1 - p), -Math.PI * 0.9, -Math.PI * 0.6); G.ring(ctx, SX + 200, SY - 280, 90 + 70 * p, 8 * (1 - p), rgba(G.SUN, 1 - p), -Math.PI * 0.4, -Math.PI * 0.1); } }
      const b = pop(lt, 2.0);
      if (b > 0) { ctx.save(); ctx.translate(sx + 20, sy + 200); ctx.scale(b, b); ctx.translate(-(sx + 20), -(sy + 200));
        G.bubble(ctx, sx + 16, sy + 60, sw - 32, 300, G.CREAM, 'l');
        G.rrect(ctx, sx + 16, sy + 60, sw - 32, 64, [30, 30, 0, 0], P.t[2]);
        G.drop(ctx, sx + sw - 60, sy + 200, 20, PAL.water.t[2]); G.seg(ctx, [sx + sw - 100, sy + 185], [sx + 50, sy + 185], 14, rgba(G.INK, 0.55));
        G.pin(ctx, sx + sw - 60, sy + 300, 16, P.t[2], G.CREAM); G.seg(ctx, [sx + sw - 100, sy + 270], [sx + 80, sy + 270], 14, rgba(G.INK, 0.55));
        ctx.restore(); }
    },
    filter(ctx, lt, P) {
      bg(ctx, P, 35, lt);
      const col = stageDisc(ctx, P, lt);
      G.funnel(ctx, SX, SY - 170, 260, G.CREAM);
      // twelve leads fall in; one comes out
      for (let i = 0; i < 12; i++) {
        const t0 = 0.25 + i * 0.0625, p = inv(t0, t0 + 0.4, lt);
        if (p <= 0 || p >= 1) continue;
        const x0 = SX - 220 + (i % 6) * 88 + (i > 5 ? 44 : 0), y = lerp(SY - 640, SY - 190, p * p);
        G.disc(ctx, x0, y, 22, i === 7 ? G.SUN : P.t[4]);
      }
      const chips = ['תחום', 'אזור', 'עומס'];
      chips.forEach((c, i) => chip(ctx, SX + 330, SY - 120 + i * 90, c, G.INK, G.SUN, E.oExp(inv(0.5 + i * 0.25, 0.75 + i * 0.25, lt)), { px: 36 }));
      const out = inv(2.5, 3.0, lt);
      if (out > 0) {
        const y = lerp(SY + 90, SY + 240, E.o3(out)), s = 1 + 0.6 * E.oBack(inv(2.9, 3.2, lt));
        G.disc(ctx, SX, y, 24 * s, G.SUN); if (lt > 3.0) G.tick(ctx, SX, y + 2, 22 * s, 6 * s, G.INK, inv(3.0, 3.2, lt));
      }
      void col;
    },
    take(ctx, lt, P) {
      bg(ctx, P, 45, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx, P);
      const [sx, sy, sw] = [SX - 134, SY - 230, 268];
      G.bubble(ctx, sx + 16, sy + 40, sw - 60, 150, G.CREAM, 'l'); bars(ctx, sx + 46, sy + 80, sw - 130, 3, rgba(G.INK, 0.5), 13);
      if (lt >= 0.4) bubbleWord(ctx, sx + 90, sy + 250, sw - 110, 110, PAL.mint.t[3], 'אשר', G.CREAM, inv(0.5, 0.95, lt), 'r', 62);
      const s = pop(lt, 2.0, 0.3);
      if (s > 0) { ctx.save(); ctx.translate(SX - 230, SY + 210); ctx.scale(s, s); G.disc(ctx, 0, 0, 120, G.SUN); G.tick(ctx, 0, 6, 120, 28, G.INK, inv(2.05, 2.3, lt)); ctx.restore();
        const p = inv(2.0, 2.6, lt); if (p < 1) G.ring(ctx, SX - 230, SY + 210, 120 + 200 * E.o3(p), 10 * (1 - p), rgba(G.SUN, 1 - p)); }
    },
    commands(ctx, lt, P) {
      // pace ladder: one command per half bar, each a band sliding in from alternate sides; they stay
      ctx.fillStyle = P.t[2]; ctx.fillRect(0, 0, W, H);
      G.drawPattern(ctx, G.pattern('night', 9, { spread: 0.3, density: 0.4, set: 'day' }), rowDrift(lt, 9, 60));
      G.maskText(ctx, 'פקודות טקסט', TX, 250, F.he(800, 64), G.SUN, inv(0.0, 0.5, lt));
      const rh = 190, y0 = 320;
      CMDS.forEach((c, i) => {
        const t0 = i * CMD_DT, p = E.ioExp(inv(t0, t0 + 0.3, lt));
        if (p <= 0) return;
        const C = PAL[c.P], y = y0 + i * (rh + 14), dir = i % 2 ? -1 : 1, x = dir * W * (1 - p);
        ctx.save(); ctx.translate(x, 0);
        G.rrect(ctx, 64, y, W - 128, rh, 26, C.t[2]);
        G.maskText(ctx, c.w, W - 120, y + rh * 0.7, F.he(900, 120), C.fg, inv(t0 + 0.15, t0 + 0.5, lt));
        const ix = 180, iy = y + rh / 2, ic = C.fg, ib = C.t[2];
        G.disc(ctx, ix, iy, 66, C.t[3]);
        if (c.ico === 'tick') G.tick(ctx, ix, iy + 4, 64, 16, ic);
        else if (c.ico === 'cross') G.cross(ctx, ix, iy, 56, 16, ic);
        else if (c.ico === 'mag') G.magnifier(ctx, ix - 10, iy - 10, 28, ic);
        else if (c.ico === 'cup') G.cup(ctx, ix - 4, iy + 10, 34, ic, 0.8, lt);
        else if (c.ico === 'toggle') G.toggle(ctx, ix, iy, 40, E.io3(inv(t0 + 0.4, t0 + 0.7, lt)), C.t[0], G.CREAM, PAL.mint.t[2]);
        else if (c.ico === 'menu') G.menu(ctx, ix, iy, 34, ic);
        else G.calendar(ctx, ix, iy + 6, 40, ic, ib, 5, G.SUN);
        ctx.restore();
      });
    },
    break(ctx, lt, P) {
      bg(ctx, P, 55, lt, { offs: rowDrift(lt, 55, 10) });   // the slow card: the rows barely move
      stageDisc(ctx, P, lt);
      const on = 1 - E.io3(inv(1.0, 1.3, lt)) + E.io3(inv(2.5, 2.8, lt));   // זמין → הפסקה → זמין
      G.cup(ctx, SX, SY - 90, 140, G.CREAM, 1 - on * 0.0, lt);
      G.toggle(ctx, SX, SY + 200, 100, on, P.t[0], G.CREAM, PAL.mint.t[2]);
      const word = (s, x, a) => { ctx.save(); ctx.globalAlpha = a; ctx.font = F.he(800, 50); ctx.direction = 'rtl'; ctx.textAlign = 'center'; ctx.fillStyle = G.CREAM; ctx.fillText(s, x, SY + 340); ctx.restore(); };
      word('זמין', SX + 200, 0.4 + 0.6 * on); word('הפסקה', SX - 200, 0.4 + 0.6 * (1 - on));
    },
    dusk(ctx, lt, P) {
      bg(ctx, P, 65, lt);
      ctx.fillStyle = rgba(PAL.lilac.t[0], 0.45 * E.io(inv(0.0, 3.0, lt))); ctx.fillRect(0, 0, W, H);
      stageDisc(ctx, P, lt, { col: mix(P.t[3], PAL.lilac.t[2], E.io(inv(0, 3, lt))) });
      ctx.save(); ctx.beginPath(); ctx.arc(SX, SY, SR, 0, Math.PI * 2); ctx.clip();
      const set = E.io3(inv(0.5, 2.5, lt));
      G.sunIcon(ctx, SX - 150, lerp(SY - 160, SY + 300, set), 100, G.SUN, 0, 1 - set);
      G.moon(ctx, SX + 180, lerp(SY + 400, SY - 210, E.o3(inv(2.0, 3.2, lt))), 70, G.CREAM);
      G.rrect(ctx, SX - SR, SY + 140, SR * 2, SR, 0, mix(P.t[1], PAL.lilac.t[0], set));
      ctx.restore();
      const s = pop(lt, 0.3);
      if (s > 0) { ctx.save(); ctx.translate(SX + 40, SY + 30); ctx.scale(s, s);
        const lit = [2.0, 2.25, 2.5].filter((b) => lt >= b).length;
        G.calendar(ctx, 0, 0, 120, G.CREAM, PAL.lilac.t[3], -1);
        for (let k = 0; k < lit; k++) { const cx = -120 * 0.66 + (k + 1) * 120 * 0.44, cy = -120 * 0.12 + 120 * 0.36; G.rrect(ctx, cx - 18, cy - 14, 36, 28, 6, G.SUN); }
        ctx.restore(); }
    },
    end(ctx, lt, P) { endScene(ctx, lt, P, 'pro'); },
  };

  // ───────── the end: icon wall → one disc → title ─────────
  function wallIcon(ctx, k, x, y, s, col, bg2) {
    switch (k % 12) {
      case 0: G.drop(ctx, x, y + s * 0.25, s * 0.32, col); break;
      case 1: G.phone(ctx, x, y, s * 0.55, col, bg2); break;
      case 2: G.bubble(ctx, x - s * 0.55, y - s * 0.35, s * 1.1, s * 0.6, col, 'r'); break;
      case 3: G.pin(ctx, x, y + s * 0.6, s * 0.3, col, bg2); break;
      case 4: G.wrench(ctx, x, y, s * 0.6, 0.6, col, bg2); break;
      case 5: G.padlock(ctx, x, y + s * 0.1, s * 0.4, 1, col, bg2); break;
      case 6: G.tick(ctx, x, y, s * 0.8, s * 0.2, col); break;
      case 7: G.toolbox(ctx, x, y + s * 0.1, s * 0.55, col, bg2); break;
      case 8: G.sunIcon(ctx, x, y, s * 0.3, col); break;
      case 9: G.bell(ctx, x, y - s * 0.5, s, col); break;
      case 10: G.calendar(ctx, x, y + s * 0.05, s * 0.5, col, bg2); break;
      default: G.cup(ctx, x, y + s * 0.15, s * 0.4, col, 0); break;
    }
  }
  function endScene(ctx, lt, P, which) {
    const tw = which === 'pro' ? 0 : 0.25;       // the customer film has a little longer for its wall
    bg(ctx, P, 7, lt, { spread: 0.42, offs: (j) => rowDrift(Math.min(lt, 2.5 + j * 0.2), 7)(j) });   // rows stop one by one
    const collapse = E.ioExp(inv(0.75 + tw * 5, 1.25 + tw * 5, lt));
    const cs = 300, x0 = W / 2 - cs * 1.5, y0 = 300;
    if (collapse < 1) for (let j = 0; j < 4; j++) for (let i = 0; i < 3; i++) {
      const k = j * 3 + i, t0 = (which === 'pro' ? 0 : 0.25) + k * (which === 'pro' ? 0.0625 : 0.125), s = pop(lt, t0, 0.25);
      if (s <= 0) continue;
      const yellow = inv(0.5 + tw * 4 + j * 0.06, 0.62 + tw * 4 + j * 0.06, lt);
      const cx = lerp(x0 + cs * (i + 0.5), SX, collapse), cy = lerp(y0 + cs * (j + 0.5), 760, collapse), sc = s * (1 - collapse * 0.7);
      ctx.save(); ctx.translate(cx, cy); ctx.scale(sc, sc);
      const discCol = mix(P.t[3], G.SUN, yellow);
      G.disc(ctx, 0, 0, cs * 0.44, discCol);
      wallIcon(ctx, which === 'pro' ? (k + 7) % 12 : k, 0, 0, cs * 0.34, mix(G.CREAM, G.INK, yellow), discCol);
      ctx.restore();
    }
    if (collapse > 0) {
      const r = 300 * E.oBack(inv(0.9 + tw * 5, 1.4 + tw * 5, lt), 1.6);
      G.disc(ctx, SX, 760, r, G.SUN);
      if (r > 40) {
        const k = r / 300;
        ctx.save(); ctx.translate(SX, 760); ctx.scale(k, k);
        if (which === 'pro') G.toolbox(ctx, 0, 30, 130, G.INK, G.SUN);
        else { G.bubble(ctx, -150, -110, 300, 190, G.INK, 'r'); G.tick(ctx, 0, -18, 110, 24, G.SUN); }
        ctx.restore();
      }
    }
    // fine print: not meant to be read in full at a glance (the site carries it)
    const fp = which === 'pro'
      ? ['פרולי מספקת שירות טכנולוגי לתיווך בלבד.', 'בתקופת הפיילוט שתיקבע מולכם, השימוש ללא עלות.']
      : ['השירות פועל במתכונת פיילוט. פרולי אינה שירות חירום:', 'בסכנה מיידית חייגו 102 · 100 · 101'];
    const fa = E.oExp(inv(2.8 + tw * 4, 3.3 + tw * 4, lt));
    if (fa > 0) { ctx.save(); ctx.globalAlpha = fa; ctx.font = F.he(500, 30); ctx.direction = 'rtl'; ctx.textAlign = 'right'; ctx.fillStyle = rgba(G.CREAM, 0.72);
      fp.forEach((l, i) => ctx.fillText(l, TX, 1720 + i * 46)); ctx.restore(); }
  }

  // ───────── the film in use ─────────
  const FILMDEF = FILM === 'pro' ? B : A;
  const SHOTS = FILMDEF.SHOTS, TRANS = FILMDEF.TRANS, DRAW = FILMDEF.draw, DURF = FILMDEF.FULL;
  SHOTS.forEach((s, i) => { s.t1 = i + 1 < SHOTS.length ? SHOTS[i + 1].t0 : DURF; s.pal = PAL[s.P]; });
  const EV = [];
  SHOTS.forEach((s, i) => {
    if (TRANS[s.t0]) EV.push({ t: s.t0, type: 'trans', kind: TRANS[s.t0], shot: i, P: s.P });
    (s.ev || []).forEach(([lt, type, k]) => EV.push({ t: +(s.t0 + lt).toFixed(4), type, i: k, shot: i, P: s.P }));
  });

  const TA = 0.12, TB = 0.26;
  const bufA = G.canvas(), bufB = G.canvas(), bufSub = G.canvas();
  function shotIdx(t) { for (let i = SHOTS.length - 1; i >= 0; i--) if (t >= SHOTS[i].t0) return i; return 0; }
  function drawShot(ctx, s, t) {
    ctx.save();
    const lt = t - s.t0;
    DRAW[s.kind](ctx, lt, s.pal);
    textBlock(ctx, lt, s.T, s.pal.fg);
    hud(ctx, t, s.pal, s.hud);
    ctx.restore();
  }
  function composite(ctx, type, p, Aa, Bb) {
    const S = CELL, rows = Math.ceil(H / S), cols = Math.ceil(W / S);
    if (type === 'rows') {
      ctx.drawImage(Aa, 0, 0);
      for (let j = 0; j < rows; j++) {
        const pj = E.ioExp(clamp(p * 1.6 - j * 0.06)), dir = j % 2 ? 1 : -1;
        ctx.drawImage(Aa, 0, j * S, W, S, dir * W * 0.25 * pj, j * S, W, S);
        ctx.drawImage(Bb, 0, j * S, W, S, -dir * W * (1 - pj), j * S, W, S);
      }
    } else if (type === 'cols') {
      ctx.drawImage(Aa, 0, 0);
      for (let i = 0; i < cols; i++) {
        const pj = E.ioExp(clamp(p * 1.5 - (cols - 1 - i) * 0.08)), dir = i % 2 ? 1 : -1;
        ctx.drawImage(Bb, i * S, 0, S, H, i * S, -dir * H * (1 - pj), S, H);
      }
    } else if (type === 'slide') {
      const pj = E.ioExp(p);   // right to left, the way Hebrew reads
      ctx.drawImage(Aa, W * 0.3 * pj, 0); ctx.drawImage(Bb, -W * (1 - pj), 0);
      ctx.fillStyle = `rgba(0,0,0,${0.15 * (1 - Math.abs(pj - 0.5) * 2)})`; ctx.fillRect(-W * (1 - pj) + W, 0, 8, H);
    } else if (type === 'quarter') {
      ctx.drawImage(Aa, 0, 0);
      ctx.save(); ctx.beginPath();
      for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
        const d = ((cols - 1 - i) + j) * 0.03, r = S * 1.42 * E.o3(clamp((p - d) / 0.5));
        if (r <= 0) continue;
        const corner = (i + j) % 4, cx = i * S + (corner === 1 || corner === 2 ? S : 0), cy = j * S + (corner >= 2 ? S : 0);
        ctx.moveTo(cx, cy); ctx.arc(cx, cy, r, 0, Math.PI * 2);
      }
      ctx.clip(); ctx.drawImage(Bb, 0, 0); ctx.restore();
    } else if (type === 'flip') {
      ctx.drawImage(Bb, 0, 0); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fillRect(0, 0, W, H);
      for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
        const d = (((cols - 1 - i) * 0.6 + j) / (cols * 0.6 + rows)) * 0.5, pc = clamp((p - d) / 0.5);
        const src = pc < 0.5 ? Aa : Bb, sx = Math.abs(Math.cos(pc * Math.PI)), x = i * S, y = j * S, w = S * sx;
        ctx.drawImage(src, x, y, S, S, x + (S - w) / 2, y, Math.max(1, w), S);
        if (pc > 0 && pc < 1) { ctx.fillStyle = `rgba(0,0,0,${0.3 * (1 - sx)})`; ctx.fillRect(x + (S - w) / 2, y, w, S); }
      }
    }
  }
  function frame(ctx, t) {
    const i = shotIdx(t), s = SHOTS[i], nx = SHOTS[i + 1];
    let tr = null;
    if (nx && TRANS[nx.t0] && t >= nx.t0 - TA) tr = [s, nx];
    else if (i > 0 && TRANS[s.t0] && t < s.t0 + TB) tr = [SHOTS[i - 1], s];
    if (tr) {
      const ca = bufA.getContext('2d'), cb = bufB.getContext('2d'); ca.setTransform(1, 0, 0, 1, 0, 0); cb.setTransform(1, 0, 0, 1, 0, 0);
      const [a, b] = tr, p = clamp((t - (b.t0 - TA)) / (TA + TB));
      drawShot(ca, a, Math.min(t, a.t1 - 0.001)); drawShot(cb, b, Math.max(t, b.t0));
      composite(ctx, TRANS[b.t0], p, bufA, bufB);
    } else drawShot(ctx, s, t);
  }
  const isFast = (t) => SHOTS.some((s) => TRANS[s.t0] && t >= s.t0 - TA && t < s.t0 + TB);

  // ───────── page contract ─────────
  window.DUR = DURF;
  window.EV = EV.map((e) => ({ ...e })).sort((a, b) => a.t - b.t);
  window.SHOTS = SHOTS.map((s) => ({ t0: s.t0, t1: s.t1, kind: s.kind, P: s.P }));
  window.FILM = FILM;
  const ctxMain = document.getElementById('c').getContext('2d');
  window.render = (t) => {
    const ctx = ctxMain;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (!isFast(t)) { frame(ctx, t); return; }
    const N = 5, shutter = 0.45 / 24, sc = bufSub.getContext('2d');
    for (let k = 0; k < N; k++) {
      sc.setTransform(1, 0, 0, 1, 0, 0);
      frame(sc, t - shutter / 2 + (shutter * k) / (N - 1));
      ctx.globalAlpha = 1 / (k + 1); ctx.drawImage(bufSub, 0, 0);
    }
    ctx.globalAlpha = 1;
  };
  // every text meant to be read, from the moment it is fully in until the shot hands over
  const WORDS = FILMDEF.WORDS.map(([si, w, lt]) => [si, w, SHOTS[si].t0 + lt, SHOTS[si].t1]);
  window.TEXTS = (t) => {
    const out = [];
    SHOTS.forEach((s, i) => {
      if (!s.T) return;
      const ready = s.t0 + textReady(s.T), end = s.t1 - TA;
      if (t < ready || t >= end) return;
      const [x0, y0, x1, y1] = textBox(s.T);
      out.push({ id: 's' + i, text: [].concat(s.T.title).join(' ') + (s.T.sub ? ' / ' + s.T.sub : '') + (s.T.chip ? ' / ' + s.T.chip : ''), x0, y0, x1, y1 });
    });
    WORDS.forEach(([, w, a, b], k) => { if (t >= a && t < b - TA) out.push({ id: 'w' + k, text: w, x0: 300, y0: 400, x1: 800, y1: 1100 }); });
    if (FILM === 'pro') {
      const c0 = SHOTS[5].t0, c1 = SHOTS[5].t1 - TA;
      CMDS.forEach((c, i) => { const a = c0 + i * CMD_DT + 0.5; if (t >= a && t < c1) out.push({ id: 'c' + i, text: c.w, x0: 64, y0: 320 + i * 204, x1: W - 64, y1: 320 + i * 204 + 190 }); });
      if (t >= c0 + 0.5 && t < c1) out.push({ id: 'ch', text: 'פקודות טקסט', x0: 300, y0: 180, x1: W - 64, y1: 270 });
    }
    const endT = SHOTS[SHOTS.length - 1].t0, fpT = endT + (FILM === 'pro' ? 3.3 : 4.3);
    if (t >= fpT) out.push({ id: 'fp', text: 'fine print', x0: 96, y0: 1680, x1: W - 64, y1: 1790, pass: true });
    return out;
  };
})();
