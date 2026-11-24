// Smoke test for the share worker, run locally with `wrangler dev` (nothing is deployed;
// see devserver.mjs). Checks that the desktop flow (vshare's API calls with ADMIN_TOKEN,
// the share page, Range streaming, the dashboard sign-in) still works, plus device
// tokens, CORS for Lumen for Chrome, /api/info, the per-device daily limits, and the
// self-migration of a site whose database predates device tokens.
//
//   cd share && node test/smoke.mjs        (needs `npm ci` in share/ first)

import { startDevWorker, d1 } from './devserver.mjs';

let failures = 0;
const ok = (cond, what) => { console.log(`${cond ? '  ✓' : '  ✗'} ${what}`); if (!cond) failures++; };
const EXT = 'chrome-extension://abcdefghijklmnopabcdefghijklmnop';

function client(url, token) {
  return async (method, path, body, { headers = {}, raw = false } = {}) => {
    const h = { ...headers };
    if (token) h.Authorization = `Bearer ${token}`;
    let data = body;
    if (body && !(body instanceof Uint8Array) && typeof body !== 'string') { data = JSON.stringify(body); h['Content-Type'] = 'application/json'; }
    const r = await fetch(url + path, { method, headers: h, body: data, redirect: 'manual' });
    if (raw) return r;
    const text = await r.text();
    let j = null;
    try { j = JSON.parse(text); } catch { /* not JSON */ }
    return { status: r.status, json: j, text, headers: r.headers };
  };
}

// Upload bytes into a share the way vshare does: thumb, multipart in 10 MiB parts, complete.
async function uploadVideo(call, id, bytes) {
  const PART = 10 * 1024 * 1024;
  let r = await call('PUT', `/api/videos/${id}/thumb`, new Uint8Array([0xff, 0xd8, 0xff, 0xd9]), { headers: { 'Content-Type': 'image/jpeg' } });
  if (r.status !== 200) return r;
  r = await call('POST', `/api/videos/${id}/mpu`);
  if (r.status !== 200) return r;
  const up = r.json.uploadId;
  const parts = [];
  for (let n = 1, off = 0; off < bytes.length; n++, off += PART) {
    const p = await call('PUT', `/api/videos/${id}/mpu/${encodeURIComponent(up)}/${n}`, bytes.subarray(off, off + PART),
      { headers: { 'Content-Type': 'application/octet-stream' } });
    if (p.status !== 200) return p;
    parts.push({ partNumber: p.json.partNumber, etag: p.json.etag });
  }
  return call('POST', `/api/videos/${id}/mpu/${encodeURIComponent(up)}/complete`, { parts, duration: 3.2, width: 1280, height: 720, codec: 'vp9' });
}

// The local database is only read with `wrangler d1 execute` while the worker is
// stopped (two processes on one SQLite file can trip each other up).
let secretToCheck = null;

async function fresh() {
  console.log('Fresh site (schema.sql):');
  const w = await startDevWorker({ schema: 'fresh' });
  try {
    const admin = client(w.url, w.admin);
    const anon = client(w.url);

    // ---- the desktop flow, unchanged
    ok((await anon('GET', '/api/videos')).status === 401, 'API without a key: 401');
    const c = await admin('POST', '/api/videos', { title: 'Desktop video', codec: 'h264' });
    ok(c.status === 200 && /\/v\/[A-Za-z0-9]{12}$/.test(c.json.url), `vshare create → ${c.json?.url}`);
    const big = new Uint8Array(10 * 1024 * 1024 + 4321).map((_, i) => i % 251);
    const done = await uploadVideo(admin, c.json.id, big);
    ok(done.status === 200 && done.json.size === big.length, `multipart upload in 10 MiB parts → ${done.json?.size} bytes`);
    const pageR = await anon('GET', `/v/${c.json.id}`);
    ok(pageR.status === 200 && pageR.text.includes('Desktop video'), 'share page shows the video');
    const rng = await anon('GET', `/v/${c.json.id}/video.mp4`, null, { headers: { Range: 'bytes=0-99' } });
    ok(rng.status === 206 && rng.headers.get('content-range') === `bytes 0-99/${big.length}`, `Range request → 206 ${rng.headers.get('content-range')}`);
    ok((await anon('GET', `/v/${c.json.id}/thumb.jpg`)).status === 200, 'thumb.jpg served');
    ok((await admin('PATCH', `/api/videos/${c.json.id}`, { title: 'Renamed' })).status === 200, 'PATCH title');
    const pw = await admin('PATCH', `/api/videos/${c.json.id}`, { password: 'hunter2' });
    const locked = await anon('GET', `/v/${c.json.id}`);
    ok(pw.status === 200 && locked.text.includes('password protected'), 'password locks the page');
    ok((await anon('GET', `/v/${c.json.id}/video.mp4`)).status === 403, 'locked video not served');
    await admin('PATCH', `/api/videos/${c.json.id}`, { password: '' });
    const list = await admin('GET', '/api/videos');
    ok(list.status === 200 && list.json.videos.length === 1 && list.json.videos[0].source === 'desktop', 'vshare list (source = desktop)');

    // ---- dashboard sign-in (session cookie, never the key)
    const form = new URLSearchParams({ key: w.admin });
    const login = await fetch(`${w.url}/dashboard/login`, { method: 'POST', body: form, redirect: 'manual' });
    const cookie = (login.headers.get('set-cookie') || '').split(';')[0];
    ok(login.status === 200 && /^__Host-adm=[0-9a-f]{32}\.\d{13}\.[0-9a-f]{64}$/.test(cookie), 'dashboard login sets a session cookie (id, issued-at, signature)');
    const dash = await fetch(`${w.url}/dashboard`, { headers: { cookie } });
    ok(dash.status === 200 && (await dash.text()).includes('Devices'), 'dashboard opens with the cookie (has the Devices list)');
    ok((await client(w.url)('GET', '/api/videos', null, { headers: { cookie } })).status === 200, 'dashboard API works with the cookie');
    ok((await fetch(`${w.url}/dashboard`)).status === 403, 'dashboard without the cookie: 403');

    // ---- device tokens
    const mint = await admin('POST', '/api/tokens', { name: 'Test Chrome' });
    ok(mint.status === 200 && /^lt_[A-Za-z0-9]{12}_[A-Za-z0-9]{32}$/.test(mint.json.token), 'POST /api/tokens mints lt_<id>_<secret>');
    const toks = await admin('GET', '/api/tokens');
    ok(toks.json.tokens.length === 1 && !('hash' in toks.json.tokens[0]) && !('token' in toks.json.tokens[0]), 'GET /api/tokens lists devices without secrets or hashes');
    secretToCheck = mint.json.token;
    const dev = client(w.url, mint.json.token);
    const inf = await dev('GET', '/api/info');
    ok(inf.status === 200 && inf.json.features.includes('tokens') && inf.json.you.kind === 'device' && inf.json.you.name === 'Test Chrome',
      `GET /api/info as the device: api ${inf.json?.api}, features ${inf.json?.features}`);
    ok((await dev('GET', '/api/tokens')).status === 403, 'device can’t list devices');
    ok((await dev('POST', '/api/tokens', { name: 'x' })).status === 403, 'device can’t mint tokens');
    const dc = await dev('POST', '/api/videos', { title: 'From Chrome', codec: 'vp9' });
    ok(dc.status === 200, 'device creates a share');
    const dup = await uploadVideo(dev, dc.json.id, new Uint8Array(2000).fill(7));
    ok(dup.status === 200, 'device uploads into its own share');
    ok((await dev('PUT', `/api/videos/${dc.json.id}/card`, new Uint8Array([1, 2]), { headers: { 'Content-Type': 'image/jpeg' } })).status === 200, 'device puts a card');
    ok((await dev('PUT', `/api/videos/${dc.json.id}/sprite`, new Uint8Array([1, 2]), { headers: { 'Content-Type': 'image/jpeg' } })).status === 200, 'device puts a sprite');
    ok((await dev('PUT', `/api/videos/${dc.json.id}/transcript`, '{"storyboard":{"count":1}}', { headers: { 'Content-Type': 'application/json' } })).status === 200, 'device puts a transcript');
    const dl = await dev('GET', '/api/videos');
    ok(dl.json.videos.length === 1 && dl.json.videos[0].id === dc.json.id && dl.json.videos[0].source === 'chrome', 'device lists only its own video (source = chrome)');
    ok((await dev('DELETE', `/api/videos/${c.json.id}`)).status === 404, 'device can’t delete the owner’s video (404)');
    ok((await dev('PATCH', `/api/videos/${c.json.id}`, { title: 'hijack' })).status === 404, 'device can’t rename the owner’s video');
    ok((await dev('PUT', `/api/videos/${c.json.id}/thumb`, new Uint8Array([1]))).status === 404, 'device can’t overwrite the owner’s thumb');
    ok((await dev('POST', `/api/videos/${c.json.id}/mpu`)).status === 404, 'device can’t upload into the owner’s video');
    ok((await admin('GET', '/api/videos')).json.videos.length === 2, 'the owner still sees both');

    // ---- CORS for the extension
    const pre = await fetch(`${w.url}/api/videos`, { method: 'OPTIONS', headers: { Origin: EXT, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type' } });
    ok(pre.status === 204 && pre.headers.get('access-control-allow-origin') === EXT && /Authorization/.test(pre.headers.get('access-control-allow-headers')),
      'OPTIONS from a chrome-extension:// origin: 204 + allow headers');
    const evil = await fetch(`${w.url}/api/videos`, { method: 'OPTIONS', headers: { Origin: 'https://evil.example' } });
    ok(!evil.headers.get('access-control-allow-origin'), 'other web origins get no CORS');
    const got = await dev('GET', '/api/info', null, { headers: { Origin: EXT } });
    ok(got.headers.get('access-control-allow-origin') === EXT && /Origin/.test(got.headers.get('vary')), 'API answers carry Access-Control-Allow-Origin + Vary: Origin');
    const unauth = await anon('GET', '/api/info', null, { headers: { Origin: EXT } });
    ok(unauth.status === 401 && unauth.headers.get('access-control-allow-origin') === EXT, '401s carry CORS too (the extension can read the error)');
    ok(!(await anon('GET', `/v/${c.json.id}`, null, { headers: { Origin: EXT } })).headers.get('access-control-allow-origin'), 'share pages get no CORS headers');

    // ---- revoke / disconnect
    ok((await dev('DELETE', `/api/videos/${dc.json.id}`)).status === 200, 'device deletes its own video');
    const m2 = await admin('POST', '/api/tokens', { name: 'Second' });
    const dev2 = client(w.url, m2.json.token);
    ok((await admin('DELETE', `/api/tokens/${m2.json.id}`)).status === 200 && (await dev2('GET', '/api/info')).status === 401, 'owner revokes a device → its token stops working');
    ok((await dev('DELETE', '/api/tokens/self')).status === 200 && (await dev('GET', '/api/info')).status === 401, 'DELETE /api/tokens/self disconnects the device');
    ok((await client(w.url, 'lt_aaaaaaaaaaaa_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')('GET', '/api/info')).status === 401, 'made-up token: 401');
    ok((await admin('DELETE', '/api/tokens/self')).status === 400, 'the owner key can’t "self-delete"');
    ok((await admin('DELETE', `/api/videos/${c.json.id}`)).status === 200 && (await anon('GET', `/v/${c.json.id}`)).status === 404, 'owner deletes a video; link stops working');
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e, '\n', w.log().slice(-2000));
  }
  await w.stop({ keep: true });
  const rows = d1(w.persist, ['--command', 'SELECT hash FROM tokens', '--json']);
  ok(secretToCheck && !rows.includes(secretToCheck) && !rows.includes(secretToCheck.slice(16)), 'no token secret in the database (hashes only)');
  (await import('node:fs')).rmSync(w.persist, { recursive: true, force: true });
}

// 1.3.0 hardening, from a security review: device keys can't plant markup, the dashboard
// cookie only acts from the site's own pages, passwords resist guessing, bodies are capped.
async function hardening() {
  console.log('Hardening (1.3.0):');
  const w = await startDevWorker({ schema: 'fresh' });
  let vidLocked = null;
  try {
    const admin = client(w.url, w.admin);
    const anon = client(w.url);
    const mint = await admin('POST', '/api/tokens', { name: 'Probe' });
    const dev = client(w.url, mint.json.token);

    // A device sends markup where numbers and codec names belong.
    const bad = '1"><script>alert(1)</script>';
    const c = await dev('POST', '/api/videos', { title: 'Probe', codec: bad });
    const up = (await dev('POST', `/api/videos/${c.json.id}/mpu`)).json.uploadId;
    const part = await dev('PUT', `/api/videos/${c.json.id}/mpu/${encodeURIComponent(up)}/1`, new Uint8Array(1000).fill(3), { headers: { 'Content-Type': 'application/octet-stream' } });
    const fin = await dev('POST', `/api/videos/${c.json.id}/mpu/${encodeURIComponent(up)}/complete`,
      { parts: [{ partNumber: 1, etag: part.json.etag }], width: bad, height: bad, duration: bad, codec: bad });
    const page = await anon('GET', `/v/${c.json.id}`);
    ok(fin.status === 200 && !page.text.includes('<script>alert'), 'width/height/codec markup never reaches the share page');
    const row = (await dev('GET', '/api/videos')).json.videos[0];
    ok(row.width === null && row.height === null && row.duration === null && row.codec === null, 'non-numbers are stored as null');

    // Transcripts: JSON objects only, re-serialised, capped.
    const T = (body) => dev('PUT', `/api/videos/${c.json.id}/transcript`, body, { headers: { 'Content-Type': 'application/json' } });
    ok((await T('<script>')).status === 400 && (await T('[1,2]')).status === 400, 'a transcript that is not a JSON object is refused');
    ok((await T(new Uint8Array(4 * 1024 * 1024 + 1).fill(32))).status === 413, 'a transcript over 4 MB is refused (413)');
    ok((await T('{"cleanup":{"fillers":"<img src=x onerror=alert(1)>","marks":[{"t":1}]}}')).status === 200, 'odd but valid JSON is stored (the page reads it as data)');
    ok((await dev('PUT', `/api/videos/${c.json.id}/card`, new Uint8Array(8 * 1024 * 1024 + 1), { headers: { 'Content-Type': 'image/jpeg' } })).status === 413, 'images over 8 MB are refused (413)');

    // The dashboard cookie: only from the site itself, ends on the server, can sign out.
    const login = await fetch(`${w.url}/dashboard/login`, { method: 'POST', body: new URLSearchParams({ key: w.admin }), redirect: 'manual' });
    const cookie = (login.headers.get('set-cookie') || '').split(';')[0];
    const viaCookie = (method, path, origin) => fetch(w.url + path, { method, headers: { cookie, 'Content-Type': 'text/plain', ...(origin ? { Origin: origin } : {}) }, body: method === 'GET' ? undefined : '{"name":"x"}' });
    ok((await viaCookie('POST', '/api/tokens', 'https://evil.example')).status === 401, 'cookie + another site’s Origin can’t mint tokens (401)');
    ok((await viaCookie('POST', '/api/tokens', null)).status === 401, 'cookie without an Origin can’t change anything (401)');
    ok((await viaCookie('POST', '/api/tokens', w.url)).status === 200, 'cookie from the dashboard itself still works');
    ok((await viaCookie('GET', '/api/videos', null)).status === 200, 'cookie GETs still work (the dashboard list)');
    const [sid, , sig] = cookie.split('=')[1].split('.');
    const old = `__Host-adm=${sid}.${Date.now() - 31 * 86400000}.${sig}`;
    ok((await fetch(`${w.url}/dashboard`, { headers: { cookie: old } })).status === 403, 'a session older than 30 days (or forged date) is refused');
    ok((await fetch(`${w.url}/dashboard`, { headers: { cookie: 'adm=' + 'a'.repeat(32) + '.' + 'b'.repeat(64) } })).status === 403, 'the pre-1.3.0 cookie name no longer signs in');
    const out = await fetch(`${w.url}/dashboard/logout`, { method: 'POST', headers: { cookie }, redirect: 'manual' });
    ok(out.status === 303 && /__Host-adm=;.*Max-Age=0/.test(out.headers.get('set-cookie') || ''), 'Sign out clears the cookie');

    // Passwords: PBKDF2, a private title, an HMAC cookie, and a lock after 10 wrong tries.
    const o = await admin('POST', '/api/videos', { title: 'Secret plans' });
    vidLocked = o.json.id;
    await admin('PATCH', `/api/videos/${o.json.id}`, { password: 'hunter2' });
    const lockedPage = await anon('GET', `/v/${o.json.id}`);
    ok(lockedPage.text.includes('Password-protected video') && !lockedPage.text.includes('Secret plans'), 'a locked page doesn’t show the title');
    const unlockWith = (pw) => fetch(`${w.url}/v/${o.json.id}/unlock`, { method: 'POST', body: new URLSearchParams({ password: pw }), redirect: 'manual' });
    const good = await unlockWith('hunter2');
    const pwc = (good.headers.get('set-cookie') || '').split(';')[0];
    ok(good.status === 303 && good.headers.get('location') === `/v/${o.json.id}` && pwc.startsWith(`pw_${o.json.id}=`), 'right password → cookie');
    ok((await fetch(`${w.url}/v/${o.json.id}`, { headers: { cookie: pwc } })).status === 200, 'the cookie opens the page');
    const weird = await fetch(`${w.url}/v/${o.json.id}/unlock`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}', redirect: 'manual' });
    ok(weird.status === 303 && !(await weird.text()).includes('FormData'), 'a malformed unlock gets the wrong-password page, no internal error text');
    for (let i = 0; i < 10; i++) await unlockWith('nope' + i);
    const blocked = await unlockWith('hunter2');
    ok(blocked.status === 303 && /\?wait$/.test(blocked.headers.get('location') || ''), 'after 10 wrong tries even the right password waits 15 minutes');
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e, '\n', w.log().slice(-2000));
  }
  await w.stop({ keep: true });
  const hash = d1(w.persist, ['--command', `SELECT pw_hash FROM videos WHERE id = '${vidLocked}'`, '--json']);
  ok(/"pw_hash":\s*"p2\$[0-9a-f]{64}"/.test(hash), 'passwords are stored as PBKDF2 (p2$…)');

  // A password set before 1.3.0 (one SHA-256) still opens the video, and is upgraded.
  const crypto = await import('node:crypto');
  const legacyHash = crypto.createHash('sha256').update(`${w.salt}:${vidLocked}:hunter2`).digest('hex');
  d1(w.persist, ['--command', `UPDATE videos SET pw_hash = '${legacyHash}' WHERE id = '${vidLocked}'; DELETE FROM unlock_fails;`]);
  const w2 = await startDevWorker({ persist: w.persist, salt: w.salt });
  try {
    const r = await fetch(`${w2.url}/v/${vidLocked}/unlock`, { method: 'POST', body: new URLSearchParams({ password: 'hunter2' }), redirect: 'manual' });
    ok(r.status === 303 && r.headers.get('location') === `/v/${vidLocked}`, 'an old (SHA-256) password still unlocks');
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e, '\n', w2.log().slice(-2000));
  }
  await w2.stop({ keep: true });
  ok(/"pw_hash":\s*"p2\$/.test(d1(w.persist, ['--command', `SELECT pw_hash FROM videos WHERE id = '${vidLocked}'`, '--json'])), '…and is re-hashed as PBKDF2 on that unlock');
  (await import('node:fs')).rmSync(w.persist, { recursive: true, force: true });
}

// 1.3.1: transcription counts by minutes of audio (live captions call it every few seconds),
// not against the AI-call cap, which still holds for /api/complete.
async function audioMinutes() {
  console.log('Transcription by audio minutes (1.3.1):');
  const w = await startDevWorker({ schema: 'fresh', ai: true, vars: { DEVICE_AI_PER_DAY: '2', DEVICE_AUDIO_MIN_PER_DAY: '1' } });
  try {
    const admin = client(w.url, w.admin);
    const dev = client(w.url, (await admin('POST', '/api/tokens', { name: 'Captions' })).json.token);
    // n seconds of silence as a 16 kHz, 16-bit mono WAV.
    const wav = (n) => {
      const b = Buffer.alloc(44 + n * 32000);
      b.write('RIFF', 0); b.writeUInt32LE(36 + n * 32000, 4); b.write('WAVEfmt ', 8); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20);
      b.writeUInt16LE(1, 22); b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
      b.write('data', 36); b.writeUInt32LE(n * 32000, 40);
      return new Uint8Array(b);
    };
    const T = (who, n) => who('POST', '/api/transcribe?language=en', wav(n), { headers: { 'Content-Type': 'audio/wav' } });
    let inf = (await dev('GET', '/api/info')).json;
    ok(inf.features.includes('audio-minutes') && inf.schema === 4, 'the site says it counts audio minutes (schema 4)');
    ok(inf.you.limits?.audioMin === 1 && inf.you.today?.audioMin === 0, '/api/info shows the device’s allowance and what it used today');
    const runs = [];
    for (let i = 0; i < 5; i++) runs.push((await T(dev, 10)).status);
    ok(runs.every((x) => x === 200), `5 transcriptions pass a 2-call AI cap (${runs.join(' ')}): they count by minutes`);
    inf = (await dev('GET', '/api/info')).json;
    ok(Math.abs(inf.you.today.audioMin - 0.8) < 0.05, `50 s of audio counted as ${inf.you.today.audioMin} min`);
    ok((await T(dev, 10)).status === 200, 'the call that crosses the minute still finishes');
    const over = await T(dev, 1);
    ok(over.status === 429 && /minutes of transcription/.test(over.json?.error || ''), 'the next one is refused (429) and says why');
    ok((await T(admin, 5)).status === 200, 'the owner key is never limited');
    const c = [];
    for (let i = 0; i < 3; i++) c.push((await dev('POST', '/api/complete', { prompt: 'Title?' })).status);
    ok(c[0] === 200 && c[1] === 200 && c[2] === 429, `/api/complete keeps the AI-call cap (${c.join(' ')})`);
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e, '\n', w.log().slice(-2000));
  }
  await w.stop();
}

async function legacy() {
  console.log('Existing site from before device tokens (schema version 1):');
  let w = await startDevWorker({ schema: 'legacy' });
  try {
    ok((await client(w.url)('GET', '/v/legacyVid123')).status === 200, 'old share page works before any API call');
    const list = await client(w.url, w.admin)('GET', '/api/videos');
    ok(list.status === 200 && list.json.videos[0].id === 'legacyVid123', 'first API call works and lists the old video');
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e, '\n', w.log().slice(-2000));
  }
  await w.stop({ keep: true });
  const cols = d1(w.persist, ['--command', 'PRAGMA table_info(videos)', '--json']);
  ok(['created_by', 'source', 'cut_seconds'].every((c) => cols.includes(c)), 'the worker added created_by, source, cut_seconds');
  ok(d1(w.persist, ['--command', 'PRAGMA table_info(usage)', '--json']).includes('audio_s'), 'the worker added usage.audio_s (1.3.1)');
  ok(/"v":\s*"4"/.test(d1(w.persist, ['--command', "SELECT v FROM meta WHERE k = 'schema_version'", '--json'])), 'meta.schema_version = 4');
  // Running the migration again (say, two copies of the worker at once) is harmless:
  // forget the version, restart on the same database, use the API again.
  d1(w.persist, ['--command', "DELETE FROM meta WHERE k = 'schema_version'"]);
  w = await startDevWorker({ persist: w.persist });
  try {
    const admin = client(w.url, w.admin);
    const r = await admin('GET', '/api/videos');
    ok(r.status === 200 && r.json.videos.length === 1, 'migration runs again without errors (columns already there)');
    const mint = await admin('POST', '/api/tokens', { name: 'After update' });
    ok(mint.status === 200 && (await client(w.url, mint.json.token)('POST', '/api/videos', { title: 'x' })).status === 200, 'device tokens work on the updated site');
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e, '\n', w.log().slice(-2000));
  } finally {
    await w.stop();
  }
}

// The real `vshare` command against the local worker, in a throwaway HOME (your own
// vshare settings are never read or written): setup, create, upload, list, connect-code.
async function desktop() {
  console.log('The vshare command (desktop flow) against the local worker:');
  const { execFileSync, spawnSync } = await import('node:child_process');
  const fs = await import('node:fs');
  const path = await import('node:path');
  const w = await startDevWorker({ schema: 'fresh' });
  const home = fs.mkdtempSync(path.join(path.dirname(w.persist), 'lumen-vshare-home-'));
  const bin = path.join(home, 'bin');
  fs.mkdirSync(bin);
  // No desktop pop-ups or clipboard from a test.
  for (const t of ['omarchy-notification-send', 'wl-copy', 'xdg-open']) fs.writeFileSync(path.join(bin, t), '#!/bin/sh\ncat >/dev/null 2>&1; exit 0\n', { mode: 0o755 });
  const vshare = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..', '..', 'bin', 'vshare');
  const env = { ...process.env, HOME: home, PATH: `${bin}:${process.env.PATH}`, XDG_CACHE_HOME: path.join(home, '.cache'),
    XDG_DATA_HOME: path.join(home, '.local/share'), LUMEN_VENV: path.join(home, 'no-venv') };
  const run = (args, input) => {
    const r = spawnSync(vshare, args, { env, input, encoding: 'utf8', timeout: 120000 });
    if (r.status !== 0) throw new Error(`vshare ${args.join(' ')} → ${r.status}: ${r.stderr}`);
    return r.stdout.trim();
  };
  try {
    ok(run(['setup', 'cloudflare', w.url, '-'], `${w.admin}\n`) === 'saved', 'vshare setup cloudflare URL - (key on stdin)');
    const mp4 = path.join(home, 'clip.mp4');
    execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30', '-f', 'lavfi', '-i', 'sine=f=440',
      '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', mp4]);
    const created = JSON.parse(run(['create', '--title', 'Desktop smoke']));
    ok(/^[A-Za-z0-9]{12}$/.test(created.id), `vshare create → ${created.url}`);
    const tr = path.join(home, 't.json');
    fs.writeFileSync(tr, JSON.stringify({ cues: [{ s: 0, e: 1, t: 'hello' }] }));
    run(['upload', mp4, '--id', created.id, '--title', 'Desktop smoke', '--transcript', tr]);
    const st = await (await fetch(`${w.url}/v/${created.id}/status`)).json();
    ok(st.status === 'ready', 'vshare upload → the share is ready');
    const r = await fetch(`${w.url}/v/${created.id}/video.mp4`, { headers: { Range: 'bytes=0-9' } });
    ok(r.status === 206 && Number(r.headers.get('content-range').split('/')[1]) === fs.statSync(mp4).size, 'uploaded video streams with Range (206, full size)');
    ok((await fetch(`${w.url}/v/${created.id}/thumb.jpg`)).status === 200, 'vshare uploaded the poster');
    ok(JSON.parse(run(['list']))[0].id === created.id, 'vshare list');
    const code = run(['connect-code', '--name', 'Smoke Chrome']);
    ok(/^lumen1:[A-Za-z0-9_-]+$/.test(code), 'vshare connect-code prints lumen1:<base64url>');
    const j = JSON.parse(Buffer.from(code.slice(7), 'base64url').toString());
    ok(j.u === w.url && j.n === 'Smoke Chrome' && /^lt_/.test(j.t), 'the code holds the site URL, a device key and its name');
    const inf = await (await fetch(`${w.url}/api/info`, { headers: { Authorization: `Bearer ${j.t}` } })).json();
    ok(inf.you?.kind === 'device' && inf.you.name === 'Smoke Chrome', 'the device key works (GET /api/info)');
    ok(run(['connect-code', '--copy', '--name', 'Copied']).startsWith('Copied a connection code'), 'connect-code --copy (clipboard, nothing printed)');
  } catch (e) {
    failures++;
    console.error('  ✗ crashed:', e.message, '\n', w.log().slice(-1500));
  } finally {
    await w.stop();
    fs.rmSync(home, { recursive: true, force: true });
  }
}

await fresh();
await hardening();
await audioMinutes();
await legacy();
await desktop();
console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
process.exit(failures ? 1 : 0);
