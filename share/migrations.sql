-- Schema version 1 (summary, link-preview card). Historical: new versions are applied
-- by the worker itself on the first API call after an update (migrate() in src/index.js),
-- so nothing here needs to be run by hand any more.
ALTER TABLE videos ADD COLUMN summary TEXT;
ALTER TABLE videos ADD COLUMN has_card INTEGER NOT NULL DEFAULT 0;
