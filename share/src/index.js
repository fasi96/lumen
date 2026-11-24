// Lumen's share worker: Loom-style share links for the recorder.
//
//   /v/:id              share page (player, title, download; "processing" until uploaded)
//   /v/:id/video.mp4    the video, with Range support so seeking works
//   /v/:id/thumb.jpg    poster + link-preview image
//   /dashboard          your library: views, rename, password, expiry, delete, devices
//   /api/...            used by the `vshare` command (Bearer ADMIN_TOKEN) and by
//                       connected devices like Lumen for Chrome (Bearer device token)
//
// Device tokens (`vshare connect-code` mints one): scope "record" may create shares,
// upload into them, and list/rename/delete only the videos it made. Only a SHA-256 of
// each token is stored. /api/* answers CORS for chrome-extension:// origins, so the
// extension needs no host permission for your site; the token is still what counts.
//
// Videos live in R2 under videos/<id>.mp4 and thumbs/<id>.jpg; details in D1.
// The dashboard is unlocked by `vshare dashboard`: it opens a private local page that
// POSTs the key to /dashboard/login, which answers with a session cookie (an HMAC of
// a random id, never the key itself). Rotating ADMIN_TOKEN signs every session out.

import { STUDIO_CSS, STUDIO_JS, studioBody } from "./player.js"

const BOTS = /bot|crawl|spider|slack|discord|whatsapp|telegram|facebookexternalhit|twitter|linkedin|embed|preview/i
const DAY = 86400000
const FREE_BYTES = 10 * 1024 ** 3
// What this worker can do, for /api/info (clients check features, not version numbers).
const API_VERSION = 2
const FEATURES = ["tokens", "cors", "info", "source", "limits", "audio-minutes"]
const SCHEMA_VERSION = 4
const MB = 1024 ** 2

// Sizes a request body may have (the Worker itself caps any body at 100 MB).
const MAX = { image: 8 * MB, transcript: 4 * MB, audio: 25 * MB, prompt: 64 * 1024, json: 256 * 1024, part: 100 * MB }
// What one device key may use per day (UTC). A leaked key can't run up your Cloudflare
// bill: Workers AI calls, minutes of audio transcribed and uploaded bytes are counted, and a
// device can't upload once the site is past the free 10 GB. Transcription counts by audio
// minutes, not calls, so live captions (Lumen Notes: a call every few seconds) fit; 240
// minutes is a little over what Cloudflare's free 10,000 Neurons a day cover (~214).
// Override with DEVICE_AI_PER_DAY / DEVICE_AUDIO_MIN_PER_DAY / DEVICE_GB_PER_DAY.
const DEVICE_AI_PER_DAY = 600
const DEVICE_AUDIO_MIN_PER_DAY = 240
const DEVICE_GB_PER_DAY = 5
// Wrong video passwords: this many in a row lock that video's form for a while.
const UNLOCK_TRIES = 10
const UNLOCK_WAIT = 15 * 60000
// Dashboard sessions end after this, whatever the browser keeps.
const SESSION_MS = 30 * DAY

export default {
  async fetch(req, env, ctx) {
    const res = await (async () => {
      try {
        return await route(req, env, ctx)
      } catch (e) {
        // The detail goes to `wrangler tail`, not to whoever made the request.
        console.error(e)
        return json({ error: "the share site hit an error" }, 500)
      }
    })()
    return withCors(req, env, res)
  },
}

// ---------- CORS for the Chrome extension
// chrome-extension://<32 letters a-p> (any extension id: the Bearer token is the lock),
// plus any origins listed in the EXTENSION_ORIGINS var (comma-separated), for /api only.
function corsOrigin(req, env) {
  const o = req.headers.get("origin") || ""
  if (/^chrome-extension:\/\/[a-p]{32}$/.test(o)) return o
  const extra = String(env.EXTENSION_ORIGINS || "").split(",").map(s => s.trim()).filter(Boolean)
  return extra.includes(o) ? o : null
}

function withCors(req, env, res) {
  if (!new URL(req.url).pathname.startsWith("/api/")) return res
  const o = corsOrigin(req, env)
  const out = new Response(res.body, res)
  out.headers.append("Vary", "Origin")
  if (o) {
    out.headers.set("Access-Control-Allow-Origin", o)
    out.headers.set("Access-Control-Expose-Headers", "Content-Length")
  }
  return out
}

async function route(req, env, ctx) {
  const url = new URL(req.url)
  const p = url.pathname.split("/").filter(Boolean)
  const m = req.method

  // CORS preflight: answered before any auth (browsers send no Authorization on it).
  if (p[0] === "api" && m === "OPTIONS") {
    const o = corsOrigin(req, env)
    return new Response(null, { status: 204, headers: o ? {
      "Access-Control-Allow-Origin": o,
      "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE",
      "Access-Control-Allow-Headers": "Authorization, Content-Type",
      "Access-Control-Max-Age": "86400",
    } : {} })
  }

  if (p[0] === "v" && p[1]) {
    const v = await getVideo(env, p[1])
    if (!v) return page("Not found", `<p class="muted">This video doesn't exist or was deleted.</p>`, 404)
    if (v.expires && v.expires < Date.now()) return page("Link expired", `<p class="muted">This link has expired.</p>`, 410)
    if (p.length === 2 && m === "GET") return sharePage(req, env, v)
    if (p[2] === "unlock" && m === "POST") return unlock(req, env, v)
    if (p[2] === "status") return json({ status: v.status })
    // Poster and link-preview card: public for an open video (previews happen before
    // anyone signs in); for a locked one only the owner gets them. The hover frames show
    // the video's content, so they follow the password like the video itself.
    if (p[2] === "thumb.jpg") return v.pw_hash && !(await isAdmin(req, env)) ? notFound() : r2Object(req, env, `thumbs/${v.id}.jpg`, "image/jpeg")
    if (p[2] === "card.jpg") return v.pw_hash && !(await isAdmin(req, env)) ? notFound() : r2Object(req, env, `cards/${v.id}.jpg`, "image/jpeg")
    if (p[2] === "sprite.jpg") {
      if (!(await canWatch(req, env, v))) return new Response("Locked", { status: 403 })
      return r2Object(req, env, `sprites/${v.id}.jpg`, "image/jpeg")
    }
    if (p[2] === "transcript.json") {
      if (!(await canWatch(req, env, v))) return new Response("Locked", { status: 403 })
      // no-cache: the transcript can arrive (or change) after the page was first opened
      return r2Object(req, env, `transcripts/${v.id}.json`, "application/json", null, "no-cache")
    }
    if (p[2] === "video.mp4") {
      if (!(await canWatch(req, env, v))) return new Response("Locked", { status: 403 })
      const dl = url.searchParams.has("dl") ? `attachment; filename="${safeName(v.title)}.mp4"` : null
      return r2Object(req, env, `videos/${v.id}.mp4`, "video/mp4", dl)
    }
    return notFound()
  }

  if (p[0] === "dashboard" && p[1] === "login" && req.method === "POST") {
    const form = await req.formData().catch(() => null)
    const key = form && form.get("key")
    if (!env.ADMIN_TOKEN || !same(String(key || ""), env.ADMIN_TOKEN)) return page("Dashboard", `<p class="muted">Wrong key.</p>`, 403)
    const sid = [...crypto.getRandomValues(new Uint8Array(16))].map(x => x.toString(16).padStart(2, "0")).join("")
    const iat = String(Date.now())
    // A same-site page then navigates on, so the Strict cookie is sent from here on.
    return new Response(`<!doctype html><meta charset="utf-8"><title>Lumen</title><script>location.replace("/dashboard")</script>`, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "Set-Cookie": `__Host-adm=${sid}.${iat}.${await sessionSig(env, sid, iat)}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_MS / 1000}`,
        "Cache-Control": "no-store",
      },
    })
  }

  if (p[0] === "dashboard" && p[1] === "logout" && req.method === "POST") {
    return new Response(null, { status: 303, headers: {
      Location: "/dashboard",
      "Set-Cookie": "__Host-adm=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
    } })
  }

  if (p[0] === "dashboard") {
    if (!(await isAdmin(req, env))) return page("Dashboard", `<p class="muted">Open the dashboard with <code>vshare dashboard</code> on your laptop.</p>`, 403)
    return html(DASHBOARD)
  }

  if (p[0] === "api") {
    const who = await auth(req, env, ctx)
    if (!who) return json({ error: "unauthorised" }, 401)
    await ensureSchema(env)
    return api(req, env, p.slice(1), url, who)
  }

  return page("Lumen", `<p class="muted">Nothing here.</p>`, 404)
}

// ---------- API (vshare + dashboard)

// who: { kind: "admin" } or { kind: "device", id, name, scope } (see auth()).
async function api(req, env, p, url, who) {
  const m = req.method
  const admin = who.kind === "admin"
  if (p[0] === "info" && m === "GET") return info(env, who)
  if (p[0] === "tokens") return tokens(req, env, p, who)
  if (p[0] === "transcribe" && m === "POST") return transcribe(req, env, url, who)
  if (p[0] === "complete" && m === "POST") return complete(req, env, who)
  if (p[0] !== "videos") return notFound()

  if (p.length === 1 && m === "GET") {
    // A device sees only the videos it made; the owner sees everything (newest 1000).
    const q = admin ? env.DB.prepare("SELECT * FROM videos ORDER BY created DESC LIMIT 1000")
      : env.DB.prepare("SELECT * FROM videos WHERE created_by = ? ORDER BY created DESC LIMIT 1000").bind(who.id)
    const { results } = await q.all()
    const used = await usedBytes(env)
    const origin = url.origin
    return json({ videos: results.map(v => ({ ...v, pw_hash: undefined, locked: !!v.pw_hash, url: `${origin}/v/${v.id}` })),
      used, free: FREE_BYTES })
  }

  if (p.length === 1 && m === "POST") {
    const b = await readJson(req, MAX.json)
    if (!b) return tooBig()
    const id = newId()
    const source = admin ? (b.source ? String(b.source).slice(0, 20) : "desktop") : String(b.source || "chrome").slice(0, 20)
    await env.DB.prepare("INSERT INTO videos (id, title, status, created, codec, created_by, source) VALUES (?, ?, 'processing', ?, ?, ?, ?)")
      .bind(id, String(b.title || "Screen recording").slice(0, 200), Date.now(), codecOf(b.codec), admin ? null : who.id, source).run()
    return json({ id, url: `${url.origin}/v/${id}` })
  }

  const id = p[1]
  const v = await getVideo(env, id)
  // A device can't see (or learn about) videos it didn't make: same answer as "no such video".
  if (!v || (!admin && v.created_by !== who.id)) return json({ error: "no such video" }, 404)

  if (p.length === 2 && m === "DELETE") {
    await env.VIDEOS.delete([`videos/${id}.mp4`, `thumbs/${id}.jpg`, `transcripts/${id}.json`, `cards/${id}.jpg`, `sprites/${id}.jpg`])
    await env.DB.prepare("DELETE FROM videos WHERE id = ?").bind(id).run()
    return json({ ok: true })
  }

  if (p.length === 2 && m === "PATCH") {
    const b = await readJson(req, MAX.json)
    if (!b) return tooBig()
    if (typeof b.title === "string") await env.DB.prepare("UPDATE videos SET title = ? WHERE id = ?").bind(b.title.slice(0, 200) || "Untitled", id).run()
    if ("password" in b) {
      const h = b.password ? await hashPw(env, id, String(b.password)) : null
      await env.DB.prepare("UPDATE videos SET pw_hash = ? WHERE id = ?").bind(h, id).run()
    }
    if (typeof b.summary === "string") await env.DB.prepare("UPDATE videos SET summary = ? WHERE id = ?").bind(b.summary.slice(0, 400) || null, id).run()
    if ("expires" in b) await env.DB.prepare("UPDATE videos SET expires = ? WHERE id = ?").bind(num(b.expires), id).run()
    if ("cut_seconds" in b) await env.DB.prepare("UPDATE videos SET cut_seconds = ? WHERE id = ?").bind(num(b.cut_seconds), id).run()
    return json({ ok: true })
  }

  if ((p[2] === "card" || p[2] === "sprite" || p[2] === "thumb") && m === "PUT") {
    const body = await readBody(req, MAX.image)
    if (!body) return tooBig()
    const over = await spend(env, who, { bytes: body.length })
    if (over) return over
    await env.VIDEOS.put(`${p[2]}s/${id}.jpg`, body, { httpMetadata: { contentType: "image/jpeg" } })
    if (p[2] === "card") await env.DB.prepare("UPDATE videos SET has_card = 1 WHERE id = ?").bind(id).run()
    if (p[2] === "thumb") await env.DB.prepare("UPDATE videos SET has_thumb = 1 WHERE id = ?").bind(id).run()
    return json({ ok: true })
  }

  // The transcript must be one JSON object; it's stored as parsed and re-serialised, so
  // the share page only ever reads well-formed JSON from it.
  if (p[2] === "transcript" && m === "PUT") {
    const body = await readBody(req, MAX.transcript)
    if (!body) return tooBig()
    let doc
    try { doc = JSON.parse(new TextDecoder().decode(body)) } catch { return json({ error: "the transcript must be JSON" }, 400) }
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) return json({ error: "the transcript must be a JSON object" }, 400)
    const over = await spend(env, who, { bytes: body.length })
    if (over) return over
    await env.VIDEOS.put(`transcripts/${id}.json`, JSON.stringify(doc), { httpMetadata: { contentType: "application/json" } })
    return json({ ok: true })
  }

  // Multipart upload: a Worker request body is capped (100 MB on the free plan),
  // so vshare sends the video in parts and R2 stitches them together.
  if (p[2] === "mpu") {
    const key = `videos/${id}.mp4`
    if (p.length === 3 && m === "POST") {
      // A device can't add videos once the site is past Cloudflare's free storage.
      if (!admin && !env.DEVICE_PAST_FREE && (await usedBytes(env)) >= FREE_BYTES) {
        return json({ error: "this share site is past the free 10 GB; delete some videos on the dashboard" }, 507)
      }
      const up = await env.VIDEOS.createMultipartUpload(key, { httpMetadata: { contentType: "video/mp4" } })
      return json({ uploadId: up.uploadId })
    }
    const up = env.VIDEOS.resumeMultipartUpload(key, decodeURIComponent(p[3] || ""))
    if (p.length === 5 && p[4] === "complete" && m === "POST") {
      const b = await readJson(req, MAX.json)
      if (!b || !Array.isArray(b.parts) || b.parts.length > 10000) return json({ error: "bad parts list" }, 400)
      const obj = await up.complete(b.parts.map(x => ({ partNumber: Number(x.partNumber), etag: String(x.etag) })))
      await env.DB.prepare("UPDATE videos SET status = 'ready', size = ?, duration = ?, width = ?, height = ?, codec = COALESCE(?, codec) WHERE id = ?")
        .bind(obj.size, num(b.duration), whole(b.width), whole(b.height), codecOf(b.codec), id).run()
      return json({ ok: true, size: obj.size })
    }
    if (p.length === 5 && p[4] === "abort" && m === "POST") {
      await up.abort()
      return json({ ok: true })
    }
    if (p.length === 5 && m === "PUT") {
      const n = Number(p[4])
      if (!Number.isInteger(n) || n < 1 || n > 10000) return json({ error: "bad part number" }, 400)
      const size = Number(req.headers.get("content-length"))
      if (!Number.isFinite(size) || size <= 0) return json({ error: "Content-Length is required" }, 411)
      if (size > MAX.part) return tooBig()
      const over = await spend(env, who, { bytes: size })
      if (over) return over
      const part = await up.uploadPart(n, req.body)
      return json({ partNumber: part.partNumber, etag: part.etag })
    }
  }
  return notFound()
}

// ---------- connected devices

// GET /api/info: lets a client check the connection and what this worker supports.
async function info(env, who) {
  return json({
    api: API_VERSION, schema: SCHEMA_VERSION, features: FEATURES,
    owner: env.OWNER_NAME || "", used: await usedBytes(env), free: FREE_BYTES,
    you: who.kind === "admin" ? { kind: "admin" }
      : { kind: "device", id: who.id, name: who.name, scope: who.scope, limits: limits(env), today: await usedToday(env, who) },
  })
}

async function usedBytes(env) {
  const r = await env.DB.prepare("SELECT COALESCE(SUM(size), 0) AS used FROM videos").first()
  return (r && r.used) || 0
}

// /api/tokens: POST {name} mints one (the token is shown once), GET lists them,
// DELETE /api/tokens/:id revokes one (owner only); DELETE /api/tokens/self is how a
// device disconnects itself.
async function tokens(req, env, p, who) {
  const m = req.method
  if (p[1] === "self" && m === "DELETE") {
    if (who.kind !== "device") return json({ error: "only a device token can remove itself" }, 400)
    await env.DB.prepare("DELETE FROM tokens WHERE id = ?").bind(who.id).run()
    return json({ ok: true })
  }
  if (who.kind !== "admin") return json({ error: "only the owner can manage devices" }, 403)
  if (p.length === 1 && m === "GET") {
    const { results } = await env.DB.prepare("SELECT id, name, scope, created, last_used FROM tokens ORDER BY created DESC").all()
    return json({ tokens: results })
  }
  if (p.length === 1 && m === "POST") {
    const b = await req.json().catch(() => ({}))
    const scope = "record"            // the only device scope; the owner key covers the rest
    const name = String(b.name || "Device").slice(0, 60)
    const id = randomText(12)
    const token = `lt_${id}_${randomText(32)}`
    await env.DB.prepare("INSERT INTO tokens (id, hash, name, scope, created) VALUES (?, ?, ?, ?, ?)")
      .bind(id, await sha256(token), name, scope, Date.now()).run()
    return json({ id, token, name, scope })
  }
  if (p.length === 2 && m === "DELETE") {
    await env.DB.prepare("DELETE FROM tokens WHERE id = ?").bind(p[1]).run()
    return json({ ok: true })
  }
  return notFound()
}

// Who is calling: the owner (ADMIN_TOKEN or the dashboard cookie), a device token, or nobody.
// The dashboard cookie only counts for a change (anything but GET) when the request comes
// from this site's own pages, so another site can't make your browser act for you.
async function auth(req, env, ctx) {
  if (await isAdminKey(req, env)) return { kind: "admin" }
  if (await isAdminSession(req, env)) {
    const safe = req.method === "GET" || req.method === "HEAD"
    return safe || req.headers.get("origin") === new URL(req.url).origin ? { kind: "admin" } : null
  }
  const a = req.headers.get("authorization") || ""
  const m = a.match(/^Bearer (lt_([A-Za-z0-9]{12})_[A-Za-z0-9]{32})$/)
  if (!m) return null
  await ensureSchema(env)
  const row = await env.DB.prepare("SELECT id, hash, name, scope, last_used FROM tokens WHERE id = ?").bind(m[2]).first()
  if (!row || !same(await sha256(m[1]), row.hash)) return null
  // Note when it was last used (at most once a minute, after answering).
  if (!row.last_used || Date.now() - row.last_used > 60000) {
    const w = env.DB.prepare("UPDATE tokens SET last_used = ? WHERE id = ?").bind(Date.now(), row.id).run()
    ctx && ctx.waitUntil ? ctx.waitUntil(w) : await w
  }
  return { kind: "device", id: row.id, name: row.name, scope: row.scope }
}

// The database grows with Lumen. Sites set up before device tokens get the new
// columns and tables here, once, on the first API call after an update: no manual
// step, and running it twice is harmless (each change is skipped if already there).
let schemaReady = null
function ensureSchema(env) {
  schemaReady ??= migrate(env).catch(e => { schemaReady = null; throw e })
  return schemaReady
}

async function migrate(env) {
  await env.DB.prepare("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)").run()
  const row = await env.DB.prepare("SELECT v FROM meta WHERE k = 'schema_version'").first()
  if (row && Number(row.v) >= SCHEMA_VERSION) return
  const addColumn = async sql => {
    try { await env.DB.prepare(sql).run() } catch (e) { if (!/duplicate column/i.test(String(e && e.message || e))) throw e }
  }
  // Version 1 (in case a site never ran migrations.sql): summary and preview card.
  await addColumn("ALTER TABLE videos ADD COLUMN summary TEXT")
  await addColumn("ALTER TABLE videos ADD COLUMN has_card INTEGER NOT NULL DEFAULT 0")
  // Version 1 → 2: who made each video, from where, how much was cut; device tokens.
  await addColumn("ALTER TABLE videos ADD COLUMN created_by TEXT")
  await addColumn("ALTER TABLE videos ADD COLUMN source TEXT")
  await addColumn("ALTER TABLE videos ADD COLUMN cut_seconds REAL")
  await env.DB.batch([
    env.DB.prepare("CREATE TABLE IF NOT EXISTS tokens (id TEXT PRIMARY KEY, hash TEXT NOT NULL, name TEXT, scope TEXT NOT NULL DEFAULT 'record', created INTEGER NOT NULL, last_used INTEGER)"),
    env.DB.prepare("CREATE INDEX IF NOT EXISTS videos_created_by ON videos (created_by)"),
    // Version 2 → 3: what each device used today (AI calls, uploaded bytes), and wrong
    // password tries per video.
    env.DB.prepare("CREATE TABLE IF NOT EXISTS usage (token_id TEXT NOT NULL, day TEXT NOT NULL, ai INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (token_id, day))"),
    env.DB.prepare("CREATE TABLE IF NOT EXISTS unlock_fails (id TEXT PRIMARY KEY, n INTEGER NOT NULL, since INTEGER NOT NULL)"),
  ])
  // Version 3 → 4: seconds of audio each device had transcribed today.
  await addColumn("ALTER TABLE usage ADD COLUMN audio_s REAL NOT NULL DEFAULT 0")
  await env.DB.prepare("INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', ?)").bind(String(SCHEMA_VERSION)).run()
}

// Counts a device's AI calls, transcribed audio and uploaded bytes for today; returns an
// error response once it's past its daily allowance (the owner key is never limited).
// audio_s is added after a transcription (its length is known then), so the call that
// crosses the line still finishes and the next one is refused.
const limits = (env) => ({
  ai: Number(env.DEVICE_AI_PER_DAY) || DEVICE_AI_PER_DAY,
  audioMin: Number(env.DEVICE_AUDIO_MIN_PER_DAY) || DEVICE_AUDIO_MIN_PER_DAY,
  bytes: (Number(env.DEVICE_GB_PER_DAY) || DEVICE_GB_PER_DAY) * 1024 ** 3,
})
const today = () => new Date().toISOString().slice(0, 10)

async function spend(env, who, { ai = 0, bytes = 0, audio_s = 0, transcribing = false }) {
  if (who.kind !== "device") return null
  const row = await env.DB.prepare("INSERT INTO usage (token_id, day, ai, bytes, audio_s) VALUES (?, ?, ?, ?, ?) " +
    "ON CONFLICT (token_id, day) DO UPDATE SET ai = ai + excluded.ai, bytes = bytes + excluded.bytes, audio_s = audio_s + excluded.audio_s " +
    "RETURNING ai, bytes, audio_s")
    .bind(who.id, today(), ai, bytes, audio_s).first()
  const max = limits(env)
  if (row && (row.ai > max.ai || row.bytes > max.bytes)) {
    return json({ error: `this device used its daily allowance on this share site (${max.ai} AI calls, ${max.bytes / 1024 ** 3} GB); it resets at midnight UTC` }, 429)
  }
  // Used-up audio minutes stop only transcription.
  if (row && transcribing && row.audio_s >= max.audioMin * 60) {
    return json({ error: `this device used its ${max.audioMin} minutes of transcription for today on this share site; it resets at midnight UTC` }, 429)
  }
  return null
}

async function usedToday(env, who) {
  const r = await env.DB.prepare("SELECT ai, bytes, audio_s FROM usage WHERE token_id = ? AND day = ?").bind(who.id, today()).first()
  return { ai: (r && r.ai) || 0, audioMin: +(((r && r.audio_s) || 0) / 60).toFixed(1), bytes: (r && r.bytes) || 0 }
}

// How long an audio file is: Whisper says so; else, for a WAV, its header; else a guess
// from its size that errs long (16 kB a second, a 128 kbit/s MP3).
function audioSeconds(buf, r) {
  const d = Number(r && r.transcription_info && r.transcription_info.duration)
  if (d > 0) return d
  const v = new DataView(buf.buffer, buf.byteOffset, buf.byteLength)
  if (buf.length > 44 && v.getUint32(0) === 0x52494646 && v.getUint32(8) === 0x57415645) {   // "RIFF" … "WAVE"
    const rate = v.getUint32(28, true)
    if (rate) return (buf.length - 44) / rate
  }
  return buf.length / 16000
}

async function sha256(text) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))
  return [...new Uint8Array(d)].map(x => x.toString(16).padStart(2, "0")).join("")
}

function randomText(n) {
  const a = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
  // Rejection sampling, so every character is equally likely.
  const lim = 256 - (256 % a.length)
  let out = ""
  while (out.length < n) {
    for (const x of crypto.getRandomValues(new Uint8Array(n * 2))) {
      if (x < lim && out.length < n) out += a[x % a.length]
    }
  }
  return out
}

// ---------- transcription on Cloudflare (for laptops without a capable GPU)
// Body: an audio file (mp3/wav). Query: language, prompt. Returns Whisper's words
// with start/end times, the same shape the local clean-up uses.
async function transcribe(req, env, url, who) {
  if (!env.AI) return json({ error: "Workers AI is not bound to this worker" }, 501)
  const buf = await readBody(req, MAX.audio)
  if (!buf) return tooBig()
  if (!buf.length) return json({ error: "empty audio" }, 400)
  const over = await spend(env, who, { bytes: buf.length, transcribing: true })
  if (over) return over
  let bin = ""
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000))
  const input = { audio: btoa(bin), vad_filter: true, condition_on_previous_text: false }
  const lang = url.searchParams.get("language"), prompt = url.searchParams.get("prompt")
  if (lang) input.language = lang.slice(0, 12)
  if (prompt) input.initial_prompt = prompt.slice(0, 1000)
  const r = await env.AI.run("@cf/openai/whisper-large-v3-turbo", input)
  await spend(env, who, { audio_s: audioSeconds(buf, r) })
  const words = []
  for (const s of r.segments || []) for (const w of s.words || []) if (String(w.word).trim()) words.push({ w: String(w.word).trim(), s: w.start, e: w.end })
  return json({ language: r.transcription_info && r.transcription_info.language, duration: r.transcription_info && r.transcription_info.duration, words })
}

// Titles/summaries for laptops that can't run a local model: a small LLM on your
// own Cloudflare account. Body: {prompt}. Returns {text}.
async function complete(req, env, who) {
  if (!env.AI) return json({ error: "Workers AI is not bound to this worker" }, 501)
  const b = await readJson(req, MAX.prompt)
  if (!b) return tooBig()
  const over = await spend(env, who, { ai: 1 })
  if (over) return over
  const { prompt } = b
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
  if (!(await canWatch(req, env, v))) {
    // The title stays private too: it's often written from what was said.
    const q = new URL(req.url).searchParams
    const note = q.has("wait") ? "Too many wrong passwords. Try again in 15 minutes."
      : q.has("wrong") ? "Wrong password, try again." : "This video is password protected."
    return page("Password-protected video", `
      <h1>Password-protected video</h1>
      <form class="lock" method="post" action="/v/${v.id}/unlock">
        <p class="muted">${note}</p>
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
  if (!BOTS.test(req.headers.get("user-agent") || "") && !(await isAdmin(req, env))) {
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
    ${whole(v.width) && whole(v.height) ? `<meta property="og:video:width" content="${whole(v.width)}"><meta property="og:video:height" content="${whole(v.height)}">` : ""}` : ""}
    ${desc}${img && v.has_card ? `<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">` : ""}
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="${esc(v.title)}">`
}

// Wrong passwords are counted per video: after UNLOCK_TRIES in a row the form waits
// UNLOCK_WAIT before it checks again, so a password can't be guessed by brute force.
async function unlock(req, env, v) {
  if (!v.pw_hash) return new Response(null, { status: 303, headers: { Location: `/v/${v.id}` } })
  await ensureSchema(env)
  const now = Date.now()
  const fails = await env.DB.prepare("SELECT n, since FROM unlock_fails WHERE id = ?").bind(v.id).first()
  const recent = fails && now - fails.since < UNLOCK_WAIT
  if (recent && fails.n >= UNLOCK_TRIES) return new Response(null, { status: 303, headers: { Location: `/v/${v.id}?wait` } })
  const form = await req.formData().catch(() => null)
  const pw = String((form && form.get("password")) || "").slice(0, 200)
  const ok = await checkPw(env, v, pw)
  if (!ok) {
    await env.DB.prepare("INSERT INTO unlock_fails (id, n, since) VALUES (?, 1, ?) ON CONFLICT (id) DO UPDATE SET " +
      "n = CASE WHEN ? - since < ? THEN n + 1 ELSE 1 END, since = CASE WHEN ? - since < ? THEN since ELSE ? END")
      .bind(v.id, now, now, UNLOCK_WAIT, now, UNLOCK_WAIT, now).run()
    return new Response(null, { status: 303, headers: { Location: `/v/${v.id}?wrong` } })
  }
  if (fails) await env.DB.prepare("DELETE FROM unlock_fails WHERE id = ?").bind(v.id).run()
  // A password set before 1.3.0 used a single SHA-256: it's re-hashed now that we have it.
  let hash = v.pw_hash
  if (!hash.startsWith("p2$")) {
    hash = await hashPw(env, v.id, pw)
    await env.DB.prepare("UPDATE videos SET pw_hash = ? WHERE id = ? AND pw_hash = ?").bind(hash, v.id, v.pw_hash).run()
  }
  return new Response(null, { status: 303, headers: {
    Location: `/v/${v.id}`,
    "Set-Cookie": `pw_${v.id}=${await watchKey(env, v.id, hash)}; Path=/v/${v.id}; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`,
  } })
}

// The unlock cookie is an HMAC of the video's password hash, never the hash itself, so a
// copy of the database doesn't open videos. Changing the password signs every viewer out.
async function canWatch(req, env, v) {
  if (!v.pw_hash) return true
  const c = cookie(req, `pw_${v.id}`)
  return !!c && same(c, await watchKey(env, v.id, v.pw_hash))
}

async function watchKey(env, id, hash) {
  return hmac(env.PASSWORD_SALT || "", `lumen-watch:${id}:${hash}`)
}

// ---------- R2 streaming with Range

async function r2Object(req, env, key, type, disposition, cache = "private, max-age=3600") {
  const range = req.headers.get("range")
  const obj = await env.VIDEOS.get(key, range ? { range: req.headers } : {})
  if (!obj) return notFound()
  const h = new Headers({ "Content-Type": type, "Accept-Ranges": "bytes", "Cache-Control": cache, ETag: obj.httpEtag })
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
  return randomText(12)
}

// Each install has its own salt (PASSWORD_SALT, set by `lumen setup`). Changing it
// would invalidate every password already set on a video, so it never changes.
// Passwords are PBKDF2-SHA256 (100,000 rounds, the most Workers allow): "p2$<hex>".
const PW_ROUNDS = 100000
async function hashPw(env, id, pw) {
  // No silent shared fallback: without this install's own salt, passwords can't be set or checked.
  if (!env.PASSWORD_SALT) throw new Error("PASSWORD_SALT is not set")
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"])
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", iterations: PW_ROUNDS,
    salt: new TextEncoder().encode(`${env.PASSWORD_SALT}:${id}`) }, key, 256)
  return "p2$" + hex(bits)
}

async function checkPw(env, v, pw) {
  if (!v.pw_hash) return true
  if (v.pw_hash.startsWith("p2$")) return same(await hashPw(env, v.id, pw), v.pw_hash)
  // Before 1.3.0: one SHA-256 of salt, id and password.
  if (!env.PASSWORD_SALT) throw new Error("PASSWORD_SALT is not set")
  return same(await sha256(`${env.PASSWORD_SALT}:${v.id}:${pw}`), v.pw_hash)
}

async function hmac(secret, text) {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"])
  return hex(await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(text)))
}

const hex = buf => [...new Uint8Array(buf)].map(x => x.toString(16).padStart(2, "0")).join("")

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

// A dashboard session is <random id>.<issued at>.<HMAC of both under ADMIN_TOKEN>: the
// server ends it after SESSION_MS, and rotating ADMIN_TOKEN ends every session.
async function sessionSig(env, sid, iat) {
  return hmac(env.ADMIN_TOKEN, `lumen-dashboard:${sid}:${iat}`)
}

async function isAdmin(req, env) {
  return (await isAdminKey(req, env)) || (await isAdminSession(req, env))
}

async function isAdminKey(req, env) {
  if (!env.ADMIN_TOKEN) return false
  const auth = req.headers.get("authorization") || ""
  return auth.startsWith("Bearer ") && same(auth.slice(7), env.ADMIN_TOKEN)
}

async function isAdminSession(req, env) {
  if (!env.ADMIN_TOKEN) return false
  const c = cookie(req, "__Host-adm")
  if (!c || !/^[0-9a-f]{32}\.\d{13}\.[0-9a-f]{64}$/.test(c)) return false
  const [sid, iat, sig] = c.split(".")
  const age = Date.now() - Number(iat)
  return age >= 0 && age < SESSION_MS && same(sig, await sessionSig(env, sid, iat))
}

// Request bodies, read with a size limit (Content-Length first, then while streaming).
async function readBody(req, max) {
  const declared = Number(req.headers.get("content-length"))
  if (Number.isFinite(declared) && declared > max) return null
  if (!req.body) return new Uint8Array(0)
  const reader = req.body.getReader(), chunks = []
  let size = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    size += value.length
    if (size > max) { reader.cancel().catch(() => {}); return null }
    chunks.push(value)
  }
  const out = new Uint8Array(size)
  let at = 0
  for (const c of chunks) { out.set(c, at); at += c.length }
  return out
}

// A JSON object body (or {} when empty or not JSON); null when it's too big.
async function readJson(req, max) {
  const body = await readBody(req, max)
  if (!body) return null
  try {
    const o = JSON.parse(new TextDecoder().decode(body))
    return o && typeof o === "object" && !Array.isArray(o) ? o : {}
  } catch { return {} }
}

const tooBig = () => json({ error: "request too large" }, 413)
const whole = x => { const n = Number(x); return Number.isInteger(n) && n > 0 && n < 100000 ? n : null }
const num = x => { const n = Number(x); return x !== null && x !== "" && Number.isFinite(n) && n >= 0 ? n : null }
const codecOf = c => (typeof c === "string" && /^[A-Za-z0-9.+-]{1,20}$/.test(c) ? c : null)

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
  #devices { margin-top:32px; } #devices h2 { font-size:16px; margin:0 0 4px; }
  #devlist { list-style:none; padding:0; margin:12px 0 0; display:flex; flex-direction:column; gap:8px; }
  #devlist li { display:flex; justify-content:space-between; align-items:center; gap:12px; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
</style></head><body><main>
<header><div><h1><span style="background:linear-gradient(120deg,#8b8cf6,#e879f9);-webkit-background-clip:text;color:transparent">✦ Lumen</span> · Shared videos</h1><p class="muted" id="sum"></p></div>
<div><p class="muted" id="used" style="font-size:13px"></p><div class="meter"><div id="usedBar"></div></div>
<form method="post" action="/dashboard/logout" style="margin-top:8px;text-align:right"><button style="padding:4px 10px;font-size:13px">Sign out</button></form></div></header>
<div class="grid" id="grid"></div>
<section id="devices" hidden><h2>Devices</h2>
<p class="muted">Apps connected to this site with their own key (like Lumen for Chrome). They can share videos and see only the ones they made. Revoke one to disconnect it.</p>
<ul id="devlist"></ul></section>
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
        <div class="tags">\${v.source === "chrome" ? "<span>Chrome</span>" : ""}\${v.status !== "ready" ? "<span>uploading</span>" : ""}\${v.locked ? "<span>🔒 password</span>" : ""}\${exp ? "<span>"+exp+"</span>" : ""}</div>
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
async function devices() {
  const r = await fetch("/api/tokens").then(r => r.ok ? r.json() : null).catch(() => null)
  const list = (r && r.tokens) || []
  $("#devices").hidden = !list.length
  const ul = $("#devlist"); ul.innerHTML = ""
  for (const t of list) {
    const li = document.createElement("li")
    li.innerHTML = \`<div><b>\${esc(t.name)}</b><div class="facts">connected \${new Date(t.created).toLocaleDateString()} · last used \${ago(t.last_used)}</div></div><button class="danger">Revoke</button>\`
    li.querySelector("button").onclick = async () => {
      if (!confirm("Disconnect “" + t.name + "”? It can no longer share to this site. Videos it already shared stay.")) return
      await fetch("/api/tokens/" + t.id, { method: "DELETE" }); devices()
    }
    ul.appendChild(li)
  }
}
load(); devices(); setInterval(load, 30000)
</script></main></body></html>`
