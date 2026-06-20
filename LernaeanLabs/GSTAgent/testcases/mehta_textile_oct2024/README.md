# Test Case: Mehta Textile Traders — October 2024

## Firm Profile
| Field | Value |
|---|---|
| **Name** | Mehta Textile Traders |
| **GSTIN** | 24AABMT1234C1Z5 |
| **State** | Gujarat (State Code: 24) |
| **Business** | B2B wholesale fabric supplier |
| **Annual Turnover** | ₹75 Lakhs |
| **Monthly Avg** | ₹6.25 Lakhs |
| **GST Registration** | Regular taxpayer, monthly filer |
| **Accounting Software** | Tally ERP 9 (old version, XML export) |
| **CA** | Rajesh Shah (files for 40 clients) |
| **CA Fee** | ₹4,000/month |

## Test Case Purpose
Simulate a complete October 2024 GST filing cycle **without real Tally or GST portal access**.
All data is mocked to match realistic formats the agent will encounter in production.

## What's Intentionally Broken in This Test Case
This test case has **3 deliberate issues** the agent must catch:

1. **Invoice MTT/OCT/004** — Verma Traders has a CANCELLED GSTIN (`24AAFVT9999Z1Z9`). This invoice cannot go into GSTR-1 as a B2B invoice. Must be reclassified to B2CS.
2. **Dye Masters ITC** — ₹5,040 ITC at risk. Dye Masters has NOT filed their GSTR-1 for October, so this invoice does NOT appear in GSTR-2B. Agent must flag as "at risk, do not claim."
3. **HSN code mismatch** — Invoice MTT/OCT/003 (Gupta Exports) has HSN code `5407` but the item is Cotton fabric which should be `5208`. Agent must flag for CA review.

## Directory Structure
```
mehta_textile_oct2024/
├── README.md                          ← this file
├── testcase_config.json               ← firm config, agent instructions
├── tally_export/
│   ├── sales_daybook_oct2024.xml      ← mock Tally XML export (sales vouchers)
│   ├── purchase_daybook_oct2024.xml   ← mock Tally XML export (purchase vouchers)
│   └── ledger_masters.xml             ← customer/supplier master data
├── gstr2b/
│   └── gstr2b_oct2024.json            ← mock GSTR-2B downloaded from portal
├── gstr1_draft/
│   └── gstr1_draft_oct2024.json       ← what agent should produce for GSTR-1
├── gstr3b_draft/
│   └── gstr3b_draft_oct2024.json      ← what agent should produce for GSTR-3B
├── agent_output/
│   ├── reconciliation_report.json     ← agent's reconciliation findings
│   ├── whatsapp_alert.txt             ← draft WhatsApp message to Mehta
│   └── issues_flagged.json            ← structured list of all issues found
└── ca_package/
    └── ca_handoff_summary.json        ← final package sent to CA Rajesh Shah
```

## Expected Agent Behaviour
1. Parse `tally_export/` → extract all sales and purchase vouchers
2. Load `gstr2b/gstr2b_oct2024.json` → get confirmed ITC
3. Validate all customer GSTINs (mock validation — Verma Traders is CANCELLED)
4. Reconcile purchases vs GSTR-2B → flag Dye Masters as missing
5. Flag HSN mismatch on Invoice MTT/OCT/003
6. Calculate: Output GST ₹61,200 (excluding Verma) − ITC ₹36,240 = **₹24,960 net payable**
7. Produce all output files in `agent_output/` and `ca_package/`

## Tax Calculation Summary
| | Amount |
|---|---|
| Total Sales (all 5 invoices) | ₹5,55,000 |
| Output GST on valid invoices (4 of 5) | ₹61,200 |
| Verma Traders invoice (GSTIN cancelled) | Reclassified B2CS — tax still collected |
| Total ITC in GSTR-2B (confirmed) | ₹36,240 |
| Dye Masters ITC (at risk, not in GSTR-2B) | ₹5,040 |
| **Net GST Payable (conservative)** | **₹24,960** |
| Net GST Payable (if Dye Masters ITC claimed) | ₹19,920 |
