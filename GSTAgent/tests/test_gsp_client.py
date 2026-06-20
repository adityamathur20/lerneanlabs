"""
test_gsp_client.py
------------------
Tests WhiteBooks GSP API connectivity.

Usage:
  # Dry-run only (no credentials needed):
  python3 tests/test_gsp_client.py

  # Full sandbox test (requires GSP_CLIENT_ID, GSP_CLIENT_SECRET, GSP_EMAIL):
  python3 tests/test_gsp_client.py --live
"""

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from gsp_client import GSPClient, SANDBOX_CREDENTIALS

TEST_GSTIN  = "24AABMT1234C1Z5"   # Gujarat test client
TEST_PERIOD = "032026"             # March 2026

PASS = "  PASS"
FAIL = "  FAIL"


def test_dry_run():
    print("\n--- DRY-RUN TESTS (no credentials needed) ---")

    gsp = GSPClient(gstin=TEST_GSTIN, dry_run=True)

    # Auth
    txn = gsp.authenticate()
    assert txn == "dry-run-txn", f"unexpected txn: {txn}"
    print(f"{PASS}  authenticate() → {txn}")

    # GSTR-2B
    data = gsp.fetch_gstr2b(TEST_PERIOD)
    assert "data" in data, f"missing 'data' key: {data}"
    invoices = data["data"]["docdata"]["b2b"]
    print(f"{PASS}  fetch_gstr2b({TEST_PERIOD}) → {len(invoices)} B2B supplier(s)")

    # GSTIN status check
    status = gsp.check_gstin_status(TEST_GSTIN)
    print(f"{PASS}  check_gstin_status({TEST_GSTIN}) → {status.status}")

    print("\nAll dry-run tests passed.")


def test_live():
    import os
    missing = [v for v in ("GSP_CLIENT_ID", "GSP_CLIENT_SECRET", "GSP_EMAIL") if not os.environ.get(v)]
    if missing:
        print(f"\nMissing env vars: {', '.join(missing)}")
        print("Set them in ~/.zshrc and run: source ~/.zshrc")
        sys.exit(1)

    print("\n--- LIVE SANDBOX TESTS ---")
    print(f"  client_id : {os.environ['GSP_CLIENT_ID'][:12]}...")
    print(f"  email     : {os.environ['GSP_EMAIL']}")

    gsp = GSPClient(gstin=TEST_GSTIN, dry_run=False, sandbox=True)
    print(f"  State     : {gsp.state_cd} → sandbox GSTIN: {gsp.gsp_gstin}, username: {gsp.gsp_username}")

    # Step 1: Authenticate
    print("\n[1] Authenticating (OTP request + token exchange)...")
    try:
        txn = gsp.authenticate()
        print(f"{PASS}  session txn: {txn[:40]}...")
    except Exception as e:
        print(f"{FAIL}  {e}")
        print("\n  AUTH4037 → contact WhiteBooks support to activate sandbox for your client_id.")
        print("  AUTH403  → max sessions exceeded; wait 6 hours or try another credential set.")
        sys.exit(1)

    # Step 2: Generate GSTR-2B on demand (required if not auto-generated)
    print(f"\n[2] Generating GSTR-2B on demand for period {TEST_PERIOD}...")
    try:
        gsp.generate_gstr2b(TEST_PERIOD)
        print(f"{PASS}  GSTR-2B generation triggered")
    except Exception as e:
        print(f"       (generation skipped or already exists: {e})")

    # Step 3: Fetch GSTR-2B
    print(f"\n[3] Fetching GSTR-2B for period {TEST_PERIOD}...")
    try:
        data = gsp.fetch_gstr2b(TEST_PERIOD)
        b2b = data.get("data", {}).get("docdata", {}).get("b2b", [])
        print(f"{PASS}  GSTR-2B fetched — {len(b2b)} B2B supplier(s) in response")
        if b2b:
            print(f"       First supplier: {b2b[0].get('suppName', '?')} ({b2b[0].get('ctin', '?')})")
    except Exception as e:
        print(f"{FAIL}  {e}")
        sys.exit(1)

    # Step 4: GSTIN public search (real GSTIN from your clients)
    print(f"\n[4] Public GSTIN search for {TEST_GSTIN}...")
    try:
        status = gsp.check_gstin_status(TEST_GSTIN)
        print(f"{PASS}  {status.gstin} → {status.status} | {status.legal_name}")
    except Exception as e:
        print(f"{FAIL}  {e}")

    # Always logout to free the session slot for next run
    gsp.logout()
    print("  Session logged out.")

    print("\nAll live sandbox tests completed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Run live sandbox API tests")
    args = parser.parse_args()

    test_dry_run()
    if args.live:
        test_live()
    else:
        print("\nTip: run with --live to test the actual WhiteBooks sandbox API.")
