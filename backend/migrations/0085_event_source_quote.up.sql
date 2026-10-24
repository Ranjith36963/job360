-- 0085_event_source_quote: feature 9 (G073) - an email-sourced event can keep the ONE
-- sentence that decides it, copied verbatim (source.quote). Checked in
-- spine.validate_source: whitespace collapsed to single spaces, other control
-- characters refused, <= APPLICATION_EVENT_SOURCE_QUOTE_MAX_CHARS (500).
--
-- DDL ONLY. No existing row is read, copied or changed. Conventions copied
-- from 0041/0068: TEXT NOT NULL DEFAULT '' ('' = none), IF NOT EXISTS.
-- No index: nothing looks a quote up.

ALTER TABLE application_events ADD COLUMN IF NOT EXISTS source_quote TEXT NOT NULL DEFAULT '';  -- '' = no quote
