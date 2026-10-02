// Lumen's share worker: Loom-style share links for the recorder.
//
//   /v/:id              share page (player, title, download; "processing" until uploaded)
//   /v/:id/video.mp4    the video, with Range support so seeking works
//   /v/:id/thumb.jpg    poster + link-preview image
//   /dashboard          your library: views, rename, password, expiry, delete
//   /api/...            used by the `vshare` command (Bearer ADMIN_TOKEN)
//
// Videos live in R2 under videos/<id>.mp4 and thumbs/<id>.jpg; details in D1.
// The dashboard is unlocked by opening /dashboard?key=<ADMIN_TOKEN> once, which
// sets an HttpOnly cookie; `vshare dashboard` does that for you.

import { STUDIO_CSS, STUDIO_JS, studioBody } from "./player.js"

const BOTS = /bot|crawl|spider|slack|discord|whatsapp|telegram|facebookexternalhit|twitter|linkedin|embed|preview/i
const DAY = 86400000
const FREE_BYTES = 10 * 1024 ** 3

export default {
  async fetch(req, env) {
    try {
      return await route(req, env)
    } catch (e) {
      return json({ error: String(e && e.message || e) }, 500)
    }
  },
}

async function route(req, env) {
  const url = new URL(req.url)
  const p = url.pathname.split("/").filter(Boolean)
  const m = req.method

  if (p[0] === "v" && p[1]) {
    const v = await getVideo(env, p[1])
    if (!v) return page("Not found", `<p class="muted">This video doesn't exist or was deleted.</p>`, 404)
    if (v.expires && v.expires < Date.now()) return page("Link expired", `<p class="muted">This link has expired.</p>`, 410)
    if (p.length === 2 && m === "GET") return sharePage(req, env, v)
    if (p[2] === "unlock" && m === "POST") return unlock(req, env, v)
    if (p[2] === "status") return json({ status: v.status })
    if (p[2] === "thumb.jpg") return v.pw_hash && !isAdmin(req, env) ? notFound() : r2Object(req, env, `thumbs/${v.id}.jpg`, "image/jpeg")
    // The link-preview card is public (previews happen before anyone logs in); the
    // hover frames show the video's content, so they follow the password like the video.
    if (p[2] === "card.jpg") return v.pw_hash && !isAdmin(req, env) ? notFound() : r2Object(req, env, `cards/${v.id}.jpg`, "image/jpeg")
    if (p[2] === "sprite.jpg") {
      if (!(await canWatch(req, v))) return new Response("Locked", { status: 403 })
      return r2Object(req, env, `sprites/${v.id}.jpg`, "image/jpeg")
    }
    if (p[2] === "transcript.json") {
      if (!(await canWatch(req, v))) return new Response("Locked", { status: 403 })
      return r2Object(req, env, `transcripts/${v.id}.json`, "application/json")
    }
    if (p[2] === "video.mp4") {
      if (!(await canWatch(req, v))) return new Response("Locked", { status: 403 })
      const dl = url.searchParams.has("dl") ? `attachment; filename="${safeName(v.title)}.mp4"` : null
      return r2Object(req, env, `videos/${v.id}.mp4`, "video/mp4", dl)
    }
    return notFound()
  }

  if (p[0] === "dashboard") {
    const key = url.searchParams.get("key")
    if (key) {
      if (!same(key, env.ADMIN_TOKEN)) return page("Dashboard", `<p class="muted">Wrong key.</p>`, 403)
      return new Response(null, { status: 302, headers: {
        Location: "/dashboard",
        "Set-Cookie": `admin=${key}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=31536000`,
      } })
    }
    if (!isAdmin(req, env)) return page("Dashboard", `<p class="muted">Open the dashboard with <code>vshare dashboard</code> on your laptop.</p>`, 403)
    return html(DASHBOARD)
  }

  if (p[0] === "api") {
    if (!isAdmin(req, env)) return json({ error: "unauthorised" }, 401)
    return api(req, env, p.slice(1), url)
  }

  return page("Lumen", `<p class="muted">Nothing here.</p>`, 404)
}

// ---------- API (vshare + dashboard)

async function api(req, env, p, url) {
  const m = req.method
  if (p[0] === "transcribe" && m === "POST") return transcribe(req, env, url)
  if (p[0] === "complete" && m === "POST") return complete(req, env)
  if (p[0] !== "videos") return notFound()

  if (p.length === 1 && m === "GET") {
    const { results } = await env.DB.prepare("SELECT * FROM videos ORDER BY created DESC").all()
    const used = results.reduce((a, v) => a + (v.size || 0), 0)
    const origin = url.origin
    return json({ videos: results.map(v => ({ ...v, pw_hash: undefined, locked: !!v.pw_hash, url: `${origin}/v/${v.id}` })),
      used, free: FREE_BYTES })
  }

  if (p.length === 1 && m === "POST") {
    const b = await req.json().catch(() => ({}))
    const id = newId()
    await env.DB.prepare("INSERT INTO videos (id, title, status, created, codec) VALUES (?, ?, 'processing', ?, ?)")
      .bind(id, String(b.title || "Screen recording").slice(0, 200), Date.now(), b.codec || null).run()
    return json({ id, url: `${url.origin}/v/${id}` })
  }

  const id = p[1]
  const v = await getVideo(env, id)
  if (!v) return json({ error: "no such video" }, 404)

  if (p.length === 2 && m === "DELETE") {
    await env.VIDEOS.delete([`videos/${id}.mp4`, `thumbs/${id}.jpg`, `transcripts/${id}.json`, `cards/${id}.jpg`, `sprites/${id}.jpg`])
    await env.DB.prepare("DELETE FROM videos WHERE id = ?").bind(id).run()
    return json({ ok: true })
  }

  if (p.length === 2 && m === "PATCH") {
    const b = await req.json()
    if (typeof b.title === "string") await env.DB.prepare("UPDATE videos SET title = ? WHERE id = ?").bind(b.title.slice(0, 200) || "Untitled", id).run()
    if ("password" in b) {
      const h = b.password ? await hashPw(env, id, String(b.password)) : null
      await env.DB.prepare("UPDATE videos SET pw_hash = ? WHERE id = ?").bind(h, id).run()
    }
    if (typeof b.summary === "string") await env.DB.prepare("UPDATE videos SET summary = ? WHERE id = ?").bind(b.summary.slice(0, 400) || null, id).run()
    if ("expires" in b) await env.DB.prepare("UPDATE videos SET expires = ? WHERE id = ?").bind(b.expires ? Number(b.expires) : null, id).run()
    return json({ ok: true })
  }

  if ((p[2] === "card" || p[2] === "sprite") && m === "PUT") {
    await env.VIDEOS.put(`${p[2]}s/${id}.jpg`, req.body, { httpMetadata: { contentType: "image/jpeg" } })
    if (p[2] === "card") await env.DB.prepare("UPDATE videos SET has_card = 1 WHERE id = ?").bind(id).run()
    return json({ ok: true })
  }

  if (p[2] === "transcript" && m === "PUT") {
    await env.VIDEOS.put(`transcripts/${id}.json`, req.body, { httpMetadata: { contentType: "application/json" } })
    return json({ ok: true })
  }

  if (p[2] === "thumb" && m === "PUT") {
    await env.VIDEOS.put(`thumbs/${id}.jpg`, req.body, { httpMetadata: { contentType: "image/jpeg" } })
    await env.DB.prepare("UPDATE videos SET has_thumb = 1 WHERE id = ?").bind(id).run()
    return json({ ok: true })
  }

  // Multipart upload: a Worker request body is capped (100 MB on the free plan),
  // so vshare sends the video in parts and R2 stitches them together.
  if (p[2] === "mpu") {
    const key = `videos/${id}.mp4`
    if (p.length === 3 && m === "POST") {
      const up = await env.VIDEOS.createMultipartUpload(key, { httpMetadata: { contentType: "video/mp4" } })
      return json({ uploadId: up.uploadId })
    }
    const up = env.VIDEOS.resumeMultipartUpload(key, p[3])
    if (p.length === 5 && p[4] === "complete" && m === "POST") {
      const b = await req.json()
      const obj = await up.complete(b.parts)
      await env.DB.prepare("UPDATE videos SET status = 'ready', size = ?, duration = ?, width = ?, height = ?, codec = COALESCE(?, codec) WHERE id = ?")
        .bind(obj.size, b.duration || null, b.width || null, b.height || null, b.codec || null, id).run()
      return json({ ok: true, size: obj.size })
    }
    if (p.length === 5 && p[4] === "abort" && m === "POST") {
      await up.abort()
      return json({ ok: true })
    }
    if (p.length === 5 && m === "PUT") {
      const part = await up.uploadPart(Number(p[4]), req.body)
      return json({ partNumber: part.partNumber, etag: part.etag })
    }
  }
  return notFound()
}

// ---------- transcription on Cloudflare (for laptops without a capable GPU)
// Body: an audio file (mp3/wav). Query: language, prompt. Returns Whisper's words
// with start/end times, the same shape the local clean-up uses.
async function transcribe(req, env, url) {
  if (!env.AI) return json({ error: "Workers AI is not bound to this worker" }, 501)
  const buf = new Uint8Array(await req.arrayBuffer())
  if (!buf.length) return json({ error: "empty audio" }, 400)
  let bin = ""
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000))
  const input = { audio: btoa(bin), vad_filter: true, condition_on_previous_text: false }
  const lang = url.searchParams.get("language"), prompt = url.searchParams.get("prompt")
  if (lang) input.language = lang
  if (prompt) input.initial_prompt = prompt
  const r = await env.AI.run("@cf/openai/whisper-large-v3-turbo", input)
  const words = []
  for (const s of r.segments || []) for (const w of s.words || []) if (String(w.word).trim()) words.push({ w: String(w.word).trim(), s: w.start, e: w.end })
  return json({ language: r.transcription_info && r.transcription_info.language, duration: r.transcription_info && r.transcription_info.duration, words })
}

// Titles/summaries for laptops that can't run a local model: a small LLM on your
// own Cloudflare account. Body: {prompt}. Returns {text}.
async function complete(req, env) {
  if (!env.AI) return json({ error: "Workers AI is not bound to this worker" }, 501)
  const { prompt } = await req.json()
  const r = await env.AI.run("@cf/meta/llama-3.1-8b-instruct-fp8", {
    messages: [{ role: "system", content: "Reply with only a JSON object." }, { role: "user", content: String(prompt).slice(0, 24000) }],
    max_tokens: 400, temperature: 0.3,
  })
  return json({ text: typeof r.response === "string" ? r.response : JSON.stringify(r.response) })
}

// ---------- share page

async function sharePage(req, env, v) {
  const origin = new URL(req.url).origin
  const link = `${origin}/v/${v.id}`
  if (!(await canWatch(req, v))) {
    return page(v.title, `
      <h1>${esc(v.title)}</h1>
      <form class="lock" method="post" action="/v/${v.id}/unlock">
        <p class="muted">${new URL(req.url).searchParams.has("wrong") ? "Wrong password, try again." : "This video is password protected."}</p>
        <input type="password" name="password" placeholder="Password" autofocus>
        <button>Watch</button>
      </form>`, 200)
  }
  if (v.status !== "ready") {
    return page(v.title, `
      <h1>${esc(v.title)}</h1>
      <div class="processing"><div class="spin"></div><p>Uploading. This page opens the video as soon as it's ready.</p></div>
      <script>setInterval(async()=>{const r=await fetch(location.pathname+"/status");if((await r.json()).status==="ready")location.reload()},2000)</script>`,
      200, meta(v, link, origin))
  }
  if (!BOTS.test(req.headers.get("user-agent") || "") && !isAdmin(req, env)) {
    await env.DB.prepare("UPDATE videos SET views = views + 1, last_view = ? WHERE id = ?").bind(Date.now(), v.id).run()
  }
  const poster = v.has_thumb && !v.pw_hash ? ` poster="/v/${v.id}/thumb.jpg"` : ""
  const facts = [fmtDate(v.created), v.duration ? fmtDur(v.duration) : null].filter(Boolean).join(" · ")
  return html(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(v.title)} · Lumen</title>${meta(v, link, origin)}
<style>${STUDIO_CSS}</style></head><body>
${studioBody({ id: v.id, title: esc(v.title), facts, link, poster, owner: esc(env.OWNER_NAME || ""), summary: esc(v.summary || "") })}
<script>${STUDIO_JS}</script></body></html>`)
}

function meta(v, link, origin) {
  const img = v.pw_hash ? "" : v.has_card ? `${origin}/v/${v.id}/card.jpg` : v.has_thumb ? `${origin}/v/${v.id}/thumb.jpg` : ""
  const desc = v.summary ? `<meta property="og:description" content="${esc(v.summary)}"><meta name="description" content="${esc(v.summary)}"><meta name="twitter:description" content="${esc(v.summary)}">` : ""
  const vid = `${origin}/v/${v.id}/video.mp4`
  return `
    <meta property="og:type" content="video.other">
    <meta property="og:site_name" content="Lumen">
    <meta property="og:title" content="${esc(v.title)}">
    <meta property="og:url" content="${link}">
    ${img ? `<meta property="og:image" content="${img}"><meta name="twitter:image" content="${img}">` : ""}
    ${v.status === "ready" && !v.pw_hash ? `<meta property="og:video" content="${vid}"><meta property="og:video:type" content="video/mp4">
    ${v.width ? `<meta property="og:video:width" content="${v.width}"><meta property="og:video:height" content="${v.height}">` : ""}` : ""}
    ${desc}${img && v.has_card ? `<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">` : ""}
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="${esc(v.title)}">`
}

async function unlock(req, env, v) {
  const form = await req.formData()
  const h = await hashPw(env, v.id, String(form.get("password") || ""))
  const ok = v.pw_hash && same(h, v.pw_hash)
  return new Response(null, { status: 303, headers: {
    Location: `/v/${v.id}${ok ? "" : "?wrong"}`,
    ...(ok ? { "Set-Cookie": `pw_${v.id}=${h}; Path=/v/${v.id}; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000` } : {}),
  } })
}

async function canWatch(req, v) {
  if (!v.pw_hash) return true
  const c = cookie(req, `pw_${v.id}`)
  return !!c && same(c, v.pw_hash)
}

// ---------- R2 streaming with Range

async function r2Object(req, env, key, type, disposition) {
  const range = req.headers.get("range")
  const obj = await env.VIDEOS.get(key, range ? { range: req.headers } : {})
  if (!obj) return notFound()
  const h = new Headers({ "Content-Type": type, "Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600", ETag: obj.httpEtag })
  if (disposition) h.set("Content-Disposition", disposition)
  if (range && obj.range) {
    const start = obj.range.offset ?? (obj.size - obj.range.suffix)
    const len = obj.range.length ?? (obj.size - start)
    h.set("Content-Range", `bytes ${start}-${start + len - 1}/${obj.size}`)
    h.set("Content-Length", String(len))
    return new Response(obj.body, { status: 206, headers: h })
  }
  h.set("Content-Length", String(obj.size))
  return new Response(obj.body, { headers: h })
}

// ---------- helpers

async function getVideo(env, id) {
  if (!/^[A-Za-z0-9]{6,20}$/.test(id)) return null
  return env.DB.prepare("SELECT * FROM videos WHERE id = ?").bind(id).first()
}

function newId() {
  const a = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
  const b = crypto.getRandomValues(new Uint8Array(12))
  return Array.from(b, x => a[x % a.length]).join("")
}

// Each install has its own salt (PASSWORD_SALT, set by `lumen setup`). Changing it
// would invalidate every password already set on a video, so it never changes.
async function hashPw(env, id, pw) {
  const salt = env.PASSWORD_SALT || "lumen"
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${salt}:${id}:${pw}`))
  return [...new Uint8Array(d)].map(x => x.toString(16).padStart(2, "0")).join("")
}

function same(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false
  let r = 0
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return r === 0
}

function cookie(req, name) {
  const m = (req.headers.get("cookie") || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`))
  return m ? m[1] : null
}

function isAdmin(req, env) {
  if (!env.ADMIN_TOKEN) return false
  const auth = req.headers.get("authorization") || ""
  if (auth.startsWith("Bearer ") && same(auth.slice(7), env.ADMIN_TOKEN)) return true
  const c = cookie(req, "admin")
  return !!c && same(c, env.ADMIN_TOKEN)
}

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]))
const safeName = s => String(s).replace(/[^\w .-]+/g, "_").slice(0, 80) || "video"
const fmtDate = ms => new Date(ms).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
const fmtDur = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } })
const html = (body, status = 200) => new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8" } })
const notFound = () => new Response("Not found", { status: 404 })

const STYLE = `
  :root { --bg:#0f1113; --panel:#171a1d; --line:#262a2e; --text:#e4e6e7; --muted:#8b9196; --accent:#e4e6e7; }
  @media (prefers-color-scheme: light) { :root { --bg:#f6f6f4; --panel:#fff; --line:#e3e3e0; --text:#1b1d1f; --muted:#6b7075; --accent:#1b1d1f; } }
  * { box-sizing:border-box }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
  main { max-width:1100px; margin:0 auto; padding:24px 16px 48px; }
  h1 { font-size:20px; margin:0 0 2px; font-weight:600; }
  .muted { color:var(--muted); margin:0; }
  video { width:100%; max-height:75vh; background:#000; border-radius:12px; display:block; }
  .bar { display:flex; gap:16px; justify-content:space-between; align-items:flex-start; margin-top:16px; flex-wrap:wrap; }
  .actions { display:flex; gap:8px; }
  button, .btn, input { font:inherit; border-radius:8px; border:1px solid var(--line); background:var(--panel); color:var(--text); padding:8px 14px; text-decoration:none; cursor:pointer; }
  button:hover, .btn:hover { border-color:var(--muted); }
  .processing { margin-top:24px; padding:48px 16px; text-align:center; background:var(--panel); border:1px solid var(--line); border-radius:12px; }
  .spin { width:28px; height:28px; margin:0 auto 12px; border:3px solid var(--line); border-top-color:var(--text); border-radius:50%; animation:s 0.9s linear infinite; }
  @keyframes s { to { transform:rotate(360deg) } }
  .lock { margin-top:24px; display:flex; flex-direction:column; gap:10px; max-width:320px; }
  code { background:var(--panel); padding:2px 6px; border-radius:4px; }`

function page(title, body, status = 200, head = "") {
  return html(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(title)}</title>${head}
<style>${STYLE}</style></head><body><main>${body}</main></body></html>`, status)
}

const DASHBOARD = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lumen · Shared videos</title>
<style>${STYLE}
  header { display:flex; justify-content:space-between; align-items:baseline; gap:12px; flex-wrap:wrap; margin-bottom:18px; }
  .meter { width:220px; height:6px; background:var(--line); border-radius:3px; overflow:hidden; margin-top:6px; }
  .meter div { height:100%; background:var(--text); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }
  .thumb { aspect-ratio:16/9; background:#000 center/cover no-repeat; display:block; position:relative; }
  .thumb span { position:absolute; right:8px; bottom:8px; background:#000b; color:#fff; font-size:12px; padding:1px 6px; border-radius:4px; }
  .body { padding:12px; display:flex; flex-direction:column; gap:6px; flex:1; }
  .title { font-weight:600; border:0; background:transparent; padding:2px 0; width:100%; border-radius:0; cursor:text; }
  .title:focus { outline:none; border-bottom:1px solid var(--muted); }
  .facts { font-size:13px; color:var(--muted); }
  .tags { display:flex; gap:6px; flex-wrap:wrap; font-size:12px; }
  .tags span { border:1px solid var(--line); border-radius:10px; padding:0 8px; color:var(--muted); }
  .row { display:flex; gap:6px; flex-wrap:wrap; margin-top:auto; padding-top:6px; }
  .row button, .row select { padding:5px 9px; font-size:13px; }
  select { font:inherit; border-radius:8px; border:1px solid var(--line); background:var(--panel); color:var(--text); }
  .danger:hover { border-color:#d9534f; color:#d9534f; }
  .empty { text-align:center; padding:60px 0; color:var(--muted); }
</style></head><body><main>
<header><div><h1><span style="background:linear-gradient(120deg,#8b8cf6,#e879f9);-webkit-background-clip:text;color:transparent">✦ Lumen</span> · Shared videos</h1><p class="muted" id="sum"></p></div>
<div><p class="muted" id="used" style="font-size:13px"></p><div class="meter"><div id="usedBar"></div></div></div></header>
<div class="grid" id="grid"></div>
<script>
const $ = s => document.querySelector(s)
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))
const mb = b => b >= 1073741824 ? (b/1073741824).toFixed(2)+" GB" : (b/1048576).toFixed(1)+" MB"
const dur = s => s ? Math.floor(s/60)+":"+String(Math.round(s%60)).padStart(2,"0") : ""
const ago = ms => { if (!ms) return "never"; const m = (Date.now()-ms)/60000
  return m < 1 ? "just now" : m < 60 ? Math.round(m)+" min ago" : m < 1440 ? Math.round(m/60)+" h ago" : Math.round(m/1440)+" d ago" }
const DAY = 86400000
async function api(path, opts = {}) {
  const r = await fetch("/api/videos" + path, { ...opts, headers: { "Content-Type": "application/json" } })
  return r.json()
}
async function load() {
  const d = await api("")
  $("#used").textContent = mb(d.used) + " of " + mb(d.free) + " free tier"
  $("#usedBar").style.width = Math.min(100, d.used / d.free * 100) + "%"
  const views = d.videos.reduce((a, v) => a + v.views, 0)
  $("#sum").textContent = d.videos.length + (d.videos.length === 1 ? " video · " : " videos · ") + views + (views === 1 ? " view" : " views")
  const g = $("#grid"); g.innerHTML = ""
  if (!d.videos.length) g.innerHTML = '<div class="empty">Nothing shared yet.</div>'
  for (const v of d.videos) {
    const c = document.createElement("div"); c.className = "card"
    const exp = v.expires ? (v.expires < Date.now() ? "expired" : "expires in " + Math.ceil((v.expires-Date.now())/DAY) + " d") : ""
    c.innerHTML = \`
      <a class="thumb" href="\${v.url}" target="_blank" style="background-image:url('/v/\${v.id}/thumb.jpg')">\${v.duration ? "<span>"+dur(v.duration)+"</span>" : ""}</a>
      <div class="body">
        <input class="title" value="\${esc(v.title)}">
        <div class="facts">\${new Date(v.created).toLocaleString()} · \${v.size ? mb(v.size) : "uploading…"}\${v.codec ? " · "+esc(v.codec) : ""}</div>
        <div class="facts">\${v.views} view\${v.views === 1 ? "" : "s"} · last watched \${ago(v.last_view)}</div>
        <div class="tags">\${v.status !== "ready" ? "<span>uploading</span>" : ""}\${v.locked ? "<span>🔒 password</span>" : ""}\${exp ? "<span>"+exp+"</span>" : ""}</div>
        <div class="row">
          <button data-a="copy">Copy link</button>
          <button data-a="pw">\${v.locked ? "Remove password" : "Password"}</button>
          <select data-a="exp"><option value="">Expiry…</option><option value="0">Never</option><option value="1">1 day</option><option value="7">7 days</option><option value="30">30 days</option></select>
          <button data-a="del" class="danger">Delete</button>
        </div>
      </div>\`
    const title = c.querySelector(".title")
    title.onchange = () => api("/" + v.id, { method: "PATCH", body: JSON.stringify({ title: title.value }) })
    title.onkeydown = e => { if (e.key === "Enter") title.blur() }
    c.querySelector('[data-a=copy]').onclick = e => { navigator.clipboard.writeText(v.url); e.target.textContent = "Copied" }
    c.querySelector('[data-a=pw]').onclick = async () => {
      const pw = v.locked ? "" : prompt("Password for this video:")
      if (pw === null) return
      await api("/" + v.id, { method: "PATCH", body: JSON.stringify({ password: pw }) }); load()
    }
    c.querySelector('[data-a=exp]').onchange = async e => {
      const d = Number(e.target.value)
      await api("/" + v.id, { method: "PATCH", body: JSON.stringify({ expires: d ? Date.now() + d*DAY : null }) }); load()
    }
    c.querySelector('[data-a=del]').onclick = async () => {
      if (!confirm("Delete “" + v.title + "”? The link stops working.")) return
      await api("/" + v.id, { method: "DELETE" }); load()
    }
    g.appendChild(c)
  }
}
load(); setInterval(load, 30000)
</script></main></body></html>`
