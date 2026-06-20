"""
run_all_tests.py
----------------
Master test runner — runs the full test suite across all modules.

Uses only Python stdlib (unittest). No pytest required.

Run:
  cd GSTAgent/tests
  python run_all_tests.py          # summary
  python run_all_tests.py -v       # verbose per-test output

Coverage:
  run_tests.py        → tally_parser, gstr2b_reader, reconciler   (100 tests)
  test_claude_agent.py → prompt_builder, claude_agent              (134 tests)
  ─────────────────────────────────────────────────────────────────
  TOTAL                                                            (234 tests)
"""

import sys
import unittest
from pathlib import Path

# Ensure both agent/ and tests/ are importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

# ── Import test classes from run_tests.py ──────────────────────────────────
from run_tests import (
    TestAmountParsing,
    TestCleanGSTIN,
    TestTextExtraction,
    TestParseSalesVouchers,
    TestGSTR2BReader,
    TestTaxCalculation,
    TestHSNMatchesItem,
    TestReconcilerRun,
    TestRealTestcaseEndToEnd,
)

# ── Import test classes from test_claude_agent.py ─────────────────────────
from test_claude_agent import (
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
)

ALL_CLASSES = [
    # ── tally_parser ──────────────────────────────────────────────────────
    ("tally_parser / _amount()",           TestAmountParsing),
    ("tally_parser / _clean_gstin()",      TestCleanGSTIN),
    ("tally_parser / _text()",             TestTextExtraction),
    ("tally_parser / parse_sales",         TestParseSalesVouchers),
    # ── gstr2b_reader ─────────────────────────────────────────────────────
    ("gstr2b_reader / all",                TestGSTR2BReader),
    # ── reconciler ────────────────────────────────────────────────────────
    ("reconciler / TaxCalculation",        TestTaxCalculation),
    ("reconciler / _hsn_matches_item",     TestHSNMatchesItem),
    ("reconciler / Reconciler.run()",      TestReconcilerRun),
    ("reconciler / real testcase E2E",     TestRealTestcaseEndToEnd),
    # ── prompt_builder ────────────────────────────────────────────────────
    ("prompt_builder / _serialise",        TestSerialiseForPrompt),
    ("prompt_builder / whatsapp_prompt",   TestBuildWhatsappPrompt),
    ("prompt_builder / ca_prompt",         TestBuildCAPrompt),
    # ── claude_agent ──────────────────────────────────────────────────────
    ("claude_agent / APIClient.call()",    TestClaudeAPIClientCall),
    ("claude_agent / fallback templates",  TestFallbackTemplates),
    ("claude_agent / APIClient init",      TestClaudeAPIClientInit),
    ("claude_agent / GSTClaudeAgent",      TestGSTClaudeAgentDryRun),
    ("claude_agent / issues builder",      TestBuildIssuesDeterministic),
    ("claude_agent / helpers",             TestHelperFunctions),
    ("claude_agent / AgentOutput",         TestAgentOutput),
    ("claude_agent / save_agent_output",   TestSaveAgentOutput),
    ("claude_agent / full E2E dry_run",    TestFullDryRunIntegration),
    ("claude_agent / negative cases",      TestNegativeCases),
]


def run(verbose: bool = False) -> bool:
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    for _, cls in ALL_CLASSES:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    verbosity = 2 if verbose else 1
    runner    = unittest.TextTestRunner(verbosity=verbosity)
    result    = runner.run(suite)

    # Print module-level summary
    if not verbose:
        print("\n── Module breakdown ──────────────────────────────────────")
        for label, cls in ALL_CLASSES:
            n = loader.loadTestsFromTestCase(cls).countTestCases()
            print(f"  {label:<40} {n:>3} tests")
        print(f"  {'─'*48}")
        total = sum(
            loader.loadTestsFromTestCase(cls).countTestCases()
            for _, cls in ALL_CLASSES
        )
        print(f"  {'TOTAL':<40} {total:>3} tests")

    return result.wasSuccessful()


if __name__ == "__main__":
    verbose = "-v" in sys.argv
    success = run(verbose)
    sys.exit(0 if success else 1)
