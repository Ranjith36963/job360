-- 0081_ask_withdraw_reason: who took an ask back, and why (owner, 2026-10-23).
--
-- An assistant may now withdraw an ask IT asked, once it is out of date, and must
-- say why (at most 200 characters). The user may still withdraw any ask, reason
-- optional. Both are slots on the ask (asks are not the spine event log), set
-- once in the same UPDATE as withdrawn_at, never cleared:
--   withdrawn_by      the actor that withdrew it ('' = before this migration).
--   withdrawn_reason  the reason given ('' = none).
--
-- DDL ONLY. IF NOT EXISTS so a re-run is harmless; every existing ask reads ''.

ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS withdrawn_by TEXT NOT NULL DEFAULT '';
ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS withdrawn_reason TEXT NOT NULL DEFAULT '';
