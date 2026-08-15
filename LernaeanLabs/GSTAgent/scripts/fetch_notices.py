"""
fetch_notices.py
-----------------
Pulls GST notices (via WhiteBooks /notices/noticelist + /notices/noticedetails)
for a fixed list of sandbox test GSTINs.

Usage:
  export GSP_CLIENT_ID=...
  export GSP_CLIENT_SECRET=...
  export GSP_EMAIL=...
  cd scripts
  python3 fetch_notices.py                    # today's date, all 4 test GSTINs
  python3 fetch_notices.py --date 02/05/2025   # DD/MM/YYYY
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gsp_client import GSPClient, GSPError

# (gsp_username, gstin) pairs supplied for this run — client_id/client_secret
# are shared across all four and come from GSP_CLIENT_ID / GSP_CLIENT_SECRET.
TEST_ACCOUNTS = [
    ("TN_NT2.152383", "33AAGCB1286Q1ZB"),
    ("MH_NT2.1641",   "27AAGCB1286Q1Z4"),
    ("TN_NT2.152384", "33AAGCB1286Q2ZA"),
    ("MH_NT2.1642",   "27AAGCB1286Q2Z3"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%d/%m/%Y"),
                         help="DD/MM/YYYY — notices are listed for the 60 days before this date")
    parser.add_argument("--out", default=str(Path(__file__).parent.parent / "notices_output.json"))
    parser.add_argument("--delay", type=float, default=8,
                         help="seconds to wait between accounts — the underlying NIC/GSTN "
                              "government API throttles requests sent too close together")
    args = parser.parse_args()

    missing = [v for v in ("GSP_CLIENT_ID", "GSP_CLIENT_SECRET", "GSP_EMAIL") if not os.environ.get(v)]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)

    results = {}

    for i, (gsp_username, gstin) in enumerate(TEST_ACCOUNTS):
        if i > 0 and args.delay > 0:
            time.sleep(args.delay)
        print(f"\n=== {gsp_username} / {gstin} ===")
        gsp = GSPClient(gstin=gstin, dry_run=False, sandbox=True,
                         gsp_gstin=gstin, gsp_username=gsp_username)
        entry = {"gstin": gstin, "gsp_username": gsp_username}
        try:
            gsp.authenticate()
            print(f"  authenticated, txn={gsp._session_txn[:20]}...")

            notice_list = gsp.list_notices(args.date)
            notices = notice_list.get("notices") or notice_list.get("data") or []
            entry["notice_list_raw"] = notice_list
            print(f"  list_notices({args.date}) -> {len(notices) if isinstance(notices, list) else 'n/a'} notice(s)")

            details = []
            if isinstance(notices, list):
                for n in notices:
                    ref_id = n.get("refId") or n.get("ref_id") or n.get("RefId")
                    if not ref_id:
                        continue
                    try:
                        d = gsp.get_notice_details(ref_id)
                        details.append(d)
                        print(f"    detail fetched for refId={ref_id}")
                    except GSPError as e:
                        print(f"    detail fetch failed for refId={ref_id}: {e}")
                        details.append({"refId": ref_id, "error": str(e)})
            entry["notice_details"] = details

        except GSPError as e:
            print(f"  ERROR: {e}")
            entry["error"] = str(e)
            entry["raw"] = e.raw
        finally:
            gsp.logout()
            print("  logged out")

        results[gstin] = entry

    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote results to {args.out}")


if __name__ == "__main__":
    main()
