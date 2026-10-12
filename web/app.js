'use strict';
const $ = s => document.querySelector(s);
const video = $('#video'), overlay = $('#overlay'), padCv = $('#pad'), chartCv = $('#charts');

const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const COL = { L: css('--L'), R: css('--R'), com: css('--com'), ev: '#f472b6' };
const PANEL_COL = { '←': css('--aL'), '↓': css('--aD'), '↑': css('--aU'), '→': css('--aR'),
  '中央': '#9ca3af', '中間列': '#9ca3af', '角落': '#f59e0b', '板外': '#ef4444' };
const ARROW_KEYS = { L: '←', D: '↓', U: '↑', R: '→' };
const ARROW_POS = { L: [-1, 0], D: [0, 1], U: [0, -1], R: [1, 0] };
const ARROW_COL = { L: css('--aL'), D: css('--aD'), U: css('--aU'), R: css('--aR') };

// Halpe26 骨架連線：[a, b, 側別]
const BONES = [[17, 18, 'C'], [18, 5, 'L'], [18, 6, 'R'], [5, 7, 'L'], [7, 9, 'L'], [6, 8, 'R'], [8, 10, 'R'],
  [18, 19, 'C'], [19, 11, 'L'], [19, 12, 'R'], [11, 13, 'L'], [13, 15, 'L'], [12, 14, 'R'], [14, 16, 'R'],
  [15, 24, 'L'], [15, 20, 'L'], [15, 22, 'L'], [20, 22, 'L'], [24, 20, 'L'],
  [16, 25, 'R'], [16, 21, 'R'], [16, 23, 'R'], [21, 23, 'R'], [25, 21, 'R']];

const S = { list: [], id: null, A: null, fi: 0, layers: { skel: 1, com: 1, grid: 1, feet: 1 },
  win: 8, showAll: true, calib: null, drag: null, dirty: true, poll: null, ev: null, rot: {} };
const esc = s => String(s ?? '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]);
const L = x => (x && typeof x === 'object') ? (x[LANG] ?? x.zh) : x;    // 後端雙語文字 {zh, ja}
const dirTxt = x => t('dir', { x: tv(x) });
const stepAt = (s, i) => S.A.steps.find(x => x.foot === s && x.frame <= i && x.end >= i);

// ───────── 影片清單 ─────────
async function loadList(keep = true) {
  const r = await fetch('/api/videos'); S.list = await r.json();
  renderList(keep);
  const busy = S.list.some(v => v.status !== 'done' && v.status !== 'error');
  clearTimeout(S.poll); if (busy) S.poll = setTimeout(() => loadList(), 3000);
}
function renderList(keep = true) {
  const sel = $('#videoSel'); const cur = keep ? S.id : null;
  sel.innerHTML = '';
  for (const v of S.list) {
    const o = document.createElement('option'); o.value = v.id;
    const st = v.status === 'done' ? '' : v.status === 'error' ? t('st.error')
      : `（${t('st.' + v.status)}` + (v.progress ? ` ${Math.round(v.progress * 100)}%` : '') +
        (v.eta ? t('st.eta', { m: Math.ceil(v.eta / 60) }) : '') + '）';
    o.textContent = (v.name || v.id) + st; o.disabled = v.status !== 'done';
    sel.appendChild(o);
  }
  const target = cur && S.list.find(v => v.id === cur) ? cur : (S.list.find(v => v.status === 'done') || {}).id;
  if (target) { sel.value = target; if (target !== S.id) openVideo(target); }
}
$('#videoSel').onchange = e => openVideo(e.target.value);

async function openVideo(id) {
  S.id = id; S.A = null; S.calib = null; S.ev = null; setCalibUI(false); setOptsUI();
  $('#empty').textContent = t('loading'); $('#empty').hidden = false;
  const r = await fetch(`/api/videos/${id}/analysis`);
  if (!r.ok) { $('#empty').textContent = (await r.json()).detail || t('load.fail'); return; }
  setAnalysis(await r.json());
  video.src = `/data/${id}/preview.mp4`;
  video.playbackRate = +$('#rate').value;
}

function setAnalysis(A) {
  S.A = A; S.dirty = true; $('#empty').hidden = true;
  buildFootTracks(A);
  setOptsUI();
  renderAll();
}

// ───────── 每支影片的設定（踏板亮燈、不在畫面的手）：存在 calib.json，改了就重新分析 ─────────
function setOptsUI() {
  const C = S.A && S.A.calibration;
  $('#vidOpts').hidden = !C;
  if (!C) return;
  const arms = C.hidden_arms || [];
  $('#optLights').checked = C.lights !== false;
  $('#optHideL').checked = arms.includes('L');
  $('#optHideR').checked = arms.includes('R');
}
async function saveOpts() {
  const id = S.id, boxes = ['#optLights', '#optHideL', '#optHideR'].map(s => $(s));
  const body = { lights: $('#optLights').checked,
    hidden_arms: [['L', '#optHideL'], ['R', '#optHideR']].filter(([, s]) => $(s).checked).map(([k]) => k) };
  boxes.forEach(b => b.disabled = true); $('#optBusy').hidden = false;
  try {
    const r = await fetch(`/api/videos/${id}/options`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) { alert(t('opt.fail') + ((await r.json()).detail || r.status)); setOptsUI(); return; }
    const A = await r.json();
    if (S.id === id) { S.ev = null; setAnalysis(A); }
  } finally {
    boxes.forEach(b => b.disabled = false); $('#optBusy').hidden = true;
  }
}
['#optLights', '#optHideL', '#optHideR'].forEach(s => $(s).onchange = saveOpts);

// ───────── 俯視圖用的腳：固定鞋長、對準施力部位、步與步之間平滑移動 ─────────
// 分析用的「腳長延伸」是亮燈判定的寬容範圍（會讓腳看起來約 38 cm），畫圖時不用它，
// 改用骨架量到的腳長中位數 × 1.1（鞋子比腳長一點）。
const SHOE_MARGIN = 1.1, SHOE_W = 0.38, MOVE_S = 0.35;
function footPose(s, Lf) {
  const hx = s.heel_pad[0], hy = s.heel_pad[1], tx = s.toe_pad[0], ty = s.toe_pad[1];
  let ux = tx - hx, uy = ty - hy; const n = Math.hypot(ux, uy) || 1; ux /= n; uy /= n;
  let cx = (hx + tx) / 2, cy = (hy + ty) / 2;
  if (s.pad && s.pad[0] != null && s.part && s.part !== '未碰到') {
    // 施力部位落在判定的板上：腳跟踩 → 腳跟端在該處；腳尖踩 → 腳尖端在該處
    const off = s.part === '腳跟' ? Lf * 0.3 : s.part === '腳尖' ? -Lf * 0.3 : 0;
    cx = s.pad[0] + ux * off; cy = s.pad[1] + uy * off;
  }
  return { cx, cy, ang: Math.atan2(uy, ux) };
}
function buildFootTracks(A) {
  const n = A.frames.t.length, fps = A.fps || 30;
  const ok = s => s.heel_pad && s.heel_pad[0] != null && s.toe_pad && s.toe_pad[0] != null;
  const lens = A.steps.filter(ok).map(s => Math.hypot(s.toe_pad[0] - s.heel_pad[0], s.toe_pad[1] - s.heel_pad[1])).sort((a, b) => a - b);
  const Lf = SHOE_MARGIN * (lens.length ? lens[Math.floor(lens.length / 2)] : 0.85);
  A.shoeLen = Lf;
  A.track = {};
  for (const f of ['L', 'R']) {
    const ss = A.steps.filter(s => s.foot === f && ok(s)).sort((a, b) => a.frame - b.frame);
    const tr = new Array(n).fill(null);
    const P = ss.map(s => ({ ...footPose(s, Lf), s }));
    const lerpAng = (a, b, k) => { let d = b - a; while (d > Math.PI) d -= 2 * Math.PI; while (d < -Math.PI) d += 2 * Math.PI; return a + d * k; };
    P.forEach((p, k) => {
      for (let i = p.s.frame; i <= Math.min(n - 1, p.s.end); i++) tr[i] = { ...p, air: 0 };
      const q = P[k + 1];
      const g0 = p.s.end + 1, g1 = q ? q.s.frame - 1 : n - 1;
      if (g1 < g0) return;
      if (!q) { for (let i = g0; i <= g1; i++) tr[i] = { ...p, air: 0, idle: 1 }; return; }
      // 兩步之間：停在原地，直到下一步前 MOVE_S 秒才移動（smoothstep）
      const m0 = Math.max(g0, q.s.frame - Math.round(MOVE_S * fps));
      for (let i = g0; i <= g1; i++) {
        if (i < m0) { tr[i] = { ...p, air: 0, idle: 1 }; continue; }
        const k2 = (i - m0 + 1) / (q.s.frame - m0 + 1), e = k2 * k2 * (3 - 2 * k2);
        tr[i] = { cx: p.cx + (q.cx - p.cx) * e, cy: p.cy + (q.cy - p.cy) * e, ang: lerpAng(p.ang, q.ang, e),
          s: e < 0.5 ? p.s : q.s, air: Math.sin(Math.PI * k2) };
      }
    });
    if (P.length) for (let i = 0; i < P[0].s.frame; i++) tr[i] = { ...P[0], air: 0, idle: 1 };
    A.track[f] = tr;
  }
}
// 鞋型輪廓（局部座標：x 沿腳跟→腳尖 -0.5..0.5，y 為寬度 -1..1，正值＝大拇趾側）
const SHOE = [[-0.5, 0], [-0.47, 0.5], [-0.36, 0.78], [-0.12, 0.72], [0.12, 0.86], [0.3, 1.0], [0.43, 0.85], [0.5, 0.4],
  [0.49, -0.2], [0.4, -0.72], [0.22, -0.9], [0.0, -0.8], [-0.2, -0.7], [-0.38, -0.72], [-0.47, -0.45]];
function drawShoe(g, P, pose, foot, Lf, { alpha = 0.85, heelUp = false, outline = '#fff', dashed = false } = {}) {
  const ux = Math.cos(pose.ang), uy = Math.sin(pose.ang);
  const side = foot === 'L' ? 1 : -1;                    // 大拇趾側朝向另一隻腳
  const vx = -uy * side, vy = ux * side, W = SHOE_W * Lf / 2;
  const pt = ([x, y]) => P(pose.cx + ux * x * Lf + vx * y * W, pose.cy + uy * x * Lf + vy * y * W);
  g.beginPath(); SHOE.forEach((q, k) => { const [x, y] = pt(q); k ? g.lineTo(x, y) : g.moveTo(x, y); }); g.closePath();
  g.fillStyle = COL[foot]; g.globalAlpha = alpha; g.fill(); g.globalAlpha = 1;
  g.strokeStyle = outline; g.lineWidth = 1.5; g.setLineDash(dashed ? [3, 3] : []); g.stroke(); g.setLineDash([]);
  if (heelUp) {                                          // 腳跟離地：後段挖空成虛線
    g.beginPath(); SHOE.filter(q => q[0] <= -0.1).forEach((q, k) => { const [x, y] = pt(q); k ? g.lineTo(x, y) : g.moveTo(x, y); });
    g.closePath(); g.fillStyle = '#1a1f29'; g.globalAlpha = 0.6; g.fill(); g.globalAlpha = 1;
    g.strokeStyle = COL[foot]; g.setLineDash([3, 3]); g.stroke(); g.setLineDash([]);
  }
}
function renderAll() {
  if (!S.A) return;
  const A = S.A, v = A.view, b = [];
  b.push(v.mode === 'homography' ? [t('b.homo')] : v.mode === 'side' ? [t('b.side'), 1] : [t('b.none'), 1]);
  b.push([{ manual: t('b.manual'), 'auto-glow': t('b.glow'), 'auto-feet': t('b.feet') }[A.calibration.source] || t('b.nocal'),
    A.calibration.source === 'auto-feet']);
  b.push([v.com_mode === 'trunk' ? t('b.trunk') : t('b.pelvis'), v.com_mode !== 'trunk']);
  const ae = v.arm_est_pct;
  if (ae) for (const sd of ['R', 'L']) if (ae[sd] >= 20) b.push([t('b.armEst', { s: t('foot.' + sd + 'h'), p: Math.round(ae[sd]) }), 1]);
  if (A.calibration.lights === false) b.push([t('b.noLights'), 1]);
  b.push([t('b.steps', { n: A.metrics.steps, r: A.metrics.steps_per_s ?? '-' })]);
  $('#badges').innerHTML = b.map(([s, hl]) => `<span class="badge${hl ? ' hl' : ''}">${esc(s)}</span>`).join('');
  renderAdvice(); renderEvents(); renderSteps(); renderMetrics(); renderEvSel(); updPadRotBtn(); redraw();
}

// ───────── 幀索引 ─────────
function frameAt(tm) {
  const T = S.A.frames.t; let lo = 0, hi = T.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (T[m] <= tm + 1e-4) lo = m; else hi = m - 1; }
  return lo;
}
const fmt = tm => `${Math.floor(tm / 60)}:${(tm % 60).toFixed(2).padStart(5, '0')}`;

// ───────── 鏡頭晃動補償：參考幀 ↔ 目前幀 ─────────
function inv3(m) {
  const [a, b, c, d, e, f, g, h, i] = m, A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
  const det = a * A + b * B + c * C;
  return [A / det, -(b * i - c * h) / det, (b * f - c * e) / det, B / det, (a * i - c * g) / det,
    -(a * f - c * d) / det, C / det, -(a * h - b * g) / det, (a * e - b * d) / det];
}
const ap3 = (m, [x, y]) => { const w = m[6] * x + m[7] * y + m[8]; return [(m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w]; };
// 4 點對應求單應矩陣（src → dst），回傳 9 元素陣列
function homog(src, dst) {
  const A = [], b = [];
  for (let k = 0; k < 4; k++) {
    const [x, y] = src[k], [u, v] = dst[k];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.push(u);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y]); b.push(v);
  }
  for (let c = 0; c < 8; c++) {                      // 高斯消去（部分樞軸）
    let piv = c; for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[piv][c])) piv = r;
    [A[c], A[piv]] = [A[piv], A[c]]; [b[c], b[piv]] = [b[piv], b[c]];
    for (let r = 0; r < 8; r++) if (r !== c) {
      const f = A[r][c] / A[c][c]; for (let k = c; k < 8; k++) A[r][k] -= f * A[c][k]; b[r] -= f * b[c];
    }
  }
  return [...b.map((v, k) => v / A[k][k]), 1];
}
const PAD_CORNERS = [[-1.5, -1.5], [1.5, -1.5], [1.5, 1.5], [-1.5, 1.5]];   // 前左、前右、後右、後左
const Z = { k: 1, x: 0, y: 0 };                         // 畫面縮放：screen = offset + local * k
function applyZoom() {
  $('#zoomwrap').style.transform = `translate(${Z.x}px, ${Z.y}px) scale(${Z.k})`;
  overlay.classList.toggle('zoomed', Z.k > 1.01); redraw();
}
const toRef = p => ap3(S.A.frames.M[S.fi], p);
const fromRef = p => ap3(inv3(S.A.frames.M[S.fi]), p);

// ───────── 畫面疊圖 ─────────
function fitCanvas(cv) {
  const dpr = (devicePixelRatio || 1) * (cv === overlay ? Math.min(Z.k, 4) : 1), w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
  }
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  return [g, w, h];
}
function vmap() {
  const m = S.A.meta, cw = overlay.clientWidth, ch = overlay.clientHeight;
  const s = Math.min(cw / m.width, ch / m.height);
  const ox = (cw - m.width * s) / 2, oy = (ch - m.height * s) / 2;
  return { s, ox, oy, f: p => [ox + p[0] * s, oy + p[1] * s], inv: (x, y) => [(x - ox) / s, (y - oy) / s] };
}
const padH = () => { const P = S.A.calibration.points; return homog('LDUR'.split('').map(k => ARROW_POS[k]), 'LDUR'.split('').map(k => P[k])); };

function drawOverlay() {
  const [g] = fitCanvas(overlay); if (!S.A) return;
  const A = S.A, F = A.frames, i = S.fi, M = vmap();
  // 踏板格線
  if (S.layers.grid && A.calibration.grid.length && !S.calib) {
    g.strokeStyle = 'rgba(255,255,255,.45)'; g.lineWidth = 1.2; g.setLineDash([5, 4]);
    const Mi = inv3(F.M[i]);
    for (const ln of A.calibration.grid) { g.beginPath(); ln.forEach((p, k) => { const [x, y] = M.f(ap3(Mi, p)); k ? g.lineTo(x, y) : g.moveTo(x, y); }); g.stroke(); }
    g.setLineDash([]);
    for (const [k, p] of Object.entries(A.calibration.points || {})) label(g, M.f(ap3(Mi, p)), ARROW_KEYS[k], ARROW_COL[k], 0.55);
  } else if (S.layers.grid && A.view.mode === 'side' && !S.calib) {
    for (const k of ['U', 'D']) { const p = A.calibration.points[k]; label(g, M.f(fromRef(p)), t('row', { a: ARROW_KEYS[k] }), ARROW_COL[k], 0.55); }
  }
  // 亮燈中的箭頭板（機台判定）
  if (F.lights && A.calibration.points && A.view.mode === 'homography' && !S.calib) {
    const Hp = padH(), Mi = inv3(F.M[i]);
    for (const k of 'LDUR') {
      const v = F.lights[ARROW_KEYS[k]][i]; if (!(v > 0.4)) continue;
      const [ax, ay] = ARROW_POS[k];
      g.beginPath(); [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]].forEach(([dx, dy], n) => {
        const [x, y] = M.f(ap3(Mi, ap3(Hp, [ax + dx, ay + dy]))); n ? g.lineTo(x, y) : g.moveTo(x, y); });
      g.closePath(); g.strokeStyle = ARROW_COL[k]; g.lineWidth = 3; g.stroke();
      g.fillStyle = ARROW_COL[k]; g.globalAlpha = 0.25; g.fill(); g.globalAlpha = 1;
    }
  }
  // 骨架
  const K = F.kpts[i], sc = F.score[i];
  if (S.layers.skel && K) {
    const AE = F.arm_est;                                // 後端判定為推估的手臂（與重心計算一致）
    const est = ['R', 'L'].map(sd => [sd, armEstimate(K, sc, sd, A.meta.width, A.meta.height, AE ? !!AE[sd][i] : null)]).filter(x => x[1]);
    const hidden = new Set(est.flatMap(([, e]) => e.hide));
    g.lineWidth = 3; g.lineCap = 'round';
    for (const [sd, e] of est) {                          // 推定的手臂：虛線
      g.strokeStyle = sd === 'L' ? COL.L : COL.R; g.globalAlpha = 0.75; g.setLineDash([7, 6]);
      g.beginPath(); g.moveTo(...M.f(e.sh)); g.lineTo(...M.f(e.e)); g.lineTo(...M.f(e.w)); g.stroke(); g.setLineDash([]);
      const [x, y] = M.f(e.w); g.globalAlpha = 1; g.fillStyle = sd === 'L' ? COL.L : COL.R;
      g.beginPath(); g.arc(x, y, 4, 0, 7); g.fill();
      g.font = '11px sans-serif'; g.textAlign = 'left'; g.textBaseline = 'middle'; g.fillText(t('arm.est'), x + 7, y);
    }
    g.globalAlpha = 1;
    for (const [a, b, sd] of BONES) {
      if (K[a][0] < 0 || K[b][0] < 0 || sc[a] < 0.3 || sc[b] < 0.3 || hidden.has(a) || hidden.has(b)) continue;
      g.strokeStyle = sd === 'L' ? COL.L : sd === 'R' ? COL.R : '#e5e7eb';
      g.globalAlpha = Math.min(1, 0.35 + Math.min(sc[a], sc[b]));
      const p = M.f(K[a]), q = M.f(K[b]); g.beginPath(); g.moveTo(...p); g.lineTo(...q); g.stroke();
    }
    g.globalAlpha = 1;
    for (let j = 0; j < 26; j++) {
      if (K[j][0] < 0 || sc[j] < 0.3 || hidden.has(j)) continue;
      const [x, y] = M.f(K[j]); g.fillStyle = '#fff'; g.beginPath(); g.arc(x, y, 2.5, 0, 7); g.fill();
    }
  }
  // 鞋子範圍：骨架腳長 × 1.1（前後各延伸 5%）；判定用的寬容延伸不畫
  const fe = [(SHOE_MARGIN - 1) / 2, (SHOE_MARGIN - 1) / 2];
  if (S.layers.feet && K) {
    for (const [s, hj, bj, tj] of [['L', 24, 20, 22], ['R', 25, 21, 23]]) {
      if (K[hj][0] < 0 || K[bj][0] < 0 || K[tj][0] < 0) continue;
      const h = K[hj], tp = [(K[bj][0] + K[tj][0]) / 2, (K[bj][1] + K[tj][1]) / 2], v = [tp[0] - h[0], tp[1] - h[1]];
      const a = M.f([h[0] - fe[0] * v[0], h[1] - fe[0] * v[1]]), b = M.f([tp[0] + fe[1] * v[0], tp[1] + fe[1] * v[1]]);
      const st = stepAt(s, i);
      g.strokeStyle = COL[s]; g.lineWidth = 5; g.globalAlpha = 0.45; g.lineCap = 'round';
      if (st && st.heel_up) g.setLineDash([6, 5]);
      g.beginPath(); g.moveTo(...a); g.lineTo(...b); g.stroke(); g.globalAlpha = 1; g.setLineDash([]);
    }
  }
  // 落地點
  if (S.layers.feet) {
    for (const s of ['L', 'R']) {
      const p = F.ground[s][i]; if (!p) continue;
      const [x, y] = M.f(p); g.lineWidth = 2.5; g.strokeStyle = COL[s];
      g.beginPath(); g.arc(x, y, 9, 0, 7);
      if (F.planted[s][i]) { g.fillStyle = COL[s] + '99'; g.fill(); }
      g.stroke();
    }
  }
  // 重心
  if (S.layers.com) {
    const c = F.com[i], cg = F.com_ground[i];
    if (c) {
      const [x, y] = M.f(c);
      if (cg) {
        const [gx, gy] = M.f(cg);
        g.strokeStyle = COL.com; g.setLineDash([4, 4]); g.lineWidth = 1.5;
        g.beginPath(); g.moveTo(x, y); g.lineTo(gx, gy); g.stroke(); g.setLineDash([]);
        g.beginPath(); g.moveTo(gx - 10, gy); g.lineTo(gx + 10, gy); g.moveTo(gx, gy - 6); g.lineTo(gx, gy + 6); g.lineWidth = 2.5; g.stroke();
      }
      g.fillStyle = COL.com; g.strokeStyle = '#000'; g.lineWidth = 2;
      g.beginPath(); g.arc(x, y, 8, 0, 7); g.fill(); g.stroke();
    }
  }
  // 可編輯九宮格（點選對應點時先不畫格子）
  if (S.calib && !S.calib.pick) {
    const Hc = homog(PAD_CORNERS, S.calib.C), scr = q => M.f(fromRef(ap3(Hc, q))), zk = 1 / Z.k;
    for (const [k, [ax, ay]] of Object.entries(ARROW_POS)) {
      g.beginPath(); [[-.5, -.5], [.5, -.5], [.5, .5], [-.5, .5]].forEach(([dx, dy], n) => {
        const [x, y] = scr([ax + dx, ay + dy]); n ? g.lineTo(x, y) : g.moveTo(x, y); });
      g.closePath(); g.fillStyle = ARROW_COL[k]; g.globalAlpha = 0.28; g.fill(); g.globalAlpha = 1;
      const [x, y] = scr([ax, ay]);
      g.fillStyle = '#fff'; g.font = `bold ${Math.round(18 * zk)}px sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(ARROW_KEYS[k], x, y);
    }
    g.strokeStyle = '#fff'; g.lineWidth = 1.6 * zk;
    for (const v of [-1.5, -0.5, 0.5, 1.5]) for (const vert of [0, 1]) {
      g.beginPath();
      for (let u = -1.5; u <= 1.5001; u += 0.25) { const [x, y] = scr(vert ? [v, u] : [u, v]); u === -1.5 ? g.moveTo(x, y) : g.lineTo(x, y); }
      g.stroke();
    }
    const [sx, sy] = scr([0, -1.9]); g.fillStyle = '#facc15'; g.font = `${Math.round(12 * zk)}px sans-serif`; g.fillText(t('pad.screen'), sx, sy);
    for (const q of S.calib.C) {
      const [x, y] = M.f(fromRef(q));
      g.fillStyle = '#facc15'; g.strokeStyle = '#000'; g.lineWidth = 2 * zk;
      g.beginPath(); g.arc(x, y, 8 * zk, 0, 7); g.fill(); g.stroke();
    }
    // 箭頭中心把手（可拖曳）
    const AC = arrowCenters(S.calib.C);
    for (const k of CLICK_ORDER) {
      const [x, y] = M.f(fromRef(AC[k]));
      g.fillStyle = ARROW_COL[k]; g.strokeStyle = '#fff'; g.lineWidth = 2 * zk;
      g.beginPath(); g.arc(x, y, 11 * zk, 0, 7); g.fill(); g.stroke();
      g.fillStyle = '#000'; g.font = `bold ${Math.round(13 * zk)}px sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(ARROW_KEYS[k], x, y + 0.5);
    }
  }
  // 對應點：已點的位置（編號與俯視圖一致）
  if (S.calib && S.calib.pick) {
    const zk = 1 / Z.k;
    Object.entries(S.calib.pick.pairs).forEach(([k, p], n) => {
      const [x, y] = M.f(fromRef(p));
      g.strokeStyle = '#facc15'; g.lineWidth = 3 * zk;
      g.beginPath(); g.moveTo(x - 10 * zk, y); g.lineTo(x + 10 * zk, y); g.moveTo(x, y - 10 * zk); g.lineTo(x, y + 10 * zk); g.stroke();
      g.fillStyle = '#fff'; g.font = `bold ${Math.round(14 * zk)}px sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(String(n + 1), x + 14 * zk, y - 12 * zk);
    });
  }
}
// 畫面外的手：模型看不到只能亂猜（常貼在畫面邊緣或疊到另一隻手上）。
// 判定不可靠時不畫原本的點，改用另一隻手臂對「軀幹中線」鏡像推估（假設兩手對稱扶桿），以虛線顯示。
const ARM = { R: [6, 8, 10], L: [5, 7, 9] };
function armEstimate(K, sc, side, W, H, forced = null) {
  const [sh, el, wr] = ARM[side], [osh, oel, owr] = ARM[side === 'R' ? 'L' : 'R'];
  const has = j => K[j] && K[j][0] >= 0;
  if (![5, 6, 11, 12, osh, oel, owr, sh].every(has)) return null;
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const edge = p => p[0] < 0.025 * W || p[0] > 0.975 * W || p[1] < 0.02 * H || p[1] > 0.98 * H;
  const shw = dist(K[5], K[6]) || 1;
  const bad = forced != null ? forced
    : (!has(wr) || !has(el) || (sc[wr] < 0.55 && (dist(K[wr], K[owr]) < 0.35 * shw || edge(K[el]) || edge(K[wr]) || sc[el] < 0.5)));
  if (!bad || sc[owr] < 0.55 || sc[oel] < 0.5 || sc[sh] < 0.4) return null;
  const nk = [(K[5][0] + K[6][0]) / 2, (K[5][1] + K[6][1]) / 2], hp = [(K[11][0] + K[12][0]) / 2, (K[11][1] + K[12][1]) / 2];
  let ax = hp[0] - nk[0], ay = hp[1] - nk[1]; const n = Math.hypot(ax, ay) || 1; ax /= n; ay /= n;
  const refl = ([vx, vy]) => { const d = vx * ax + vy * ay; return [2 * d * ax - vx, 2 * d * ay - vy]; };
  const ve = refl([K[oel][0] - K[osh][0], K[oel][1] - K[osh][1]]), vw = refl([K[owr][0] - K[oel][0], K[owr][1] - K[oel][1]]);
  const e = [K[sh][0] + ve[0], K[sh][1] + ve[1]];
  return { hide: [el, wr], sh: K[sh], e, w: [e[0] + vw[0], e[1] + vw[1]] };
}
function label(g, [x, y], txt, col, alpha = 1) {
  g.globalAlpha = alpha; g.fillStyle = col; g.font = 'bold 16px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(txt, x, y); g.globalAlpha = 1;
}

// ───────── 俯視踏板 ─────────
// 俯視圖旋轉（順時針 0/90/180/270°）：讓俯視圖跟影片同方向，只改畫法，資料仍是踏板座標。
// 預設依校正自動判斷；手動轉過的角度每部影片各自記在瀏覽器。
const ROT_KEY = 'ddr-padrot:';
const rotCS = d => [Math.round(Math.cos(d * Math.PI / 180)), Math.round(Math.sin(d * Math.PI / 180))];
function autoPadRot() {
  // 鏡頭在踏板的哪一側：影片中踏板中心往下一點的地面點 → 換回踏板座標，就是朝鏡頭的方向。
  // 轉到讓鏡頭那側在俯視圖下方；斜角拍（鏡頭在後方 ±60° 內）維持 0°。
  const P = S.A && S.A.view.mode !== 'none' && S.A.calibration.points;
  if (!P || !'LDUR'.split('').every(k => P[k])) return 0;
  const H = padH(), c = ap3(H, [0, 0]), s = Math.hypot(P.R[0] - P.L[0], P.R[1] - P.L[1]) / 2;
  const v = ap3(inv3(H), [c[0], c[1] + 0.5 * s]), n = Math.hypot(...v);
  if (!(n > 1e-6)) return 0;
  const score = d => { const [cs, sn] = rotCS(d); return (v[0] * sn + v[1] * cs) / n; };   // 轉 d 度後朝下的程度
  if (score(0) >= 0.5) return 0;
  return [90, 180, 270].reduce((a, d) => score(d) > score(a) ? d : a);
}
function manualPadRot() {
  if (!(S.id in S.rot)) {
    let v = null; try { v = localStorage.getItem(ROT_KEY + S.id); } catch (_) { /* 無痕模式等 */ }
    S.rot[S.id] = v != null && [0, 90, 180, 270].includes(+v) ? +v : null;
  }
  return S.rot[S.id];
}
const padRot = () => S.A ? (manualPadRot() ?? autoPadRot()) : 0;
const unrotPad = (x, y) => { const [c, s] = rotCS(padRot()); return [x * c + y * s, -x * s + y * c]; };   // 畫面方向 → 踏板方向
function updPadRotBtn() {
  const b = $('#padRot'); if (!S.A) { b.textContent = '↻'; return; }
  b.textContent = `↻ ${padRot()}°` + (manualPadRot() == null ? t('pad.rotAuto') : '');
}
$('#padRot').onclick = () => {
  if (!S.A) return;
  const d = (padRot() + 90) % 360, auto = d === autoPadRot();   // 轉回自動判斷的角度就不再記
  S.rot[S.id] = auto ? null : d;
  try { auto ? localStorage.removeItem(ROT_KEY + S.id) : localStorage.setItem(ROT_KEY + S.id, String(d)); } catch (_) { /* 忽略 */ }
  updPadRotBtn(); redraw();
};
function drawFootOnPad(g, P, st, col, width, alpha) {
  const he = st.heel_ext || st.heel_pad, te = st.toe_ext || st.toe_pad;
  if (!he || he[1] == null || !te || te[1] == null) return;
  const h = P(he[0] ?? 0, he[1]), q = P(te[0] ?? 0, te[1]), m = [(h[0] + q[0]) / 2, (h[1] + q[1]) / 2];
  g.strokeStyle = col; g.lineWidth = width; g.lineCap = 'round'; g.globalAlpha = alpha;
  // 腳跟離地：後半段畫虛線
  g.setLineDash(st.heel_up ? [4, 4] : []);
  g.beginPath(); g.moveTo(...h); g.lineTo(...m); g.stroke(); g.setLineDash([]);
  g.beginPath(); g.moveTo(...m); g.lineTo(...q); g.stroke(); g.globalAlpha = 1;
}
function calibPreviewMap() {
  // 校正中：資料的踏板座標是舊校正算的；換到目前編輯中的校正（舊踏板→參考幀→新踏板）
  if (!S.calib || S.calib.pick || !S.A.calibration.points || S.A.view.mode !== 'homography') return null;
  return mul3(inv3(homog(PAD_CORNERS, S.calib.C)), padH());
}
function mul3(A, B) { return [0, 1, 2].flatMap(i => [0, 1, 2].map(j => A[i * 3] * B[j] + A[i * 3 + 1] * B[3 + j] + A[i * 3 + 2] * B[6 + j])); }
function drawPad() {
  const [g, w] = fitCanvas(padCv); if (!S.A) return;
  const A = S.A, F = A.frames, i = S.fi, tm = F.t[i];
  const R = 2.1, sc = w / (2 * R), rot = padRot(), [rc, rs] = rotCS(rot);
  const P0 = (x, y) => [w / 2 + (x * rc - y * rs) * sc, w / 2 + (x * rs + y * rc) * sc];
  // 貼著某一邊寫字（(dx, dy) 為踏板方向，(0,-1)＝螢幕那邊）；轉到左右兩邊時直著寫
  const edgeText = (txt, dx, dy) => {
    const ex = dx * rc - dy * rs, ey = dx * rs + dy * rc, m = w / 2 - 8;
    g.save(); g.translate(w / 2 + ex * m, w / 2 + ey * m); g.rotate(ex * Math.PI / 2);
    g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(txt, 0, 0); g.restore();
  };
  const G = calibPreviewMap();
  const P = G ? (x, y) => P0(...ap3(G, [x, y])) : P0;       // 資料點用 P（校正預覽會跟著動），格線用 P0
  // 腳：資料已含舊的手動偏移 off0；校正中預覽改成新偏移 off1
  const off0 = A.calibration.foot_offset || [0, 0], off1 = S.calib && S.calib.footOff ? S.calib.footOff : off0;
  const PF = (x, y) => { const q = [x - off0[0], y - off0[1]], [u, v] = G ? ap3(G, q) : q; return P0(u + off1[0], v + off1[1]); };
  const side = A.view.mode === 'side';
  for (let gx = -1; gx <= 1; gx++) for (let gy = -1; gy <= 1; gy++) {
    const [x, y] = P0(gx, gy); const arrow = Math.abs(gx) + Math.abs(gy) === 1;
    g.fillStyle = arrow ? '#232a38' : '#1a1f29'; g.fillRect(x - sc / 2 + 1, y - sc / 2 + 1, sc - 2, sc - 2);
  }
  g.strokeStyle = G ? '#facc15' : '#3a4252'; g.lineWidth = 1;
  for (let v = -1.5; v <= 1.5; v += 1) {
    g.beginPath(); g.moveTo(...P0(v, -1.5)); g.lineTo(...P0(v, 1.5)); g.stroke();
    g.beginPath(); g.moveTo(...P0(-1.5, v)); g.lineTo(...P0(1.5, v)); g.stroke();
  }
  for (const [k, [ax, ay]] of Object.entries(ARROW_POS)) {
    const lit = F.lights && F.lights[ARROW_KEYS[k]][i] > 0.4;
    if (lit) { const [x, y] = P0(ax, ay); g.fillStyle = ARROW_COL[k]; g.globalAlpha = 0.35; g.fillRect(x - sc / 2 + 1, y - sc / 2 + 1, sc - 2, sc - 2); g.globalAlpha = 1; }
    drawArrow(g, P0(ax, ay), sc * 0.28, k, ARROW_COL[k], rot * Math.PI / 180);
  }
  g.fillStyle = '#6b7280'; g.font = '11px sans-serif';
  edgeText(t('pad.screen'), 0, -1);
  g.textAlign = 'center';
  if (A.view.mode === 'none' && !(S.calib && S.calib.pick)) { g.fillStyle = '#9ca3af'; g.fillText(t('pad.noCalib'), w / 2, w / 2); return; }
  if (S.calib && S.calib.pick) {                          // 選對應點模式：只畫可選的點
    const pk = S.calib.pick, keys = Object.keys(pk.pairs), cur = pk.sel || nextDefaultTarget(pk);
    for (const p of PICK_TARGETS) {
      const [x, y] = P0(...p), k = keys.indexOf(tkey(p)), isCur = cur && tkey(cur) === tkey(p);
      g.beginPath(); g.arc(x, y, k >= 0 ? 8 : 5, 0, 7);
      g.fillStyle = k >= 0 ? '#facc15' : '#e5e7eb'; g.globalAlpha = k >= 0 ? 1 : 0.55; g.fill(); g.globalAlpha = 1;
      if (isCur) { g.strokeStyle = '#facc15'; g.lineWidth = 3; g.strokeRect(x - 11, y - 11, 22, 22); }
      if (k >= 0) { g.fillStyle = '#000'; g.font = 'bold 10px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(String(k + 1), x, y + 0.5); }
    }
    return;
  }

  const ev = S.ev;
  // 落點
  if (!ev) {
    for (const s of A.steps) {
      if (!s.pad || s.pad[1] == null) continue;
      const recent = s.t <= tm && s.t > tm - 1.5;
      if (!S.showAll && (!recent || s.t > tm)) continue;
      const [px, py] = PF(s.pad[0] ?? 0, s.pad[1]);
      g.globalAlpha = recent ? 1 : 0.28; g.fillStyle = COL[s.foot];
      g.beginPath(); g.arc(px, py, recent ? 5 : 3, 0, 7); g.fill();
    }
    g.globalAlpha = 1;
  } else {
    // 事件：當時的每一步（編號）與重心軌跡
    const seqSteps = A.steps.filter(s => s.t >= ev.t0 - 0.5 && s.t <= ev.t1 + 0.1);
    seqSteps.forEach((s, n) => {
      if (!s.heel_pad || s.heel_pad[0] == null) return;
      const pose = footPose(s, A.shoeLen);
      drawShoe(g, PF, pose, s.foot, A.shoeLen, { alpha: 0.45, heelUp: s.heel_up, outline: 'rgba(255,255,255,.5)' });
      const [x, y] = PF(pose.cx, pose.cy);
      g.fillStyle = '#fff'; g.font = 'bold 11px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(String(n + 1), x, y);
    });
    const f0 = Math.max(0, ev.f0 - 15), f1 = Math.min(F.t.length - 1, ev.f1 + 15);
    let prev = null;
    for (let k = f0; k <= f1; k++) {
      const p = F.com_pad[k]; if (!p) { prev = null; continue; }
      const q = P(...(side ? [0, p[1]] : p));
      if (prev) {
        const inEv = k >= ev.f0 && k <= ev.f1;
        g.strokeStyle = inEv ? COL.ev : '#e5e7eb'; g.globalAlpha = inEv ? 1 : 0.5; g.lineWidth = inEv ? 3 : 1.5;
        g.beginPath(); g.moveTo(...prev); g.lineTo(...q); g.stroke(); g.globalAlpha = 1;
      }
      prev = q;
    }
    const pk = F.com_pad[ev.frame];
    if (pk) { const [x, y] = P(...(side ? [0, pk[1]] : pk)); g.strokeStyle = COL.ev; g.lineWidth = 2.5;
      g.beginPath(); g.moveTo(x - 6, y - 6); g.lineTo(x + 6, y + 6); g.moveTo(x + 6, y - 6); g.lineTo(x - 6, y + 6); g.stroke(); }
  }
  // 目前的腳：平滑軌跡上的鞋型（騰空時半透明、虛線外框；腳跟離地時後段挖空）
  if (!side && A.track) for (const s of ['L', 'R']) {
    const pose = A.track[s][i]; if (!pose) continue;
    const planted = pose.air === 0 && !pose.idle;
    drawShoe(g, PF, pose, s, A.shoeLen, { alpha: planted ? 0.9 : 0.35 + 0.25 * (1 - pose.air),
      heelUp: planted && pose.s.heel_up, dashed: !planted, outline: planted ? '#fff' : COL[s] });
    const st = planted ? pose.s : null;
    if (st && st.pad && st.pad[0] != null && st.part && st.part !== '未碰到') {
      const c = PF(st.pad[0], st.pad[1]); g.strokeStyle = '#fff'; g.lineWidth = 2;
      g.beginPath(); g.arc(...c, 6, 0, 7); g.stroke();
    }
  } else for (const s of ['L', 'R']) {
    const st = stepAt(s, i);
    if (st && st.heel_pad && st.heel_pad[1] != null) drawFootOnPad(g, PF, st, COL[s], 7, 0.9);
  }
  if (S.calib && !S.calib.pick) {                          // 目前的手動腳位置偏移
    const [ox, oy] = S.calib.footOff;
    if (Math.hypot(ox, oy) > 0.005) { g.fillStyle = '#facc15'; g.font = '11px sans-serif'; g.textAlign = 'right';
      // 轉 180° 時「螢幕方向」在下面 → 改寫在右上角
      g.fillText(t('pad.footOff', { x: (ox * 28).toFixed(0), y: (oy * 28).toFixed(0) }), w - 6, rot === 180 ? 16 : w - 6); }
  }
  // 重心
  const c = F.com_pad[i], ax = A.view.com_axis;
  const comXY = p => side ? [0, p[1]] : p;
  if (c && ax) {
    // 重心只量得到沿 ax 的位置 → 畫一條與 ax 垂直的虛線：重心在這條線上的某處
    const o = F.com_obs[i] ?? 0, wv = [-ax[1], ax[0]];
    const a0 = [ax[0] * o - wv[0] * 2, ax[1] * o - wv[1] * 2], a1 = [ax[0] * o + wv[0] * 2, ax[1] * o + wv[1] * 2];
    g.save(); g.beginPath(); g.rect(w / 2 - 1.5 * sc, w / 2 - 1.5 * sc, 3 * sc, 3 * sc); g.clip();
    g.strokeStyle = COL.com; g.lineWidth = 1.5; g.setLineDash([4, 4]);
    g.beginPath(); g.moveTo(...P(...a0)); g.lineTo(...P(...a1)); g.stroke(); g.setLineDash([]); g.restore();
    g.strokeStyle = 'rgba(250,204,21,.25)'; g.lineWidth = 1;
    g.beginPath(); g.moveTo(...P(-ax[0] * 1.5, -ax[1] * 1.5)); g.lineTo(...P(ax[0] * 1.5, ax[1] * 1.5)); g.stroke();
    if (!ev) {
      g.beginPath(); let first = true;
      for (let k = Math.max(0, i - Math.round(A.fps)); k <= i; k++) {
        const p = F.com_pad[k]; if (!p) continue;
        const [x, y] = P(...comXY(p)); first ? g.moveTo(x, y) : g.lineTo(x, y); first = false;
      }
      g.strokeStyle = COL.com; g.lineWidth = 2; g.globalAlpha = 0.6; g.stroke(); g.globalAlpha = 1;
    }
    const [x, y] = P(...comXY(c));
    g.fillStyle = COL.com; g.strokeStyle = '#000'; g.lineWidth = 2; g.beginPath(); g.arc(x, y, 7, 0, 7); g.fill(); g.stroke();
  }
  if (side) { g.fillStyle = '#9ca3af'; g.font = '11px sans-serif'; edgeText(t('pad.sideNote'), 0, 1); }
}
function drawArrow(g, [x, y], r, k, col, rot = 0) {
  const ang = { U: 0, R: Math.PI / 2, D: Math.PI, L: -Math.PI / 2 }[k] + rot;
  g.save(); g.translate(x, y); g.rotate(ang); g.fillStyle = col; g.globalAlpha = 0.35;
  g.beginPath(); g.moveTo(0, -r); g.lineTo(r * 0.9, 0); g.lineTo(r * 0.35, 0); g.lineTo(r * 0.35, r);
  g.lineTo(-r * 0.35, r); g.lineTo(-r * 0.35, 0); g.lineTo(-r * 0.9, 0); g.closePath(); g.fill();
  g.restore(); g.globalAlpha = 1;
}

// ───────── 時間軸圖 ─────────
function chartRange() {
  const F = S.A.frames, T = F.t, dur = T[T.length - 1], tm = T[S.fi];
  if (!S.win) return [0, dur];
  let a = tm - S.win / 2, b = tm + S.win / 2;
  if (a < 0) { b -= a; a = 0; } if (b > dur) { a -= b - dur; b = dur; }
  return [Math.max(0, a), b];
}
function drawCharts() {
  const [g, w, h] = fitCanvas(chartCv); if (!S.A) return;
  const A = S.A, F = A.frames, T = F.t, [t0, t1] = chartRange();
  const L = 64, Rm = 10, X = tt => L + (tt - t0) / (t1 - t0) * (w - L - Rm);
  const i0 = frameAt(t0), i1 = frameAt(t1), stepN = Math.max(1, Math.floor((i1 - i0) / (w * 1.5)));
  const AN = A.view.com_axis_names || { axis: '-', pos: '+', neg: '-' };
  const lanes = [{ name: t('lane.com'), h: 0.30 }, { name: t('lane.hip'), h: 0.22 }, { name: t('lane.steps'), h: 0.22 }];
  if (F.lights) lanes.push({ name: t('lane.lights'), h: 0.26 }); else lanes[2].h = 0.48;
  let y = 6; const H = h - 22;
  g.font = '11px sans-serif'; g.textBaseline = 'middle';
  lanes.forEach((ln, li) => {
    const lh = H * ln.h - 6, top = y; y += H * ln.h;
    g.fillStyle = '#10131a'; g.fillRect(L, top, w - L - Rm, lh);
    g.fillStyle = '#8b93a7'; g.textAlign = 'right'; g.fillText(ln.name, L - 6, top + lh / 2);
    if (li === 0) {
      const Y = v => top + lh / 2 + (v / 1.5) * (lh / 2);
      // 重心事件區段
      (A.events || []).forEach((e, n) => {
        if (e.t1 < t0 || e.t0 > t1) return;
        const xa = Math.max(L, X(e.t0 - 0.05)), xb = Math.min(w - Rm, X(e.t1 + 0.05));
        const sel = S.ev && S.ev.frame === e.frame;
        g.fillStyle = sel ? 'rgba(244,114,182,.35)' : 'rgba(244,114,182,.16)'; g.fillRect(xa, top, Math.max(3, xb - xa), lh);
        g.fillStyle = COL.ev; g.textAlign = 'center'; g.fillText('#' + (n + 1), (xa + xb) / 2, top + lh - 7);
      });
      g.fillStyle = 'rgba(52,211,153,.08)'; g.fillRect(L, Y(-0.5), w - L - Rm, Y(0.5) - Y(-0.5));
      g.strokeStyle = '#2f3746'; g.beginPath(); g.moveTo(L, Y(0)); g.lineTo(w - Rm, Y(0)); g.stroke();
      g.fillStyle = '#6b7280'; g.textAlign = 'left';
      g.fillText(`${tv(AN.axis)}　${dirTxt(AN.neg)}`, L + 4, top + 8); g.fillText(dirTxt(AN.pos), L + 4, top + lh - 8);
      g.strokeStyle = COL.com; g.lineWidth = 1.5; g.beginPath(); let pen = false;
      for (let k = i0; k <= i1; k += stepN) {
        const v = F.com_obs[k];
        if (v == null) { pen = false; continue; }
        const xx = X(T[k]), yy = Y(Math.max(-1.5, Math.min(1.5, v)));
        pen ? g.lineTo(xx, yy) : g.moveTo(xx, yy); pen = true;
      }
      g.stroke();
    } else if (li === 1) {
      const vals = []; for (let k = i0; k <= i1; k += stepN) if (F.hip_h[k] != null) vals.push(F.hip_h[k]);
      if (vals.length) {
        vals.sort((a, b) => a - b); const lo = vals[Math.floor(vals.length * 0.02)], hi = vals[Math.floor(vals.length * 0.98)] + 1e-6;
        const Y = v => top + lh - 4 - (v - lo) / (hi - lo) * (lh - 8);
        g.strokeStyle = '#c4b5fd'; g.lineWidth = 1.5; g.beginPath(); let pen = false;
        for (let k = i0; k <= i1; k += stepN) {
          const v = F.hip_h[k]; if (v == null) { pen = false; continue; }
          pen ? g.lineTo(X(T[k]), Y(v)) : g.moveTo(X(T[k]), Y(v)); pen = true;
        }
        g.stroke();
      }
    } else if (li === 3) {
      const rh = lh / 4;
      ['L', 'D', 'U', 'R'].forEach((k, r) => {
        const act = F.lights[ARROW_KEYS[k]];
        g.fillStyle = '#6b7280'; g.textAlign = 'left'; g.fillText(ARROW_KEYS[k], L + 3, top + rh * r + rh / 2);
        g.fillStyle = ARROW_COL[k];
        for (let q = i0; q <= i1; q++) {
          const v = act[q]; if (!(v > 0.2)) continue;
          g.globalAlpha = Math.min(1, v); const xa = X(T[q]), xb = X(T[Math.min(q + 1, T.length - 1)]);
          g.fillRect(xa, top + rh * r + 1, Math.max(1, xb - xa + 0.5), rh - 2);
        }
        g.globalAlpha = 1;
      });
    } else {
      const rh = lh / 2;
      ['L', 'R'].forEach((s, r) => {
        g.fillStyle = '#6b7280'; g.textAlign = 'left'; g.fillText(t('short.' + s), L + 3, top + rh * r + rh / 2);
        for (const st of A.steps) {
          if (st.foot !== s) continue;
          const te = T[st.end]; if (te < t0 || st.t > t1) continue;
          g.fillStyle = PANEL_COL[st.panel] || COL[s];
          const xa = Math.max(L, X(st.t)), xb = Math.min(w - Rm, X(te));
          g.globalAlpha = st.from_light ? 0.6 : 1;
          g.fillRect(xa, top + rh * r + 2, Math.max(2, xb - xa), rh - 4); g.globalAlpha = 1;
          if (st.heel_up) { g.fillStyle = '#fff'; g.fillRect(xa, top + rh * r + 2, Math.max(2, xb - xa), 2); }
          if (xb - xa > 12) { g.fillStyle = '#0b0d12'; g.textAlign = 'center'; g.fillText(st.panel?.[0] || '', (xa + xb) / 2, top + rh * r + rh / 2); }
        }
      });
    }
  });
  g.fillStyle = '#6b7280'; g.textAlign = 'center';
  const span = t1 - t0, tick = span > 60 ? 10 : span > 20 ? 5 : 1;
  for (let tt = Math.ceil(t0 / tick) * tick; tt <= t1; tt += tick) g.fillText(fmt(tt).replace(/\.\d+$/, ''), X(tt), h - 8);
  const xp = X(T[S.fi]); g.strokeStyle = '#fff'; g.lineWidth = 1; g.beginPath(); g.moveTo(xp, 4); g.lineTo(xp, h - 18); g.stroke();
}
chartCv.addEventListener('click', e => {
  if (!S.A) return;
  const r = chartCv.getBoundingClientRect(), [t0, t1] = chartRange(), L = 64, Rm = 10;
  const tt = t0 + (e.clientX - r.left - L) / (r.width - L - Rm) * (t1 - t0);
  video.currentTime = Math.max(0, Math.min(tt, S.A.frames.t.at(-1)));
});

// ───────── 目前狀態文字 ─────────
function drawNow() {
  const A = S.A; if (!A) return; const F = A.frames, i = S.fi;
  const parts = [`<b>${fmt(F.t[i])}</b>　${t('now.frame', { i })}`];
  for (const s of ['L', 'R']) {
    const st = stepAt(s, i);
    let txt = t('now.moving');
    if (st) {
      txt = `${t('now.planted')} <b>${esc(tv(st.panel) || '-')}</b>` + (st.part ? `（${esc(tv(st.part))}）` : '') + (st.lit ? t('now.lit') : '');
      if (st.heel_lift != null) txt += '・' + (st.heel_up ? t('now.heelUp', { h: st.heel_lift }) : t('now.heelDown'));
      if (st.conf) txt += '・' + t('now.conf', { c: t('conf.' + st.conf) });
    }
    parts.push(`${t('foot.' + s)}：${txt}`);
  }
  const o = F.com_obs[i], AN = A.view.com_axis_names;
  if (o != null && AN) parts.push(`${t('now.com', { axis: tv(AN.axis) })}<b>${dirTxt(o > 0 ? AN.pos : AN.neg)} ${Math.abs(o).toFixed(2)} ${t('unit.panel')}</b>` +
    (F.com_rel[i] != null ? t('now.rel', { d: dirTxt(F.com_rel[i] > 0 ? AN.pos : AN.neg), v: Math.abs(F.com_rel[i]).toFixed(2) }) : ''));
  if (F.lights) { const on = 'LDUR'.split('').filter(k => F.lights[ARROW_KEYS[k]][i] > 0.4).map(k => ARROW_KEYS[k]); parts.push(`${t('now.lights')}：<b>${on.join(' ') || '—'}</b>`); }
  const kn = ['L', 'R'].map(s => F.knee[s][i]).filter(v => v != null);
  if (kn.length && A.view.side_view) parts.push(`${t('now.knee')} ${kn.map(v => v.toFixed(0) + '°').join(' / ')}`);
  $('#nowInfo').innerHTML = parts.join('<br>');
}

// ───────── 建議／事件／落點／指標 ─────────
function renderAdvice() {
  const A = S.A;
  $('#tab-advice').innerHTML = (A.advice.length ? A.advice.map(a => `
    <div class="adv ${a.level}">
      <div class="t"><span class="lv ${a.level}">${t('lv.' + a.level)}</span>${esc(L(a.title))}<span class="area">${esc(tv(a.area))}</span></div>
      <div class="ev">${esc(L(a.evidence))}</div>
      <div class="fx"><span class="fxl">${t('adv.fix')}</span>${esc(L(a.fix))}</div>
    </div>`).join('') : `<p class="note">${t('adv.none')}</p>`) +
    `<p class="note">${t('adv.note')}</p>`;
}

function renderEvents() {
  const A = S.A, E = A.events || [], ST = A.event_stats || {}, AN = A.view.com_axis_names;
  if (!E.length) { $('#tab-events').innerHTML = `<p class="note">${t('ev.none')}</p>`; return; }
  let html = `<p class="note" style="margin-top:0">${t('ev.desc', { axis: tv(AN ? AN.axis : '-'), b: ST.baseline_cm ?? '-', n: E.length })}</p>`;
  html += '<div class="evlist">' + E.map((e, n) => {
    const causes = e.causes.length ? e.causes.map(c => `<span class="chip">${t('ev.c.' + c)}</span>`).join('') : `<span class="chip dim">${t('ev.noCause')}</span>`;
    const seq = e.seq.map(s => `<span class="sq ${s.foot}">${s.foot === 'L' ? t('short.L') : t('short.R')}${esc(s.panel || '')}${s.heel_up ? '˄' : ''}</span>`).join('');
    const sel = S.ev && S.ev.frame === e.frame ? ' sel' : '';
    return `<div class="evrow${sel}" data-k="${n}">
      <div class="evh"><b>#${n + 1}</b> ${fmt(e.t)}　${dirTxt(e.dir === 'pos' ? AN.pos : AN.neg)} <b>${e.dev_cm} cm</b>　<span class="dim">${t('ev.type.' + e.type)}</span></div>
      <div>${causes}</div><div class="seq">${seq}</div></div>`;
  }).join('') + '</div>';
  if (ST.causes && Object.keys(ST.causes).length) {
    html += `<h2 style="margin-top:14px">${t('ev.causeTitle')}</h2><table>` +
      Object.entries(ST.causes).sort((a, b) => b[1] - a[1]).map(([c, v]) => `<tr><td>${t('ev.c.' + c)}</td><td>${v}${t('u.times')}</td></tr>`).join('') +
      `<tr><td colspan="2" class="dim">${t('ev.types', { a: ST.types?.travel ?? 0, b: ST.types?.balance ?? 0 })}</td></tr></table>`;
  }
  if (ST.patterns && ST.patterns.length) {
    html += `<h2 style="margin-top:14px">${t('ev.statsTitle')}</h2><p class="note" style="margin-top:0">${t('ev.statsDesc')}</p><table>` +
      ST.patterns.map(p => `<tr><td class="pat">${esc(p.pattern)}</td><td>${t('ev.pat', { cm: p.mean_cm, r: p.ratio, n: p.n })}</td></tr>`).join('') + '</table>';
  }
  $('#tab-events').innerHTML = html;
  document.querySelectorAll('.evrow').forEach(row => row.onclick = () => selectEvent(E[+row.dataset.k], +row.dataset.k));
}
function selectEvent(e, k) {
  S.ev = { ...e, k };
  video.pause();
  $('#rate').value = '0.25'; video.playbackRate = 0.25;
  video.currentTime = Math.max(0, e.t0 - 0.6);
  renderEvents(); renderEvSel(); redraw();
  video.play();
}
function renderEvSel() {
  const on = !!S.ev; $('#evSel').hidden = !on;
  if (on) $('#evSelLbl').textContent = t('ev.selected', { k: S.ev.k + 1 });
}
$('#evClear').onclick = () => { S.ev = null; renderEvents(); renderEvSel(); redraw(); };

function renderMetrics() {
  const M = S.A.metrics, AN = S.A.view.com_axis_names;
  const pos = AN ? t('u.posAxis', { x: tv(AN.pos) }) : '';
  const rows = [
    [t('m.duration'), M.duration_s, t('u.sec')], [t('m.steps'), M.steps, ''], [t('m.rate'), M.steps_per_s, t('u.sps')],
    [t('m.comAxis'), M.com_axis ? tv(M.com_axis) : null, ''],
    [t('m.comSway'), M.com_sway, t('u.panel') + (M.com_sway_cm != null ? `（≈${M.com_sway_cm} cm）` : '')],
    [t('m.comBias'), M.com_bias, t('u.panel') + pos], [t('m.comRel'), M.com_rel_feet, t('u.panel') + pos],
    [t('m.comCenter'), M.com_center_pct, ' %'], [t('m.events'), M.events, t('u.events')],
    [t('m.bounce'), M.bounce_pct, t('u.pctLeg')], [t('m.liftMed'), M.lift_cm_median, ' cm'], [t('m.liftP90'), M.lift_cm_p90, ' cm'],
    [t('m.liftL'), M.lift_cm_L, ' cm'], [t('m.liftR'), M.lift_cm_R, ' cm'], [t('m.knee'), M.knee_stance_deg, '°'],
    [t('m.heelUp'), M.heel_up_pct, ' %'], [t('m.edge'), M.edge_pct, ' %'], [t('m.off'), M.offpanel_pct, ' %'],
  ].filter(r => r[1] != null);
  let html = '<table>' + rows.map(([k, v, u]) => `<tr><td>${k}</td><td>${esc(v)}${u}</td></tr>`).join('') + '</table>';
  if (M.hand_hold) html += `<p class="note">${t('m.hold', { l: M.hand_hold.L ?? '-', r: M.hand_hold.R ?? '-' })}</p>`;
  $('#tab-metrics').innerHTML = html;
}
function renderSteps() {
  const M = S.A.metrics; let html = '';
  if (M.lit_steps) {
    const C = S.A.calibration;
    html += `<h2>${t('s.litTitle')}</h2><table>
      <tr><td>${t('s.litSteps')}</td><td>${M.lit_steps}${t('u.steps')}</td></tr>
      <tr><td>${t('s.lightOnly')}</td><td>${M.light_only_steps}${t('u.steps')}</td></tr>
      <tr><td>${t('s.agree')}</td><td>${M.lit_agree_pct}%</td></tr>
      <tr><td>${t('s.untouched')}</td><td>${M.lit_untouched}${t('u.steps')}</td></tr>
      ${S.A.shoeLen ? `<tr><td>${t('s.shoe')}</td><td>${(S.A.shoeLen * 28).toFixed(0)} cm</td></tr>` : ''}
      ${C.foot_ext ? `<tr><td>${t('s.footExt')}</td><td>${Math.round(C.foot_ext[0] * 100)}% / ${Math.round(C.foot_ext[1] * 100)}%</td></tr>` : ''}
      ${C.fit ? `<tr><td>${t('s.fit')}</td><td>${C.fit[0]}% → ${C.fit[1]}%</td></tr>` : ''}
    </table>`;
  } else if (M.press_part) html += `<h2>${t('s.partTitle')}</h2>`;   // 踏板不亮燈：部位只靠骨架
  if (M.press_part) html += '<table>' + Object.entries(M.press_part).map(([k, v]) =>
    `<tr><td>${t('s.partOf', { a: k })}</td><td>${Object.entries(v).sort((a, b) => b[1] - a[1]).map(([p, n]) => `${esc(tv(p))} ${n}`).join('・')}</td></tr>`).join('') + '</table>';
  if (M.lit_steps) {
    if (M.lit_mismatch && Object.keys(M.lit_mismatch).length) html += '<table>' + Object.entries(M.lit_mismatch).map(([k, v]) => {
      const m = k.match(/^亮 (\S+)／骨架 (.+)$/);
      return `<tr><td>${m ? t('s.mm', { a: m[1], b: esc(tv(m[2])) }) : esc(k)}</td><td>${v}${t('u.times')}</td></tr>`;
    }).join('') + `</table><p class="note">${t('s.mmNote')}</p>`;
  }
  if (M.heel_up_by_arrow) {
    html += `<h2 style="margin-top:12px">${t('s.heelTitle')}</h2><table>` + Object.entries(M.heel_up_by_arrow).map(([k, v]) =>
      `<tr><td>${k}</td><td>${t('s.heelRow', { p: v.pct, n: v.n })}</td></tr>`).join('') + '</table>';
  }
  if (M.conf_counts) {
    const c = M.conf_counts;
    html += `<table><tr><td>${t('s.conf')}</td><td>${t('s.confRow', { h: c.high, m: c.medium, l: c.low })}</td></tr></table><p class="note">${t('s.confNote')}</p>`;
  }
  if (M.arrow_spread) {
    html += `<table style="margin-top:12px"><tr><td>${t('s.arrow')}</td><td>${t('s.spreadHead')}</td></tr>` +
      Object.entries(M.arrow_spread).map(([k, v]) => `<tr><td>${k}</td><td>${v.n}${t('u.steps')}・±${v.spread_cm} cm・(${v.offset[0] > 0 ? '+' : ''}${v.offset[0]}, ${v.offset[1] > 0 ? '+' : ''}${v.offset[1]})</td></tr>`).join('') + '</table>';
    html += `<p class="note">${t('s.spreadNote')}</p>`;
  }
  const cnt = M.panel_counts || M.row_counts;
  if (cnt) html += `<table><tr><td colspan="2">${t('s.counts')}</td></tr>` + Object.entries(cnt).map(([k, v]) => `<tr><td>${esc(tv(k))}</td><td>${v}${t('u.steps')}</td></tr>`).join('') + '</table>';
  $('#tab-steps').innerHTML = html || `<p class="note">${t('s.none')}</p>`;
}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('.tab').forEach(x => x.hidden = x.id !== 'tab-' + b.dataset.tab);
});

// ───────── 播放控制 ─────────
function tick() {
  if (S.A && video.readyState >= 1) {
    const i = frameAt(video.currentTime);
    if (i !== S.fi || S.dirty) {
      S.fi = i; S.dirty = false;
      drawOverlay(); drawPad(); drawCharts(); drawNow();
      $('#timeLbl').textContent = fmt(video.currentTime);
      if (video.duration) $('#seek').value = Math.round(video.currentTime / video.duration * 1000);
    }
    // 事件慢放：播到事件結束後 0.6 秒自動暫停
    if (S.ev && !video.paused && video.currentTime > S.ev.t1 + 0.6) video.pause();
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
const redraw = () => { S.dirty = true; };
window.addEventListener('resize', redraw);
video.addEventListener('loadedmetadata', redraw);
video.addEventListener('play', () => $('#playBtn').textContent = '⏸');
video.addEventListener('pause', () => $('#playBtn').textContent = '▶');
$('#playBtn').onclick = () => video.paused ? video.play() : video.pause();
const step = n => { video.pause(); const T = S.A.frames.t; const j = Math.max(0, Math.min(T.length - 1, S.fi + n)); video.currentTime = T[j] + 0.001; };
$('#prevF').onclick = () => step(-1); $('#nextF').onclick = () => step(1);
$('#seek').oninput = e => { if (video.duration) video.currentTime = e.target.value / 1000 * video.duration; };
$('#rate').onchange = e => { video.playbackRate = +e.target.value; };
document.querySelectorAll('[data-layer]').forEach(c => c.onchange = () => { S.layers[c.dataset.layer] = c.checked; redraw(); });
document.querySelectorAll('[name=win]').forEach(r => r.onchange = () => { S.win = +r.value; redraw(); });
$('#showAll').onchange = e => { S.showAll = e.target.checked; redraw(); };
document.addEventListener('keydown', e => {
  if (!S.A || e.target.matches('input[type=text], input:not([type]), select, textarea') || $('#addDlg').open) return;
  if (e.code === 'Space') { e.preventDefault(); $('#playBtn').click(); }
  else if (e.code === 'ArrowLeft') { e.preventDefault(); e.shiftKey ? (video.currentTime -= 1) : step(-1); }
  else if (e.code === 'ArrowRight') { e.preventDefault(); e.shiftKey ? (video.currentTime += 1) : step(1); }
});

// ───────── 手動校正 ─────────
function setCalibUI(on) {
  $('#calibBar').hidden = !on; overlay.classList.toggle('calib', on);
  $('#calibBtn').textContent = on ? t('calib.active') : t('calib.btn');
}
$('#calibBtn').onclick = () => {
  if (!S.A || S.calib) return;
  video.pause();
  const P = S.A.calibration.points;
  let C;
  if (P && S.A.view.mode === 'homography') {
    const Hp = padH();
    C = PAD_CORNERS.map(q => ap3(Hp, q));
  } else {
    const m = S.A.meta, F = S.A.frames, gl = F.ground.L[S.fi], gr = F.ground.R[S.fi];
    const c = gl && gr ? [(gl[0] + gr[0]) / 2, (gl[1] + gr[1]) / 2] : [m.width / 2, m.height * 0.8];
    const r = m.width * 0.18;
    C = [[c[0] - r, c[1] - r * 0.5], [c[0] + r, c[1] - r * 0.5], [c[0] + r, c[1] + r * 0.5], [c[0] - r, c[1] + r * 0.5]].map(toRef);
  }
  S.calib = { C, pick: null, footOff: (S.A.calibration.foot_offset || [0, 0]).slice() };
  setCalibUI(true); setClickPrompt(); redraw();
};
$('#calibRotate').onclick = () => { if (S.calib) { const C = S.calib.C; S.calib.C = [C[3], C[0], C[1], C[2]]; redraw(); } };

// 箭頭中心（參考幀座標）⇄ 九宮格四角
const CLICK_ORDER = ['L', 'D', 'U', 'R'];
const arrowCenters = C => { const Hc = homog(PAD_CORNERS, C); return Object.fromEntries(CLICK_ORDER.map(k => [k, ap3(Hc, ARROW_POS[k])])); };
const cornersFromCenters = P => { const Hp = homog(CLICK_ORDER.map(k => ARROW_POS[k]), CLICK_ORDER.map(k => P[k])); return PAD_CORNERS.map(q => ap3(Hp, q)); };
// 對應點：踏板上的已知位置（16 個格線交點＋9 個板中心），在俯視圖選、在影片上點
const PICK_TARGETS = [];
for (const x of [-1.5, -0.5, 0.5, 1.5]) for (const y of [-1.5, -0.5, 0.5, 1.5]) PICK_TARGETS.push([x, y]);
for (const x of [-1, 0, 1]) for (const y of [-1, 0, 1]) PICK_TARGETS.push([x, y]);
const DEFAULT_PICKS = CLICK_ORDER.map(k => ARROW_POS[k]);
const tkey = p => p.join(',');
function nextDefaultTarget(pk) { return DEFAULT_PICKS.find(p => !pk.pairs[tkey(p)]) || null; }
// 最小平方單應矩陣（n ≥ 4，含座標正規化）：src → dst
function homogLSQ(src, dst) {
  const norm = P => {
    const m = [P.reduce((a, p) => a + p[0], 0) / P.length, P.reduce((a, p) => a + p[1], 0) / P.length];
    const d = P.reduce((a, p) => a + Math.hypot(p[0] - m[0], p[1] - m[1]), 0) / P.length || 1, s = Math.SQRT2 / d;
    return { T: [s, 0, -s * m[0], 0, s, -s * m[1], 0, 0, 1], P: P.map(p => [(p[0] - m[0]) * s, (p[1] - m[1]) * s]) };
  };
  const a = norm(src), b = norm(dst), N = Array.from({ length: 8 }, () => new Array(9).fill(0));
  const add = (row, rhs) => { for (let i = 0; i < 8; i++) { for (let j = 0; j < 8; j++) N[i][j] += row[i] * row[j]; N[i][8] += row[i] * rhs; } };
  a.P.forEach(([x, y], k) => { const [u, v] = b.P[k]; add([x, y, 1, 0, 0, 0, -u * x, -u * y], u); add([0, 0, 0, x, y, 1, -v * x, -v * y], v); });
  for (let c = 0; c < 8; c++) {
    let piv = c; for (let r = c + 1; r < 8; r++) if (Math.abs(N[r][c]) > Math.abs(N[piv][c])) piv = r;
    [N[c], N[piv]] = [N[piv], N[c]];
    if (Math.abs(N[c][c]) < 1e-10) return null;
    for (let r = 0; r < 8; r++) if (r !== c) { const f = N[r][c] / N[c][c]; for (let k = c; k < 9; k++) N[r][k] -= f * N[c][k]; }
  }
  const hn = [...N.map((row, i) => row[8] / row[i]), 1];
  const mul = (A, B) => [0, 1, 2].flatMap(i => [0, 1, 2].map(j => A[i * 3] * B[j] + A[i * 3 + 1] * B[3 + j] + A[i * 3 + 2] * B[6 + j]));
  return mul(mul(inv3(b.T), hn), a.T);
}
function setClickPrompt() {
  const pk = S.calib && S.calib.pick;
  $('#calibPickDone').hidden = !pk;
  if (!pk) { $('#calibHint').innerHTML = t('calib.hint'); return; }
  const n = Object.keys(pk.pairs).length;
  $('#calibHint').innerHTML = t('calib.pickPrompt', { n });
  $('#calibPickDone').textContent = t('calib.pickDone', { n });
  $('#calibPickDone').disabled = n < 4;
}
$('#calibClick').onclick = () => {
  if (!S.calib) return;
  video.pause(); S.calib.pick = { sel: null, pairs: {} }; setClickPrompt(); redraw();
};
$('#calibPickDone').onclick = () => {
  const pk = S.calib && S.calib.pick; if (!pk) return;
  const keys = Object.keys(pk.pairs); if (keys.length < 4) return;
  const src = keys.map(k => k.split(',').map(Number)), dst = keys.map(k => pk.pairs[k]);
  // 共線檢查：踏板座標的散佈要有兩個方向
  const mx = src.reduce((a, p) => a + p[0], 0) / src.length, my = src.reduce((a, p) => a + p[1], 0) / src.length;
  let sxx = 0, syy = 0, sxy = 0; for (const [x, y] of src) { sxx += (x - mx) ** 2; syy += (y - my) ** 2; sxy += (x - mx) * (y - my); }
  const H = homogLSQ(src, dst);
  if (!H || (sxx * syy - sxy * sxy) / (src.length ** 2) < 0.02) { alert(t('calib.pickBad')); return; }
  S.calib.C = PAD_CORNERS.map(q => ap3(H, q)); S.calib.pick = null; setClickPrompt(); redraw();
};
document.addEventListener('keydown', e => {
  if (e.code === 'Escape' && S.calib && S.calib.pick) { S.calib.pick = null; setClickPrompt(); redraw(); }
});
// 校正中拖曳俯視圖：只移動「腳的位置」（手動偏移，九宮格與亮燈判定區不動）
padCv.addEventListener('pointerdown', e => {
  if (!S.calib || S.calib.pick) return;
  const r = padCv.getBoundingClientRect();
  S.padDrag = { x: e.clientX, y: e.clientY, o0: S.calib.footOff.slice(), sc: r.width / 4.2 };
  try { padCv.setPointerCapture(e.pointerId); } catch (_) { /* 模擬事件等 */ }
});
padCv.addEventListener('pointermove', e => {
  const d = S.padDrag; if (!d) return;
  const [dx, dy] = unrotPad((e.clientX - d.x) / d.sc, (e.clientY - d.y) / d.sc);
  S.calib.footOff = [d.o0[0] + dx, d.o0[1] + dy];
  redraw();
});
padCv.addEventListener('pointerup', () => { S.padDrag = null; });
// 在俯視圖上選要對應的點
padCv.addEventListener('pointerdown', e => {
  const pk = S.calib && S.calib.pick; if (!pk) return;
  const r = padCv.getBoundingClientRect(), w = r.width, sc = w / 4.2;
  const [px, py] = unrotPad((e.clientX - r.left - w / 2) / sc, (e.clientY - r.top - w / 2) / sc);
  let best = null, bd = 0.35;
  for (const p of PICK_TARGETS) { const d = Math.hypot(p[0] - px, p[1] - py); if (d < bd) { bd = d; best = p; } }
  if (best) { pk.sel = best; redraw(); }
});

function localPt(e) {                                   // 螢幕座標 → 未縮放的 overlay 座標
  const r = overlay.getBoundingClientRect();
  return [(e.clientX - r.left) / Z.k, (e.clientY - r.top) / Z.k];
}
function insideQuad(pt, Q) {
  let inside = false;
  for (let i = 0, j = 3; i < 4; j = i++) {
    const [xi, yi] = Q[i], [xj, yj] = Q[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
overlay.addEventListener('pointerdown', e => {
  if (!S.A) return;
  const M = vmap(), [x, y] = localPt(e);
  S.drag = null;
  if (S.calib && S.calib.pick) {
    // 對應點：目前選的踏板點（沒選就依序用 ← ↓ ↑ → 中心）＝影片上點的位置（換算成參考幀）
    const pk = S.calib.pick, now = performance.now();
    if (now - (pk.last || 0) < 350) return;            // 雙擊（復原縮放）不算兩個點
    pk.last = now;
    const tgt = pk.sel || nextDefaultTarget(pk);
    if (!tgt) return;
    pk.pairs[tkey(tgt)] = toRef(M.inv(x, y)); pk.sel = null;
    setClickPrompt(); redraw(); return;
  }
  if (S.calib) {
    const Q = S.calib.C.map(q => M.f(fromRef(q)));
    const AC = arrowCenters(S.calib.C);
    let best = null, bd = 18 / Z.k;
    for (const k of CLICK_ORDER) { const [px, py] = M.f(fromRef(AC[k])); const d = Math.hypot(px - x, py - y); if (d < bd) { bd = d; best = { type: 'center', k }; } }
    Q.forEach(([px, py], k) => { const d = Math.hypot(px - x, py - y); if (d < bd) { bd = d; best = { type: 'corner', k }; } });
    if (best) S.drag = best;
    else if (insideQuad([x, y], Q)) S.drag = { type: 'move', last: toRef(M.inv(x, y)) };
  }
  if (!S.drag && Z.k > 1.01) S.drag = { type: 'pan', sx: e.clientX, sy: e.clientY, zx: Z.x, zy: Z.y };
  if (S.drag) try { overlay.setPointerCapture(e.pointerId); } catch (_) { /* 模擬事件等 */ }
});
overlay.addEventListener('pointermove', e => {
  if (!S.drag) return;
  const M = vmap(), [x, y] = localPt(e), d = S.drag;
  if (d.type === 'corner') S.calib.C[d.k] = toRef(M.inv(x, y));
  else if (d.type === 'center') { const AC = arrowCenters(S.calib.C); AC[d.k] = toRef(M.inv(x, y)); S.calib.C = cornersFromCenters(AC); }
  else if (d.type === 'move') {
    const cur = toRef(M.inv(x, y)), dx = cur[0] - d.last[0], dy = cur[1] - d.last[1];
    S.calib.C = S.calib.C.map(([u, v]) => [u + dx, v + dy]); d.last = cur;
  } else if (d.type === 'pan') { Z.x = d.zx + e.clientX - d.sx; Z.y = d.zy + e.clientY - d.sy; applyZoom(); return; }
  redraw();
});
overlay.addEventListener('pointerup', () => { S.drag = null; });
overlay.addEventListener('wheel', e => {
  if (!S.A) return;
  e.preventDefault();
  const r = $('#stage').getBoundingClientRect(), cx = e.clientX - r.left, cy = e.clientY - r.top;
  const k = Math.min(8, Math.max(1, Z.k * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
  const lx = (cx - Z.x) / Z.k, ly = (cy - Z.y) / Z.k;
  Z.k = k; Z.x = k === 1 ? 0 : cx - lx * k; Z.y = k === 1 ? 0 : cy - ly * k; applyZoom();
}, { passive: false });
overlay.addEventListener('dblclick', () => { Z.k = 1; Z.x = Z.y = 0; applyZoom(); });
$('#calibCancel').onclick = () => { S.calib = null; setCalibUI(false); setClickPrompt(); redraw(); };
$('#calibApply').onclick = async () => {
  if (S.calib.pick) return;                              // 對應點還沒按「完成」
  const Hc = homog(PAD_CORNERS, S.calib.C), points = {};
  for (const k of 'LDUR') points[k] = ap3(Hc, ARROW_POS[k]);
  $('#calibApply').textContent = t('calib.applying');
  const r = await fetch(`/api/videos/${S.id}/calibration`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ points, foot_offset: S.calib.footOff }) });
  $('#calibApply').textContent = t('calib.apply');
  if (!r.ok) { alert(t('calib.fail') + ((await r.json()).detail || r.status)); return; }
  S.calib = null; S.ev = null; setCalibUI(false); setAnalysis(await r.json());
};
$('#calibReset').onclick = async () => {
  const r = await fetch(`/api/videos/${S.id}/calibration`, { method: 'DELETE' });
  S.calib = null; S.ev = null; setCalibUI(false); if (r.ok) setAnalysis(await r.json());
};

// ───────── 新增影片 ─────────
$('#addBtn').onclick = () => { $('#addErr').textContent = ''; $('#addDlg').showModal(); };
$('#addGo').onclick = async e => {
  e.preventDefault();
  const file = $('#addFile').files[0], path = $('#addPath').value.trim(), q = $('#addQ').value;
  let r;
  try {
    if (file) {
      const fd = new FormData(); fd.append('file', file); fd.append('quality', q);
      $('#addErr').textContent = t('dlg.uploading');
      r = await fetch('/api/upload', { method: 'POST', body: fd });
    } else if (path) {
      r = await fetch('/api/videos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path, quality: q }) });
    } else { $('#addErr').textContent = t('dlg.need'); return; }
    if (!r.ok) { $('#addErr').textContent = (await r.json()).detail || t('dlg.fail'); return; }
    $('#addDlg').close(); loadList();
  } catch (err) { $('#addErr').textContent = String(err); }
};

// ───────── 伺服器存活回報（關掉所有分頁後伺服器會自動結束） ─────────
const CID = Math.random().toString(36).slice(2);
let hbFail = 0;
async function heartbeat() {
  try {
    const r = await fetch(`/api/heartbeat?id=${CID}`, { method: 'POST' });
    hbFail = r.ok ? 0 : hbFail + 1;
  } catch (e) { hbFail++; }
  $('#offline').hidden = hbFail < 2;
}
heartbeat(); setInterval(heartbeat, 4000);
window.addEventListener('pagehide', () => navigator.sendBeacon(`/api/bye?id=${CID}`));

// ───────── 語言 ─────────
$('#langSel').value = LANG;
$('#langSel').onchange = e => { setLang(e.target.value); setCalibUI(!!S.calib); renderList(); renderAll(); };
applyI18n();
loadList(false);
