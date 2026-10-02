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
  has_card    INTEGER NOT NULL DEFAULT 0 -- a 1200x630 link-preview card exists
);
