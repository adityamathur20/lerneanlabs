"""
prompt_builder.py
-----------------
Converts a ReconciliationResult into Claude API prompts.

Separated from claude_agent.py so it can be unit-tested without any
API calls or network access. Pure data transformation — no I/O.

Design decisions:
  1. SYSTEM PROMPT is fixed per client type (textile business).
     It tells Claude its role, output format, and hard constraints
     (never invent numbers, always use figures from the structured data).

  2. USER PROMPT is built dynamically from the ReconciliationResult.
     It injects all structured data as a JSON block so Claude works
     from facts, not from its own inference.

  3. Two separate prompt builds — one for each output type:
     a) build_whatsapp_prompt()  → short client-facing alert
     b) build_ca_prompt()        → detailed CA handoff narrative

  4. Numbers are NEVER computed by Claude. They're pre-computed by
     reconciler.py and injected as literals. Claude's job is language
     only — explaining, summarising, and phrasing.
"""

import json
from dataclasses import asdict
from reconciler import ReconciliationResult


# ---------------------------------------------------------------------------
# System prompts — fixed role + output format instructions
# ---------------------------------------------------------------------------

WHATSAPP_SYSTEM_PROMPT = """You are a GST compliance assistant sending a WhatsApp message to a small Indian business owner.

Your job: write a concise, friendly WhatsApp message summarising their GST status for the month.

STRICT RULES:
- Write in plain English. No accounting jargon.
- Use exact numbers from the structured data provided. Never invent or round figures differently.
- Maximum 200 words.
- Start with a greeting using the firm name.
- List issues clearly but without causing panic — be factual and calm.
- End with a single clear action item: what the owner needs to do and by when.
- Use WhatsApp-friendly formatting: line breaks, bullet points with -, no markdown headers.
- Never mention Claude, AI, or any system name.
- Sign off as "Your GST Agent".
"""

CA_SYSTEM_PROMPT = """You are a GST compliance assistant preparing a handoff report for a Chartered Accountant in India.

Your job: write a professional, structured summary of a client's GST reconciliation for the month. This report goes directly to the CA who will file the returns.

STRICT RULES:
- Use exact figures from the structured data. Never compute, estimate, or round differently.
- Be precise and technical — the CA is an expert, do not over-explain basics.
- Structure your output with clear sections: Executive Summary, Issues Requiring Action, Tax Liability, Filing Checklist.
- Each issue must state: what it is, which invoice, the financial impact, and the recommended action.
- The filing checklist must be numbered steps in correct filing order.
- Maximum 400 words.
- Never mention Claude, AI, or any system name.
- Do not use markdown code blocks. Use plain text with clear section headers in CAPS.
"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

class PromptBuilder:
    """
    Builds Claude prompts from a ReconciliationResult.

    All data is serialised to a JSON block and injected into the prompt.
    Claude never infers numbers — it only phrases and structures them.
    """

    def __init__(self, result: ReconciliationResult, firm_name: str,
                 gstr1_due: str, gstr3b_due: str, ca_name: str):
        self.result = result
        self.firm_name = firm_name
        self.gstr1_due = gstr1_due
        self.gstr3b_due = gstr3b_due
        self.ca_name = ca_name

    # -----------------------------------------------------------------------
    # Public: build prompt pairs
    # -----------------------------------------------------------------------

    def build_whatsapp_prompt(self) -> tuple[str, str]:
        """
        Returns (system_prompt, user_prompt) for the WhatsApp message.
        """
        data = self._serialise_for_prompt()
        user_prompt = f"""Write a WhatsApp message for this client.

FIRM: {self.firm_name}
GSTIN: {self.result.gstin}
PERIOD: {self.result.period}
GSTR-1 DUE: {self.gstr1_due}
GSTR-3B DUE: {self.gstr3b_due}

STRUCTURED DATA (use these exact figures):
{json.dumps(data, indent=2)}

Write the WhatsApp message now."""

        return WHATSAPP_SYSTEM_PROMPT, user_prompt

    def build_ca_prompt(self) -> tuple[str, str]:
        """
        Returns (system_prompt, user_prompt) for the CA handoff report.
        """
        data = self._serialise_for_prompt()
        user_prompt = f"""Prepare a GST handoff report for the CA.

FIRM: {self.firm_name}
GSTIN: {self.result.gstin}
PERIOD: {self.result.period}
CA NAME: {self.ca_name}
GSTR-1 DUE: {self.gstr1_due}
GSTR-3B DUE: {self.gstr3b_due}

STRUCTURED DATA (use these exact figures):
{json.dumps(data, indent=2)}

Write the CA handoff report now."""

        return CA_SYSTEM_PROMPT, user_prompt

    # -----------------------------------------------------------------------
    # Private: serialise ReconciliationResult to a prompt-safe dict
    # -----------------------------------------------------------------------

    def _serialise_for_prompt(self) -> dict:
        """
        Convert ReconciliationResult to a clean dict for prompt injection.

        We deliberately exclude raw voucher objects (too verbose for a prompt)
        and include only the derived findings and computed numbers.
        """
        r = self.result
        tc = r.tax_calc

        return {
            "status": r.status,
            "issue_count": r.issue_count,
            "has_critical_issues": r.has_critical_issues,

            "sales_summary": {
                "invoice_count": len(r.sales_vouchers),
                "total_taxable_value": r.total_sales_value,
                "total_output_gst": r.total_output_gst,
            },

            "purchase_summary": {
                "invoice_count": len(r.purchase_vouchers),
                "confirmed_itc": r.confirmed_itc,
                "at_risk_itc": r.at_risk_itc,
            },

            "tax_liability": {
                "output_igst": tc.output_igst,
                "output_cgst": tc.output_cgst,
                "output_sgst": tc.output_sgst,
                "itc_igst": tc.itc_igst,
                "itc_cgst": tc.itc_cgst,
                "itc_sgst": tc.itc_sgst,
                "net_igst": tc.net_igst,
                "net_cgst": tc.net_cgst,
                "net_sgst": tc.net_sgst,
                "net_payable": tc.net_payable,
                "at_risk_itc": r.at_risk_itc,
            },

            "gstin_issues": [
                {
                    "invoice_number": i.invoice_number,
                    "buyer_name": i.name,
                    "gstin": i.gstin,
                    "status": i.status,
                    "issue": i.issue,
                    "recommended_action": i.action,
                }
                for i in r.gstin_issues
            ],

            "itc_at_risk": [
                {
                    "invoice_number": r2.purchase_voucher.voucher_number,
                    "supplier": r2.purchase_voucher.supplier_name,
                    "supplier_gstin": r2.purchase_voucher.supplier_gstin,
                    "itc_at_risk": r2.itc_at_risk,
                    "reason": r2.risk_reason,
                }
                for r2 in r.itc_results
                if r2.status == "MISSING_FROM_GSTR2B"
            ],

            "amount_mismatches": [
                {
                    "invoice_number": r2.purchase_voucher.voucher_number,
                    "supplier": r2.purchase_voucher.supplier_name,
                    "itc_difference": r2.itc_at_risk,
                    "reason": r2.risk_reason,
                }
                for r2 in r.itc_results
                if r2.status == "AMOUNT_MISMATCH"
            ],

            "hsn_flags": [
                {
                    "invoice_number": f.invoice_number,
                    "item_name": f.item_name,
                    "hsn_in_tally": f.hsn_in_tally,
                    "suggested_hsn": f.suggested_hsn,
                    "description": f.description,
                }
                for f in r.hsn_flags
            ],

            "deadlines": {
                "gstr1_due": self.gstr1_due,
                "gstr3b_due": self.gstr3b_due,
            },
        }
