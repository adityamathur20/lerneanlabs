"""
pipeline_server.py
------------------
Thin Flask HTTP wrapper around run_pipeline.py.
Replaces the n8n executeCommand node (not available on n8n Cloud).

n8n calls:  POST {PIPELINE_SERVER_URL}/run
            Header: X-Pipeline-Secret: {PIPELINE_SECRET}
            Body:   {"gstin": "...", "period": "102024", "dry_run": true}

Returns:    Same JSON as run_pipeline.py stdout (success/error shape)

Run locally:
  pip3 install flask
  export PIPELINE_SECRET=your-secret-token
  python3 scripts/pipeline_server.py

Expose to n8n Cloud (development):
  ngrok http 5001
  → copy the https://xxxx.ngrok-free.app URL into n8n as PIPELINE_SERVER_URL

Deploy to production (Railway / Render):
  Push repo to GitHub → connect to Railway → set env vars → deploy.
  Set PIPELINE_SERVER_URL to the deployed URL in n8n.

Endpoints:
  GET  /health  — liveness check, no auth required
  POST /run     — run the GST pipeline for one client+period
"""

import os
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, timezone

# --- Flask import with helpful error ---
try:
    from flask import Flask, request, jsonify
except ImportError:
    print("Flask not installed. Run: pip3 install flask", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))

from run_pipeline import run_pipeline, _parse_args as _pipeline_parse_args


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
SECRET = os.environ.get("PIPELINE_SECRET", "")

# File logger — writes to pipeline_server.log next to this script
LOG_PATH = Path(__file__).parent.parent / "pipeline_server.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("gstagent")


def _check_auth() -> bool:
    if not SECRET:
        return True   # no secret configured — open (dev only)
    return request.headers.get("X-Pipeline-Secret") == SECRET


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    log.info("HEALTH CHECK — server is up")
    return jsonify({"status": "ok", "service": "gstagent-pipeline"})


@app.route("/run", methods=["POST"])
def run():
    if not _check_auth():
        log.warning("UNAUTHORIZED request — missing or wrong X-Pipeline-Secret")
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    gstin    = body.get("gstin", "")
    period   = body.get("period", "")
    dry_run  = bool(body.get("dry_run", False))
    cost_log = body.get("cost_log", "cost_log.jsonl")

    log.info(f"RUN START  gstin={gstin}  period={period}  dry_run={dry_run}")

    if not gstin or not period:
        log.error("RUN FAILED — gstin and period are required")
        return jsonify({"success": False, "error": "gstin and period are required"}), 400

    import types
    args = types.SimpleNamespace(
        gstin           = gstin,
        period          = period,
        dry_run         = dry_run,
        tally_sales     = body.get("tally_sales"),
        tally_purchases = body.get("tally_purchases"),
        cost_log        = cost_log,
    )

    try:
        result = run_pipeline(args)
        status = result.get("reconciliation_status", "?")
        issues = result.get("issue_count", "?")
        log.info(f"RUN OK     gstin={gstin}  status={status}  issues={issues}  cost_usd={result.get('cost_usd', 0)}")
        return jsonify(result), 200
    except Exception as e:
        log.error(f"RUN ERROR  gstin={gstin}  error={e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GSTAgent pipeline HTTP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    import os as _os
    _sb_url = _os.environ.get("SUPABASE_URL", "")
    _sb_key = _os.environ.get("SUPABASE_KEY") or _os.environ.get("SUPABASE_SERVICE_KEY", "")
    print(f"  GSTAgent Pipeline Server")
    print(f"  Listening on http://{args.host}:{args.port}")
    print(f"  Auth: {'enabled (X-Pipeline-Secret header)' if SECRET else 'DISABLED — set PIPELINE_SECRET for production'}")
    print(f"  Supabase URL : {'✅ set' if _sb_url else '❌ NOT SET — client names will fall back to Mehta testcase'}")
    print(f"  Supabase Key : {'✅ set' if _sb_key else '❌ NOT SET — client names will fall back to Mehta testcase'}")
    print(f"  To expose to n8n Cloud: ngrok http {args.port}")

    app.run(host=args.host, port=args.port, debug=args.debug)
