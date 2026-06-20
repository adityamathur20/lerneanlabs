"""
test_claude_agent.py
--------------------
Unit tests for prompt_builder.py and claude_agent.py.

Both modules are tested WITHOUT making real API calls.
claude_agent.py tests use dry_run=True throughout.
prompt_builder.py is pure data transformation — no I/O at all.

Test groups:
  A. PromptBuilder._serialise_for_prompt() — data shape, completeness, types
  B. PromptBuilder.build_whatsapp_prompt() — system prompt, user prompt content
  C. PromptBuilder.build_ca_prompt()       — system prompt, user prompt content
  D. ClaudeAPIClient._extract_text()       — response parsing, empty, no text block
  E. ClaudeAPIClient._dry_run_response()   — WhatsApp stub, CA stub detection
  F. ClaudeAPIClient.__init__()            — auth validation, dry_run bypass
  G. GSTClaudeAgent dry_run mode           — output types, issue count, fallback flag
  H. GSTClaudeAgent._build_issues_deterministic() — severity, categories, counts
  I. GSTClaudeAgent helpers                — _fmt_period, _days_until
  J. AgentOutput                           — to_dict, save to file
  K. save_agent_output()                   — file creation, paths returned
  L. Integration: full dry_run pipeline    — Mehta testcase end-to-end
  M. Negative tests                        — missing key, bad API response, no key
"""

import sys
import os
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from tally_parser import SalesVoucher, PurchaseVoucher, InventoryItem
from gstr2b_reader import GSTR2BReader, GSTR2BInvoice
from reconciler import (
    Reconciler, ReconciliationResult, TaxCalculation,
    GSTINValidationResult, ITCReconciliationResult, HSNFlagResult
)
from prompt_builder import PromptBuilder, WHATSAPP_SYSTEM_PROMPT, CA_SYSTEM_PROMPT
from claude_agent import (
    GSTClaudeAgent, ClientConfig, AgentOutput,
    ClaudeAPIClient, ClaudeAPIError,
    _fmt_period, _days_until,
    build_whatsapp_prompt, build_ca_report_prompt,
    _build_issues_deterministic, _fallback_whatsapp, _fallback_ca_report,
    save_agent_output
)

TESTCASE_BASE = Path(__file__).parent.parent / "testcases" / "mehta_textile_oct2024"


# ===========================================================================
# Shared helpers — build minimal ReconciliationResult objects for tests
# ===========================================================================

def _make_tax_calc(out_igst=10200, out_cgst=28200, out_sgst=28200,
                   itc_igst=0, itc_cgst=17580, itc_sgst=17580):
    return TaxCalculation(
        output_igst=out_igst, output_cgst=out_cgst, output_sgst=out_sgst,
        itc_igst=itc_igst, itc_cgst=itc_cgst, itc_sgst=itc_sgst,
        at_risk_igst=0, at_risk_cgst=2520, at_risk_sgst=2520
    )


def _make_purchase(vnum, supplier, gstin, cgst, sgst):
    return PurchaseVoucher(
        date="20241001", voucher_number=vnum, guid=f"PG-{vnum}",
        supplier_name=supplier, supplier_gstin=gstin,
        taxable_value=(cgst+sgst)/0.12,
        invoice_total=(cgst+sgst)*1.1,
        cgst=float(cgst), sgst=float(sgst), igst=0.0
    )


def _make_sales(vnum="INV/001", gstin="24AABGT1234A1Z9",
                 name="Good Buyer", cgst=6000, sgst=6000, igst=0,
                 taxable=100000, hsn="5208", item="Cotton Fabric"):
    return SalesVoucher(
        date="20241001", voucher_number=vnum, guid=f"G-{vnum}",
        buyer_name=name, buyer_gstin=gstin, place_of_supply="Gujarat",
        taxable_value=float(taxable), invoice_total=float(taxable+cgst+sgst+igst),
        cgst=float(cgst), sgst=float(sgst), igst=float(igst), supply_type="INTRA",
        items=[InventoryItem(item, hsn, 12.0, "1000 Mtr", "100/Mtr", float(taxable))]
    )


def _make_itc_result(vnum, supplier, gstin, cgst, sgst, status="MATCHED", at_risk=0):
    purchase = _make_purchase(vnum, supplier, gstin, cgst, sgst)
    gstr2b_inv = None
    if status == "MATCHED":
        gstr2b_inv = GSTR2BInvoice(
            invoice_number=vnum, invoice_date="01-10-2024",
            invoice_value=float(cgst+sgst)*10, supplier_gstin=gstin,
            supplier_name=supplier, place_of_supply="24",
            itc_available=True, reverse_charge=False,
            igst=0.0, cgst=float(cgst), sgst=float(sgst), cess=0.0
        )
    return ITCReconciliationResult(
        purchase_voucher=purchase, gstr2b_invoice=gstr2b_inv,
        status=status, itc_claimed=float(cgst+sgst) if status=="MATCHED" else 0.0,
        itc_at_risk=float(at_risk),
        risk_reason=f"Supplier not filed" if status=="MISSING_FROM_GSTR2B" else None
    )


def _clean_result():
    """A perfectly clean ReconciliationResult — no issues."""
    tc = _make_tax_calc(out_igst=0, out_cgst=6000, out_sgst=6000,
                         itc_igst=0, itc_cgst=3000, itc_sgst=3000)
    tc.at_risk_cgst = 0; tc.at_risk_sgst = 0
    return ReconciliationResult(
        gstin="24AABMT1234C1Z5", period="102024",
        sales_vouchers=[_make_sales()],
        total_sales_value=100000.0, total_output_gst=12000.0,
        purchase_vouchers=[_make_purchase("SM/001", "Silk Mills", "24AABSM1111A1Z8", 3000, 3000)],
        itc_results=[_make_itc_result("SM/001", "Silk Mills", "24AABSM1111A1Z8", 3000, 3000)],
        total_purchase_value=50000.0, confirmed_itc=6000.0, at_risk_itc=0.0,
        gstin_issues=[], hsn_flags=[],
        tax_calc=tc, has_critical_issues=False, issue_count=0, status="CLEAN"
    )


def _issues_result():
    """ReconciliationResult with all three issue types (Mehta scenario)."""
    tc = _make_tax_calc()
    gstin_issue = GSTINValidationResult(
        gstin="24AAFVT9999Z1Z9", name="Verma Traders",
        invoice_number="MTT/OCT/004", status="Cancelled",
        issue="GSTIN 24AAFVT9999Z1Z9 is Cancelled",
        action="Reclassify invoice(s) from B2B to B2CS in GSTR-1"
    )
    missing_itc = _make_itc_result("DM/2024/387", "Dye Masters Pvt Ltd",
                                    "24AABDM5678E1Z2", 2520, 2520,
                                    status="MISSING_FROM_GSTR2B", at_risk=5040)
    matched_itc = _make_itc_result("SM/2024/1102", "Silk Mills Ltd",
                                    "24AABSM1111A1Z8", 10800, 10800)
    hsn_flag = HSNFlagResult(
        invoice_number="MTT/OCT/003", item_name="Cotton Plain Weave Fabric 60x60",
        hsn_in_tally="5407", suggested_hsn="5208",
        description="Item 'Cotton Plain Weave Fabric 60x60' may not match HSN 5407. Suggested: 5208"
    )
    sales = [
        _make_sales("MTT/OCT/001", "24AAFPG1234D1Z9", "Patel Fabrics", 7200, 7200, 0, 120000),
        _make_sales("MTT/OCT/003", "24AABGE3456F1Z7", "Gupta Exports", 12600, 12600, 0, 210000,
                    hsn="5407", item="Cotton Plain Weave Fabric 60x60"),
        _make_sales("MTT/OCT/004", "24AAFVT9999Z1Z9", "Verma Traders", 2700, 2700, 0, 45000),
    ]
    return ReconciliationResult(
        gstin="24AABMT1234C1Z5", period="102024",
        sales_vouchers=sales, total_sales_value=555000.0, total_output_gst=66600.0,
        purchase_vouchers=[matched_itc.purchase_voucher, missing_itc.purchase_voucher],
        itc_results=[matched_itc, missing_itc],
        total_purchase_value=335000.0, confirmed_itc=35160.0, at_risk_itc=5040.0,
        gstin_issues=[gstin_issue], hsn_flags=[hsn_flag],
        tax_calc=tc, has_critical_issues=True, issue_count=3, status="CRITICAL"
    )


def _make_cfg(**kwargs):
    defaults = dict(
        firm_name="Mehta Textile Traders", gstin="24AABMT1234C1Z5",
        owner_name="Mehta", ca_name="Rajesh Shah",
        ca_email="rajesh@example.com", filing_period_label="October 2024",
        gstr1_due_date="2024-11-11", gstr3b_due_date="2024-11-20"
    )
    defaults.update(kwargs)
    return ClientConfig(**defaults)


# ===========================================================================
# A. PromptBuilder._serialise_for_prompt()
# ===========================================================================

class TestSerialiseForPrompt(unittest.TestCase):

    def setUp(self):
        self.result = _issues_result()
        self.builder = PromptBuilder(
            result=self.result, firm_name="Mehta Textile",
            gstr1_due="2024-11-11", gstr3b_due="2024-11-20", ca_name="Rajesh Shah"
        )
        self.data = self.builder._serialise_for_prompt()

    def test_returns_dict(self):
        self.assertIsInstance(self.data, dict)

    def test_has_required_top_level_keys(self):
        for key in ["status", "issue_count", "tax_liability", "gstin_issues",
                    "itc_at_risk", "hsn_flags", "deadlines", "sales_summary",
                    "purchase_summary"]:
            self.assertIn(key, self.data, f"Missing key: {key}")

    def test_status_correct(self):
        self.assertEqual(self.data["status"], "CRITICAL")

    def test_issue_count_correct(self):
        self.assertEqual(self.data["issue_count"], 3)

    def test_net_payable_correct(self):
        self.assertEqual(self.data["tax_liability"]["net_payable"], 31440.0)

    def test_gstin_issues_count(self):
        self.assertEqual(len(self.data["gstin_issues"]), 1)

    def test_gstin_issue_has_invoice_number(self):
        self.assertEqual(self.data["gstin_issues"][0]["invoice_number"], "MTT/OCT/004")

    def test_gstin_issue_has_recommended_action(self):
        self.assertIn("recommended_action", self.data["gstin_issues"][0])

    def test_itc_at_risk_count(self):
        self.assertEqual(len(self.data["itc_at_risk"]), 1)

    def test_itc_at_risk_amount(self):
        self.assertEqual(self.data["itc_at_risk"][0]["itc_at_risk"], 5040.0)

    def test_itc_at_risk_supplier_name(self):
        self.assertEqual(self.data["itc_at_risk"][0]["supplier"], "Dye Masters Pvt Ltd")

    def test_hsn_flags_count(self):
        self.assertEqual(len(self.data["hsn_flags"]), 1)

    def test_hsn_flag_invoice(self):
        self.assertEqual(self.data["hsn_flags"][0]["invoice_number"], "MTT/OCT/003")

    def test_hsn_flag_suggested(self):
        self.assertEqual(self.data["hsn_flags"][0]["suggested_hsn"], "5208")

    def test_deadlines_present(self):
        self.assertEqual(self.data["deadlines"]["gstr1_due"], "2024-11-11")
        self.assertEqual(self.data["deadlines"]["gstr3b_due"], "2024-11-20")

    def test_sales_summary_invoice_count(self):
        self.assertEqual(self.data["sales_summary"]["invoice_count"], 3)

    def test_amount_mismatches_empty_for_this_result(self):
        # No AMOUNT_MISMATCH results in issues_result
        self.assertEqual(self.data["amount_mismatches"], [])

    def test_clean_result_no_issues(self):
        builder = PromptBuilder(
            result=_clean_result(), firm_name="Test Firm",
            gstr1_due="2024-11-11", gstr3b_due="2024-11-20", ca_name="CA"
        )
        data = builder._serialise_for_prompt()
        self.assertEqual(data["gstin_issues"], [])
        self.assertEqual(data["itc_at_risk"], [])
        self.assertEqual(data["hsn_flags"], [])

    def test_data_is_json_serialisable(self):
        """Must not raise — data goes into a JSON prompt."""
        try:
            json.dumps(self.data)
        except (TypeError, ValueError) as e:
            self.fail(f"Serialised data is not JSON-safe: {e}")


# ===========================================================================
# B. PromptBuilder.build_whatsapp_prompt()
# ===========================================================================

class TestBuildWhatsappPrompt(unittest.TestCase):

    def setUp(self):
        self.builder = PromptBuilder(
            result=_issues_result(), firm_name="Mehta Textile",
            gstr1_due="2024-11-11", gstr3b_due="2024-11-20", ca_name="Rajesh Shah"
        )
        self.sys_p, self.user_p = self.builder.build_whatsapp_prompt()

    def test_returns_two_strings(self):
        self.assertIsInstance(self.sys_p, str)
        self.assertIsInstance(self.user_p, str)

    def test_system_prompt_matches_constant(self):
        self.assertEqual(self.sys_p, WHATSAPP_SYSTEM_PROMPT)

    def test_user_prompt_contains_firm_name(self):
        self.assertIn("Mehta Textile", self.user_p)

    def test_user_prompt_contains_gstin(self):
        self.assertIn("24AABMT1234C1Z5", self.user_p)

    def test_user_prompt_contains_gstr1_due(self):
        self.assertIn("2024-11-11", self.user_p)

    def test_user_prompt_contains_gstr3b_due(self):
        self.assertIn("2024-11-20", self.user_p)

    def test_user_prompt_contains_net_payable(self):
        self.assertIn("31440", self.user_p)

    def test_user_prompt_contains_structured_data_json(self):
        self.assertIn('"net_payable"', self.user_p)

    def test_system_prompt_contains_200_word_limit(self):
        self.assertIn("200", self.sys_p)

    def test_system_prompt_no_jargon_rule(self):
        self.assertIn("jargon", self.sys_p.lower())

    def test_user_prompt_has_write_instruction(self):
        self.assertIn("Write", self.user_p)


# ===========================================================================
# C. PromptBuilder.build_ca_prompt()
# ===========================================================================

class TestBuildCAPrompt(unittest.TestCase):

    def setUp(self):
        self.builder = PromptBuilder(
            result=_issues_result(), firm_name="Mehta Textile",
            gstr1_due="2024-11-11", gstr3b_due="2024-11-20", ca_name="Rajesh Shah"
        )
        self.sys_p, self.user_p = self.builder.build_ca_prompt()

    def test_returns_two_strings(self):
        self.assertIsInstance(self.sys_p, str)
        self.assertIsInstance(self.user_p, str)

    def test_system_prompt_matches_constant(self):
        self.assertEqual(self.sys_p, CA_SYSTEM_PROMPT)

    def test_user_prompt_contains_ca_name(self):
        self.assertIn("Rajesh Shah", self.user_p)

    def test_user_prompt_contains_firm_name(self):
        self.assertIn("Mehta Textile", self.user_p)

    def test_user_prompt_contains_net_payable(self):
        self.assertIn("31440", self.user_p)

    def test_user_prompt_contains_structured_data(self):
        self.assertIn('"status"', self.user_p)

    def test_system_prompt_mentions_400_word_limit(self):
        self.assertIn("400", self.sys_p)

    def test_system_prompt_mentions_sections(self):
        self.assertIn("Executive Summary", self.sys_p)
        self.assertIn("Filing Checklist", self.sys_p)

    def test_system_prompt_no_markdown_rule(self):
        self.assertIn("markdown", self.sys_p.lower())


class TestClaudeAPIClientCall(unittest.TestCase):
    """ClaudeAPIClient.call() — response parsing via mocked urllib."""

    def _make_response(self, text, input_tokens=0, output_tokens=0):
        """Build a fake Claude API response dict."""
        return json.dumps({
            "content": [{"type": "text", "text": text}],
            "model": "claude-haiku-4-5-20251001",
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }).encode()

    def _make_client(self):
        return ClaudeAPIClient(api_key="sk-ant-test-key-xxxx")

    def test_successful_call_returns_text(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._make_response("Hello from Claude", input_tokens=10, output_tokens=5)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            text, in_tok, out_tok = client.call("system", "user")
        self.assertEqual(text, "Hello from Claude")
        self.assertEqual(in_tok, 10)
        self.assertEqual(out_tok, 5)

    def test_http_error_raises_claude_api_error(self):
        import urllib.error
        client = self._make_client()
        err = urllib.error.HTTPError(url="", code=500, msg="Internal Server Error",
                                      hdrs=None, fp=MagicMock(read=lambda: b"error"))
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ClaudeAPIError):
                client.call("system", "user")

    def test_network_error_raises_claude_api_error(self):
        import urllib.error
        client = self._make_client()
        with patch("urllib.request.urlopen",
                    side_effect=urllib.error.URLError("Network unreachable")):
            with self.assertRaises(ClaudeAPIError):
                client.call("system", "user")

    def test_multiline_text_joined(self):
        """Multiple text blocks are joined with newlines."""
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "content": [
                {"type": "text", "text": "Line one"},
                {"type": "text", "text": "Line two"},
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            text, _, _ = client.call("system", "user")
        self.assertIn("Line one", text)
        self.assertIn("Line two", text)


# ===========================================================================
# E. GSTClaudeAgent — fallback template content
# ===========================================================================

class TestFallbackTemplates(unittest.TestCase):
    """_fallback_whatsapp and _fallback_ca_report produce correct content."""

    def setUp(self):
        self.result = _issues_result()
        self.cfg    = _make_cfg()

    def test_whatsapp_contains_firm_name(self):
        msg = _fallback_whatsapp(self.result, self.cfg)
        self.assertIn("Mehta", msg)

    def test_whatsapp_contains_net_payable(self):
        msg = _fallback_whatsapp(self.result, self.cfg)
        self.assertIn("31,440", msg)

    def test_whatsapp_contains_gstr1_due(self):
        msg = _fallback_whatsapp(self.result, self.cfg)
        self.assertIn("2024-11-11", msg)

    def test_whatsapp_contains_gstr3b_due(self):
        msg = _fallback_whatsapp(self.result, self.cfg)
        self.assertIn("2024-11-20", msg)

    def test_whatsapp_mentions_cancelled_gstin_issue(self):
        msg = _fallback_whatsapp(self.result, self.cfg)
        self.assertIn("cancelled", msg.lower())

    def test_whatsapp_mentions_missing_itc_supplier(self):
        msg = _fallback_whatsapp(self.result, self.cfg)
        self.assertIn("Dye Masters", msg)

    def test_ca_report_has_all_sections(self):
        report = _fallback_ca_report(self.result, self.cfg)
        for section in ["FILING STATUS SUMMARY", "ISSUES REQUIRING YOUR ATTENTION",
                        "ITC POSITION", "TAX LIABILITY CALCULATION",
                        "RECOMMENDED ACTIONS", "FILING CHECKLIST"]:
            self.assertIn(section, report)

    def test_ca_report_contains_verma_invoice(self):
        report = _fallback_ca_report(self.result, self.cfg)
        self.assertIn("MTT/OCT/004", report)

    def test_ca_report_contains_dye_masters_invoice(self):
        report = _fallback_ca_report(self.result, self.cfg)
        self.assertIn("DM/2024/387", report)

    def test_ca_report_contains_net_payable(self):
        report = _fallback_ca_report(self.result, self.cfg)
        self.assertIn("31,440", report)

    def test_ca_report_contains_due_dates(self):
        report = _fallback_ca_report(self.result, self.cfg)
        self.assertIn("2024-11-11", report)
        self.assertIn("2024-11-20", report)


# ===========================================================================
# F. ClaudeAPIClient init
# ===========================================================================

class TestClaudeAPIClientInit(unittest.TestCase):

    def test_explicit_key_stored(self):
        client = ClaudeAPIClient("sk-ant-mykey")
        self.assertEqual(client.api_key, "sk-ant-mykey")

    def test_haiku_model_defined(self):
        self.assertIsNotNone(ClaudeAPIClient.HAIKU)
        self.assertIn("haiku", ClaudeAPIClient.HAIKU.lower())

    def test_sonnet_model_defined(self):
        self.assertIsNotNone(ClaudeAPIClient.SONNET)
        self.assertIn("sonnet", ClaudeAPIClient.SONNET.lower())

    def test_base_url_is_anthropic(self):
        self.assertIn("anthropic.com", ClaudeAPIClient.BASE_URL)


# ===========================================================================
# G. GSTClaudeAgent dry_run mode
# ===========================================================================

class TestGSTClaudeAgentDryRun(unittest.TestCase):

    def setUp(self):
        self.agent  = GSTClaudeAgent(dry_run=True, verbose=False)
        self.result = _issues_result()
        self.cfg    = _make_cfg()

    def test_returns_agent_output(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIsInstance(output, AgentOutput)

    def test_whatsapp_message_not_empty(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertGreater(len(output.whatsapp_message), 50)

    def test_ca_report_not_empty(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertGreater(len(output.ca_report), 100)

    def test_fallback_used_true_in_dry_run(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertTrue(output.fallback_used)

    def test_model_used_template_in_dry_run(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("template", output.model_used_whatsapp)
        self.assertIn("template", output.model_used_ca_report)

    def test_issues_structured_count(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertEqual(len(output.issues_structured), 3)

    def test_period_stored(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertEqual(output.period, "102024")

    def test_gstin_stored(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertEqual(output.client_gstin, "24AABMT1234C1Z5")

    def test_clean_result_zero_issues(self):
        output = self.agent.run(_clean_result(), self.cfg)
        self.assertEqual(len(output.issues_structured), 0)

    def test_whatsapp_contains_firm_name(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("Mehta", output.whatsapp_message)

    def test_whatsapp_contains_net_payable(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("31,440", output.whatsapp_message)

    def test_ca_report_contains_filing_status(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("FILING STATUS SUMMARY", output.ca_report)

    def test_ca_report_contains_net_payable(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("31,440", output.ca_report)

    def test_ca_report_contains_cancelled_gstin_issue(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("MTT/OCT/004", output.ca_report)

    def test_ca_report_contains_missing_itc_invoice(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("DM/2024/387", output.ca_report)

    def test_ca_report_contains_filing_checklist(self):
        output = self.agent.run(self.result, self.cfg)
        self.assertIn("FILING CHECKLIST", output.ca_report)

    def test_no_api_key_falls_back_to_dry_run(self):
        """GSTClaudeAgent with no key and dry_run=False must still work via template."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            agent = GSTClaudeAgent(dry_run=False, verbose=False)
            self.assertTrue(agent.dry_run)


# ===========================================================================
# H. _build_issues_deterministic()
# ===========================================================================

class TestBuildIssuesDeterministic(unittest.TestCase):

    def setUp(self):
        self.result = _issues_result()
        self.issues = _build_issues_deterministic(self.result)

    def test_returns_list(self):
        self.assertIsInstance(self.issues, list)

    def test_three_issues_from_three_problem_types(self):
        self.assertEqual(len(self.issues), 3)

    def test_issue_ids_sequential(self):
        ids = [i["issue_id"] for i in self.issues]
        self.assertEqual(ids, ["ISSUE_001", "ISSUE_002", "ISSUE_003"])

    def test_cancelled_gstin_is_high_severity(self):
        gstin_issues = [i for i in self.issues if i["category"] == "CANCELLED_GSTIN"]
        self.assertTrue(all(i["severity"] == "HIGH" for i in gstin_issues))

    def test_missing_itc_above_5000_is_high(self):
        itc_issues = [i for i in self.issues if i["category"] == "MISSING_ITC"]
        high = [i for i in itc_issues if i["tax_impact_inr"] > 5000]
        self.assertTrue(all(i["severity"] == "HIGH" for i in high))

    def test_missing_itc_below_5000_is_medium(self):
        purchase = _make_purchase("SMALL/001", "Small Co", "24AABSC1234A1Z1", 1000, 1000)
        small_missing = ITCReconciliationResult(
            purchase_voucher=purchase, gstr2b_invoice=None,
            status="MISSING_FROM_GSTR2B", itc_claimed=0.0, itc_at_risk=2000.0,
            risk_reason="Supplier not filed"
        )
        from reconciler import TaxCalculation
        tc = _make_tax_calc()
        result = ReconciliationResult(
            gstin="X", period="102024", sales_vouchers=[], total_sales_value=0,
            total_output_gst=0, purchase_vouchers=[purchase],
            itc_results=[small_missing], total_purchase_value=0,
            confirmed_itc=0, at_risk_itc=2000, gstin_issues=[], hsn_flags=[],
            tax_calc=tc, has_critical_issues=False, issue_count=1, status="ISSUES_FOUND"
        )
        issues = _build_issues_deterministic(result)
        self.assertEqual(issues[0]["severity"], "MEDIUM")

    def test_hsn_error_is_low_severity(self):
        hsn_issues = [i for i in self.issues if i["category"] == "HSN_ERROR"]
        self.assertTrue(all(i["severity"] == "LOW" for i in hsn_issues))

    def test_gstin_issue_has_ca_decision_needed(self):
        gstin_issues = [i for i in self.issues if i["category"] == "CANCELLED_GSTIN"]
        self.assertTrue(all(i["ca_decision_needed"] for i in gstin_issues))

    def test_hsn_issue_no_ca_decision_needed(self):
        hsn_issues = [i for i in self.issues if i["category"] == "HSN_ERROR"]
        self.assertTrue(all(not i["ca_decision_needed"] for i in hsn_issues))

    def test_missing_itc_tax_impact_correct(self):
        itc_issues = [i for i in self.issues if i["category"] == "MISSING_ITC"]
        self.assertEqual(itc_issues[0]["tax_impact_inr"], 5040.0)

    def test_all_issues_have_required_fields(self):
        required = ["issue_id", "severity", "category", "description",
                    "action_required", "ca_decision_needed", "tax_impact_inr"]
        for issue in self.issues:
            for field in required:
                self.assertIn(field, issue, f"Issue missing field: {field}")

    def test_clean_result_returns_empty_list(self):
        self.assertEqual(_build_issues_deterministic(_clean_result()), [])

    def test_amount_mismatch_category(self):
        purchase = _make_purchase("MM/001", "Supplier X", "24AABSM1111A1Z8", 3500, 3500)
        gstr2b_inv = GSTR2BInvoice(
            "MM/001", "01-10-2024", 56000, "24AABSM1111A1Z8", "Supplier X",
            "24", True, False, 0.0, 3000.0, 3000.0, 0.0
        )
        mismatch = ITCReconciliationResult(
            purchase_voucher=purchase, gstr2b_invoice=gstr2b_inv,
            status="AMOUNT_MISMATCH", itc_claimed=6000.0, itc_at_risk=1000.0,
            risk_reason="Tally ₹7,000 vs GSTR-2B ₹6,000"
        )
        tc = _make_tax_calc()
        result = ReconciliationResult(
            gstin="X", period="102024", sales_vouchers=[], total_sales_value=0,
            total_output_gst=0, purchase_vouchers=[purchase],
            itc_results=[mismatch], total_purchase_value=0,
            confirmed_itc=0, at_risk_itc=1000, gstin_issues=[], hsn_flags=[],
            tax_calc=tc, has_critical_issues=False, issue_count=1, status="ISSUES_FOUND"
        )
        issues = _build_issues_deterministic(result)
        self.assertEqual(issues[0]["category"], "AMOUNT_MISMATCH")
        self.assertEqual(issues[0]["severity"], "MEDIUM")


# ===========================================================================
# I. Helper functions: _fmt_period, _days_until
# ===========================================================================

class TestHelperFunctions(unittest.TestCase):

    def test_fmt_period_october(self):
        self.assertEqual(_fmt_period("102024"), "October 2024")

    def test_fmt_period_january(self):
        self.assertEqual(_fmt_period("012024"), "January 2024")

    def test_fmt_period_december(self):
        self.assertEqual(_fmt_period("122024"), "December 2024")

    def test_fmt_period_invalid_passthrough(self):
        # Short/invalid codes pass through unchanged
        result = _fmt_period("ABC")
        self.assertIsInstance(result, str)

    def test_days_until_future_date(self):
        future = (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")
        days = _days_until(future)
        self.assertAlmostEqual(days, 10, delta=1)

    def test_days_until_past_date_negative(self):
        past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        days = _days_until(past)
        self.assertLessEqual(days, 0)

    def test_days_until_invalid_returns_zero(self):
        self.assertEqual(_days_until("not-a-date"), 0)

    def test_days_until_today(self):
        today = datetime.now().strftime("%Y-%m-%d")
        days = _days_until(today)
        self.assertEqual(days, 0)


# ===========================================================================
# J. AgentOutput — to_dict, save
# ===========================================================================

class TestAgentOutput(unittest.TestCase):

    def _make_output(self):
        return AgentOutput(
            client_gstin="24AABMT1234C1Z5",
            period="102024",
            generated_at="2024-11-01T23:00:00",
            whatsapp_message="Hello Mehta ji",
            ca_report="CA HANDOFF REPORT",
        )

    def test_has_whatsapp_message_field(self):
        self.assertEqual(self._make_output().whatsapp_message, "Hello Mehta ji")

    def test_has_ca_report_field(self):
        self.assertEqual(self._make_output().ca_report, "CA HANDOFF REPORT")

    def test_has_client_gstin_field(self):
        self.assertEqual(self._make_output().client_gstin, "24AABMT1234C1Z5")

    def test_has_period_field(self):
        self.assertEqual(self._make_output().period, "102024")

    def test_json_serialisable(self):
        """AgentOutput fields must be JSON-safe (for save_agent_output)."""
        output = self._make_output()
        try:
            json.dumps({
                "client_gstin": output.client_gstin,
                "period": output.period,
                "whatsapp_message": output.whatsapp_message,
                "ca_report": output.ca_report,
            })
        except (TypeError, ValueError) as e:
            self.fail(f"AgentOutput is not JSON-safe: {e}")

    def test_fallback_used_default_false(self):
        output = self._make_output()
        self.assertFalse(output.fallback_used)


# ===========================================================================
# K. save_agent_output()
# ===========================================================================

class TestSaveAgentOutput(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.output = AgentOutput(
            client_gstin="24AABMT1234C1Z5",
            period="102024",
            generated_at="2024-11-01T23:00:00",
            whatsapp_message="Hello Mehta ji 🙏 Tax payable: ₹31,440",
            ca_report="EXECUTIVE SUMMARY\nNet payable: ₹31,440\nFILING CHECKLIST\n1. File GSTR-1",
            issues_structured=[{"issue_id": "ISSUE_001", "severity": "HIGH",
                                  "category": "CANCELLED_GSTIN", "description": "Test issue",
                                  "tax_impact_inr": 0, "action_required": "Do X",
                                  "ca_decision_needed": True}]
        )

    def test_save_creates_whatsapp_file(self):
        save_agent_output(self.output, self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "whatsapp_generated.txt")))

    def test_save_creates_ca_report_file(self):
        save_agent_output(self.output, self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "ca_report_generated.txt")))

    def test_save_creates_issues_json_file(self):
        save_agent_output(self.output, self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "issues_structured_generated.json")))

    def test_save_creates_metadata_file(self):
        save_agent_output(self.output, self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "agent_run_metadata.json")))

    def test_save_returns_paths_dict(self):
        paths = save_agent_output(self.output, self.tmpdir)
        self.assertIsInstance(paths, dict)
        self.assertIn("whatsapp", paths)
        self.assertIn("ca_report", paths)
        self.assertIn("issues", paths)
        self.assertIn("metadata", paths)

    def test_whatsapp_file_content_correct(self):
        save_agent_output(self.output, self.tmpdir)
        with open(os.path.join(self.tmpdir, "whatsapp_generated.txt")) as f:
            content = f.read()
        self.assertIn("Hello Mehta", content)

    def test_issues_json_valid(self):
        save_agent_output(self.output, self.tmpdir)
        with open(os.path.join(self.tmpdir, "issues_structured_generated.json")) as f:
            data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    def test_metadata_contains_gstin(self):
        save_agent_output(self.output, self.tmpdir)
        with open(os.path.join(self.tmpdir, "agent_run_metadata.json")) as f:
            meta = json.load(f)
        self.assertEqual(meta["client_gstin"], "24AABMT1234C1Z5")

    def test_creates_output_dir_if_missing(self):
        new_dir = os.path.join(self.tmpdir, "new_subdir")
        self.assertFalse(os.path.exists(new_dir))
        save_agent_output(self.output, new_dir)
        self.assertTrue(os.path.exists(new_dir))


# ===========================================================================
# L. Integration: full dry_run pipeline on Mehta testcase
# ===========================================================================

class TestFullDryRunIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        sales_xml    = TESTCASE_BASE / "tally_export" / "sales_daybook_oct2024.xml"
        purchase_xml = TESTCASE_BASE / "tally_export" / "purchase_daybook_oct2024.xml"
        gstr2b_json  = TESTCASE_BASE / "gstr2b" / "gstr2b_oct2024.json"
        if not all(p.exists() for p in [sales_xml, purchase_xml, gstr2b_json]):
            raise unittest.SkipTest("Testcase files not found")

        from tally_parser import TallyParser
        sales     = TallyParser(str(sales_xml)).parse_sales_vouchers()
        purchases = TallyParser(str(purchase_xml)).parse_purchase_vouchers()
        reader    = GSTR2BReader.from_file(str(gstr2b_json))

        with patch("builtins.print"):
            result = Reconciler(sales, purchases, reader, "24AABMT1234C1Z5", "102024").run()

        cfg = ClientConfig(
            firm_name="Mehta Textile Traders", gstin="24AABMT1234C1Z5",
            owner_name="Mehta", ca_name="Rajesh Shah",
            ca_email="rajesh.shah.ca@gmail.com",
            filing_period_label="October 2024",
            gstr1_due_date="2024-11-11", gstr3b_due_date="2024-11-20",
        )
        agent = GSTClaudeAgent(dry_run=True, verbose=False)
        cls.output = agent.run(result, cfg)
        cls.result = result

    def test_output_is_agent_output(self):
        self.assertIsInstance(self.output, AgentOutput)

    def test_three_structured_issues(self):
        self.assertEqual(len(self.output.issues_structured), 3)

    def test_whatsapp_mentions_verma_traders(self):
        self.assertIn("Verma", self.output.whatsapp_message)

    def test_whatsapp_mentions_net_payable(self):
        self.assertIn("31,440", self.output.whatsapp_message)

    def test_ca_report_has_all_sections(self):
        for section in ["FILING STATUS SUMMARY", "ISSUES REQUIRING YOUR ATTENTION",
                        "ITC POSITION", "TAX LIABILITY CALCULATION",
                        "RECOMMENDED ACTIONS", "FILING CHECKLIST"]:
            self.assertIn(section, self.output.ca_report, f"Missing section: {section}")

    def test_ca_report_mentions_dye_masters_invoice(self):
        self.assertIn("DM/2024/387", self.output.ca_report)

    def test_ca_report_mentions_verma_invoice(self):
        self.assertIn("MTT/OCT/004", self.output.ca_report)

    def test_ca_report_has_correct_net_payable(self):
        self.assertIn("31,440", self.output.ca_report)

    def test_ca_report_has_due_dates(self):
        self.assertIn("2024-11-11", self.output.ca_report)
        self.assertIn("2024-11-20", self.output.ca_report)

    def test_issues_have_correct_categories(self):
        cats = {i["category"] for i in self.output.issues_structured}
        self.assertIn("CANCELLED_GSTIN", cats)
        self.assertIn("MISSING_ITC", cats)
        self.assertIn("HSN_ERROR", cats)

    def test_cancelled_gstin_issue_correct_invoice(self):
        cancelled = [i for i in self.output.issues_structured if i["category"] == "CANCELLED_GSTIN"]
        self.assertEqual(cancelled[0]["invoice_number"], "MTT/OCT/004")

    def test_missing_itc_correct_amount(self):
        itc = [i for i in self.output.issues_structured if i["category"] == "MISSING_ITC"]
        self.assertEqual(itc[0]["tax_impact_inr"], 5040.0)

    def test_output_can_be_saved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = save_agent_output(self.output, tmpdir)
            self.assertTrue(os.path.exists(paths["whatsapp"]))
            self.assertTrue(os.path.exists(paths["ca_report"]))
            self.assertTrue(os.path.exists(paths["issues"]))

    def test_saved_whatsapp_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = save_agent_output(self.output, tmpdir)
            with open(paths["whatsapp"], encoding="utf-8") as f:
                text = f.read()
            self.assertGreater(len(text), 50)


# ===========================================================================
# M. Negative tests
# ===========================================================================

class TestNegativeCases(unittest.TestCase):

    def test_client_call_bad_key_raises(self):
        """HTTP 401 from bad API key must raise ClaudeAPIError."""
        import urllib.error
        client = ClaudeAPIClient(api_key="sk-bad-key")
        err = urllib.error.HTTPError(
            url="", code=401, msg="Unauthorized",
            hdrs=None, fp=MagicMock(read=lambda: b'{"error":"invalid key"}')
        )
        with patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ClaudeAPIError):
                client.call("system", "user")

    def test_gst_agent_no_key_no_dry_run_becomes_template(self):
        """Agent with no key and dry_run=False should gracefully use templates."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            agent = GSTClaudeAgent(verbose=False)
            self.assertTrue(agent.dry_run)

    def test_whatsapp_prompt_missing_payable_still_runs(self):
        """Even with zero net payable, prompt builds without crashing."""
        tc = TaxCalculation(0, 0, 0, 0, 0, 0, 0, 0, 0)
        result = ReconciliationResult(
            gstin="X", period="102024", sales_vouchers=[], total_sales_value=0,
            total_output_gst=0, purchase_vouchers=[], itc_results=[],
            total_purchase_value=0, confirmed_itc=0, at_risk_itc=0,
            gstin_issues=[], hsn_flags=[],
            tax_calc=tc, has_critical_issues=False, issue_count=0, status="CLEAN"
        )
        builder = PromptBuilder(result=result, firm_name="Empty Firm",
                                 gstr1_due="2024-11-11", gstr3b_due="2024-11-20",
                                 ca_name="CA")
        sys_p, user_p = builder.build_whatsapp_prompt()
        self.assertIsInstance(user_p, str)
        self.assertIn("0", user_p)  # zero payable present

    def test_prompt_builder_serialise_empty_lists(self):
        """Clean result must serialise with empty issue arrays, not crash."""
        builder = PromptBuilder(result=_clean_result(), firm_name="Clean Co",
                                 gstr1_due="2024-11-11", gstr3b_due="2024-11-20",
                                 ca_name="CA")
        data = builder._serialise_for_prompt()
        self.assertEqual(data["gstin_issues"], [])
        self.assertEqual(data["itc_at_risk"], [])
        self.assertEqual(data["hsn_flags"], [])

    def test_fmt_period_empty_string(self):
        result = _fmt_period("")
        self.assertIsInstance(result, str)

    def test_days_until_empty_string(self):
        # Should not raise
        result = _days_until("")
        self.assertEqual(result, 0)

    def test_build_issues_empty_result_no_crash(self):
        tc = _make_tax_calc()
        result = ReconciliationResult(
            gstin="X", period="102024", sales_vouchers=[], total_sales_value=0,
            total_output_gst=0, purchase_vouchers=[], itc_results=[],
            total_purchase_value=0, confirmed_itc=0, at_risk_itc=0,
            gstin_issues=[], hsn_flags=[],
            tax_calc=tc, has_critical_issues=False, issue_count=0, status="CLEAN"
        )
        issues = _build_issues_deterministic(result)
        self.assertEqual(issues, [])

    def test_fallback_whatsapp_zero_payable_message(self):
        tc = TaxCalculation(0, 6000, 6000, 0, 6000, 6000, 0, 0, 0)
        result = ReconciliationResult(
            gstin="X", period="102024", sales_vouchers=[_make_sales()],
            total_sales_value=100000, total_output_gst=12000,
            purchase_vouchers=[], itc_results=[], total_purchase_value=0,
            confirmed_itc=12000, at_risk_itc=0, gstin_issues=[], hsn_flags=[],
            tax_calc=tc, has_critical_issues=False, issue_count=0, status="CLEAN"
        )
        cfg = _make_cfg()
        msg = _fallback_whatsapp(result, cfg)
        # When net payable is 0, should say "No tax payment needed"
        self.assertIn("No tax payment needed", msg)

    def test_fallback_ca_report_zero_issues(self):
        tc = _make_tax_calc()
        result = ReconciliationResult(
            gstin="X", period="102024", sales_vouchers=[_make_sales()],
            total_sales_value=100000, total_output_gst=12000,
            purchase_vouchers=[], itc_results=[], total_purchase_value=0,
            confirmed_itc=0, at_risk_itc=0, gstin_issues=[], hsn_flags=[],
            tax_calc=tc, has_critical_issues=False, issue_count=0, status="CLEAN"
        )
        cfg = _make_cfg()
        report = _fallback_ca_report(result, cfg)
        self.assertIn("No issues found", report)


# ===========================================================================
# RUNNER
# ===========================================================================

if __name__ == "__main__":
    verbosity = 2 if "-v" in sys.argv else 1
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()

    test_classes = [
        TestSerialiseForPrompt,
        TestBuildWhatsappPrompt,
        TestBuildCAPrompt,
        TestClaudeAPIClientCall,
        TestFallbackTemplates,
        TestClaudeAPIClientInit,
        TestGSTClaudeAgentDryRun,
        TestBuildIssuesDeterministic,
        TestHelperFunctions,
        TestAgentOutput,
        TestSaveAgentOutput,
        TestFullDryRunIntegration,
        TestNegativeCases,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
