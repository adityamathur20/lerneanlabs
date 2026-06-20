# CLAUDE.md — AI Compliance Agent Ventures
## Full Project Context for Aditya Mathur | Bengaluru, India | May 2026

> **This file is the authoritative project context.** Paste it as your opening system context when starting any new Claude Code or Claude session on this project. Everything needed to pick up from where we left off — architecture, research, decisions, pending tasks — is captured here.

---

## 1. WHO YOU ARE WORKING WITH

**Name:** Aditya Mathur  
**Location:** Bengaluru, India  
**Background:** AI/data science and product, Python developer  
**Code style:** Readable over clever. No one-liners that obscure intent. Full type hints. `decimal.Decimal` for all money. Dataclasses for all models.  
**Response style:** Concise and actionable. No over-explaining when context is clear. Honest assessments over optimistic ones.  
**Setup:** Solo operator. Independent venture, separate from any employer role.  
**Academic:** Collaboration with Pratishtha Mathur at Suresh Gyan Vihar University (ICRACS 2026 research, PhD supervision reports).

---

## 2. CURRENT PRIMARY VENTURE — NCB Compliance Agent

> **STATUS: PRIMARY FOCUS. Active development target.**

The GST Agent project has been **cancelled/deprioritised** as a standalone product (see Section 4 for full historical context). The primary venture is now the **NCB Compliance Agent**.

---

## 3. NCB COMPLIANCE AGENT — FULL CONTEXT

### 3.1 What Problem It Solves

India's **NDPS (Regulation of Controlled Substances) Order 2013** (RCS Order) controls 27 precursor chemicals — substances with legitimate industrial/pharma uses that are also drug precursors. Every entity in the supply chain that handles these chemicals must:

1. Hold a **URN (Unique Registration Number)** from NCB — no URN = no legal commercial activity
2. Maintain a **daily physical register** — every working day, including nil-transaction days
3. File a **quarterly return (Form IV/V)** with the NCB Zonal Unit
4. Generate a **Form-G consignment note** (triplicate) for every movement
5. Importers/exporters: monthly report to CBN (Central Bureau of Narcotics, Gwalior)

**The automation gap:** Tally (the dominant SME accounting software in India) tracks these as rupee-value accounting entries. NCB requires a quantity-based daily ledger in a completely different format (kilograms, per-substance). There is **zero software that bridges this gap**. Currently handled by manual consultants at ₹15,000–40,000/quarter per client.

### 3.2 Controlled Substance Schedules

**Schedule A (5 chemicals — strictest, most valuable target market):**
- Acetic Anhydride
- Ephedrine
- Pseudoephedrine
- N-Acetylanthranilic Acid
- Anthranilic Acid

**Schedule B:** Export-controlled (potassium permanganate, phenylacetic acid, safrole, etc.)  
**Schedule C:** Import-controlled (norephedrine, 1-phenyl-2-propanone, pseudoephedrine, etc.)

**Key insight:** Schedule A is a **subscription product** (daily compliance, every working day). Schedule B/C is a **per-event product** (per shipment NOC). Recurring revenue logic works cleanly with Schedule A.

### 3.3 Exact Compliance Burden (Per RCS Order 2013)

**Daily (every working day, even nil-transaction days):**
- Physical register entry mandatory — quantity in kg, running serial number, authorised person's initials
- "Nil transaction" entry required even on days with zero activity
- Separate register per substance

**Per Transaction (every movement):**
- Form-G consignment note in triplicate
- Preserved 5 years by both consignor and consignee (**not 2 years** — 2013 Order updated this from the old 1993 Order)
- Sales of 100 kg+ require buyer identity verification

**Quarterly (4 times/year — Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec):**
- Due last day of the month following the quarter
- Form IV/V filed with NCB Zonal Unit via Pre-Register portal (`precursorsncb.gov.in`)
- Contents: opening stock, all purchases, all sales, closing stock reconciliation, documented losses
- 3 consecutive missed quarters = URN automatically revoked

**Monthly (importers/exporters only):**
- Separate report to CBN (Gwalior) on actual import/export quantities

### 3.4 Physical Register — Critical Legal Detail

The RCS Order 2013 and 2019 amendment do **not** authorise digital/electronic record-keeping as a substitute for the physical register. NCB zonal inspections physically examine the register — inspectors look for:
- Running serial numbers
- Date continuity (including nil-transaction days)
- Handwritten initials on each entry

**Product implication:** The NCB Agent maintains a **digital shadow register** that mirrors the physical one and auto-generates the quarterly return from it — but **cannot replace the physical register** until NCB explicitly authorises digital records. The agent's value: generate daily entries for the officer to print and sign into the physical register. The TOS must make this explicit.

### 3.5 Government Resources & Documents

| Resource | URL |
|---|---|
| NDPS Act 1985 | https://indiacode.nic.in/handle/123456789/1366 |
| RCS Order 2013 (full text PDF) | http://cbn.nic.in/pdf/exim/NewRCSEnglish.pdf |
| RCS Order 2013 (Meghalaya mirror) | https://megpolice.gov.in/sites/default/files/NDPS_Regulation_Controlled_Substances_Order_2013.pdf |
| NCB Pre-Register portal | https://precursorsncb.gov.in |
| CBN (Central Bureau of Narcotics) | https://cbn.nic.in |
| NCB Official | https://narcoticsindia.nic.in |
| RCS Amendment Order 2019 summary | https://www.legalitysimplified.com/ndps-regulation-of-controlled-substances-amendment-order-2019/ |

### 3.6 Target Market & Segmentation

**Universe:** ~5,000–8,000 firms holding URNs  
**Sweet spot:** Schedule A manufacturers + traders — concentrated in:
- **Ankleshwar GIDC, Gujarat** (highest density, best first market)
- Baddi, Himachal Pradesh (pharma manufacturing cluster)
- Hyderabad pharma cluster

**ERP segmentation (critical for architecture):**

| Segment | Company Size | ERP | Tally Integration? | Priority |
|---|---|---|---|---|
| Chemical traders/distributors | <200 employees | Tally (dominant, 80%+) | ✅ Direct | **Tier 1 — build first** |
| Pharma distributors / C&F agents | 50–500 employees | Mix: Tally + Marg ERP | ⚠️ Partial | Tier 2 — Marg integration Month 2–3 |
| Mid pharma manufacturers | 200–500 employees | SAP Business One / Tally | ⚠️ Depends | Tier 3 — CSV/Excel upload fallback |
| Large pharma (Ipca, Baxter, SRF) | 500+ employees | SAP S/4HANA / Oracle | ❌ No | Skip — 6–12 month sales cycle, internal teams |

**Why large pharma is the wrong starting point:** They already have dedicated regulatory officers (₹8–12L/year salary). Your product displaces headcount, which is politically hard. Sales cycle 6–12 months. ERP incompatible. Wrong for a solo operator's first clients.

### 3.7 Pricing (Researched)

| Tier | Criteria | Price |
|---|---|---|
| Standard | 1–2 Schedule A substances, domestic only | ₹8,999/month |
| Enterprise | 3+ substances OR any import/export activity | ₹18,999/month |

**Comparison to current market:** Manual consultants charge ₹15,000–40,000/quarter = ₹60,000–1,60,000/year. Your product is cheaper, faster, and always-on.

**Revenue target:** 20 clients at Standard tier = ₹1,79,980 MRR

### 3.8 System Architecture — NCB Agent

The architecture mirrors the GST Agent pattern (same engineer, same philosophy) but with critical differences for the NCB domain.

**Core principle:** All arithmetic is Python. Claude only interprets/explains what numbers mean.

**Pipeline:**

```
Tally XML / CSV Export (daily)
    ↓
tally_ncb_parser.py
  — reads sales/purchase ledgers
  — extracts party name, substance, quantity in rupees
  — applies item_code → substance → kg conversion factor (client-specific config)
    ↓
daily_register_module.py (deterministic Python)
  — generates daily register entry per substance
  — nil-transaction auto-fill for working days with no movement
  — stock balance running total
  — URN validation per counterparty
    ↓
quarterly_return_module.py (deterministic Python)
  — compiles Form IV/V from daily register
  — opening stock + all purchases + all sales = closing stock reconciliation
  — balance check: if doesn't balance → BLOCK, force human resolution
    ↓
claude_agent.py (AI layer — reasoning only)
  — Discrepancy interpretation (explains what a gap means, possible causes)
  — Pre-inspection narrative summary
  — WhatsApp alerts to compliance officer
    ↓
Human Approval Gate (NON-NEGOTIABLE)
  — Agent NEVER auto-submits to Pre-Register portal
  — Compliance officer reviews → approves → submits manually
    ↓
Outputs:
  — Daily register entries (printed → officer signs into physical register)
  — Form IV/V PDF (pre-filled, officer submits via Pre-Register portal)
  — WhatsApp alerts to compliance officer (not business owner — different from GST)
  — Immutable audit log (Supabase, write-locked on approval)
```

**Stack:**
- **Python 3.10+** — core pipeline
- **n8n** — orchestration (two schedules: nightly at 11pm for daily register; month-end for quarterly return)
- **Supabase** — state: clients, daily_register, quarterly_returns, urn_index, audit_log
- **Claude API** — `claude-haiku-4-5-20251001` for WhatsApp alerts, `claude-sonnet-4-6` for discrepancy interpretation and pre-inspection report
- **WhatsApp Business API** — alerts to compliance officer (not business owner)
- **On-premise option** — package as Python wheel, SQLite instead of Postgres, local LLM (Ollama/llama3.1:8b) for fully air-gapped deployments

### 3.9 Data Inputs — Two Paths

**Input 1 — Tally XML (same as GST Agent)**
```
Tally Gateway → Display → Day Book → Export → XML
```
Tally exports in rupee values. NCB Agent applies a conversion factor per item code to get kg quantities. Client config file maps: `item_code → substance → kg_per_unit_or_per_rupee_at_current_price`.

**Alternative data paths (for non-Tally clients):**
- **Excel/CSV upload** — client exports from SAP/Marg → uploads to portal
- **Google Sheets** — client shares Sheet with service account (OAuth, free, recommended for frictionless setup)
- **M365 Excel** — only if client already has M365 subscription (don't require it)

### 3.10 URN Validation Architecture

The Pre-Register portal (`precursorsncb.gov.in`) has no documented public API. Three-tier validation:

**Tier 1 — Format validation (always run first, free):**
```python
import re
URN_PATTERN = re.compile(r'^NCB-[A-Z]{2}-\d{4}-\d{6}$')
# Example valid: NCB-MH-2021-004521
def validate_urn_format(urn: str) -> bool:
    return bool(URN_PATTERN.match(urn))
```

**Tier 2 — Cached scraping + local database:**
Periodic scrape of the Pre-Register portal search page. Cache results in Supabase. On each transaction, cross-check counterparty URN against cache. Auto-alert if URN appears cancelled or expired.

**Tier 3 — Manual verification:**
Flag ambiguous URNs for compliance officer to verify manually on the portal.

### 3.11 Liability Architecture (Critical Difference from GST)

GST errors → tax penalties and notices. NCB errors → URN cancellation + potential criminal prosecution under NDPS Act. Design must reflect this asymmetry:

1. **Mandatory human approval before any submission** — agent never auto-submits to Pre-Register portal
2. **Discrepancy blocking** — if stock reconciliation doesn't balance, Form IV/V is not generated. User cannot bypass this gate.
3. **Immutable audit trail** — every register entry, once approved, is write-locked with timestamp. Tamper-evident history.
4. **Conservative interpretation** — where there's ambiguity, always flag for human decision rather than guess.
5. **TOS clause** — must explicitly state: agent is a preparation tool only; compliance officer is legally responsible for all submissions; agent does not provide legal compliance certification.

### 3.12 AI Layer — What Claude Does (and Doesn't Do)

**Claude DOES:**
- Interpret what a stock discrepancy means: `"On Aug 14th, 12 kg of Acetic Anhydride is unaccounted for. Possible causes: (a) evaporation/process loss — document with lab certificate, (b) Tally entry error — check invoice NCL/2024/078, (c) physical stock issue — conduct physical count."`
- Generate the pre-inspection narrative summary (currently takes 3 days manually, 2 hours with agent)
- Draft WhatsApp alerts to compliance officer for: stock discrepancy detected, nil-transaction entry missing, counterparty URN approaching expiry

**Claude NEVER:**
- Performs any arithmetic (quantities, stock balances, reconciliation math)
- Auto-submits anything to any government portal
- Makes compliance decisions where there's ambiguity — always flags for human

### 3.13 n8n Workflow (Two Schedules)

```
Schedule 1: Nightly at 11pm
  → Fetch day's Tally export
  → Run daily register module
  → Check URN validities
  → Send WhatsApp alerts if discrepancies

Schedule 2: Month-end (Jan 31, Apr 30, Jul 31, Oct 31)
  → Compile quarterly return
  → Run balance check
  → If balanced → generate Form IV/V PDF
  → Notify compliance officer to review and submit
  → If not balanced → block and alert immediately
```

### 3.14 On-Premise Deployment (for sensitive clients)

```bash
pip install ncb-compliance-agent
ncb-agent init --client-id=CLIENT001 --substance="Acetic Anhydride"
ncb-agent run-daily --tally-xml=/path/to/export.xml
ncb-agent generate-quarterly --quarter=Q3-2025 --output=/path/to/output/
```

For full air-gap: replace Claude API with Ollama (llama3.1:8b or mistral:7b). Quality of narrative output lower, but compliance pipeline 100% intact — it's all Python.

### 3.15 Supabase Schema (Planned)

```
clients              — client_id, name, gstin, substances[], urn, config_json
daily_register       — id, client_id, substance, date, opening_kg, purchases_kg,
                        sales_kg, closing_kg, nil_transaction, approved_by, approved_at
quarterly_returns    — id, client_id, quarter, form_iv_v_pdf_url, balance_check_passed,
                        submitted_at, submitted_by
urn_index            — urn, company_name, state, status, verified_at
alerts_sent          — id, client_id, alert_type, channel, sent_at, acknowledged
audit_log            — id, client_id, action, actor, timestamp, before_state, after_state
```

### 3.16 Channel Partners (Not Competitors)

| Partner | Role | Contact | Notes |
|---|---|---|---|
| **Diligent Associates** | NDPS-only specialist consultancy — best white-label partner | rajneesh@diligent-ndps.com, chirag@diligent-ndps.com, +91-9727750447 | They charge ₹15K–40K/quarter per client manually. White-label: you automate, they continue client relationship |
| NKG Advisory | Broad pharma regulatory, NDPS one service | navraj@nkgabc.com, +91-97111-97602 | Secondary partner target |
| Zymax Pharma Consultancy | Ankleshwar-based, geography match | support@zymaxpharma.com | Useful for Ankleshwar introductions |
| IDMA | Indian Drug Manufacturers' Association | idma-assn.org | Trade association — attend Ahmedabad chapter for introductions |
| GCPMA | Gujarat Chemical & Petrochemical Manufacturers | gcpma.in | Ankleshwar cluster access |

**Consultant partnership model considerations:**  
Diligent Associates charges ₹15K–40K/quarter per client manually. If your software automates that in 1 hour instead of 3 days, you've destroyed their billing justification for that work. Approach as a white-label tool that they offer clients, not as software that replaces them. They handle the client relationship; your tool handles the paperwork generation.

### 3.17 Prospect Companies (Research Completed)

**Tier 1 — High probability Schedule A URN holders (send outreach immediately):**

| Company | Location | Chemical | Contact |
|---|---|---|---|
| GNFC (Gujarat Narmada Valley Fertilizers) | Narmadanagar, Bharuch | Acetic Anhydride (Schedule A) — **highest priority lead** | anshah@gnfc.in |
| Paushak Limited (Alembic group) | Panelav, Halol, Panchmahal | Phosgene-based chemistry | Via company website |
| Ishita Industries | Ahmedabad | Anthranilic Acid / N-Acetylanthranilic Acid | ishitaindustries@sify.com |
| Link Pharma Chem Ltd | Ankleshwar | 4-Chloro Anthranilic Acid | linkpharmalimited@gmail.com |
| Jackson Chemical Industries | Ankleshwar | Anthranilic acid derivatives (owner-operated) | rajubhaijackson@gmail.com, +91-9824153451 |
| Nirav Dyes | Gujarat | Anthranilic acid derivatives | chintan99@gmail.com, +91-9979933288 |
| Dev International | Ankleshwar | Anthranilic acid | Via directory |
| Aarti Industries | Vapi, Gujarat | Anthranilic acid derivatives | Via company website |
| Atul Limited | Valsad, Gujarat | Dye intermediates incl. anthranilic acid-linked | Via company website |
| Deepak Nitrite | Nandesari, Vadodara | Nitrobenzene, aniline-related | Via company website |
| Jubilant Life Sciences | Gajraula UP / Nira Maharashtra | Pharma intermediates | Via company website |
| IOL Chemical and Pharmaceuticals | Barnala, Punjab | Pharma API, ibuprofen intermediates | Via company website |
| Shree Chemopharma | Gujarat | Anthranilic acid | +91-9909242468 |
| Himalaya Chemicals | Gujarat | Anthranilic acid | +91-8037303985 |
| Heet Impex | Gujarat | Verify Schedule A substance | +91-9426109882 |
| Vitrag Chemicals | Gujarat | Anthranilic acid related | +91-9099019618 |
| Endemic India Chemicals | Gujarat | Anthranilic acid related | +91-9998727107 |

**Tier 2 — Verify URN before full pitch (WhatsApp qualifier first):**
Acquire Chemicals, Kakdiya Chemicals, Element Chemilink, Bridge Chem, Bharat Rasayan, Amsal Chem, Sagar Life Sciences, Triveni Chemicals, Aarham Chemicals, Kanoria Chemicals (Ankleshwar), Laxmi Organic Industries (Mahad), Meghmani Organics (Ankleshwar)

**Qualification message (doubles as disqualification):**
> *"Namaste, I'm building software that automates the NCB daily register and quarterly Form IV/V return for companies holding URNs under the RCS Order 2013. Does your company currently hold a URN for any Schedule A controlled substance? If yes, I'd like to offer a free 3-month pilot."*

### 3.18 Outreach Materials Built

**Cold email framework:** Built using principles from "$100M Offers" and "Smart Brevity":
- Tier 1 (confirmed Schedule A): Direct pitch with free 3-month pilot offer
- Tier 2 (unconfirmed): WhatsApp qualifier first → email only after URN confirmation
- Segmented by chemical: Anthranilic Acid, Acetic Anhydride, Ephedrine API
- Gujarati P.S. for Gujarat-based owner-operated firms

**A React artifact was built (ncb_outreach_v3.jsx)** with all 40 companies, switchable email/WhatsApp tabs, tier indicators, and copy-to-clipboard.

---

## 4. HISTORICAL CONTEXT — GST Agent (CANCELLED AS STANDALONE)

> **Note: The GST Agent as a standalone product has been cancelled/deprioritised.** The codebase still exists and is fully functional. This section is preserved for context and because the codebase may inform the NCB Agent architecture.

### 4.1 Why GST Agent Was Cancelled

Competitor analysis (May 2026) revealed:
- **Vyapar acquired Suvit** (November 2025) — #1 AI-powered CA-focused GST tool is now inside the #1 SMB accounting platform (Vyapar, 1.5 crore SMBs, ₹50–75 crore revenue, $35.9M raised)
- **Suvit** had 5,000+ CA users pre-acquisition — same Tally-first, same CA audience
- **ClearTax** matches 6,000 invoices/minute, certified GSP, enterprise-grade
- **GSTHero** — bulk upload, mismatch detection, multi-client management, certified GSP
- **TallyPrime itself** has built-in GSTR-2B reconciliation
- Others: EASYGST, myGSTcafe, LEDGERS, SuperTax, SCIGST, enReconcile, Tax2win, Zoho Books, Marg GST, HostBooks

**The honest assessment:** GSTR-2B reconciliation is a commodity feature. The market is solved and consolidated. GSTAgent as a standalone ₹2,499/month product is a hard sell against Vyapar/Suvit.

**What might still be defensible (not pursued):** CA white-label + AI narrative layer on top of existing tools (not reconciliation itself) — but that's a feature, not a product. Not worth pursuing solo.

### 4.2 GST Agent Codebase (Fully Functional, 234 Tests Passing)

**Local path:** `/Users/adityamathur/Desktop/gitlabs_repo/GSTAgent/`

```
GSTAgent/
├── agent/
│   ├── tally_parser.py       ← lxml XML parser → SalesVoucher/PurchaseVoucher dataclasses
│   ├── gstr2b_reader.py      ← JSON loader, O(1) invoice+supplier index
│   ├── reconciler.py         ← deterministic pipeline → ReconciliationResult
│   ├── prompt_builder.py     ← ReconciliationResult → Claude prompt pairs
│   ├── claude_agent.py       ← GSTClaudeAgent: prompts → API → WhatsApp + CA report
│   └── cost_logger.py        ← per-call token+cost tracking (JSONL log, INR conversion)
├── testcases/mehta_textile_oct2024/
│   ├── tally_export/tally_export.xml    ← 5 sales + 4 purchases (3 bugs planted)
│   ├── gstr2b/gstr2b_oct2024.json       ← Dye Masters absent (bug #2)
│   └── agent_output/                    ← whatsapp_alert, ca_report, reconciliation JSON
├── tests/
│   ├── conftest.py
│   ├── run_tests.py          ← 100 tests: parser, reader, reconciler
│   ├── test_claude_agent.py  ← 134 tests: prompt_builder, claude_agent (dry_run)
│   └── run_all_tests.py      ← master runner, all 234 passing
└── run_testcase.py
```

**Run:**
```bash
PYTHONPATH=. python run_testcase.py --dry-run  # no API key needed
python run_testcase.py                          # live run with ANTHROPIC_API_KEY
cd tests && python run_all_tests.py             # full test suite
```

### 4.3 Key Architectural Decisions (Reusable for NCB Agent)

- **Single deterministic pipeline** — NOT multi-agent, NOT LangGraph
- **All arithmetic in Python** — Claude never touches raw numbers or performs calculations
- **Two Claude models, two jobs:** `claude-haiku-4-5-20251001` for WhatsApp (fast, cheap), `claude-sonnet-4-6` for CA/compliance report (thorough)
- **dry_run=True** — returns deterministic stubs, zero network calls (used in all tests)
- **Exponential backoff retry** — 3 attempts, handles HTTP 429/529
- **Fallback templates** — if API fails, pre-built templates cover all output types
- **ResponseValidator** — checks Claude output contains correct figures (warns but doesn't block)
- **Cost logger** — per-call token+cost tracking, JSONL log, INR conversion at ₹84/USD
- **`decimal.Decimal` everywhere** — all monetary/quantity amounts, never `float`

### 4.4 Test Case — Mehta Textile Traders (Reference)

3 deliberate bugs, all caught:
1. Verma Traders GSTIN (24AAFVT9999Z1Z9) cancelled → reclassify B2B to B2CS (HIGH)
2. Dye Masters absent from GSTR-2B (invoice DM/2024/387) → ₹5,040 ITC at risk (HIGH)
3. HSN 5407 on cotton fabric (MTT/OCT/003) → should be 5208 (MEDIUM)

Net payable correctly computed: **₹31,440** (verified)

---

## 5. OTHER EVALUATED IDEAS (Context Only)

### 5.1 PF/Labour Compliance Module
**Status:** Potential future add-on (Month 5–6), not standalone product.  
Same CA client, same channel. 12+ mandatory HR filings/year across 5 portals (PF ECR, ESIC, PT, LWF, S&E). Bundle concept: ₹8,999 NCB + PF module.

### 5.2 FSSAI Agent
**Status:** Dropped.  
- 2026 perpetual validity amendment abolished licence renewal (primary urgency hook gone)
- No monthly compliance obligation for most food businesses (subscription logic fails)
- Target market (small D2C brands) financially stretched
- May surface as ₹999/month feature for food-business clients only

### 5.3 Indian Regulatory Intelligence API
**Status:** Interesting future product, not prioritised.  
Structured, queryable feed of every regulatory change from CBIC, GST Council, SEBI, RERA, FSSAI, MCA — parsed, diffed, tagged by industry, delivered via REST API + webhooks.  
Buyers: law firms (₹15K–50K/month), banks/NBFCs, HR software, CA firms, other compliance SaaS.  
Gap: Taxmann and Manupatra sell curated databases via expensive subscriptions — not API-first, not structured data.

### 5.4 GSTIN Intelligence Data Product
**Status:** Interesting future product, not prioritised.  
Clean, queryable database of all 1.4 crore GSTINs enriched with filing regularity score, HSN pattern, cancellation risk, director linkages. Primary buyer: banks/NBFCs for GST-based credit underwriting.

### 5.5 Vernacular Compliance Document Generator
**Status:** Interesting future product, not prioritised.  
Web app where a business owner fills a form in Hindi/Kannada/Tamil/Marathi and gets a legally-valid compliance document (GST reply letter, RERA complaint, labour notice reply).

### 5.6 Sports Talent Identification Platform
**Status:** Researched, not pursued (different domain).  
SIH validation: SIH25073. Computer vision (MediaPipe) for biomechanical analysis, India-specific percentile norms. Market: ~5,000 private coaching academies.

### 5.7 Pre-Litigation Dispute Intelligence
**Status:** Researched, not pursued (6–12 month sales cycle).  
IndiaAI Innovation Challenge 2026 — ₹1 crore prize. AI-enabled dispute resolution for insurance/banks.

### 5.8 EPR / Aquaculture Compliance
**Status:** Researched, not pursued (requires physical presence in coastal Andhra).  
Digital logbook for aquaculture/poultry farms, EU MRL withdrawal periods, APEDA-ready reports.

---

## 6. SIH (SMART INDIA HACKATHON) RESEARCH — KEY INSIGHT

Researched 254 SIH 2024 + 101 SIH 2025 problem statements across 40 ministries.

**Key insight:** SIH builds government-facing civic tools. This portfolio targets business-facing commercial tools. Best opportunities are where government pain exists but the **buyer is a business, not a government**. The entire portfolio sits in this gap — SIH validates the pain but can never produce the product.

**NCB specifically:** SIH1562 — NCB submitted the precursor chemical compliance problem as a problem statement directly. The government acknowledges the problem; no one is solving it for the private sector.

**SIH traps to avoid:** agriculture, disaster management, ISRO, smart education — civic problems with no B2B commercial monetisation path.

---

## 7. CODING CONVENTIONS (APPLY TO ALL CODE IN THIS PROJECT)

```python
# Language: Python 3.10+
# Monetary/quantity amounts: decimal.Decimal ALWAYS, never float
from decimal import Decimal

# All data models: dataclasses with full type hints
from dataclasses import dataclass
from typing import Optional

@dataclass
class Transaction:
    substance: str
    quantity_kg: Decimal
    counterparty_urn: str
    date: str  # YYYYMMDD
    transaction_type: str  # "PURCHASE" | "SALE" | "TRANSFER"

# Separation of concerns: each layer has no knowledge of the layer above it
# parser → domain_module → prompt_builder → claude_agent

# Tests: pytest, all quantity/money assertions use exact Decimal comparison
# test naming: test_<module>_<scenario>
# dry_run=True in all CI tests (zero network calls)

# File style: readable over clever; no one-liners that obscure intent
# No magic constants — named variables for every threshold/limit
```

**Models to use:**
- `claude-haiku-4-5-20251001` — WhatsApp alerts (≤400 tokens, cheap, fast)
- `claude-sonnet-4-6` — Compliance reports and discrepancy interpretation (≤2000 tokens)

**Error handling:** Exponential backoff, 3 attempts, handle HTTP 429/529. Always have a fallback template if API fails.

---

## 8. ENVIRONMENT & INFRASTRUCTURE

```bash
# Python dependencies (pip install --break-system-packages if needed)
anthropic>=0.40.0
lxml>=5.0.0
python-dotenv
supabase-py
fastapi

# Environment variables
ANTHROPIC_API_KEY=sk-ant-...          # required
SUPABASE_URL=https://xxx.supabase.co  # Phase 2
SUPABASE_KEY=service-role-key         # Phase 2
NCB_PRE_REGISTER_SCRAPE_INTERVAL=86400  # seconds between URN cache refresh

# Deployment
# Cloud: Default (most clients)
# On-premise: Python wheel + SQLite + Ollama for air-gapped deployments
```

---

## 9. IMMEDIATE NEXT ACTIONS (Priority Order)

### 🔴 This Week
1. **Email Diligent NDPS Consultants** — pitch white-label partnership: rajneesh@diligent-ndps.com — long lead time, start clock now
2. **Email/WhatsApp GNFC** (anshah@gnfc.in) — highest-probability Schedule A lead (Acetic Anhydride manufacturer, Bharuch)
3. **Send outreach to Jackson Chemical** (+91-9824153451) and **Nirav Dyes** (+91-9979933288) — owner-direct, fastest to respond
4. **Send Tier 1 emails** from ncb_outreach_v3.jsx to all confirmed Schedule A companies

### 🟡 Month 1
5. **Build `tally_ncb_parser.py`** — adapt existing `tally_parser.py` from GST Agent, add unit conversion layer (rupees → kg)
6. **Build `daily_register_module.py`** — deterministic pipeline, nil-transaction auto-fill, URN format validation
7. **Build `ncb_claude_agent.py`** — discrepancy interpretation, WhatsApp alerts, pre-inspection narrative
8. **Define Supabase schema** — clients, daily_register, quarterly_returns, urn_index, audit_log
9. **Create test case** — equivalent of Mehta Textile but for a Schedule A chemical trader (synthetic data, planted discrepancy, verified reconciliation)
10. **Write system prompt** — NCB reasoning: substance classification, discrepancy interpretation, form requirements

### 🟢 Month 2–3
11. **Build `quarterly_return_module.py`** — Form IV/V generator with balance-check gate
12. **Build `urn_validator.py`** — format validation + cached Pre-Register scraping
13. **n8n workflow** — two-schedule orchestration (nightly + month-end)
14. **CA PDF generator** — `reportlab` or `weasyprint` for Form IV/V PDF output
15. **Attend IDMA Ahmedabad chapter meeting** — network with target customers
16. **First live pilot** — one Schedule A chemical trader, free 3 months

### 🔵 Month 3+
17. **Marg ERP integration** — unlock pharma distributor segment
18. **Python wheel packaging** — for on-premise deployments
19. **Razorpay billing** — ₹8,999/₹18,999 subscription, webhook-gated access
20. **MCP server wrapper** — for privacy-first on-premise CA firms

---

## 10. KEY DECISIONS LOG

| Topic | Decision | Rationale |
|---|---|---|
| Primary product | NCB Compliance Agent (not GST) | GST market consolidated — Vyapar acquired Suvit Nov 2025; NCB has zero software competition |
| Architecture pattern | Single deterministic pipeline (not multi-agent, not LangGraph) | Same as GST Agent — proven, simpler, more reliable |
| AI injection points | 2 only — WhatsApp alert + compliance report | All arithmetic Python; Claude interprets, never computes |
| Auto-submission | Never | NDPS liability too high — human gate non-negotiable |
| First ERP target | Tally only | 80%+ market share in chemical trader segment (Tier 1) |
| Marg ERP | Phase 2 add-on | Unlock pharma distributors; Marg has API/export capability |
| Physical register | Cannot replace; can shadow | RCS Order 2013 doesn't authorise digital substitution |
| Preservation period | 5 years (not 2) | 2013 Order updated from old 1993 Order |
| Schedule focus | Schedule A first | Subscription product; recurring daily burden; largest volume |
| Schedule B/C | Feature, not product | Per-event (per shipment NOC); doesn't justify subscription alone |
| Target segment | Chemical traders (Ankleshwar cluster) | Tally-dominant, high non-compliance rate, owner-operated, fastest to close |
| Avoid | Large pharma (Baxter, Ipca) | SAP ERP, internal regulatory teams, 6–12 month sales cycles |
| Channel partners | Approach as white-label, not replacement | Destroys their billing if we replace them; better as tool they offer |
| Pricing | ₹8,999 Standard / ₹18,999 Enterprise | vs ₹60K–1.6L/year manual consultants |
| On-premise | Python wheel + SQLite + Ollama | For air-gapped deployments — core value is all Python, no cloud needed |
| FSSAI | Dropped | 2026 perpetual validity amendment removed renewal hook |
| PF/Labour | Future add-on | Same channel, not standalone product |

---

## 11. REFERENCE LINKS

**NCB / NDPS:**
- NCB Official: narcoticsindia.nic.in
- NCB Pre-Register portal: precursorsncb.gov.in
- CBN (Central Bureau of Narcotics): cbn.nic.in
- RCS Order 2013 (PDF): cbn.nic.in/pdf/exim/NewRCSEnglish.pdf
- Diligent NDPS Consultants: diligent-ndps.com
- NKG Advisory: nkgabc.com
- GCPMA: gcpma.in
- IDMA: idma-assn.org

**Prospect Sourcing (DIY — no scraping needed):**
- IndiaMART: search "anthranilic acid Ankleshwar", "acetic anhydride Gujarat"
- Gujarat Directory: gujaratdirectory.com → Bharuch → Chemicals
- TradeIndia: tradeindia.com → search each controlled substance

**GST / Infrastructure (Historical):**
- Sandbox.co.in (GSP API infrastructure): developer.sandbox.co.in
- IRIS Zircon: irisgst.com/iriszircon
- MasterGST: mastergst.com (sandbox OTP = 575757 for dev)

---


*Last updated: May 2026 | Built from: 5 project files + 6 conversation threads*  
*Conversations covered: GST competitor analysis → NCB market research → potential client identification → NCB regulatory deep-dive → cold outreach materials → NCB agent technical architecture & mind map*