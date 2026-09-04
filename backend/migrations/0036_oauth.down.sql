-- 0036 down: every OAuth client, grant, code and token is gone the moment
-- this runs. Drop in child-to-parent order.
DROP INDEX IF EXISTS idx_oauth_tokens_grant_kind;
DROP TABLE IF EXISTS oauth_tokens;

DROP INDEX IF EXISTS idx_oauth_codes_grant;
DROP TABLE IF EXISTS oauth_authorization_codes;

DROP INDEX IF EXISTS uidx_oauth_grants_user_client_active;
DROP INDEX IF EXISTS idx_oauth_grants_user;
DROP TABLE IF EXISTS oauth_grants;

DROP INDEX IF EXISTS idx_oauth_authz_requests_client;
DROP TABLE IF EXISTS oauth_authorization_requests;

DROP TABLE IF EXISTS oauth_clients;
