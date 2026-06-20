-- Add application deadline columns to the shared jobs catalog.
-- deadline: ISO date YYYY-MM-DD, NULL means "no deadline listed".
-- deadline_source: "listing" (structured, e.g. JSON-LD validThrough) or
--                 "description" (parsed from free-text) or NULL.
ALTER TABLE jobs ADD COLUMN deadline TEXT;
ALTER TABLE jobs ADD COLUMN deadline_source TEXT;
