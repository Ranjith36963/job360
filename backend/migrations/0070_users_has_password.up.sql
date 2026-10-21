-- 0070_users_has_password: does this account have a password the user knows?
--
-- A magic-link sign-in creates the account with an argon2 hash of a random secret
-- (the schema needs one; services/auth/magic_link.py), so `password_hash` alone
-- cannot say whether the user ever chose a password. Without that, a
-- magic-link-only user could never delete their account (GDPR Art. 17): the
-- delete route asked for a "current password" they never had.
--
-- TRUE = the user set a password (register, password reset). FALSE = only a
-- magic link has ever signed this account in. Written by: magic_link (FALSE on
-- the INSERT that creates the row), password_reset confirm (TRUE). Read by
-- DELETE /api/auth/users/me and GET /api/auth/me.
--
-- Backfill: an account the magic link CREATED has email_verified_at equal to the
-- used_at of the token that created it (one `_now_iso()` value written to both),
-- unless the user later completed a password reset. Everything else keeps TRUE,
-- the old behaviour (password asked). A re-run changes nothing new.

ALTER TABLE users ADD COLUMN IF NOT EXISTS has_password BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE users SET has_password = FALSE
WHERE email_verified_at IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM magic_link_tokens m
      WHERE m.used_at = users.email_verified_at AND LOWER(m.email) = LOWER(users.email)
  )
  AND NOT EXISTS (
      SELECT 1 FROM password_resets r WHERE r.user_id = users.id AND r.used_at IS NOT NULL
  );
