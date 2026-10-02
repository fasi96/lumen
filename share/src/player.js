// The share page's "studio" player: video on the left with our own controls,
// live transcript on the right (click a line to jump, current line follows the
// video). Chapters show as segments on the progress bar; captions are drawn from
// the same transcript. Without a transcript it falls back to a single column.
//
// Data the page loads: /v/:id/transcript.json →
//   { lang, cues: [{ s, e, text }], chapters?: [{ t, title }] }


const svg = d => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`
const I = {
  play: `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z"/></svg>`,
  pause: `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>`,
  vol: svg('<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/>'),
  mute: svg('<path d="M11 5 6 9H3v6h3l5 4z"/><path d="m22 9-6 6"/><path d="m16 9 6 6"/>'),
  full: svg('<path d="M4 9V4h5"/><path d="M20 9V4h-5"/><path d="M4 15v5h5"/><path d="M20 15v5h-5"/>'),
  cc: svg('<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M10 10.5a2 2 0 1 0 0 3"/><path d="M16.5 10.5a2 2 0 1 0 0 3"/>'),
  spark: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 1.5l2.6 7.9 7.9 2.6-7.9 2.6L12 22.5l-2.6-7.9L1.5 12l7.9-2.6z"/></svg>`,
}

export const STUDIO_CSS = `
  :root {
    --bg:#0d0e10; --surface:#15171a; --raise:#1c1f23; --line:#26292e; --text:#eceded; --muted:#8b9096;
    --accent:#8b8cf6; --accent-soft:rgba(139,140,246,.14); --shadow:0 18px 60px -20px rgba(0,0,0,.7);
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f5f5f3; --surface:#ffffff; --raise:#f0f0ee; --line:#e2e2df; --text:#18191b; --muted:#6c7075;
      --accent:#5b5cf0; --accent-soft:rgba(91,92,240,.1); --shadow:0 18px 50px -24px rgba(0,0,0,.25); }
  }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.5 "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:1480px; margin:0 auto; padding:28px 28px 56px; }
  .head { display:flex; align-items:flex-end; justify-content:space-between; gap:16px; margin-bottom:18px; flex-wrap:wrap; }
  .head h1 { font-size:22px; line-height:1.25; margin:0; font-weight:650; letter-spacing:-.01em; }
  .head .meta { color:var(--muted); font-size:13px; margin-top:4px; }
  .head .actions { display:flex; gap:8px; }
  .btn { font:inherit; font-size:13px; color:var(--text); background:var(--surface); border:1px solid var(--line);
    border-radius:9px; padding:7px 13px; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; gap:6px; }
  .btn:hover { border-color:var(--muted); }

  /* The video column never grows taller than the window (title + controls included),
     so the whole player is always visible without scrolling. */
  .wrap { --fit:calc((100vh - 250px) * 16 / 9); }
  .studio { display:grid; gap:22px; align-items:start; justify-content:center;
    grid-template-columns:minmax(0, min(var(--fit), 100%)) 380px; }
  .studio.solo { grid-template-columns:minmax(0, min(var(--fit), 1200px)); }
  .head { max-width:calc(var(--fit) + 402px); margin-inline:auto; }
  .wrap:has(.solo) .head { max-width:min(var(--fit), 1200px); }

  .stage { background:var(--surface); border:1px solid var(--line); border-radius:16px; overflow:hidden; box-shadow:var(--shadow); }
  .screen { position:relative; background:#000; aspect-ratio:16/9; cursor:pointer; }
  .screen video { position:absolute; inset:0; width:100%; height:100%; object-fit:contain; display:block; }
  .bigplay { position:absolute; left:50%; top:50%; width:74px; height:74px; margin:-37px 0 0 -37px; border-radius:50%;
    border:0; background:rgba(255,255,255,.92); color:#111; font-size:26px; cursor:pointer; display:grid; place-items:center;
    box-shadow:0 10px 30px rgba(0,0,0,.4); transition:transform .15s, opacity .2s; padding-left:4px; }
  .bigplay:hover { transform:scale(1.06); }
  .screen.playing .bigplay { opacity:0; pointer-events:none; }
  .cap { position:absolute; left:50%; bottom:5%; transform:translateX(-50%); max-width:86%; text-align:center;
    background:rgba(0,0,0,.72); color:#fff; font-size:clamp(14px,2.1vw,22px); line-height:1.35; padding:5px 12px; border-radius:8px;
    pointer-events:none; unicode-bidi:plaintext; }
  .cap:empty { display:none; }

  .controls { padding:12px 16px 14px; }
  .track { position:relative; height:22px; cursor:pointer; touch-action:none; }
  .rail { position:absolute; left:0; right:0; top:9px; height:4px; display:flex; gap:3px; }
  .seg { position:relative; flex:1; height:100%; background:var(--line); border-radius:2px; overflow:hidden; transition:height .12s, margin .12s; }
  .track:hover .seg { height:6px; margin-top:-1px; }
  .seg .buf { position:absolute; inset:0 auto 0 0; background:color-mix(in srgb, var(--muted) 35%, transparent); }
  .seg .fill { position:absolute; inset:0 auto 0 0; background:var(--accent); }
  .knob { position:absolute; top:5px; width:12px; height:12px; margin-left:-6px; border-radius:50%; background:var(--accent);
    box-shadow:0 0 0 4px var(--accent-soft); opacity:0; transition:opacity .12s; pointer-events:none; }
  .track:hover .knob, .track.drag .knob { opacity:1; }
  .tip { position:absolute; bottom:24px; transform:translateX(-50%); background:var(--raise); border:1px solid var(--line);
    color:var(--text); font-size:12px; padding:3px 8px; border-radius:6px; white-space:nowrap; pointer-events:none; display:none; }
  .track:hover .tip { display:block; }
  .chapters { display:flex; gap:3px; margin-top:2px; font-size:11.5px; color:var(--muted); }
  .chapters span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; cursor:pointer; }
  .chapters span.on { color:var(--text); }
  .row { display:flex; align-items:center; gap:10px; margin-top:8px; }
  .icon { width:34px; height:34px; border-radius:9px; border:0; background:transparent; color:var(--text); cursor:pointer;
    display:grid; place-items:center; font-size:15px; }
  .icon:hover { background:var(--raise); }
  .icon.on { color:var(--accent); }
  .time { font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; min-width:92px; }
  .grow { flex:1; }
  .speeds { display:flex; background:var(--raise); border-radius:9px; padding:2px; }
  .speeds button { border:0; background:transparent; color:var(--muted); font:inherit; font-size:12.5px; padding:5px 9px;
    border-radius:7px; cursor:pointer; font-variant-numeric:tabular-nums; }
  .speeds button.on { background:var(--surface); color:var(--text); box-shadow:0 1px 3px rgba(0,0,0,.25); }

  .side { background:var(--surface); border:1px solid var(--line); border-radius:16px; display:flex; flex-direction:column;
    overflow:hidden; box-shadow:var(--shadow); }
  .side header { padding:14px 16px 10px; border-bottom:1px solid var(--line); display:flex; align-items:center; gap:10px; }
  .side header b { font-size:12px; letter-spacing:.08em; color:var(--muted); font-weight:600; }
  .side input { flex:1; min-width:0; font:inherit; font-size:13px; background:var(--raise); color:var(--text);
    border:1px solid transparent; border-radius:8px; padding:6px 10px; }
  .side input:focus { outline:none; border-color:var(--line); }
  .lines { overflow-y:auto; padding:8px; flex:1; scroll-behavior:smooth; }
  .line { display:grid; grid-template-columns:44px 1fr; gap:8px; padding:7px 8px; border-radius:9px; cursor:pointer; }
  .line:hover { background:var(--raise); }
  .line .t { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; padding-top:2px; }
  .line .x { unicode-bidi:plaintext; text-align:start; color:var(--muted); transition:color .15s; }
  .line.past .x { color:color-mix(in srgb, var(--text) 70%, var(--muted)); }
  .line.now { background:var(--accent-soft); }
  .line.now .x { color:var(--text); }
  .line.now .t { color:var(--accent); }
  .line.hide { display:none; }
  .line mark { background:color-mix(in srgb, var(--accent) 35%, transparent); color:inherit; border-radius:3px; }
  .chap { font-size:11px; letter-spacing:.08em; color:var(--muted); padding:12px 8px 4px; text-transform:uppercase; }
  .keys { color:var(--muted); font-size:12px; margin-top:16px; text-align:center; }

  /* product mark, top-left like any hosted video page */
  .brand { display:flex; align-items:center; gap:8px; font-weight:700; font-size:15px; letter-spacing:-.01em; color:var(--text);
    margin:0 auto 18px; max-width:calc(var(--fit) + 402px); opacity:.9; }
  .brand svg { width:22px; height:22px; padding:4px; border-radius:7px; background:linear-gradient(135deg, var(--accent), #e879f9); color:#fff; }
  .wrap:has(.solo) .brand { max-width:min(var(--fit), 1200px); }

  /* who made it */
  .who { display:flex; gap:14px; align-items:flex-start; min-width:0; }
  .avatar { flex:none; width:44px; height:44px; border-radius:50%; display:grid; place-items:center; color:#fff;
    font-weight:700; font-size:18px; background:linear-gradient(135deg, var(--accent), #e879f9); box-shadow:0 6px 18px -6px var(--accent); }
  .head .meta b { color:var(--text); font-weight:600; }
  .summary { color:var(--muted); margin:8px 0 0; max-width:760px; font-size:14px; line-height:1.55; }

  /* what the clean-up removed */
  .clean { display:inline-flex; align-items:center; gap:6px; margin-left:10px; padding:2px 10px 2px 8px; border-radius:20px;
    background:var(--accent-soft); color:var(--accent); font-size:12.5px; font-weight:600; vertical-align:1px; cursor:default; }
  .clean svg { flex:none; }
  .marks { position:absolute; left:0; right:0; top:0; height:8px; pointer-events:none; }
  .mark { position:absolute; top:0; width:6px; height:6px; margin-left:-3px; border-radius:50%; background:var(--accent);
    box-shadow:0 0 0 2px var(--surface); }
  .mark.pause { background:var(--muted); width:4px; height:4px; margin-left:-2px; top:1px; }
  .um { display:inline-block; font-size:11px; line-height:16px; color:var(--muted); border:1px dashed color-mix(in srgb, var(--muted) 45%, transparent);
    border-radius:6px; padding:0 5px; margin:0 3px; text-decoration:line-through; opacity:.75; vertical-align:1px; }

  /* hover preview frame */
  .tip { text-align:center; padding:4px; }
  .tip .frame { display:none; width:160px; height:90px; border-radius:5px; background:#000 no-repeat; margin-bottom:3px; }
  .tip.has-frame .frame { display:block; }
  .tip span { display:block; padding:0 4px; }

  /* end screen + loading */
  .end { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:14px;
    background:rgba(8,9,12,.62); backdrop-filter:blur(8px); color:#fff; cursor:default; animation:fade .25s ease; }
  .end[hidden] { display:none; }
  .end p { margin:0; font-size:18px; font-weight:600; }
  .end .end-hint { font-size:12.5px; font-weight:400; color:rgba(255,255,255,.65); }
  .end-actions { display:flex; gap:10px; }
  .end .btn { background:rgba(255,255,255,.12); color:#fff; border-color:rgba(255,255,255,.2); padding:9px 16px; font-size:14px; }
  .end .btn:first-child { background:#fff; color:#111; border-color:#fff; }
  .screen video { transition:opacity .4s ease; }
  .screen.loading video { opacity:0; }
  .screen.loading { background:linear-gradient(100deg, #0b0c0f 30%, #16181c 50%, #0b0c0f 70%); background-size:300% 100%; animation:shimmer 1.4s linear infinite; }
  @keyframes shimmer { to { background-position:-150% 0; } }
  @keyframes fade { from { opacity:0 } to { opacity:1 } }

  .stage:fullscreen { border-radius:0; border:0; display:flex; flex-direction:column; background:#000; }
  .stage:fullscreen .screen { flex:1; aspect-ratio:auto; }
  .stage:fullscreen .controls { background:#0d0e10; }

  @media (max-width: 980px) {
    .wrap { padding:16px 16px 40px; }
    .studio { grid-template-columns:minmax(0,1fr); }
    .side { max-height:55vh; }
    .keys { display:none; }
  }
`

export const STUDIO_JS = `
(() => {
  const ICONS = ${JSON.stringify({ play: I.play, pause: I.pause, vol: I.vol, mute: I.mute })}
  const SPARK = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 1.5l2.6 7.9 7.9 2.6-7.9 2.6L12 22.5l-2.6-7.9L1.5 12l7.9-2.6z"/></svg>'

  const $ = s => document.querySelector(s)
  const v = $("#vid"), screen = $(".screen"), stage = $(".stage")
  const track = $(".track"), rail = $(".rail"), knob = $(".knob"), tip = $(".tip")
  const RATES = [1, 1.25, 1.5, 1.75, 2]
  const store = (k, val) => { try { val === undefined ? 0 : localStorage.setItem(k, val); return localStorage.getItem(k) } catch { return null } }
  const fmt = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60), s = Math.floor(t % 60)
    return m >= 60 ? Math.floor(m / 60) + ":" + String(m % 60).padStart(2, "0") + ":" + String(s).padStart(2, "0") : m + ":" + String(s).padStart(2, "0") }
  let cues = [], chapters = [], words = [], cleanup = null, board = null, ccOn = store("cc") === "1"

  // ---- play / pause
  const toggle = () => v.paused ? v.play() : v.pause()
  screen.onclick = toggle
  $("#play").onclick = toggle
  v.onplay = () => { screen.classList.add("playing"); $("#play").innerHTML = ICONS.pause; $(".end").hidden = true }
  v.onended = () => { $(".end").hidden = false }
  const loaded = () => screen.classList.remove("loading")
  v.addEventListener("loadeddata", loaded); v.addEventListener("error", loaded); setTimeout(loaded, 8000)
  $(".end").onclick = e => e.stopPropagation()
  $("#replay").onclick = () => { v.currentTime = 0; v.play() }
  $("#endcopy").onclick = e => { navigator.clipboard.writeText(location.href.split("?")[0]); e.currentTarget.textContent = "Copied" }
  v.onpause = () => { $("#play").innerHTML = ICONS.play }

  // ---- speed
  const speeds = $(".speeds")
  const setRate = r => { v.playbackRate = r; store("rate", r)
    for (const b of speeds.children) b.classList.toggle("on", Number(b.dataset.r) === r) }
  for (const r of RATES) { const b = document.createElement("button"); b.textContent = r + "×"; b.dataset.r = r
    b.onclick = () => setRate(r); speeds.appendChild(b) }
  const saved = Number(store("rate")) || 1
  v.addEventListener("loadedmetadata", () => { setRate(saved); buildRail(); tick() })
  setRate(saved)

  // ---- progress rail, one segment per chapter
  function segments() {
    const d = v.duration || 0
    if (!chapters.length || !d) return [{ a: 0, b: d || 1, title: "" }]
    return chapters.map((c, i) => ({ a: c.t, b: i + 1 < chapters.length ? chapters[i + 1].t : d, title: c.title }))
  }
  function buildRail() {
    rail.innerHTML = ""; const segs = segments(), d = v.duration || 1
    for (const s of segs) { const e = document.createElement("div"); e.className = "seg"; e.style.flex = String(Math.max(0.001, (s.b - s.a) / d))
      e.innerHTML = '<div class="buf"></div><div class="fill"></div>'; rail.appendChild(e) }
    drawMarks()
    const names = $(".chapters"); names.innerHTML = ""
    if (chapters.length) for (const s of segs) { const n = document.createElement("span"); n.textContent = s.title
      n.style.flex = String((s.b - s.a) / d); n.onclick = () => { v.currentTime = s.a; v.play() }; names.appendChild(n) }
  }
  // Where the clean-up cut something: a dot per removed "uh", a smaller one per shortened pause.
  function drawMarks() {
    const box = $(".marks"), d = v.duration
    if (!box || !cleanup || !d) return
    box.innerHTML = ""
    for (const m of cleanup.marks) {
      const e = document.createElement("div"); e.className = "mark " + m.kind
      e.style.left = Math.min(100, m.t / d * 100) + "%"; box.appendChild(e)
    }
  }
  function paint() {
    const d = v.duration || 1, t = v.currentTime
    let buf = 0; for (let i = 0; i < v.buffered.length; i++) if (v.buffered.start(i) <= t) buf = Math.max(buf, v.buffered.end(i))
    const segs = segments()
    rail.querySelectorAll(".seg").forEach((el, i) => { const s = segs[i]; if (!s) return
      const span = s.b - s.a || 1
      el.querySelector(".fill").style.width = Math.min(100, Math.max(0, (t - s.a) / span * 100)) + "%"
      el.querySelector(".buf").style.width = Math.min(100, Math.max(0, (buf - s.a) / span * 100)) + "%" })
    knob.style.left = (t / d * 100) + "%"
    $(".time").textContent = fmt(t) + " / " + fmt(v.duration)
    $(".chapters") && $(".chapters").querySelectorAll("span").forEach((n, i) => n.classList.toggle("on", segs[i] && t >= segs[i].a && t < segs[i].b))
  }
  const at = e => { const r = track.getBoundingClientRect(); return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * (v.duration || 0) }
  track.onpointermove = e => { const t = at(e), r = track.getBoundingClientRect(), d = v.duration || 1
    const ch = segments().find(s => t >= s.a && t < s.b)
    const near = cleanup && cleanup.marks.find(m => Math.abs(m.t - t) / d < 0.012)
    tip.querySelector("span").textContent = fmt(t) + (ch && ch.title ? "  ·  " + ch.title : "") +
      (near ? "  ·  " + (near.kind === "filler" ? "“" + near.label + "” removed" : near.label + " shortened") : "")
    if (board) {
      const i = Math.min(board.count - 1, Math.floor(t / board.interval)), f = tip.querySelector(".frame")
      f.style.backgroundImage = "url(" + location.pathname.replace(/\\/$/, "") + "/sprite.jpg)"
      f.style.backgroundPosition = -(i % board.cols) * board.w + "px " + -Math.floor(i / board.cols) * board.h + "px"
      tip.classList.add("has-frame")
    }
    const half = (tip.offsetWidth || 90) / 2
    tip.style.left = Math.min(r.width - half, Math.max(half, e.clientX - r.left)) + "px"
    if (track.classList.contains("drag")) { v.currentTime = t; paint() } }
  track.onpointerdown = e => { track.setPointerCapture(e.pointerId); track.classList.add("drag"); v.currentTime = at(e); paint() }
  track.onpointerup = e => { track.classList.remove("drag"); track.releasePointerCapture(e.pointerId) }

  // ---- transcript + captions
  const lines = $(".lines"), cap = $(".cap")
  let rows = [], current = -1, userScrolled = 0
  lines && lines.addEventListener("wheel", () => userScrolled = Date.now(), { passive: true })
  lines && lines.addEventListener("touchmove", () => userScrolled = Date.now(), { passive: true })
  function buildLines() {
    lines.innerHTML = ""; rows = []
    let ci = 0
    cues.forEach((c, i) => {
      while (ci < chapters.length && chapters[ci].t <= c.s + 0.01) { const h = document.createElement("div"); h.className = "chap"
        h.textContent = chapters[ci].title; lines.appendChild(h); ci++ }
      const r = document.createElement("div"); r.className = "line"; r.dataset.i = i
      r.innerHTML = '<span class="t"></span><span class="x" dir="auto"></span>'
      r.querySelector(".t").textContent = fmt(c.s)
      const next = cues[i + 1] ? cues[i + 1].s : 1e9
      r.fill = () => fillLine(r.querySelector(".x"), c, next)
      r.fill()
      r.onclick = () => { v.currentTime = c.s + 0.01; v.play() }
      lines.appendChild(r); rows.push(r)
    })
  }
  // A cue's words, with a faint struck-through chip wherever an "uh" was cut out.
  function fillLine(x, c, next) {
    x.textContent = ""
    const ws = words.filter(w => w.s >= c.s - 0.01 && w.s < Math.min(next, c.e + 0.01) - 0.005)
    const ums = cleanup ? cleanup.marks.filter(m => m.kind === "filler" && m.t >= c.s - 0.05 && m.t < next - 0.05) : []
    if (!ws.length) { x.textContent = c.text; return }
    let k = 0
    const chip = m => { const u = document.createElement("span"); u.className = "um"; u.textContent = m.label
      u.title = "Removed from the video"; x.append(u, " ") }
    for (const w of ws) {
      // Whisper's word starts run early, so compare against the word's middle
      while (k < ums.length && ums[k].t <= (w.s + w.e) / 2) chip(ums[k++])
      x.append(w.w + " ")
    }
    while (k < ums.length) chip(ums[k++])
  }
  function follow() {
    const t = v.currentTime
    let i = cues.findIndex(c => t >= c.s && t < c.e + 0.25)
    cap.textContent = ccOn && i >= 0 ? cues[i].text : ""
    if (i < 0) { i = -1; for (let k = 0; k < cues.length && cues[k].s <= t; k++) i = k }
    if (i === current) return
    current = i
    rows.forEach((r, k) => { r.classList.toggle("now", k === i); r.classList.toggle("past", k < i) })
    if (i >= 0 && Date.now() - userScrolled > 4000 && !$(".side input").value) {
      const r = rows[i], box = lines.getBoundingClientRect(), rr = r.getBoundingClientRect()
      if (rr.top < box.top + 40 || rr.bottom > box.bottom - 60) lines.scrollTop += rr.top - box.top - box.height / 3
    }
  }
  const search = $(".side input")
  search && (search.oninput = () => {
    const q = search.value.trim().toLowerCase()
    rows.forEach((r, k) => { const x = r.querySelector(".x"), text = cues[k].text
      const hit = !q || text.toLowerCase().includes(q); r.classList.toggle("hide", !hit)
      if (q && hit) { const at = text.toLowerCase().indexOf(q)
        x.innerHTML = ""; x.append(text.slice(0, at)); const m = document.createElement("mark")
        m.textContent = text.slice(at, at + q.length); x.append(m, text.slice(at + q.length)) }
      else r.fill() })
  })
  const ccBtn = $("#cc")
  const setCC = on => { ccOn = on; store("cc", on ? "1" : "0"); ccBtn.classList.toggle("on", on); follow() }
  ccBtn.onclick = () => setCC(!ccOn)

  // keep the transcript as tall as the player beside it
  const side = $(".side")
  if (side) new ResizeObserver(() => { if (innerWidth > 980) side.style.height = stage.offsetHeight + "px"; else side.style.height = "" }).observe(stage)

  fetch(location.pathname.replace(/\\/$/, "") + "/transcript.json").then(r => r.ok ? r.json() : null).then(d => {
    if (!d || !d.cues || !d.cues.length) { document.querySelector(".studio").classList.add("solo"); side && side.remove(); ccBtn.remove(); return }
    cues = d.cues; chapters = (d.chapters || []).filter(c => c.t < (v.duration || 1e9))
    words = d.words || []; cleanup = d.cleanup && d.cleanup.marks && d.cleanup.marks.length ? d.cleanup : null
    board = d.storyboard || null
    if (cleanup) {
      const b = $(".clean"), parts = []
      if (cleanup.fillers) parts.push(cleanup.fillers + (cleanup.fillers === 1 ? " um" : " ums"))
      if (cleanup.pauses) parts.push(cleanup.pauses + (cleanup.pauses === 1 ? " pause" : " pauses"))
      b.innerHTML = SPARK + "Cleaned up: " + parts.join(" & ") + " removed"
      b.title = "Filler words and long pauses were cut automatically: " + cleanup.before.toFixed(0) + " s → " + cleanup.after.toFixed(0) + " s (" + Math.round(cleanup.seconds / cleanup.before * 100) + "% shorter)"
      b.hidden = false
    }
    buildLines(); buildRail(); setCC(ccOn); follow()
  }).catch(() => {})

  // ---- misc controls
  $("#mute").onclick = () => { v.muted = !v.muted; $("#mute").innerHTML = v.muted ? ICONS.mute : ICONS.vol }
  $("#fs").onclick = () => document.fullscreenElement ? document.exitFullscreen() : stage.requestFullscreen()
  function tick() { paint(); follow() }
  v.addEventListener("timeupdate", tick); v.addEventListener("progress", paint); v.addEventListener("seeked", tick)
  ;(function loop() { if (!v.paused) tick(); requestAnimationFrame(loop) })()

  document.addEventListener("keydown", e => {
    if (e.target.closest("input, textarea") || e.ctrlKey || e.metaKey || e.altKey) return
    const i = RATES.indexOf(v.playbackRate)
    if (e.key === " " || e.key === "k") { e.preventDefault(); toggle() }
    else if (e.key === "ArrowRight" || e.key === "l") { e.preventDefault(); v.currentTime = Math.min(v.duration || 1e9, v.currentTime + 5) }
    else if (e.key === "ArrowLeft" || e.key === "j") { e.preventDefault(); v.currentTime = Math.max(0, v.currentTime - 5) }
    else if (e.key === ">" || e.key === ".") setRate(RATES[Math.min(RATES.length - 1, i + 1)] ?? 1)
    else if (e.key === "<" || e.key === ",") setRate(RATES[Math.max(0, i - 1)] ?? 1)
    else if (e.key === "f") $("#fs").click()
    else if (e.key === "m") $("#mute").click()
    else if (e.key === "c") ccBtn.isConnected && ccBtn.click()
  })
})()`

export function studioBody({ id, title, facts, link, poster, owner = "", summary = "" }) {
  return `
<div class="wrap">
  <div class="brand">${I.spark}<span>Lumen</span></div>
  <div class="head">
    <div class="who">
      <div class="avatar" aria-hidden="true">${owner ? owner.slice(0, 1).toUpperCase() : "✦"}</div>
      <div>
        <h1>${title}</h1>
        <div class="meta">${owner ? `<b>${owner}</b> · ` : ""}${facts}<span class="clean" hidden></span></div>
        ${summary ? `<p class="summary">${summary}</p>` : ""}
      </div>
    </div>
    <div class="actions">
      <button class="btn" onclick="navigator.clipboard.writeText('${link}');this.textContent='Copied'">Copy link</button>
      <a class="btn" href="/v/${id}/video.mp4?dl=1">Download</a>
    </div>
  </div>
  <div class="studio">
    <div>
      <div class="stage">
        <div class="screen loading">
          <video id="vid" src="/v/${id}/video.mp4" playsinline preload="metadata"${poster}></video>
          <button class="bigplay" aria-label="Play">${I.play.replace('width="18" height="18"', 'width="30" height="30"')}</button>
          <div class="cap" dir="auto"></div>
          <div class="end" hidden>
            <p>Thanks for watching</p>
            <div class="end-actions">
              <button class="btn" id="replay">${I.play} Replay</button>
              <button class="btn" id="endcopy">Copy link</button>
            </div>
            <p class="end-hint">Tip: press &gt; to watch faster</p>
          </div>
        </div>
        <div class="controls">
          <div class="track"><div class="marks"></div><div class="rail"></div><div class="knob"></div><div class="tip"><div class="frame"></div><span></span></div></div>
          <div class="chapters"></div>
          <div class="row">
            <button class="icon" id="play" title="Play (Space)">${I.play}</button>
            <span class="time">0:00 / 0:00</span>
            <span class="grow"></span>
            <div class="speeds" title="Speed ( &lt; &gt; )"></div>
            <button class="icon" id="cc" title="Captions (C)">${I.cc}</button>
            <button class="icon" id="mute" title="Mute (M)">${I.vol}</button>
            <button class="icon" id="fs" title="Fullscreen (F)">${I.full}</button>
          </div>
        </div>
      </div>
      <p class="keys">Space play · ← → 5 s · &lt; &gt; speed · C captions · F fullscreen</p>
    </div>
    <aside class="side">
      <header><b>TRANSCRIPT</b><input placeholder="Search" aria-label="Search the transcript"></header>
      <div class="lines"></div>
    </aside>
  </div>
</div>`
}
