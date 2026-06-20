"""
claude_agent.py
---------------
The AI reasoning layer of the GST Agent.

Takes a ReconciliationResult (produced by reconciler.py — pure deterministic Python)
and calls the Claude API to produce:
  1. WhatsApp alert   — plain language message to the business owner
  2. CA handoff report — structured narrative + checklist for the CA
  3. Issues JSON      — structured list of all issues (always deterministic)

Design principles:
  - Claude NEVER does arithmetic — all numbers come from ReconciliationResult
  - Claude NEVER makes filing decisions — it drafts, explains, recommends
  - Two model tiers: Haiku for WhatsApp (fast/cheap), Sonnet for CA report
  - Graceful degradation: if API fails, built-in templates cover everything
  - dry_run=True for testing without API key

Usage:
  from claude_agent import GSTClaudeAgent, ClientConfig
  agent = GSTClaudeAgent()                     # reads ANTHROPIC_API_KEY
  agent = GSTClaudeAgent(api_key="sk-ant-...")
  agent = GSTClaudeAgent(dry_run=True)         # template mode
  output = agent.run(result, config)
"""

import json, os, re, urllib.request, urllib.error
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from reconciler import ReconciliationResult
from cost_logger import CostLogger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AgentOutput:
    client_gstin: str
    period: str
    generated_at: str
    whatsapp_message: str
    ca_report: str
    issues_structured: list = field(default_factory=list)
    model_used_whatsapp: str = ""
    model_used_ca_report: str = ""
    fallback_used: bool = False


@dataclass
class ClientConfig:
    firm_name: str
    gstin: str
    owner_name: str
    ca_name: str
    ca_email: str
    filing_period_label: str
    gstr1_due_date: str
    gstr3b_due_date: str
    whatsapp_number: Optional[str] = None
    language_preference: str = "english"


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_WHATSAPP = """You are a GST compliance assistant sending WhatsApp alerts to Indian business owners.

Write a clear, friendly, actionable WhatsApp message about their monthly GST filing.

Rules:
- Plain language — the owner is NOT a tax expert
- Never use jargon like "ITC reconciliation" or "GSTR-2B"
- All numbers come EXACTLY from the data — never invent or change any figure
- Under 250 words
- Use rupee symbol for amounts
- Structure: greeting → month summary → issues in plain English → one clear action owner must take
- Tone: professional but warm, like a helpful CA assistant
- WhatsApp bold (*text*) only for the payment amount and due date
- End with exactly ONE action: amount to pay and when
- If no issues: reassure owner, just state payment and due date"""

SYSTEM_PROMPT_CA = """You are a senior GST compliance analyst preparing a CA handoff report.

Write a concise, professional technical report for the Chartered Accountant.

Rules:
- CA audience — use proper GST terminology (GSTR-1, GSTR-3B, ITC, GSTIN, B2CS, etc.)
- All numbers come EXACTLY from the data — never change any figure
- Direct and specific — no filler or padding
- Use EXACTLY these section headers (no others):
  FILING STATUS SUMMARY
  ISSUES REQUIRING YOUR ATTENTION
  ITC POSITION
  TAX LIABILITY CALCULATION
  RECOMMENDED ACTIONS
  FILING CHECKLIST
- Each issue: what it is, why it matters, action needed, tax impact
- Recommended Actions: numbered, priority order
- Filing Checklist: every item with exact due date
- Tone: factual, not alarmist — issues are solvable"""

SYSTEM_PROMPT_ISSUES_JSON = """You are a structured data extractor for a GST compliance system.

Convert the reconciliation findings into a JSON array.

Output ONLY valid JSON — no markdown, no explanation, no preamble.

Each object:
{
  "issue_id": "ISSUE_001",
  "severity": "HIGH|MEDIUM|LOW",
  "category": "CANCELLED_GSTIN|MISSING_ITC|AMOUNT_MISMATCH|HSN_ERROR|OTHER",
  "invoice_number": "string or null",
  "party_name": "string or null",
  "gstin": "string or null",
  "description": "one plain English sentence",
  "tax_impact_inr": 0.00,
  "action_required": "one sentence — who does what",
  "ca_decision_needed": true or false
}

Severity: HIGH=cancelled GSTIN or ITC risk >5000; MEDIUM=smaller risk or mismatch; LOW=HSN flag.
Return [] if no issues."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_period(code: str) -> str:
    m = {"01":"January","02":"February","03":"March","04":"April","05":"May","06":"June",
         "07":"July","08":"August","09":"September","10":"October","11":"November","12":"December"}
    return f"{m.get(code[:2], code[:2])} {code[2:]}" if len(code) == 6 else code


def _days_until(ds: str) -> int:
    try:
        due = datetime.strptime(ds, "%Y-%m-%d")
        return (due - datetime.now().replace(hour=0,minute=0,second=0,microsecond=0)).days
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_whatsapp_prompt(result: ReconciliationResult, cfg: ClientConfig) -> str:
    tc = result.tax_calc
    period = cfg.filing_period_label or _fmt_period(result.period)

    issues = []
    for g in result.gstin_issues:
        issues.append(f"- Invoice {g.invoice_number}: Buyer '{g.name}' has a "
                      f"{g.status.lower()} GST registration. Cannot be reported as B2B sale.")
    for r in result.itc_results:
        if r.status == "MISSING_FROM_GSTR2B":
            issues.append(f"- Supplier '{r.purchase_voucher.supplier_name}' has not filed "
                          f"their GST return. Tax credit of Rs.{r.itc_at_risk:,.2f} cannot be claimed.")
    for h in result.hsn_flags:
        issues.append(f"- Invoice {h.invoice_number}: Product code for '{h.item_name}' needs CA verification.")

    issues_block = "\n".join(issues) if issues else "No issues. All invoices clean."

    return f"""Write a WhatsApp message to {cfg.owner_name}, owner of {cfg.firm_name}.

PERIOD: {period}
GSTR-1 due {cfg.gstr1_due_date} ({_days_until(cfg.gstr1_due_date)} days) | GSTR-3B due {cfg.gstr3b_due_date} ({_days_until(cfg.gstr3b_due_date)} days)

EXACT NUMBERS TO USE:
- Total sales: Rs.{result.total_sales_value:,.2f}
- GST collected: Rs.{result.total_output_gst:,.2f}
- Confirmed tax credits: Rs.{result.confirmed_itc:,.2f}
- Credits at risk: Rs.{result.at_risk_itc:,.2f}
- Net GST to pay: Rs.{tc.net_payable:,.2f}
- Status: {result.status} | Issues: {result.issue_count}

ISSUES:
{issues_block}

CA: {cfg.ca_name}

Start with "Hello {cfg.owner_name} ji" — end with exactly one action the owner must take."""


def build_ca_report_prompt(result: ReconciliationResult, cfg: ClientConfig) -> str:
    tc = result.tax_calc
    period = cfg.filing_period_label or _fmt_period(result.period)

    sales_lines = "\n".join(
        f"  {v.voucher_number}: {v.buyer_name} ({v.buyer_gstin}) | "
        f"Taxable Rs.{v.taxable_value:,.2f} | CGST Rs.{v.cgst:,.2f} SGST Rs.{v.sgst:,.2f} IGST Rs.{v.igst:,.2f}"
        for v in result.sales_vouchers)

    itc_lines = "\n".join(
        f"  {r.purchase_voucher.voucher_number}: {r.purchase_voucher.supplier_name} | "
        f"{r.status} | Claimed Rs.{r.itc_claimed:,.2f} | At risk Rs.{r.itc_at_risk:,.2f}"
        for r in result.itc_results)

    gstin_lines = "\n".join(
        f"  {g.invoice_number}: {g.name} ({g.gstin}) — {g.status}. Action: {g.action}"
        for g in result.gstin_issues) or "  None"

    hsn_lines = "\n".join(
        f"  {h.invoice_number}: '{h.item_name}' — HSN {h.hsn_in_tally} (suggested: {h.suggested_hsn})"
        for h in result.hsn_flags) or "  None"

    return f"""Prepare CA handoff report for {cfg.ca_name}.

CLIENT: {cfg.firm_name} | GSTIN: {cfg.gstin} | PERIOD: {period}
GENERATED: {datetime.now().strftime("%d %b %Y %H:%M")}
GSTR-1 DUE: {cfg.gstr1_due_date} | GSTR-3B DUE: {cfg.gstr3b_due_date}
STATUS: {result.status} | ISSUES: {result.issue_count}

SALES ({len(result.sales_vouchers)} invoices):
{sales_lines}
Total taxable: Rs.{result.total_sales_value:,.2f}
Output IGST Rs.{tc.output_igst:,.2f} | CGST Rs.{tc.output_cgst:,.2f} | SGST Rs.{tc.output_sgst:,.2f}
Total output GST: Rs.{result.total_output_gst:,.2f}

PURCHASES ({len(result.purchase_vouchers)} invoices):
{itc_lines}
Confirmed ITC: IGST Rs.{tc.itc_igst:,.2f} | CGST Rs.{tc.itc_cgst:,.2f} | SGST Rs.{tc.itc_sgst:,.2f}
Total confirmed: Rs.{result.confirmed_itc:,.2f} | At risk: Rs.{result.at_risk_itc:,.2f}

TAX: Net IGST Rs.{tc.net_igst:,.2f} | CGST Rs.{tc.net_cgst:,.2f} | SGST Rs.{tc.net_sgst:,.2f}
TOTAL NET PAYABLE: Rs.{tc.net_payable:,.2f}
(If at-risk ITC also claimed: Rs.{max(0, tc.net_payable - result.at_risk_itc):,.2f})

GSTIN ISSUES:
{gstin_lines}

HSN FLAGS:
{hsn_lines}

Write the CA report now using the required section headers."""


def build_issues_json_prompt(result: ReconciliationResult) -> str:
    findings = []
    for g in result.gstin_issues:
        findings.append(f"GSTIN issue: Invoice {g.invoice_number}, buyer '{g.name}' "
                        f"({g.gstin}) status is {g.status}. Detail: {g.issue}")
    for r in result.itc_results:
        if r.status == "MISSING_FROM_GSTR2B":
            findings.append(f"Missing ITC: Invoice {r.purchase_voucher.voucher_number} from "
                            f"'{r.purchase_voucher.supplier_name}' ({r.purchase_voucher.supplier_gstin}) "
                            f"not in GSTR-2B. ITC at risk Rs.{r.itc_at_risk:,.2f}. {r.risk_reason}")
        elif r.status == "AMOUNT_MISMATCH":
            findings.append(f"Amount mismatch: Invoice {r.purchase_voucher.voucher_number} from "
                            f"'{r.purchase_voucher.supplier_name}'. "
                            f"Tally Rs.{r.purchase_voucher.itc_claimable:,.2f} vs GSTR-2B Rs.{r.itc_claimed:,.2f}.")
    for h in result.hsn_flags:
        findings.append(f"HSN flag: Invoice {h.invoice_number}, '{h.item_name}', "
                        f"HSN {h.hsn_in_tally} in Tally, suggested {h.suggested_hsn}. No tax impact.")
    if not findings:
        return "No issues. Return []."
    return "Convert to JSON array:\n\n" + "\n".join(f"{i+1}. {f}" for i,f in enumerate(findings)) + "\n\nReturn ONLY the JSON array."


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class ClaudeAPIError(Exception):
    pass


class ClaudeAPIClient:
    BASE_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    HAIKU  = "claude-haiku-4-5-20251001"
    SONNET = "claude-sonnet-4-6"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def call(self, system_prompt, user_message, model=None, max_tokens=1024, temperature=0.3) -> tuple[str, int, int]:
        """
        Returns (text, input_tokens, output_tokens).
        Raises ClaudeAPIError on HTTP/network failure.
        """
        model = model or self.HAIKU
        payload = json.dumps({
            "model": model, "max_tokens": max_tokens, "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}]
        }).encode("utf-8")
        req = urllib.request.Request(
            self.BASE_URL, data=payload,
            headers={"Content-Type": "application/json",
                     "X-API-Key": self.api_key,
                     "anthropic-version": self.API_VERSION},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = "\n".join(
                    b.get("text","") for b in data.get("content",[]) if b.get("type")=="text"
                ).strip()
                usage = data.get("usage", {})
                return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        except urllib.error.HTTPError as e:
            raise ClaudeAPIError(f"HTTP {e.code}: {e.read().decode()}") from e
        except urllib.error.URLError as e:
            raise ClaudeAPIError(f"Network: {e.reason}") from e


# ---------------------------------------------------------------------------
# Fallback templates
# ---------------------------------------------------------------------------

def _fallback_whatsapp(result: ReconciliationResult, cfg: ClientConfig) -> str:
    tc = result.tax_calc
    period = cfg.filing_period_label or _fmt_period(result.period)
    issues = ""
    for g in result.gstin_issues:
        issues += f"\n⚠️ Invoice {g.invoice_number}: {g.name}'s GST registration is {g.status.lower()}. Your CA will handle this.\n"
    for r in result.itc_results:
        if r.status == "MISSING_FROM_GSTR2B":
            issues += f"\n⚠️ {r.purchase_voucher.supplier_name} hasn't filed their GST return. Tax credit of ₹{r.itc_at_risk:,.2f} is at risk.\n"
    if result.hsn_flags:
        issues += f"\nℹ️ {len(result.hsn_flags)} product code(s) need verification by your CA.\n"
    action = (f"Please deposit *₹{tc.net_payable:,.2f}* in your GST cash ledger before *{cfg.gstr3b_due_date}*."
              if tc.net_payable > 0 else "No tax payment needed this month. Your credits cover the full amount.")
    return f"""Hello {cfg.owner_name} ji 🙏

Your *{period}* GST filing is being prepared by {cfg.ca_name}.

📊 *Summary*
• Sales this month: ₹{result.total_sales_value:,.2f}
• GST collected: ₹{result.total_output_gst:,.2f}
• Tax credits available: ₹{result.confirmed_itc:,.2f}
• *Tax payable: ₹{tc.net_payable:,.2f}*
{issues}
✅ *Action needed:* {action}

GSTR-1 due: {cfg.gstr1_due_date} | GSTR-3B due: {cfg.gstr3b_due_date}
— GST Agent"""


def _fallback_ca_report(result: ReconciliationResult, cfg: ClientConfig) -> str:
    tc = result.tax_calc
    period = cfg.filing_period_label or _fmt_period(result.period)
    n = 1
    issues_block = ""
    for g in result.gstin_issues:
        issues_block += f"\n{n}. [HIGH] CANCELLED GSTIN — {g.invoice_number}: {g.name} ({g.gstin})\n   Action: {g.action}\n   Tax impact: Nil (invoice must be reclassified B2B → B2CS)\n"
        n += 1
    for r in result.itc_results:
        if r.status == "MISSING_FROM_GSTR2B":
            sev = "HIGH" if r.itc_at_risk > 5000 else "MEDIUM"
            issues_block += f"\n{n}. [{sev}] MISSING ITC — {r.purchase_voucher.voucher_number} ({r.purchase_voucher.supplier_name})\n   {r.risk_reason}\n   Tax impact: ₹{r.itc_at_risk:,.2f} ITC at risk. Do not claim. Follow up with supplier.\n"
            n += 1
    for h in result.hsn_flags:
        issues_block += f"\n{n}. [LOW] HSN FLAG — {h.invoice_number}: '{h.item_name}'\n   HSN in Tally: {h.hsn_in_tally} | Suggested: {h.suggested_hsn}\n   Tax impact: Nil (same rate). Verify correct HSN before filing.\n"
        n += 1
    if not issues_block:
        issues_block = "\n  No issues found. All sales and purchase invoices reconciled cleanly.\n"

    actions = []
    i = 1
    if result.gstin_issues:
        actions.append(f"{i}. Reclassify cancelled-GSTIN invoice(s) from B2B to B2CS before uploading GSTR-1"); i += 1
    if result.hsn_flags:
        actions.append(f"{i}. Verify HSN code(s) with client — correct in Tally if required"); i += 1
    if result.at_risk_itc > 0:
        actions.append(f"{i}. Contact at-risk supplier(s) — request GSTR-1 filing before {cfg.gstr1_due_date}"); i += 1
    actions.append(f"{i}. Upload and file GSTR-1 by {cfg.gstr1_due_date}"); i += 1
    actions.append(f"{i}. Instruct client to deposit ₹{tc.net_payable:,.2f} via GST portal challan by {cfg.gstr3b_due_date}"); i += 1
    actions.append(f"{i}. Review auto-populated GSTR-3B and file by {cfg.gstr3b_due_date}")
    actions_block = "\n".join(actions)

    checklist_resolve = (
        f"[ ] Resolve all flagged issues                               (by {cfg.gstr1_due_date})\n"
        if (result.gstin_issues or result.hsn_flags or result.at_risk_itc > 0) else ""
    )

    return f"""CA HANDOFF REPORT — {cfg.firm_name}
GSTIN: {cfg.gstin} | Period: {period} | Status: {result.status} | Issues: {result.issue_count}
Generated: {datetime.now().strftime("%d %b %Y %H:%M")}
{'─'*60}

FILING STATUS SUMMARY
Sales: {len(result.sales_vouchers)} invoices | Total taxable value: ₹{result.total_sales_value:,.2f}
Output GST: IGST ₹{tc.output_igst:,.2f} | CGST ₹{tc.output_cgst:,.2f} | SGST ₹{tc.output_sgst:,.2f}
Purchases: {len(result.purchase_vouchers)} invoices | Total purchase value: ₹{result.total_purchase_value:,.2f}
Overall status: {result.status}

ISSUES REQUIRING YOUR ATTENTION{issues_block}
ITC POSITION
Confirmed ITC (present in GSTR-2B):
  IGST: ₹{tc.itc_igst:,.2f} | CGST: ₹{tc.itc_cgst:,.2f} | SGST: ₹{tc.itc_sgst:,.2f}
  Total confirmed: ₹{result.confirmed_itc:,.2f}
ITC at risk (absent from GSTR-2B): ₹{result.at_risk_itc:,.2f}
Strategy: Conservative — at-risk ITC excluded this period.

TAX LIABILITY CALCULATION
Output:  IGST ₹{tc.output_igst:,.2f} | CGST ₹{tc.output_cgst:,.2f} | SGST ₹{tc.output_sgst:,.2f}
ITC:     IGST ₹{tc.itc_igst:,.2f} | CGST ₹{tc.itc_cgst:,.2f} | SGST ₹{tc.itc_sgst:,.2f}
Net:     IGST ₹{tc.net_igst:,.2f} | CGST ₹{tc.net_cgst:,.2f} | SGST ₹{tc.net_sgst:,.2f}
TOTAL NET PAYABLE: ₹{tc.net_payable:,.2f}
(If at-risk ITC also claimed: ₹{max(0, tc.net_payable - result.at_risk_itc):,.2f})

RECOMMENDED ACTIONS
{actions_block}

FILING CHECKLIST
[ ] Review and approve GSTR-1 draft                         (by {cfg.gstr1_due_date})
{checklist_resolve}[ ] File GSTR-1 on GST portal                               (by {cfg.gstr1_due_date})
[ ] Client deposits ₹{tc.net_payable:,.2f} challan          (by {cfg.gstr3b_due_date})
[ ] Verify GSTR-3B auto-population from GSTR-1              (by {cfg.gstr3b_due_date})
[ ] File GSTR-3B with DSC/EVC                               (by {cfg.gstr3b_due_date})
{'─'*60}"""


# ---------------------------------------------------------------------------
# Deterministic issues builder
# ---------------------------------------------------------------------------

def _build_issues_deterministic(result: ReconciliationResult) -> list:
    issues, idx = [], 1
    for g in result.gstin_issues:
        issues.append({
            "issue_id": f"ISSUE_{idx:03d}", "severity": "HIGH",
            "category": "CANCELLED_GSTIN",
            "invoice_number": g.invoice_number, "party_name": g.name, "gstin": g.gstin,
            "description": f"Invoice {g.invoice_number}: '{g.name}' GSTIN {g.gstin} is {g.status}.",
            "tax_impact_inr": 0.0,
            "action_required": g.action or "Reclassify invoice from B2B to B2CS in GSTR-1.",
            "ca_decision_needed": True
        }); idx += 1
    for r in result.itc_results:
        if r.status == "MISSING_FROM_GSTR2B":
            issues.append({
                "issue_id": f"ISSUE_{idx:03d}",
                "severity": "HIGH" if r.itc_at_risk > 5000 else "MEDIUM",
                "category": "MISSING_ITC",
                "invoice_number": r.purchase_voucher.voucher_number,
                "party_name": r.purchase_voucher.supplier_name,
                "gstin": r.purchase_voucher.supplier_gstin,
                "description": f"Invoice {r.purchase_voucher.voucher_number} from '{r.purchase_voucher.supplier_name}' absent from GSTR-2B. Supplier has not filed GSTR-1.",
                "tax_impact_inr": r.itc_at_risk,
                "action_required": "Do not claim ITC this period. Follow up with supplier to file GSTR-1.",
                "ca_decision_needed": True
            }); idx += 1
        elif r.status == "AMOUNT_MISMATCH":
            issues.append({
                "issue_id": f"ISSUE_{idx:03d}", "severity": "MEDIUM",
                "category": "AMOUNT_MISMATCH",
                "invoice_number": r.purchase_voucher.voucher_number,
                "party_name": r.purchase_voucher.supplier_name,
                "gstin": r.purchase_voucher.supplier_gstin,
                "description": f"ITC mismatch on {r.purchase_voucher.voucher_number}: Tally ₹{r.purchase_voucher.itc_claimable:,.2f} vs GSTR-2B ₹{r.itc_claimed:,.2f}.",
                "tax_impact_inr": r.itc_at_risk,
                "action_required": "Claim GSTR-2B amount only. Investigate discrepancy with supplier.",
                "ca_decision_needed": True
            }); idx += 1
    for h in result.hsn_flags:
        issues.append({
            "issue_id": f"ISSUE_{idx:03d}", "severity": "LOW",
            "category": "HSN_ERROR",
            "invoice_number": h.invoice_number, "party_name": None, "gstin": None,
            "description": f"HSN {h.hsn_in_tally} on '{h.item_name}' (invoice {h.invoice_number}) may be incorrect. Suggested: {h.suggested_hsn}.",
            "tax_impact_inr": 0.0,
            "action_required": f"Verify correct HSN. Correct to {h.suggested_hsn} if wrong before filing.",
            "ca_decision_needed": False
        }); idx += 1
    return issues


# ---------------------------------------------------------------------------
# Main agent
# ---------------------------------------------------------------------------

class GSTClaudeAgent:
    """
    Orchestrates Claude API calls to generate GST filing outputs.

    LIVE mode    — calls Claude API (requires ANTHROPIC_API_KEY or api_key)
    DRY RUN mode — uses built-in templates, no API calls (dry_run=True or no key)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        dry_run: bool = False,
        verbose: bool = True,
        cost_logger: Optional[CostLogger] = None,
    ):
        self.verbose = verbose
        self.dry_run = dry_run
        self._client: Optional[ClaudeAPIClient] = None
        self._cost_logger = cost_logger   # None = no cost tracking
        if not dry_run:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if key:
                self._client = ClaudeAPIClient(key)
                self._log("  [INFO] Claude API client initialised.")
            else:
                self._log("  [WARN] No ANTHROPIC_API_KEY. Using fallback templates.")
                self.dry_run = True

    def _log(self, msg: str):
        if self.verbose: print(msg)

    def run(self, result: ReconciliationResult, config: ClientConfig) -> AgentOutput:
        self._log(f"\n{'='*60}")
        self._log(f"  CLAUDE AGENT — {config.firm_name} | {config.filing_period_label}")
        self._log(f"  Mode: {'DRY RUN (templates)' if self.dry_run else 'LIVE (Claude API)'}")
        self._log(f"{'='*60}")

        output = AgentOutput(
            client_gstin=result.gstin, period=result.period,
            generated_at=datetime.now().isoformat(),
            whatsapp_message="", ca_report="")

        self._log("\n[1/3] Building structured issues list...")
        output.issues_structured = self._issues_json(result)
        self._log(f"  → {len(output.issues_structured)} issue(s)")

        self._log("\n[2/3] Generating WhatsApp alert...")
        output.whatsapp_message, output.model_used_whatsapp, fb1 = self._whatsapp(result, config)
        self._log(f"  → {len(output.whatsapp_message)} chars | {output.model_used_whatsapp}")

        self._log("\n[3/3] Generating CA handoff report...")
        output.ca_report, output.model_used_ca_report, fb2 = self._ca_report(result, config)
        self._log(f"  → {len(output.ca_report)} chars | {output.model_used_ca_report}")

        output.fallback_used = fb1 or fb2
        self._log(f"\n✅ Agent complete. Fallback used: {output.fallback_used}")
        return output

    def _log_cost(self, result, call_type, model, input_tok, output_tok, fallback):
        if self._cost_logger:
            self._cost_logger.log_call(
                client_gstin=result.gstin,
                period=result.period,
                call_type=call_type,
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                fallback=fallback,
            )

    def _issues_json(self, result: ReconciliationResult) -> list:
        base = _build_issues_deterministic(result)
        if self.dry_run or not self._client or not base:
            self._log_cost(result, "issues_json", "template", 0, 0, True)
            return base
        try:
            raw, in_tok, out_tok = self._client.call(
                SYSTEM_PROMPT_ISSUES_JSON,
                build_issues_json_prompt(result),
                model=ClaudeAPIClient.HAIKU, max_tokens=1000, temperature=0.0)
            self._log_cost(result, "issues_json", ClaudeAPIClient.HAIKU, in_tok, out_tok, False)
            cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
        except (ClaudeAPIError, json.JSONDecodeError, ValueError):
            self._log_cost(result, "issues_json", "template (fallback)", 0, 0, True)
        return base

    def _whatsapp(self, result, cfg) -> tuple:
        if self.dry_run or not self._client:
            self._log_cost(result, "whatsapp", "template", 0, 0, True)
            return _fallback_whatsapp(result, cfg), "template", True
        try:
            msg, in_tok, out_tok = self._client.call(
                SYSTEM_PROMPT_WHATSAPP, build_whatsapp_prompt(result, cfg),
                model=ClaudeAPIClient.HAIKU, max_tokens=600, temperature=0.4)
            self._log_cost(result, "whatsapp", ClaudeAPIClient.HAIKU, in_tok, out_tok, False)
            return msg, ClaudeAPIClient.HAIKU, False
        except ClaudeAPIError as e:
            self._log(f"  [WARN] WhatsApp call failed: {e}. Using template.")
            self._log_cost(result, "whatsapp", "template (fallback)", 0, 0, True)
            return _fallback_whatsapp(result, cfg), "template (fallback)", True

    def _ca_report(self, result, cfg) -> tuple:
        if self.dry_run or not self._client:
            self._log_cost(result, "ca_report", "template", 0, 0, True)
            return _fallback_ca_report(result, cfg), "template", True
        try:
            report, in_tok, out_tok = self._client.call(
                SYSTEM_PROMPT_CA, build_ca_report_prompt(result, cfg),
                model=ClaudeAPIClient.SONNET, max_tokens=2000, temperature=0.2)
            self._log_cost(result, "ca_report", ClaudeAPIClient.SONNET, in_tok, out_tok, False)
            return report, ClaudeAPIClient.SONNET, False
        except ClaudeAPIError as e:
            self._log(f"  [WARN] CA report call failed: {e}. Using template.")
            self._log_cost(result, "ca_report", "template (fallback)", 0, 0, True)
            return _fallback_ca_report(result, cfg), "template (fallback)", True


# ---------------------------------------------------------------------------
# Save helper
# ---------------------------------------------------------------------------

def save_agent_output(output: AgentOutput, output_dir: str) -> dict:
    os.makedirs(output_dir, exist_ok=True)
    paths = {}
    for name, content, ext in [
        ("whatsapp", output.whatsapp_message, "txt"),
        ("ca_report", output.ca_report, "txt"),
    ]:
        p = os.path.join(output_dir, f"{name}_generated.{ext}")
        with open(p, "w", encoding="utf-8") as f: f.write(content)
        paths[name] = p

    p = os.path.join(output_dir, "issues_structured_generated.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(output.issues_structured, f, indent=2, ensure_ascii=False)
    paths["issues"] = p

    p = os.path.join(output_dir, "agent_run_metadata.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({
            "client_gstin": output.client_gstin, "period": output.period,
            "generated_at": output.generated_at,
            "model_whatsapp": output.model_used_whatsapp,
            "model_ca_report": output.model_used_ca_report,
            "fallback_used": output.fallback_used,
            "issue_count": len(output.issues_structured),
        }, f, indent=2)
    paths["metadata"] = p
    return paths
