-- 0047 down: reverses the DDL only. No pre-existing `users` row or other
-- column is touched beyond the two added by the up file.
--
-- WHAT THIS COSTS, STATED PLAINLY: whether a user's `first_tool_call` /
-- `first_bring` event already fired is forgotten. A re-apply of the up
-- migration would let both fire again for every existing user on their next
-- tool call / bring — acceptable for a rarely-rolled-back analytics slot,
-- never for anything the product itself reads.

ALTER TABLE users DROP COLUMN IF EXISTS first_tool_call_at;
ALTER TABLE users DROP COLUMN IF EXISTS first_bring_at;
