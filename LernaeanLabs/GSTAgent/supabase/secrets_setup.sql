-- =============================================================================
-- GSTAgent — Secrets Setup  (run ONCE, after schema.sql)
-- =============================================================================
-- Supabase Dashboard → SQL Editor → New Query → paste → Run
--
-- What this does:
--   1. Stores your GSP encryption key inside Supabase Vault (pgsodium-encrypted)
--   2. Verifies the secret was stored correctly
--   3. Tests encrypt → decrypt round-trip using pgcrypto + Vault key
--
-- After this runs, Python NEVER needs to hold the encryption key.
-- All encrypt/decrypt happens inside Postgres via the RPC functions in schema.sql.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- STEP 1: Store GSP key in Vault
-- ---------------------------------------------------------------------------
-- Generate a passphrase in your terminal (32+ chars) — do NOT paste it into
-- this file. Paste it directly into the SQL Editor, run once, then discard it
-- from your clipboard/shell history. This file must never hold a real value.
--
--   python3 -c "import secrets; print(secrets.token_urlsafe(40))"
--
-- Already have a 'gsp_key' secret and rotating it? Use vault.update_secret()
-- instead of create_secret() so you update in place rather than duplicate:
--
--   select vault.update_secret(
--     (select id from vault.secrets where name = 'gsp_key'),
--     '<PASTE-YOUR-NEW-PASSPHRASE-HERE>'
--   );

select vault.create_secret(
  '<PASTE-YOUR-GENERATED-PASSPHRASE-HERE>',
  'gsp_key',
  'GSP OTP session token encryption key — managed by Supabase Vault'
);


-- ---------------------------------------------------------------------------
-- STEP 2: Verify it was stored (value is masked — you only see the name)
-- ---------------------------------------------------------------------------

select id, name, description, created_at
from vault.secrets
where name = 'gsp_key';


-- ---------------------------------------------------------------------------
-- STEP 3: Smoke test — encrypt then decrypt using the Vault key
-- ---------------------------------------------------------------------------
-- Expected output: decrypted_token = "test-otp-session-token-abc123"
-- If this works, your full encryption pipeline is verified.

select extensions.pgp_sym_decrypt(
  extensions.pgp_sym_encrypt(
    'test-otp-session-token-abc123'::text,
    (select decrypted_secret from vault.decrypted_secrets where name = 'gsp_key')
  )::bytea,
  (select decrypted_secret from vault.decrypted_secrets where name = 'gsp_key')
) as decrypted_token;


-- ---------------------------------------------------------------------------
-- STEP 4: Test the RPC functions defined in schema.sql
-- (Run these AFTER schema.sql has been executed)
-- ---------------------------------------------------------------------------

-- 4a. Write an encrypted session for the test client
select upsert_gsp_session(
  (select id from clients where gstin = '24AABMT1234C1Z5'),
  '24AABMT1234C1Z5',
  'mastergst',
  'sample-otp-token-from-mastergst-api',
  now() + interval '30 days'
);

-- 4b. Read it back — should return plaintext token
select * from get_gsp_token(
  (select id from clients where gstin = '24AABMT1234C1Z5')
);

-- 4c. Check token expiry helper
select * from gsp_session_status(
  (select id from clients where gstin = '24AABMT1234C1Z5')
);

-- 4d. Clean up test session
delete from gsp_sessions
where client_id = (select id from clients where gstin = '24AABMT1234C1Z5');
