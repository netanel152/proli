// Proli explainers — one page, two films, two cuts each. 1080×1920, 24 fps.
//   ?film=customer | pro        which film
//   ?cut=full | short           the full film, or the 15–20 s cut (problem → how → CTA)
//   ?approve=button | typed     pro film, approve scene: a tap on the reply button (default) or the typed word
//   ?wa=<number>&qr=<image>     the WhatsApp number and QR for the end card; placeholders are drawn without them
// 120 BPM: beat 0.5 s, bar 2 s. Every shot starts on a beat. render(t) keeps no state between frames.
(function () {
  const { W, H, E, PAL, clamp, lerp, inv, rgba, mix, CELL } = G;
  const F = G.F;
  const Q = new URLSearchParams(location.search);
  const FILM = Q.get('film') === 'pro' ? 'pro' : 'customer';
  const CUT = Q.get('cut') === 'short' ? 'short' : 'full';
  const APPROVE = Q.get('approve') === 'typed' ? 'typed' : 'button';
  const WA_NUMBER = Q.get('wa') || '05X-XXX-XXXX';      // placeholder until the number exists (cutover)

  // ───────── layout (vertical) ─────────
  const SX = 540, SY = 760, SR = 390;      // stage disc
  const TX = W - 96;                       // text column: right edge (Hebrew reads from the right)
  const TY = 1400;                         // first title baseline
  const MAXW = W - 192;
  const pad2 = (n) => String(n).padStart(2, '0');

  // ───────── helpers ─────────
  const rowDrift = (t, seed, sp = 22, amp = 22) => (j) => (j % 2 ? 1 : -1) * t * sp + Math.sin(j * 1.7 + seed) * amp;
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
    const tw = ctx.measureText(text).width + px * 1.3, h = Math.round(px * 1.75);
    const x = xr - tw;
    ctx.fillStyle = bg2; ctx.beginPath(); ctx.rect(xr - tw * p, y, tw * p, h); ctx.fill();
    ctx.beginPath(); ctx.rect(xr - tw * p, y, tw * p, h); ctx.clip();
    ctx.fillStyle = fg; ctx.direction = o.mono ? 'ltr' : 'rtl'; ctx.textAlign = 'center';
    ctx.fillText(text, x + tw / 2, y + px * 1.22);
    ctx.restore(); return tw;
  }
  // Text block: optional meta (mono, ltr), title lines (Heebo 900, mask slide-up), second line (Heebo 700), chip.
  function titlePx(lines, px) {
    const c = bandBuf.getContext('2d');
    const wmax = Math.max(...lines.map((l) => G.measure(c, l, F.he(900, px), (-0.01 * px) + 'px')));
    return wmax > MAXW ? Math.floor(px * MAXW / wmax) : px;
  }
  // Where each piece of a text block sits; used by the drawing and by TEXTS, so they cannot drift apart.
  function textLayout(T) {
    const lines = [].concat(T.title), px = titlePx(lines, T.px || 200), lh = px * 0.98, y0 = T.y ?? TY;
    let yy = y0 + (lines.length - 1) * lh;
    const L = { lines, px, lh, y0, title: [96, y0 - px * 0.9, W - 60, yy + px * 0.12] };
    if (T.sub) { const sp = T.spx || 58; yy += sp + 34; L.subY = yy; L.sp = sp; L.sub = [96, yy - sp, W - 60, yy + sp * 0.3]; }
    if (T.chip) { const cp = T.chipPx || 34; L.chipY = yy + 34; L.cp = cp; L.chip = [96, yy + 34, W - 60, yy + 34 + cp * 1.75]; }
    return L;
  }
  function textBlock(ctx, lt, T, fg) {
    if (!T) return;
    const d0 = T.d0 ?? 0.1, L = textLayout(T);
    if (T.meta) G.typeText(ctx, T.meta, TX, L.y0 - L.px * 0.82 - 30, F.mono(28), rgba(fg, 0.78), inv(d0, d0 + 0.3, lt), { ls: '4px', dir: 'ltr', align: 'right' });
    L.lines.forEach((ln, i) => G.maskText(ctx, ln, TX + L.px * 0.02, L.y0 + i * L.lh, F.he(900, L.px), T.titleCol || fg, inv(d0 + 0.08 * i, d0 + 0.5 + 0.08 * i, lt), { ls: (-0.01 * L.px) + 'px' }));
    if (T.sub) G.maskText(ctx, T.sub, TX, L.subY, F.he(700, L.sp), fg, inv(d0 + 0.16, d0 + 0.66, lt));
    if (T.chip) chip(ctx, TX, L.chipY, T.chip, T.chipFg || PAL.night.t[2], T.chipBg || G.SUN, E.oExp(inv(d0 + 0.3, d0 + 0.65, lt)), { mono: T.chipMono, px: L.cp });
  }
  const READY = 0.75;   // a text block is fully in this long after its d0

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
      const x = N > 1 ? lerp(x0, x1, (i - 1) / (N - 1)) : x0;
      const now = i === info.cur, done = i < info.cur;
      ctx.fillStyle = rgba(fg, now ? 1 : done ? 0.75 : 0.28);
      ctx.fillRect(Math.round(x) - (now ? 3 : 1.5), y - (now ? 18 : 8), now ? 6 : 3, now ? 26 : 16);
    }
    ctx.restore();
  }

  // ───────── shared pictogram scenes ─────────
  const SCR = [SX - 134, SY - 230, 268];   // the phone screen: x, y, width
  function phoneOn(ctx) { return G.phone(ctx, SX, SY + 10, 300, G.INK, G.CREAM); }
  function bars(ctx, x, y, w, n, col, lw = 16) { for (let i = 0; i < n; i++) G.seg(ctx, [x + w, y + i * 32], [x + w * (i === n - 1 ? 0.45 : 0.05), y + i * 32], lw, col); }
  function bubbleWord(ctx, x, y, w, h, col, word, fg, p = 1, side = 'r', px = 64) {
    G.bubble(ctx, x, y, w, h, col, side);
    G.typeText(ctx, word, x + w / 2, y + h / 2 + px * 0.36, F.he(800, px), fg, p, { align: 'center' });
  }
  const scaleAt = (ctx, x, y, s, fn) => { if (s <= 0) return; ctx.save(); ctx.translate(x, y); ctx.scale(s, s); ctx.translate(-x, -y); fn(); ctx.restore(); };

  // ═════════════════════════ FILM A — customers ═════════════════════════
  const A = { right: 'פרולי · ללקוחות' };
  A.S = {
    // problem + logo, three seconds: the tap drips, the question, the name
    hook: { dur: 3, P: 'night', T: { title: 'נזילה?', chip: 'פרולי', chipPx: 64, d0: 0.1, px: 230 },
      ev: [[0.5, 'drip'], [1.5, 'drip'], [2.5, 'drip'], [0.1, 'title']] },
    write: { dur: 4.5, P: 'water', step: true, trans: 'quarter', T: { title: 'כותבים', sub: 'בוואטסאפ: טקסט, תמונה, קול, וידאו', spx: 54 },
      ev: [[0.35, 'morph'], [1.0, 'popnote', 0], [1.5, 'popnote', 1], [2.0, 'popnote', 2], [2.5, 'popnote', 3]] },
    consent: { dur: 4.5, P: 'water', step: true, trans: 'slide', T: { title: 'מאשרים', sub: 'פעם אחת, בפנייה הראשונה' },
      ev: [[0.5, 'bubble'], [1.5, 'tick']] },
    find: { dur: 6, P: 'mint', step: true, trans: 'cols', T: { title: 'מאתרים', sub: 'בעל מקצוע פנוי, קרוב אליכם' },
      ev: [[0.5, 'slam'], [1.0, 'ping'], [1.5, 'ping'], [2.0, 'ping'], [2.5, 'ping'], [3.0, 'found'], [4.0, 'line']] },
    approve: { dur: 4.5, P: 'lilac', step: true, trans: 'rows', T: { title: 'בעל המקצוע מאשר', sub: 'המספר שלכם נחשף רק אחרי שאישר', spx: 52, d0: 0 },
      ev: [[0.25, 'bubble'], [1.0, 'unlock'], [1.5, 'reveal']], words: [['אשר', 0.6]], rest: [0.5, 1.0] },
    status: { dur: 4.5, P: 'sun', step: true, trans: 'flip', T: { title: 'תמיד בתמונה', sub: 'כתבו "סטטוס" או "נציג"' },
      ev: [[0.5, 'key'], [0.625, 'key'], [0.75, 'key'], [0.875, 'key'], [1.0, 'key'], [1.5, 'bubble'], [2.0, 'key'], [2.125, 'key'], [2.25, 'key'], [2.375, 'key']],
      words: [['סטטוס', 1.05], ['נציג', 2.45]] },
    fixed: { dur: 4.5, P: 'mint', trans: 'quarter', T: { title: 'תוקן.', sub: 'ופרולי מוודאת שהכול בסדר', px: 230 },
      ev: [[0.5, 'turn'], [1.0, 'tickbig']] },
    end: { dur: 8, P: 'night', trans: 'rows', end: { sub: 'בלי אפליקציה · ללא עלות ללקוחות', site: 'proli.me' } },
  };
  A.SEQ = { full: ['hook', 'write', 'consent', 'find', 'approve', 'status', 'fixed', 'end'], short: ['hook', 'write', 'find', 'end'] };
  A.END_DUR = { full: 8, short: 6 };
  A.grooveFrom = 'write';

  // drops in the hook: splash on the beat
  const DRIPS = [0.5, 1.5, 2.5, 3.5], FALL = 0.3;
  const TAP = { x: SX - 70, y: SY - 140, s: 230 }, FLOOR = SY + 250;
  function dripScene(ctx, lt, P, dry) {
    const fg = G.CREAM;
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
    write(ctx, lt, P) {
      bg(ctx, P, 21, lt);
      stageDisc(ctx, P, lt);
      assemble(ctx, lt, (c) => phoneOn(c));
      const [sx, sy, sw] = SCR;
      // the drop falls into the screen and becomes a message
      const fall = inv(0, 0.35, lt), morph = inv(0.35, 0.7, lt);
      if (lt < 0.55) G.drop(ctx, SX + 30, lerp(SY - 640, sy + 80, fall * fall), 26 * (1 - E.i3(morph)), PAL.water.t[4]);
      scaleAt(ctx, sx + sw - 20, sy + 150, E.oBack(morph, 2), () => {
        G.bubble(ctx, sx + 30, sy + 40, sw - 50, 110, PAL.water.t[4], 'r'); bars(ctx, sx + 60, sy + 78, sw - 120, 2, G.INK, 14);
      });
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
      phoneOn(ctx);
      const [sx, sy, sw] = SCR;
      G.bubble(ctx, sx + 30, sy + 40, sw - 50, 110, PAL.water.t[4], 'r'); bars(ctx, sx + 60, sy + 78, sw - 120, 2, G.INK, 14);
      // Proli asks once (yellow, from the left), the reply is a tick
      scaleAt(ctx, sx + 20, sy + 260, pop(lt, 0.5), () => {
        G.bubble(ctx, sx + 20, sy + 190, sw - 60, 120, G.SUN, 'l'); bars(ctx, sx + 46, sy + 232, sw - 120, 2, G.INK, 14);
        G.padlock(ctx, sx + sw - 70, sy + 252, 18, 0, G.INK, G.SUN);
      });
      scaleAt(ctx, sx + sw - 30, sy + 430, pop(lt, 1.5), () => {
        G.bubble(ctx, sx + 110, sy + 350, sw - 140, 110, PAL.mint.t[3], 'r'); G.tick(ctx, sx + 110 + (sw - 140) / 2, sy + 405, 54, 14, G.CREAM, inv(1.55, 1.85, lt));
      });
    },
    find(ctx, lt, P) {
      bg(ctx, P, 41, lt);
      const k = lerp(1, 1.1, E.io(inv(0, 6, lt)));    // a slow push into the map
      ctx.save(); ctx.translate(SX, SY); ctx.scale(k, k); ctx.translate(-SX, -SY);
      const col = stageDisc(ctx, P, lt, { col: P.t[3] });
      ctx.save(); ctx.beginPath(); ctx.arc(SX, SY, SR, 0, Math.PI * 2); ctx.clip();
      ctx.globalAlpha = E.oExp(inv(0, 0.4, lt));
      for (let i = -3; i <= 3; i++) { G.seg(ctx, [SX + i * 120, SY - SR], [SX + i * 120, SY + SR], 14, P.t[4]); G.seg(ctx, [SX - SR, SY + i * 120], [SX + SR, SY + i * 120], 14, P.t[4]); }
      G.seg(ctx, [SX - SR, SY + SR * 0.7], [SX + SR, SY - SR * 0.5], 30, P.t[4]);
      ctx.globalAlpha = 1;
      for (const b of [1.0, 1.5, 2.0, 2.5]) { const p = inv(b, b + 1.1, lt); if (p > 0 && p < 1) G.ring(ctx, SX, SY + 30, 40 + 360 * E.o3(p), 10 * (1 - p), rgba(G.CREAM, 1 - p)); }
      const pros = [[SX - 250, SY - 150], [SX + 210, SY - 230], [SX + 90, SY + 160], [SX - 170, SY + 250], [SX + 290, SY + 60], [SX - 60, SY - 290]];
      const near = 2;
      pros.forEach(([x, y], i) => {
        const s = pop(lt, 0.7 + i * 0.125), lit = i === near ? E.oExp(inv(3.0, 3.25, lt)) : 0, dim = i === near ? 0 : 0.55 * inv(4.0, 4.4, lt);
        if (s <= 0) return;
        ctx.save(); ctx.globalAlpha = 1 - dim; ctx.translate(x, y); ctx.scale(s * (1 + 0.25 * lit), s * (1 + 0.25 * lit));
        G.disc(ctx, 0, 0, 44, mix(G.CREAM, G.SUN, lit)); G.toolbox(ctx, 0, 8, 22, P.t[0], mix(G.CREAM, G.SUN, lit)); ctx.restore();
      });
      const lp = E.io3(inv(4.0, 4.6, lt));
      if (lp > 0) { ctx.setLineDash([4, 26]); G.pline(ctx, [[pros[near][0], pros[near][1]], [lerp(pros[near][0], SX, lp), lerp(pros[near][1], SY + 30, lp)]], 14, G.SUN); ctx.setLineDash([]); }
      ctx.restore();
      const d = inv(0.2, 0.5, lt);
      if (d > 0) { const y = lerp(SY - 700, SY + 30, E.i3(d)), sq = lt > 0.5 ? 1 - 0.18 * Math.exp(-(lt - 0.5) * 10) * Math.cos((lt - 0.5) * 30) : 1;
        ctx.save(); ctx.translate(SX, y); ctx.scale(1 / sq, sq); G.pin(ctx, 0, 0, 52, G.CREAM, col); ctx.restore(); }
      ctx.restore();
    },
    approve(ctx, lt, P) {
      bg(ctx, P, 52, lt);
      stageDisc(ctx, P, lt);
      // the pro's answer comes in, then the lock opens and the number is revealed
      scaleAt(ctx, SX + 150, SY - 230, pop(lt, 0.25), () => {
        bubbleWord(ctx, SX - 10, SY - 330, 300, 130, G.CREAM, 'אשר', G.INK, 1, 'r', 76);
        G.wrench(ctx, SX - 110, SY - 265, 70, -0.6, G.SUN, P.t[3]);
      });
      const open = inv(1.0, 1.35, lt), shake = lt > 1.0 ? Math.sin((lt - 1.0) * 40) * 6 * Math.exp(-(lt - 1.0) * 8) : 0;
      G.padlock(ctx, SX + shake, SY + 120, 120, open, G.CREAM, P.t[3]);
      const r = pop(lt, 1.5);
      if (r > 0) { ctx.save(); ctx.translate(SX + 250, SY + 130); ctx.scale(r, r); G.disc(ctx, 0, 0, 74, G.SUN); G.phone(ctx, 0, 0, 40, G.INK, G.SUN); ctx.restore(); }
    },
    status(ctx, lt, P) {
      bg(ctx, P, 61, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx);
      const [sx, sy, sw] = SCR;
      if (lt >= 0.4) bubbleWord(ctx, sx + 40, sy + 40, sw - 60, 100, PAL.water.t[3], 'סטטוס', G.CREAM, inv(0.5, 1.0, lt), 'r', 52);
      scaleAt(ctx, sx + 20, sy + 270, pop(lt, 1.5), () => { G.bubble(ctx, sx + 20, sy + 180, sw - 50, 120, G.SUN, 'l'); bars(ctx, sx + 46, sy + 222, sw - 110, 2, G.INK, 14); });
      if (lt >= 1.9) bubbleWord(ctx, sx + 100, sy + 350, sw - 120, 100, PAL.water.t[3], 'נציג', G.CREAM, inv(2.0, 2.4, lt), 'r', 52);
    },
    fixed(ctx, lt, P) {
      bg(ctx, P, 71, lt);
      stageDisc(ctx, P, lt);
      dripScene(ctx, lt, P, true);
      const s = pop(lt, 1.0, 0.3);
      if (s > 0) { ctx.save(); ctx.translate(SX + 220, SY - 200); ctx.scale(s, s); G.disc(ctx, 0, 0, 110, G.SUN); G.tick(ctx, 0, 6, 110, 26, G.INK, inv(1.05, 1.3, lt)); ctx.restore(); }
    },
    end(ctx, lt, P, s) { endScene(ctx, lt, P, s); },
  };

  // ═════════════════════════ FILM B — professionals ═════════════════════════
  const B = { right: 'פרולי · לבעלי מקצוע' };
  // The three commands the film names; "אשר" has its own scene, "הפסקה" / "זמין" the break card.
  const CMDS = [{ w: 'עבודות', P: 'water', ico: 'cal' }, { w: 'חפש', P: 'mint', ico: 'mag' }, { w: 'תפריט', P: 'coral', ico: 'menu' }];
  const CMD_DT = 0.25, CMD_Y0 = 440, CMD_RH = 320, CMD_GAP = 30;
  const TAKE_SUB = APPROVE === 'button' ? 'לחיצה אחת, והעבודה שלכם' : 'מילה אחת, והעבודה שלכם';
  B.S = {
    // the benefit first: jobs land in the pro's WhatsApp
    open: { dur: 4.5, P: 'night', clock: '08:00',
      T: { title: ['עבודות מגיעות', 'אליכם בוואטסאפ'], chip: 'פיילוט ללא עלות', chipPx: 50, px: 150, y: 1330 },
      ev: [[0.1, 'title'], [0.0, 'bell'], [0.25, 'bubble'], [0.75, 'bubble'], [1.0, 'bell'], [1.25, 'bubble']] },
    summary: { dur: 4.5, P: 'sun', clock: '08:00', step: true, trans: 'rows', T: { title: 'סיכום בוקר', sub: 'העבודות של היום, כל בוקר' },
      ev: [[1.0, 'tick'], [1.5, 'tick'], [2.0, 'tick']] },
    lead: { dur: 4.5, P: 'coral', clock: '09:12', step: true, trans: 'cols', T: { title: 'יש עבודה', sub: 'תיאור התקלה והכתובת' },
      ev: [[-0.25, 'bell'], [0.5, 'bell'], [1.0, 'bell'], [1.5, 'bell'], [2.0, 'bubble']] },
    filter: { dur: 4.5, P: 'water', clock: '09:12', step: true, trans: 'quarter', T: { title: 'רק מה שמתאים', sub: 'לפי תחום, אזור ועומס' },
      ev: [...Array.from({ length: 12 }, (_, i) => [0.65 + i * 0.0625, 'dot', i]), [0.5, 'chipon'], [0.75, 'chipon'], [1.0, 'chipon'], [2.5, 'through'], [3.0, 'tick']],
      words: [['תחום · אזור · עומס', 1.3]] },
    take: { dur: 4.5, P: 'mint', clock: '09:13', step: true, trans: 'slide', T: { title: 'אשר.', sub: TAKE_SUB, px: 230 },
      ev: APPROVE === 'button' ? [[0.3, 'bubble'], [1.0, 'key'], [2.0, 'tickbig']] : [[0.5, 'key'], [0.67, 'key'], [0.83, 'key'], [2.0, 'tickbig']],
      words: APPROVE === 'button' ? [['אשר · דחה', 0.8]] : [['אשר', 1.0]], rest: [1.75, 2.0] },
    commands: { dur: 3, P: 'night', clock: '12:30', step: true, trans: 'rows', run: true,
      ev: [[0.0, 'brand'], ...CMDS.map((c, i) => [i * CMD_DT, 'cmd', i])] },
    break: { dur: 4.5, P: 'lilac', clock: '13:00', trans: 'flip', breath: true, T: { title: 'צריך הפסקה?', sub: 'מתי שתרצו, בהודעה אחת' },
      ev: [[0.5, 'clink'], [1.0, 'switch'], [2.5, 'switch']], words: [['זמין · הפסקה', 0.3]] },
    dusk: { dur: 4.5, P: 'coral', clock: '19:00', step: true, trans: 'quarter', T: { title: 'סוף יום', sub: 'כל העבודות, לפי מועד' },
      ev: [[0.5, 'set'], [2.0, 'tick'], [2.25, 'tick'], [2.5, 'tick']] },
    end: { dur: 7.5, P: 'night', trans: 'rows', end: { sub: 'לבעלי מקצוע · פיילוט ללא עלות' } },
  };
  B.SEQ = { full: ['open', 'summary', 'lead', 'filter', 'take', 'commands', 'break', 'dusk', 'end'], short: ['open', 'lead', 'take', 'end'] };
  B.END_DUR = { full: 7.5, short: 6 };
  B.grooveFrom = 'open';
  // one incoming job on the phone: a cream bubble with a sign and two lines
  function jobBubble(ctx, y, h, sign, P) {
    const [sx, , sw] = SCR;
    G.bubble(ctx, sx + 16, y, sw - 50, h, G.CREAM, 'l');
    const ix = sx + sw - 72, iy = y + h / 2;
    G.disc(ctx, ix, iy, 30, G.SUN);
    if (sign === 'drop') G.drop(ctx, ix, iy + 8, 11, G.INK); else if (sign === 'wrench') G.wrench(ctx, ix, iy, 20, 0.6, G.INK, G.SUN); else G.toolbox(ctx, ix, iy + 3, 16, G.INK, G.SUN);
    bars(ctx, sx + 40, iy - 16, sw - 170, 2, rgba(G.INK, 0.55), 12);
    void P;
  }
  B.draw = {
    open(ctx, lt, P) {
      bg(ctx, P, 5, lt, { spread: 0.45, set: 'day' });
      stageDisc(ctx, P, lt);
      assemble(ctx, lt, (c) => phoneOn(c), 0.35);
      const [sx, sy] = SCR;
      [['drop', 0.25], ['wrench', 0.75], ['box', 1.25]].forEach(([sign, t0], i) => {
        const y = sy + 36 + i * 142;
        scaleAt(ctx, sx + 20, y + 110, pop(lt, t0), () => jobBubble(ctx, y, 112, sign, P));
      });
      const sw0 = [0.0, 1.0].reduce((a, b) => a + (lt > b ? Math.sin((lt - b) * 26) * 0.4 * Math.exp(-(lt - b) * 6) : 0), 0);
      G.bell(ctx, SX + 210, SY - 370, 130, G.SUN, sw0);
      for (const b of [0.0, 1.0]) { const p = inv(b, b + 0.45, lt); if (p > 0 && p < 1) { G.ring(ctx, SX + 210, SY - 290, 90 + 70 * p, 8 * (1 - p), rgba(G.SUN, 1 - p), -Math.PI * 0.9, -Math.PI * 0.6); G.ring(ctx, SX + 210, SY - 290, 90 + 70 * p, 8 * (1 - p), rgba(G.SUN, 1 - p), -Math.PI * 0.4, -Math.PI * 0.1); } }
    },
    summary(ctx, lt, P) {
      bg(ctx, P, 15, lt, { set: 'day' });
      stageDisc(ctx, P, lt);
      assemble(ctx, lt, (c) => phoneOn(c));
      const [sx, sy, sw] = SCR;
      G.bubble(ctx, sx + 16, sy + 30, sw - 32, 380, PAL.sun.t[4], 'l');
      G.sunIcon(ctx, sx + sw - 60, sy + 80, 18, G.SUN0);
      for (let i = 0; i < 3; i++) {
        const y = sy + 150 + i * 88;
        G.rrect(ctx, sx + sw - 120, y - 22, 80, 44, 10, G.INK);
        G.seg(ctx, [sx + sw - 150, y], [sx + 90, y], 14, rgba(G.INK, 0.55));
        const p = pop(lt, 1.0 + i * 0.5, 0.25);
        if (p > 0) { G.disc(ctx, sx + 56, y, 26 * p, PAL.mint.t[2]); G.tick(ctx, sx + 56, y + 2, 24, 7, G.CREAM, inv(1.0 + i * 0.5, 1.2 + i * 0.5, lt)); }
      }
    },
    lead(ctx, lt, P) {
      bg(ctx, P, 25, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx);
      const [sx, sy, sw] = SCR;
      const sw0 = [0.0, 0.5, 1.0, 1.5].reduce((a, b) => a + (lt > b ? Math.sin((lt - b) * 26) * 0.4 * Math.exp(-(lt - b) * 6) : 0), 0);
      G.bell(ctx, SX + 200, SY - 360, 130, G.SUN, sw0);
      for (const b of [0.0, 0.5, 1.0, 1.5]) { const p = inv(b, b + 0.45, lt); if (p > 0 && p < 1) { G.ring(ctx, SX + 200, SY - 280, 90 + 70 * p, 8 * (1 - p), rgba(G.SUN, 1 - p), -Math.PI * 0.9, -Math.PI * 0.6); G.ring(ctx, SX + 200, SY - 280, 90 + 70 * p, 8 * (1 - p), rgba(G.SUN, 1 - p), -Math.PI * 0.4, -Math.PI * 0.1); } }
      scaleAt(ctx, sx + 20, sy + 200, pop(lt, 2.0), () => {
        G.bubble(ctx, sx + 16, sy + 60, sw - 32, 300, G.CREAM, 'l');
        G.rrect(ctx, sx + 16, sy + 60, sw - 32, 64, [30, 30, 0, 0], P.t[2]);
        G.drop(ctx, sx + sw - 60, sy + 200, 20, PAL.water.t[2]); G.seg(ctx, [sx + sw - 100, sy + 185], [sx + 50, sy + 185], 14, rgba(G.INK, 0.55));
        G.pin(ctx, sx + sw - 60, sy + 300, 16, P.t[2], G.CREAM); G.seg(ctx, [sx + sw - 100, sy + 270], [sx + 80, sy + 270], 14, rgba(G.INK, 0.55));
      });
    },
    filter(ctx, lt, P) {
      bg(ctx, P, 35, lt);
      stageDisc(ctx, P, lt);
      G.funnel(ctx, SX, SY - 170, 260, G.CREAM);
      for (let i = 0; i < 12; i++) {
        const t0 = 0.25 + i * 0.0625, p = inv(t0, t0 + 0.4, lt);
        if (p <= 0 || p >= 1) continue;
        const x0 = SX - 220 + (i % 6) * 88 + (i > 5 ? 44 : 0), y = lerp(SY - 640, SY - 190, p * p);
        G.disc(ctx, x0, y, 22, i === 7 ? G.SUN : P.t[4]);
      }
      ['תחום', 'אזור', 'עומס'].forEach((c, i) => chip(ctx, SX + 330, SY - 120 + i * 90, c, G.INK, G.SUN, E.oExp(inv(0.5 + i * 0.25, 0.75 + i * 0.25, lt)), { px: 36 }));
      const out = inv(2.5, 3.0, lt);
      if (out > 0) {
        const y = lerp(SY + 90, SY + 240, E.o3(out)), s = 1 + 0.6 * E.oBack(inv(2.9, 3.2, lt));
        G.disc(ctx, SX, y, 24 * s, G.SUN); if (lt > 3.0) G.tick(ctx, SX, y + 2, 22 * s, 6 * s, G.INK, inv(3.0, 3.2, lt));
      }
    },
    take(ctx, lt, P) {
      bg(ctx, P, 45, lt);
      stageDisc(ctx, P, lt);
      phoneOn(ctx);
      const [sx, sy, sw] = SCR;
      if (APPROVE === 'button') {
        // what the pro sees: the offer, and under it two reply buttons; one tap on "אשר"
        G.bubble(ctx, sx + 16, sy + 30, sw - 40, 190, G.CREAM, 'l'); bars(ctx, sx + 44, sy + 72, sw - 110, 4, rgba(G.INK, 0.5), 13);
        const bw = (sw - 44) / 2, by = sy + 262, bh = 84, tap = inv(1.0, 1.12, lt) * (1 - inv(1.12, 1.4, lt)), done = inv(1.0, 1.15, lt);
        const bx = [sx + 16 + bw + 12, sx + 16];   // "אשר" on the right, "דחה" on the left
        const b0 = pop(lt, 0.3, 0.25), b1 = pop(lt, 0.4, 0.25);
        scaleAt(ctx, bx[0] + bw / 2, by + bh / 2, b0 * (1 - 0.08 * tap), () => {
          G.rrect(ctx, bx[0], by, bw, bh, 22, mix(PAL.mint.t[2], G.SUN, done));
          G.typeText(ctx, 'אשר', bx[0] + bw / 2, by + bh / 2 + 17, F.he(800, 48), mix(G.CREAM, G.INK, done), 1, { align: 'center' });
        });
        scaleAt(ctx, bx[1] + bw / 2, by + bh / 2, b1, () => {
          G.rrect(ctx, bx[1], by, bw, bh, 22, rgba(G.INK, 0.14));
          G.typeText(ctx, 'דחה', bx[1] + bw / 2, by + bh / 2 + 17, F.he(800, 48), rgba(G.INK, 0.75), 1, { align: 'center' });
        });
        const rp = inv(1.0, 1.5, lt); if (rp > 0 && rp < 1) G.ring(ctx, bx[0] + bw / 2, by + bh / 2, 30 + 90 * E.o3(rp), 10 * (1 - rp), rgba(G.INK, 0.6 * (1 - rp)));
      } else {
        G.bubble(ctx, sx + 16, sy + 40, sw - 60, 150, G.CREAM, 'l'); bars(ctx, sx + 46, sy + 80, sw - 130, 3, rgba(G.INK, 0.5), 13);
        if (lt >= 0.4) bubbleWord(ctx, sx + 90, sy + 250, sw - 110, 110, PAL.mint.t[3], 'אשר', G.CREAM, inv(0.5, 0.95, lt), 'r', 62);
      }
      const s = pop(lt, 2.0, 0.3);
      if (s > 0) { ctx.save(); ctx.translate(SX - 230, SY + 240); ctx.scale(s, s); G.disc(ctx, 0, 0, 120, G.SUN); G.tick(ctx, 0, 6, 120, 28, G.INK, inv(2.05, 2.3, lt)); ctx.restore();
        const p = inv(2.0, 2.6, lt); if (p < 1) G.ring(ctx, SX - 230, SY + 240, 120 + 200 * E.o3(p), 10 * (1 - p), rgba(G.SUN, 1 - p)); }
    },
    commands(ctx, lt, P) {
      // a three-second run: three commands, each a band sliding in from alternate sides
      ctx.fillStyle = P.t[2]; ctx.fillRect(0, 0, W, H);
      G.drawPattern(ctx, G.pattern('night', 9, { spread: 0.3, density: 0.4, set: 'day' }), rowDrift(lt, 9, 60));
      G.maskText(ctx, 'פקודות טקסט', TX, 330, F.he(800, 84), G.SUN, inv(0.0, 0.4, lt));
      CMDS.forEach((c, i) => {
        const t0 = i * CMD_DT, p = E.ioExp(inv(t0, t0 + 0.3, lt));
        if (p <= 0) return;
        const C = PAL[c.P], y = CMD_Y0 + i * (CMD_RH + CMD_GAP), dir = i % 2 ? -1 : 1;
        ctx.save(); ctx.translate(dir * W * (1 - p), 0);
        G.rrect(ctx, 64, y, W - 128, CMD_RH, 36, C.t[2]);
        G.maskText(ctx, c.w, W - 130, y + CMD_RH * 0.66, F.he(900, 180), C.fg, inv(t0 + 0.1, t0 + 0.45, lt));
        const ix = 230, iy = y + CMD_RH / 2;
        G.disc(ctx, ix, iy, 104, C.t[3]);
        if (c.ico === 'mag') G.magnifier(ctx, ix - 16, iy - 16, 44, C.fg);
        else if (c.ico === 'menu') G.menu(ctx, ix, iy, 52, C.fg);
        else G.calendar(ctx, ix, iy + 8, 62, C.fg, C.t[2], 5, G.SUN);
        ctx.restore();
      });
    },
    break(ctx, lt, P) {
      bg(ctx, P, 55, lt, { offs: rowDrift(lt, 55, 10) });   // the slow card: the rows barely move
      stageDisc(ctx, P, lt);
      const on = 1 - E.io3(inv(1.0, 1.3, lt)) + E.io3(inv(2.5, 2.8, lt));   // זמין → הפסקה → זמין
      G.cup(ctx, SX, SY - 90, 140, G.CREAM, 1, lt);
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
    end(ctx, lt, P, s) { endScene(ctx, lt, P, s); },
  };

  // ───────── the end: icon wall → one disc → title, WhatsApp number and QR ─────────
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
  // End-card geometry and timing. slow = the full customer film's longer wall.
  const EC = { titleY: 1290, subY: 1374, qr: [96, 1440, 250], labelY: 1492, numY: 1522, siteY: 1626, fineY: 1770 };
  const endTiming = (s) => (s.slow ? { tile0: 0.25, tileDt: 0.125, yellow: 1.5, col0: 2.0, col1: 2.5, text: 2.6 } : { tile0: 0, tileDt: 0.0625, yellow: 0.5, col0: 0.75, col1: 1.25, text: 1.0 });
  const WA_LABEL = 'שלחו הודעה בוואטסאפ';
  function qrBox(ctx, x, y, sz, p) {
    scaleAt(ctx, x + sz / 2, y + sz / 2, p, () => {
      G.rrect(ctx, x, y, sz, sz, 22, G.CREAM);
      const img = window.QR_IMG;
      if (img) { ctx.imageSmoothingEnabled = false; ctx.drawImage(img, x + 16, y + 16, sz - 32, sz - 32); ctx.imageSmoothingEnabled = true; return; }
      // placeholder: the three finder squares of a QR and the letters, no code
      const f = sz * 0.24, m = 20;
      for (const [fx, fy] of [[x + m, y + m], [x + sz - m - f, y + m], [x + m, y + sz - m - f]]) {
        G.rrect(ctx, fx, fy, f, f, 8, G.INK); G.rrect(ctx, fx + f * 0.18, fy + f * 0.18, f * 0.64, f * 0.64, 5, G.CREAM); G.rrect(ctx, fx + f * 0.32, fy + f * 0.32, f * 0.36, f * 0.36, 3, G.INK);
      }
      ctx.save(); ctx.font = F.mono(52); ctx.direction = 'ltr'; ctx.textAlign = 'center'; ctx.fillStyle = rgba(G.INK, 0.6); ctx.fillText('QR', x + sz * 0.62, y + sz * 0.72); ctx.restore();
      ctx.save(); ctx.setLineDash([12, 12]); ctx.strokeStyle = rgba(G.INK, 0.35); ctx.lineWidth = 4; ctx.beginPath(); ctx.roundRect(x + 8, y + 8, sz - 16, sz - 16, 16); ctx.stroke(); ctx.restore();
    });
  }
  function endScene(ctx, lt, P, s) {
    const pro = FILM === 'pro', K = endTiming(s), D = s.end;
    bg(ctx, P, 7, lt, { spread: 0.42, offs: (j) => rowDrift(Math.min(lt, 2.5 + j * 0.2), 7)(j) });   // rows stop one by one
    const collapse = E.ioExp(inv(K.col0, K.col1, lt));
    const cs = 300, x0 = W / 2 - cs * 1.5, y0 = 300;
    if (collapse < 1) for (let j = 0; j < 4; j++) for (let i = 0; i < 3; i++) {
      const k = j * 3 + i, sc0 = pop(lt, K.tile0 + k * K.tileDt, 0.25);
      if (sc0 <= 0) continue;
      const yellow = inv(K.yellow + j * 0.06, K.yellow + 0.12 + j * 0.06, lt);
      const cx = lerp(x0 + cs * (i + 0.5), SX, collapse), cy = lerp(y0 + cs * (j + 0.5), 760, collapse), sc = sc0 * (1 - collapse * 0.7);
      ctx.save(); ctx.translate(cx, cy); ctx.scale(sc, sc);
      const discCol = mix(P.t[3], G.SUN, yellow);
      G.disc(ctx, 0, 0, cs * 0.44, discCol);
      wallIcon(ctx, pro ? (k + 7) % 12 : k, 0, 0, cs * 0.34, mix(G.CREAM, G.INK, yellow), discCol);
      ctx.restore();
    }
    if (collapse > 0) {
      const r = 300 * E.oBack(inv(K.col0 + 0.15, K.col1 + 0.15, lt), 1.6);
      G.disc(ctx, SX, 760, r, G.SUN);
      if (r > 40) {
        const k = r / 300;
        ctx.save(); ctx.translate(SX, 760); ctx.scale(k, k);
        if (pro) G.toolbox(ctx, 0, 30, 130, G.INK, G.SUN);
        else { G.bubble(ctx, -150, -110, 300, 190, G.INK, 'r'); G.tick(ctx, 0, -18, 110, 24, G.SUN); }
        ctx.restore();
      }
    }
    // title, the line under it, then the call to action: QR on the left, number (and site) on the right
    const d0 = K.text;
    G.maskText(ctx, 'פרולי', TX + 5, EC.titleY, F.he(900, 260), G.SUN, inv(d0, d0 + 0.5, lt), { ls: '-2.6px' });
    G.maskText(ctx, D.sub, TX, EC.subY, F.he(700, 50), G.CREAM, inv(d0 + 0.16, d0 + 0.66, lt));
    qrBox(ctx, EC.qr[0], EC.qr[1], EC.qr[2], pop(lt, d0 + 0.3, 0.3));
    G.maskText(ctx, WA_LABEL, TX, EC.labelY, F.he(700, 42), G.CREAM, inv(d0 + 0.3, d0 + 0.7, lt));
    chip(ctx, TX, EC.numY, WA_NUMBER, G.INK, G.SUN, E.oExp(inv(d0 + 0.4, d0 + 0.75, lt)), { mono: true, px: 46 });
    if (D.site) chip(ctx, TX, EC.siteY, D.site, G.CREAM, P.t[4], E.oExp(inv(d0 + 0.5, d0 + 0.85, lt)), { mono: true, px: 34 });
    // fine print: carried in full by the site
    const fp = pro
      ? ['פרולי מספקת שירות טכנולוגי לתיווך בלבד.', 'בתקופת הפיילוט שתיקבע מולכם, השימוש ללא עלות.']
      : ['השירות פועל במתכונת פיילוט. פרולי אינה שירות חירום:', 'בסכנה מיידית חייגו 102 · 100 · 101'];
    const fa = E.oExp(inv(d0 + 0.9, d0 + 1.4, lt));
    if (fa > 0) { ctx.save(); ctx.globalAlpha = fa; ctx.font = F.he(500, 30); ctx.direction = 'rtl'; ctx.textAlign = 'right'; ctx.fillStyle = rgba(G.CREAM, 0.72);
      fp.forEach((l, i) => ctx.fillText(l, TX, EC.fineY + i * 44)); ctx.restore(); }
  }

  // ───────── build the cut: shots, HUD, events, music plan, cues ─────────
  const DEF = FILM === 'pro' ? B : A, DRAW = DEF.draw;
  const SHOTS = DEF.SEQ[CUT].map((key) => ({ key, kind: key, ...DEF.S[key] }));
  const nSteps = SHOTS.filter((s) => s.step).length;
  let tAcc = 0, stepNo = 0;
  SHOTS.forEach((s, i) => {
    if (s.key === 'end') { s.dur = DEF.END_DUR[CUT]; s.slow = FILM === 'customer' && CUT === 'full'; }
    s.t0 = tAcc; s.t1 = (tAcc += s.dur); s.pal = PAL[s.P]; s.i = i;
    if (s.step) stepNo++;
    const cur = s.step ? stepNo : stepNo >= nSteps && i > 0 ? nSteps + 1 : stepNo;
    const count = pad2(Math.min(cur, nSteps)) + ' / ' + pad2(nSteps);
    // one step counter per film: customers carry it in the HUD; the pros' HUD is a clock, so theirs sits over the title
    if (s.key !== 'end') s.hud = { right: DEF.right, left: FILM === 'pro' ? s.clock : count, n: nSteps, cur };
    if (FILM === 'pro' && s.step && s.T) s.T = { ...s.T, meta: count };
  });
  const DUR = tAcc;
  const at = (key) => { const s = SHOTS.find((x) => x.key === key); return s ? s.t0 : null; };
  const endShot = SHOTS[SHOTS.length - 1], KE = endTiming(endShot);
  const END_HIT = endShot.slow ? 2.5 : 1.0;   // the last chord lands on a beat, as the title arrives
  endShot.ev = [...Array.from({ length: 12 }, (_, i) => [KE.tile0 + i * KE.tileDt, 'tile', i]), [KE.col0, 'collapse'], [END_HIT, 'end']];

  const EV = [];
  SHOTS.forEach((s) => {
    if (s.trans) EV.push({ t: s.t0, type: 'trans', kind: s.trans, shot: s.key });
    (s.ev || []).forEach(([lt, type, k]) => EV.push({ t: +(s.t0 + lt).toFixed(4), type, i: k, shot: s.key }));
  });
  // the music plan, read by mix.py: where the groove plays, where it rests, where the last chord lands
  const FINAL = endShot.t0 + END_HIT;
  let groove = [[at(DEF.grooveFrom), endShot.t0]];
  const cutSpan = (spans, a, b) => spans.flatMap(([x, y]) => (b <= x || a >= y ? [[x, y]] : [[x, a], [b, y]].filter(([p, q]) => q > p)));
  SHOTS.forEach((s) => {
    if (s.rest) groove = cutSpan(groove, s.t0 + s.rest[0], s.t0 + s.rest[1]);
    if (s.breath) groove = cutSpan(groove, s.t0, s.t1);
  });
  const spanOf = (flag) => { const s = SHOTS.find((x) => x[flag]); return s ? [s.t0, s.t1] : null; };
  EV.push({ t: 0, type: 'plan', groove, pad: [0, at(DEF.grooveFrom) || 0.001], strip: [endShot.t0, FINAL], final: FINAL, run: spanOf('run'), breath: spanOf('breath') });

  // every text meant to be read: one piece per line, from the moment it is fully in until the shot hands over
  const TA = 0.12, TB = 0.26;
  function pieces(s) {
    const out = [];
    if (s.T) {
      const L = textLayout(s.T), r = (s.T.d0 ?? 0.1) + READY;
      out.push({ k: 't', text: L.lines.join(' '), ready: r, box: L.title });
      if (s.T.sub) out.push({ k: 's', text: s.T.sub, ready: r, box: L.sub });
      if (s.T.chip) out.push({ k: 'c', text: s.T.chip, ready: r, box: L.chip });
    }
    (s.words || []).forEach(([w, r], k) => out.push({ k: 'w' + k, text: w, ready: r, box: [SX - 300, SY - 400, SX + 340, SY + 400] }));
    if (s.key === 'commands') {
      out.push({ k: 'h', text: 'פקודות טקסט', ready: 0.5, box: [300, 240, W - 64, 350] });
      CMDS.forEach((c, i) => { const y = CMD_Y0 + i * (CMD_RH + CMD_GAP); out.push({ k: 'c' + i, text: c.w, ready: i * CMD_DT + 0.5, box: [64, y, W - 64, y + CMD_RH] }); });
    }
    if (s.key === 'end') {
      const r = KE.text + 0.9;
      out.push({ k: 't', text: 'פרולי', ready: r, box: [96, EC.titleY - 240, W - 60, EC.titleY + 30] });
      out.push({ k: 's', text: s.end.sub, ready: r, box: [96, EC.subY - 50, W - 60, EC.subY + 16] });
      out.push({ k: 'l', text: WA_LABEL, ready: r, box: [380, EC.labelY - 44, W - 60, EC.labelY + 14] });
      out.push({ k: 'n', text: WA_NUMBER, ready: r, box: [380, EC.numY, W - 60, EC.numY + 82] });
      if (s.end.site) out.push({ k: 'u', text: s.end.site, ready: r, box: [380, EC.siteY, W - 60, EC.siteY + 60] });
      out.push({ k: 'f', text: 'fine print', ready: KE.text + 1.4, box: [96, EC.fineY - 34, W - 60, EC.fineY + 60], pass: true });
    }
    return out;
  }
  SHOTS.forEach((s) => { s.pieces = pieces(s); s.readEnd = s.t1 - (s.i + 1 < SHOTS.length ? TA : 0); });
  // subtitle cues, read by srt.py: one per shot, the card's own words
  SHOTS.forEach((s) => {
    const ps = s.pieces.filter((p) => !p.pass);
    if (!ps.length) return;
    const text = s.key === 'commands' ? 'פקודות טקסט: ' + CMDS.map((c) => c.w).join(' · ')
      : ps.filter((p) => !/^w/.test(p.k)).map((p) => p.text).join(s.key === 'end' ? ' · ' : ' — ');
    EV.push({ t: +(s.t0 + Math.min(...ps.map((p) => p.ready))).toFixed(3), type: 'cue', t1: s.t1, text });
  });
  EV.sort((a, b) => a.t - b.t);

  // ───────── frame, transitions ─────────
  const bufA = G.canvas(), bufB = G.canvas(), bufSub = G.canvas();
  function shotIdx(t) { for (let i = SHOTS.length - 1; i >= 0; i--) if (t >= SHOTS[i].t0) return i; return 0; }
  function drawShot(ctx, s, t) {
    ctx.save();
    const lt = t - s.t0;
    DRAW[s.kind](ctx, lt, s.pal, s);
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
    if (nx && nx.trans && t >= nx.t0 - TA) tr = [s, nx];
    else if (i > 0 && s.trans && t < s.t0 + TB) tr = [SHOTS[i - 1], s];
    if (tr) {
      const ca = bufA.getContext('2d'), cb = bufB.getContext('2d'); ca.setTransform(1, 0, 0, 1, 0, 0); cb.setTransform(1, 0, 0, 1, 0, 0);
      const [a, b] = tr, p = clamp((t - (b.t0 - TA)) / (TA + TB));
      drawShot(ca, a, Math.min(t, a.t1 - 0.001)); drawShot(cb, b, Math.max(t, b.t0));
      composite(ctx, b.trans, p, bufA, bufB);
    } else drawShot(ctx, s, t);
  }
  const isFast = (t) => SHOTS.some((s) => s.trans && t >= s.t0 - TA && t < s.t0 + TB);

  // ───────── page contract ─────────
  window.DUR = DUR;
  window.EV = EV;
  window.SHOTS = SHOTS.map((s) => ({ key: s.key, t0: s.t0, t1: s.t1, P: s.P }));
  window.FILM = FILM; window.CUT = CUT;
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
  window.TEXTS = (t) => {
    const out = [];
    SHOTS.forEach((s) => s.pieces.forEach((p) => {
      if (t < s.t0 + p.ready || t >= s.readEnd) return;
      const [x0, y0, x1, y1] = p.box;
      out.push({ id: s.key + '.' + p.k, text: p.text, x0, y0, x1, y1, ...(p.pass ? { pass: true } : {}) });
    }));
    return out;
  };
})();
