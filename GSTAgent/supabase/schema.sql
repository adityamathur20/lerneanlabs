-- =============================================================================
-- GSTAgent — Supabase Schema  (fully idempotent — safe to re-run)
-- =============================================================================
-- Paste this entire file into: Supabase Dashboard → SQL Editor → New Query → Run
--
-- Tables created:
--   clients                — one row per subscribing business
--   filing_runs            — one row per monthly reconciliation run
--   reconciliation_results — full pipeline output stored as JSONB
--   alerts_sent            — WhatsApp + email delivery audit log
--   gsp_sessions           — GSP OTP session tokens, encrypted at rest via Vault
--
-- RPC functions (called by gsp_client.py via Supabase REST API):
--   upsert_gsp_session     — write/refresh an encrypted GSP token
--   get_gsp_token          — read and decrypt a GSP token
--   gsp_session_status     — check if a valid session exists (no token returned)
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

create extension if not exists "pgcrypto";   -- provides gen_random_uuid() + pgp_sym_encrypt/decrypt


-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

create or replace function set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;


-- ---------------------------------------------------------------------------
-- 1. clients
-- ---------------------------------------------------------------------------

create table if not exists clients (
  id                       uuid primary key default gen_random_uuid(),

  gstin                    text not null unique,
  firm_name                text not null,
  owner_name               text not null,
  owner_whatsapp           text,

  ca_name                  text not null,
  ca_email                 text not null,

  language_preference      text not null default 'english'
                             check (language_preference in ('english', 'hindi', 'gujarati')),

  subscription_status      text not null default 'trial'
                             check (subscription_status in ('trial', 'active', 'cancelled', 'paused')),
  razorpay_subscription_id text unique,
  trial_ends_at            timestamptz,
  subscription_started_at  timestamptz,

  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create or replace trigger clients_updated_at
  before update on clients
  for each row execute function set_updated_at();

create index if not exists idx_clients_subscription_status on clients(subscription_status);
create index if not exists idx_clients_gstin               on clients(gstin);


-- ---------------------------------------------------------------------------
-- 2. filing_runs
-- ---------------------------------------------------------------------------

create table if not exists filing_runs (
  id                    uuid primary key default gen_random_uuid(),
  client_id             uuid not null references clients(id) on delete cascade,

  period                text not null,
  period_label          text not null,
  gstr1_due_date        date not null,
  gstr3b_due_date       date not null,

  run_status            text not null default 'pending'
                          check (run_status in ('pending', 'running', 'completed', 'failed')),
  error_message         text,

  reconciliation_status text
                          check (reconciliation_status in ('CLEAN', 'ISSUES_FOUND', 'CRITICAL')),
  issue_count           int,
  net_payable_inr       numeric(12, 2),

  whatsapp_sent         boolean not null default false,
  ca_email_sent         boolean not null default false,

  cost_usd              numeric(10, 8) not null default 0,
  cost_inr              numeric(10, 4) not null default 0,

  started_at            timestamptz,
  completed_at          timestamptz,
  created_at            timestamptz not null default now(),

  unique (client_id, period)
);

create index if not exists idx_filing_runs_client_id   on filing_runs(client_id);
create index if not exists idx_filing_runs_period      on filing_runs(period);
create index if not exists idx_filing_runs_run_status  on filing_runs(run_status);
create index if not exists idx_filing_runs_created_at  on filing_runs(created_at desc);


-- ---------------------------------------------------------------------------
-- 3. reconciliation_results
-- ---------------------------------------------------------------------------

create table if not exists reconciliation_results (
  id              uuid primary key default gen_random_uuid(),
  filing_run_id   uuid not null unique references filing_runs(id) on delete cascade,

  result_json       jsonb not null,
  whatsapp_message  text,
  ca_report         text,
  issues_structured jsonb,

  model_whatsapp    text,
  model_ca_report   text,
  fallback_used     boolean not null default false,

  created_at        timestamptz not null default now()
);

create index if not exists idx_rec_results_run_id     on reconciliation_results(filing_run_id);
create index if not exists idx_rec_results_issues_gin on reconciliation_results using gin(issues_structured);
create index if not exists idx_rec_results_result_gin on reconciliation_results using gin(result_json);


-- ---------------------------------------------------------------------------
-- 4. alerts_sent
-- ---------------------------------------------------------------------------

create table if not exists alerts_sent (
  id                  uuid primary key default gen_random_uuid(),
  filing_run_id       uuid not null references filing_runs(id) on delete cascade,

  alert_type          text not null
                        check (alert_type in ('whatsapp', 'email_ca', 'email_owner')),
  recipient           text not null,

  status              text not null default 'pending'
                        check (status in ('pending', 'sent', 'failed', 'delivered')),
  provider            text,
  provider_message_id text,
  error_message       text,

  sent_at             timestamptz,
  created_at          timestamptz not null default now()
);

create index if not exists idx_alerts_filing_run_id on alerts_sent(filing_run_id);
create index if not exists idx_alerts_status        on alerts_sent(status);
create index if not exists idx_alerts_alert_type    on alerts_sent(alert_type);


-- ---------------------------------------------------------------------------
-- 5. gsp_sessions
-- ---------------------------------------------------------------------------
-- session_token is encrypted using pgp_sym_encrypt + a key stored in Supabase Vault.
-- Python never holds the key. All encrypt/decrypt happens inside Postgres
-- via the upsert_gsp_session() and get_gsp_token() RPC functions below.

create table if not exists gsp_sessions (
  id                uuid primary key default gen_random_uuid(),
  client_id         uuid not null unique references clients(id) on delete cascade,

  gstin             text not null,
  gsp_provider      text not null
                      check (gsp_provider in ('mastergst', 'tera')),

  session_token     text not null,   -- pgp_sym_encrypt output, key lives in Vault

  otp_expiry        timestamptz not null,
  last_refreshed_at timestamptz not null default now(),

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create or replace trigger gsp_sessions_updated_at
  before update on gsp_sessions
  for each row execute function set_updated_at();

create index if not exists idx_gsp_sessions_client_id  on gsp_sessions(client_id);
create index if not exists idx_gsp_sessions_otp_expiry on gsp_sessions(otp_expiry);


-- =============================================================================
-- Row Level Security
-- =============================================================================

alter table clients                enable row level security;
alter table filing_runs            enable row level security;
alter table reconciliation_results enable row level security;
alter table alerts_sent            enable row level security;
alter table gsp_sessions           enable row level security;

-- Policies: wrapped in DO blocks so re-running this file doesn't error
-- if the policies already exist.

do $$ begin
  create policy "service_role_full_access_clients"
    on clients for all to service_role using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
  create policy "service_role_full_access_filing_runs"
    on filing_runs for all to service_role using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
  create policy "service_role_full_access_rec_results"
    on reconciliation_results for all to service_role using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
  create policy "service_role_full_access_alerts"
    on alerts_sent for all to service_role using (true);
exception when duplicate_object then null;
end $$;

do $$ begin
  create policy "service_role_full_access_gsp_sessions"
    on gsp_sessions for all to service_role using (true);
exception when duplicate_object then null;
end $$;


-- =============================================================================
-- RPC Functions  (security definer — run as postgres, can access vault)
-- =============================================================================


-- ---------------------------------------------------------------------------
-- upsert_gsp_session
-- ---------------------------------------------------------------------------
-- Write or refresh a GSP session token for a client.
-- Fetches the encryption key from Vault, encrypts the token, stores ciphertext.
-- Python passes plaintext; the key never leaves Postgres.

create or replace function upsert_gsp_session(
  p_client_id    uuid,
  p_gstin        text,
  p_gsp_provider text,
  p_token        text,
  p_otp_expiry   timestamptz
)
returns void
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_key text;
begin
  select decrypted_secret into v_key
  from vault.decrypted_secrets
  where name = 'gsp_key';

  if v_key is null then
    raise exception 'gsp_key not found in Vault — run secrets_setup.sql first.';
  end if;

  insert into gsp_sessions (
    client_id, gstin, gsp_provider, session_token, otp_expiry, last_refreshed_at
  ) values (
    p_client_id,
    p_gstin,
    p_gsp_provider,
    pgp_sym_encrypt(p_token, v_key),
    p_otp_expiry,
    now()
  )
  on conflict (client_id) do update set
    session_token     = pgp_sym_encrypt(p_token, v_key),
    gsp_provider      = excluded.gsp_provider,
    otp_expiry        = excluded.otp_expiry,
    last_refreshed_at = now(),
    updated_at        = now();
end;
$$;


-- ---------------------------------------------------------------------------
-- get_gsp_token
-- ---------------------------------------------------------------------------
-- Read and decrypt the GSP session token for a client.
-- Returns plaintext token + expiry metadata.
-- Returns no rows if the client has no session.

create or replace function get_gsp_token(p_client_id uuid)
returns table (
  gstin        text,
  gsp_provider text,
  token        text,
  otp_expiry   timestamptz,
  is_expired   boolean
)
language plpgsql
security definer
set search_path = public, extensions
as $$
declare
  v_key text;
begin
  select decrypted_secret into v_key
  from vault.decrypted_secrets
  where name = 'gsp_key';

  if v_key is null then
    raise exception 'gsp_key not found in Vault — run secrets_setup.sql first.';
  end if;

  return query
  select
    gs.gstin,
    gs.gsp_provider,
    pgp_sym_decrypt(gs.session_token::bytea, v_key) as token,
    gs.otp_expiry,
    (gs.otp_expiry < now()) as is_expired
  from gsp_sessions gs
  where gs.client_id = p_client_id;
end;
$$;


-- ---------------------------------------------------------------------------
-- gsp_session_status
-- ---------------------------------------------------------------------------
-- Quick check: does this client have a live session?
-- Used by n8n to decide whether to trigger an OTP refresh before a run.
-- Does NOT return the token itself.

create or replace function gsp_session_status(p_client_id uuid)
returns table (
  has_session  boolean,
  is_valid     boolean,
  otp_expiry   timestamptz,
  days_left    int,
  gsp_provider text
)
language plpgsql
security definer
set search_path = public, extensions
as $$
begin
  return query
  select
    true                                         as has_session,
    (gs.otp_expiry > now())                      as is_valid,
    gs.otp_expiry,
    extract(day from gs.otp_expiry - now())::int as days_left,
    gs.gsp_provider
  from gsp_sessions gs
  where gs.client_id = p_client_id

  union all

  select false, false, null::timestamptz, null::int, null::text
  where not exists (
    select 1 from gsp_sessions where client_id = p_client_id
  );
end;
$$;


-- Grant execute to service_role (used by n8n and gsp_client.py)
grant execute on function upsert_gsp_session to service_role;
grant execute on function get_gsp_token      to service_role;
grant execute on function gsp_session_status to service_role;


-- =============================================================================
-- Seed data — Mehta Textile Traders test client
-- (matches testcases/mehta_textile_oct2024/ — safe to re-run, conflict ignored)
-- =============================================================================

insert into clients (
  gstin, firm_name, owner_name, owner_whatsapp,
  ca_name, ca_email, subscription_status
) values (
  '24AABMT1234C1Z5',
  'Mehta Textile Traders',
  'Ramesh Mehta',
  '+919876543210',
  'Rajesh Shah',
  'rajesh.shah@caoffice.com',
  'trial'
) on conflict (gstin) do nothing;
