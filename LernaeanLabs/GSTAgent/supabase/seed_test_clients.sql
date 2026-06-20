-- =============================================================================
-- GSTAgent — Seed: Clients + GSP Sessions only
-- =============================================================================
-- Run in: Supabase Dashboard → SQL Editor → New Query
-- Safe to re-run (all inserts use ON CONFLICT DO NOTHING)
--
-- Adds INPUTS only — tables that must exist before running the n8n workflow:
--   clients      — 11 clients (Mehta Textile + 10 dummy)
--   gsp_sessions — valid 30-day sessions for all active/trial clients
--
-- Does NOT touch filing_runs, reconciliation_results, alerts_sent.
-- Those are OUTPUT tables — populated exclusively by the n8n workflow.
-- =============================================================================


-- =============================================================================
-- SECTION 1 — Clients
-- =============================================================================

insert into clients (gstin, firm_name, owner_name, owner_whatsapp, ca_name, ca_email, subscription_status) values

-- Mehta Textile (the real testcase client)
('24AABMT1234C1Z5', 'Mehta Textile Traders',      'Mehta',           '+919824001234', 'Rajesh Shah',    'rajesh.shah.ca@gmail.com',    'active'),

-- Textiles
('27AABCP1234D1Z3', 'Patel Cotton Mills',          'Suresh Patel',    '+919876100001', 'Rajesh Shah',    'rajesh.shah@caoffice.com',    'active'),
('27AABCS5678E1Z1', 'Shah Synthetics Ltd',          'Nilesh Shah',     '+919876100002', 'Priya Mehta',    'priya.mehta@capractice.in',   'active'),

-- Pharma
('24AABCP9012F1Z5', 'Cadila Pharma Distributors',  'Amit Cadila',     '+919876100003', 'Kiran Desai',    'kiran.desai@caservices.com',  'active'),
('24AABCA3456G1Z2', 'Apollo Medical Supplies',      'Rajan Apollo',    null,            'Kiran Desai',    'kiran.desai@caservices.com',  'trial'),

-- Electronics
('29AABCE7890H1Z4', 'Bengaluru Electronics Hub',   'Vijay Kumar',     '+919876100005', 'Sanjay Rao',     'sanjay.rao@raoca.com',        'active'),
('29AABCI1234J1Z6', 'Infotech Components Pvt',     'Pradeep Sharma',  '+919876100006', 'Sanjay Rao',     'sanjay.rao@raoca.com',        'trial'),

-- Food & FMCG
('06AABCF5678K1Z3', 'Delhi Spice Traders',          'Mohit Gupta',     '+919876100007', 'Anil Verma',     'anil.verma@vermaCA.com',      'active'),
('06AABCA9012L1Z1', 'Aggarwal Foods Pvt Ltd',       'Rakesh Aggarwal', null,            'Anil Verma',     'anil.verma@vermaCA.com',      'active'),

-- Auto Parts
('33AABCA3456M1Z8', 'Chennai Auto Ancillaries',    'Murugan Pillai',  '+919876100009', 'Ramesh Iyer',    'ramesh.iyer@iyerca.com',      'active'),
('33AABCT7890N1Z5', 'Tamil Nadu Tyres & Parts',    'Senthil Kumar',   '+919876100010', 'Ramesh Iyer',    'ramesh.iyer@iyerca.com',      'cancelled')

on conflict (gstin) do nothing;


-- =============================================================================
-- SECTION 2 — GSP sessions for all active/trial clients
-- =============================================================================
-- All get valid 30-day sessions so the workflow's "IF Session Valid" passes.
-- In production these are created by the OTP authentication flow.

select upsert_gsp_session(
  (select id from clients where gstin = '24AABMT1234C1Z5'),
  '24AABMT1234C1Z5'::text, 'mastergst'::text,
  'session-token-mehta-textile'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '27AABCP1234D1Z3'),
  '27AABCP1234D1Z3'::text, 'mastergst'::text,
  'session-token-patel-cotton'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '27AABCS5678E1Z1'),
  '27AABCS5678E1Z1'::text, 'mastergst'::text,
  'session-token-shah-synthetics'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '24AABCP9012F1Z5'),
  '24AABCP9012F1Z5'::text, 'mastergst'::text,
  'session-token-cadila-pharma'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '24AABCA3456G1Z2'),
  '24AABCA3456G1Z2'::text, 'mastergst'::text,
  'session-token-apollo-medical'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '29AABCE7890H1Z4'),
  '29AABCE7890H1Z4'::text, 'mastergst'::text,
  'session-token-bengaluru-electronics'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '29AABCI1234J1Z6'),
  '29AABCI1234J1Z6'::text, 'mastergst'::text,
  'session-token-infotech-components'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '06AABCF5678K1Z3'),
  '06AABCF5678K1Z3'::text, 'mastergst'::text,
  'session-token-delhi-spice'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '06AABCA9012L1Z1'),
  '06AABCA9012L1Z1'::text, 'mastergst'::text,
  'session-token-aggarwal-foods'::text,
  (now() + interval '30 days')::timestamptz
);

select upsert_gsp_session(
  (select id from clients where gstin = '33AABCA3456M1Z8'),
  '33AABCA3456M1Z8'::text, 'mastergst'::text,
  'session-token-chennai-auto'::text,
  (now() + interval '30 days')::timestamptz
);

-- Tamil Nadu Tyres is cancelled — no session needed


-- =============================================================================
-- VERIFICATION
-- =============================================================================

select
  (select count(*) from clients)                                       as total_clients,
  (select count(*) from clients where subscription_status = 'active')  as active_clients,
  (select count(*) from clients where subscription_status = 'trial')   as trial_clients,
  (select count(*) from clients where subscription_status='cancelled')  as cancelled_clients,
  (select count(*) from gsp_sessions)                                  as gsp_sessions,
  (select count(*) from gsp_sessions where otp_expiry > now())         as valid_sessions;
