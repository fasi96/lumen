-- The share site's database (D1). `lumen setup` runs this once on a new site.
-- Sites set up earlier don't need to run anything: the worker adds what's missing
-- itself on the first API call after an update (see migrate() in src/index.js).
CREATE TABLE IF NOT EXISTS videos (
  id          TEXT PRIMARY KEY,          -- the public, unguessable link id
  title       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'processing',   -- processing | ready
  created     INTEGER NOT NULL,          -- ms since epoch
  duration    REAL,                      -- seconds
  width       INTEGER,
  height      INTEGER,
  size        INTEGER,                   -- bytes
  codec       TEXT,
  has_thumb   INTEGER NOT NULL DEFAULT 0,
  views       INTEGER NOT NULL DEFAULT 0,
  last_view   INTEGER,
  pw_hash     TEXT,                      -- sha-256 of salt+password, null = open
  expires     INTEGER,                   -- ms since epoch, null = never
  summary     TEXT,                      -- AI summary: link previews, dashboard
  has_card    INTEGER NOT NULL DEFAULT 0, -- a 1200x630 link-preview card exists
  created_by  TEXT,                      -- device token id that made it; null = the owner (vshare)
  source      TEXT,                      -- 'desktop' | 'chrome'
  cut_seconds REAL                       -- seconds of ums/pauses cut (shown on the dashboard)
);

-- Connected devices (Lumen for Chrome…): only a SHA-256 of each token is kept.
CREATE TABLE IF NOT EXISTS tokens (
  id        TEXT PRIMARY KEY,
  hash      TEXT NOT NULL,
  name      TEXT,
  scope     TEXT NOT NULL DEFAULT 'record',
  created   INTEGER NOT NULL,
  last_used INTEGER
);
CREATE INDEX IF NOT EXISTS videos_created_by ON videos (created_by);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', '2');
