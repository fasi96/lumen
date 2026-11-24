// Starts the share worker locally with `wrangler dev` (miniflare): R2 and D1 live in a
// throwaway folder, nothing touches Cloudflare, and your real wrangler.toml and
// .dev.vars are never read (the config is dev/wrangler.toml). Used by test/smoke.mjs
// and by Lumen for Chrome's end-to-end test.
//
//   const w = await startDevWorker({ schema: 'fresh' | 'legacy' | 'none', ai, vars })
//     ai: true = a stand-in for Workers AI (dev/ai-stub.js); vars: extra worker vars
//   w.url, w.admin (the ADMIN_TOKEN), w.salt (PASSWORD_SALT), await w.stop()

import { spawn, execFileSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SHARE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WRANGLER = path.join(SHARE, 'node_modules', '.bin', 'wrangler');
const CONFIG = path.join(SHARE, 'dev', 'wrangler.toml');
const CONFIG_AI = path.join(SHARE, 'dev', 'wrangler.ai.toml');
const ENV = { ...process.env, WRANGLER_SEND_METRICS: 'false', CI: '1', NO_COLOR: '1' };

// The database as a site from before device tokens had it (schema version 1).
export const LEGACY_SCHEMA = `CREATE TABLE IF NOT EXISTS videos (id TEXT PRIMARY KEY, title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'processing', created INTEGER NOT NULL, duration REAL, width INTEGER, height INTEGER,
  size INTEGER, codec TEXT, has_thumb INTEGER NOT NULL DEFAULT 0, views INTEGER NOT NULL DEFAULT 0, last_view INTEGER,
  pw_hash TEXT, expires INTEGER, summary TEXT, has_card INTEGER NOT NULL DEFAULT 0);
INSERT INTO videos (id, title, status, created, size) VALUES ('legacyVid123', 'Made before tokens', 'ready', 1, 1000);`;

const freePort = () => new Promise((res) => {
  const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
});

export function d1(persist, args) {
  return execFileSync(WRANGLER, ['d1', 'execute', 'lumen-dev', '--local', '--config', CONFIG, '--persist-to', persist, ...args],
    { cwd: SHARE, env: ENV, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
}

// persist: reuse an earlier run's local R2/D1 folder (then `schema` is ignored); salt: keep
// an earlier run's PASSWORD_SALT (passwords set then still check).
export async function startDevWorker({ schema = 'fresh', tmp, persist: reuse, salt: keepSalt, ai = false, vars = {} } = {}) {
  const base = tmp || (fs.existsSync('/tmp/claude-1000') ? '/tmp/claude-1000' : os.tmpdir());
  const persist = reuse || fs.mkdtempSync(path.join(base, 'lumen-share-dev-'));
  if (reuse) schema = 'none';
  if (schema === 'fresh') d1(persist, ['--file', path.join(SHARE, 'schema.sql')]);
  if (schema === 'legacy') {
    const f = path.join(persist, 'legacy.sql');
    fs.writeFileSync(f, LEGACY_SCHEMA);
    d1(persist, ['--file', f]);
  }
  const port = await freePort();
  const admin = crypto.randomBytes(24).toString('hex');
  const salt = keepSalt || crypto.randomBytes(16).toString('hex');
  const child = spawn(WRANGLER, ['dev', '--config', ai ? CONFIG_AI : CONFIG, '--ip', '127.0.0.1', '--port', String(port),
    '--persist-to', persist, '--var', `ADMIN_TOKEN:${admin}`, '--var', `PASSWORD_SALT:${salt}`,
    ...Object.entries(vars).flatMap(([k, v]) => ['--var', `${k}:${v}`]),
    '--show-interactive-dev-session=false', '--log-level', 'warn'],
  { cwd: SHARE, env: ENV, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '';
  child.stdout.on('data', (d) => { log += d; });
  child.stderr.on('data', (d) => { log += d; });
  const url = `http://127.0.0.1:${port}`;
  const t0 = Date.now();
  for (;;) {
    try {
      const r = await fetch(`${url}/`);
      if (r.status === 404) break;          // the worker's own "Nothing here" page
    } catch { /* not up yet */ }
    if (child.exitCode != null) throw new Error(`wrangler dev exited:\n${log}`);
    if (Date.now() - t0 > 60000) { process.kill(-child.pid, 'SIGTERM'); throw new Error(`wrangler dev didn't start:\n${log}`); }
    await new Promise((r) => setTimeout(r, 300));
  }
  return {
    url, admin, salt, persist, log: () => log,
    // keep: leave the local R2/D1 folder for another run.
    async stop({ keep = false } = {}) {
      try { process.kill(-child.pid, 'SIGTERM'); } catch { /* gone */ }   // its own process group, by PID
      await new Promise((r) => setTimeout(r, 500));
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* gone */ }
      if (!keep) fs.rmSync(persist, { recursive: true, force: true });
    },
  };
}
