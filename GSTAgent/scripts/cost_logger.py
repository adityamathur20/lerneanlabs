"""
cost_logger.py
--------------
Tracks every Claude API call made by GSTAgent — tokens, USD cost, INR cost.
Persists to a JSONL file (one JSON object per line) for easy grep/analysis.

Model pricing (as of April 2026):
  claude-haiku-4-5-20251001  : $0.80/MTok input  | $4.00/MTok output
  claude-sonnet-4-6          : $3.00/MTok input  | $15.00/MTok output

Usage:
  logger = CostLogger()                         # logs to cost_log.jsonl in CWD
  logger = CostLogger("logs/cost_log.jsonl")    # custom path

  # Log a single API call
  logger.log_call(
      client_gstin="24AABMT1234C1Z5",
      period="102024",
      call_type="ca_report",
      model="claude-sonnet-4-6",
      input_tokens=1800,
      output_tokens=950,
  )

  # Summaries
  logger.run_summary("24AABMT1234C1Z5", "102024")   # cost for one reconciliation
  logger.monthly_summary(2024, 10)                   # total for October 2024
  logger.print_summary()                             # all-time totals
"""

import json, os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Model pricing table  (USD per 1M tokens)
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5":          {"input": 0.80, "output": 4.00},   # alias
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5":         {"input": 3.00, "output": 15.00},  # alias
    "template":                  {"input": 0.00, "output": 0.00},   # dry-run / fallback
    "template (fallback)":       {"input": 0.00, "output": 0.00},
}

DEFAULT_USD_TO_INR = 84.0   # update monthly; also overridable per-logger


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model, {"input": 3.00, "output": 15.00})  # default to Sonnet price (safe)
    return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Log entry dataclass
# ---------------------------------------------------------------------------

@dataclass
class CostEntry:
    timestamp: str           # ISO-8601
    client_gstin: str
    period: str              # MMYYYY  e.g. "102024"
    call_type: str           # "whatsapp" | "ca_report" | "issues_json" | "gsp_fetch" | etc.
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_inr: float
    usd_to_inr_rate: float
    fallback: bool           # True if template was used (cost = 0)
    run_id: str              # "{client_gstin}_{period}"


# ---------------------------------------------------------------------------
# CostLogger
# ---------------------------------------------------------------------------

class CostLogger:
    """
    Append-only JSONL cost logger.  Thread-safe for single-process use.
    Each line in the JSONL file is one CostEntry serialised to JSON.
    """

    def __init__(self, log_path: Optional[str] = None, usd_to_inr: float = DEFAULT_USD_TO_INR):
        if log_path is None:
            log_path = os.path.join(os.getcwd(), "cost_log.jsonl")
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.usd_to_inr = usd_to_inr

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_call(
        self,
        *,
        client_gstin: str,
        period: str,
        call_type: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        fallback: bool = False,
    ) -> CostEntry:
        """Record one API call and append to the JSONL log."""
        cost_usd = _cost_usd(model, input_tokens, output_tokens)
        entry = CostEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            client_gstin=client_gstin,
            period=period,
            call_type=call_type,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost_usd, 8),
            cost_inr=round(cost_usd * self.usd_to_inr, 4),
            usd_to_inr_rate=self.usd_to_inr,
            fallback=fallback,
            run_id=f"{client_gstin}_{period}",
        )
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def _load_entries(self) -> list[CostEntry]:
        if not self.log_path.exists():
            return []
        entries = []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(CostEntry(**json.loads(line)))
                    except (json.JSONDecodeError, TypeError):
                        pass   # skip malformed lines
        return entries

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def run_summary(self, client_gstin: str, period: str) -> dict:
        """Cost breakdown for a single reconciliation run."""
        target = f"{client_gstin}_{period}"
        entries = [e for e in self._load_entries() if e.run_id == target]
        return self._aggregate(entries, label=f"Run {target}")

    def monthly_summary(self, year: int, month: int) -> dict:
        """Total cost for all runs in a given calendar month."""
        prefix = f"{year}-{month:02d}"
        entries = [e for e in self._load_entries() if e.timestamp.startswith(prefix)]
        return self._aggregate(entries, label=f"{year}-{month:02d}")

    def client_summary(self, client_gstin: str) -> dict:
        """All-time cost for a single client."""
        entries = [e for e in self._load_entries() if e.client_gstin == client_gstin]
        return self._aggregate(entries, label=client_gstin)

    def all_time_summary(self) -> dict:
        """Grand total across all clients and periods."""
        return self._aggregate(self._load_entries(), label="all-time")

    # ------------------------------------------------------------------
    # Print helpers
    # ------------------------------------------------------------------

    def print_summary(self):
        """Print a human-readable all-time summary to stdout."""
        entries = self._load_entries()
        if not entries:
            print("  [CostLogger] No entries recorded yet.")
            return

        total_usd  = sum(e.cost_usd  for e in entries)
        total_inr  = sum(e.cost_inr  for e in entries)
        total_in   = sum(e.input_tokens  for e in entries)
        total_out  = sum(e.output_tokens for e in entries)
        live_calls = sum(1 for e in entries if not e.fallback)
        fb_calls   = sum(1 for e in entries if e.fallback)

        # Per-model breakdown
        models: dict[str, dict] = {}
        for e in entries:
            m = models.setdefault(e.model, {"calls": 0, "input": 0, "output": 0, "usd": 0.0})
            m["calls"] += 1
            m["input"] += e.input_tokens
            m["output"] += e.output_tokens
            m["usd"] += e.cost_usd

        print(f"\n{'─'*55}")
        print(f"  COST LOG SUMMARY  ({self.log_path.name})")
        print(f"{'─'*55}")
        print(f"  Total API calls  : {len(entries)}  (live: {live_calls}, fallback: {fb_calls})")
        print(f"  Total tokens     : {total_in:,} in  +  {total_out:,} out")
        print(f"  Total cost (USD) : ${total_usd:.6f}")
        print(f"  Total cost (INR) : ₹{total_inr:.4f}")
        print(f"\n  Per-model breakdown:")
        for model, m in sorted(models.items()):
            print(f"    {model:<40}  {m['calls']} calls  ${m['usd']:.6f}")
        print(f"{'─'*55}\n")

    def print_run_summary(self, client_gstin: str, period: str):
        """Print cost for a specific run."""
        s = self.run_summary(client_gstin, period)
        print(f"\n  Cost for {client_gstin} / {period}:")
        print(f"    Calls: {s['call_count']}  |  Tokens: {s['input_tokens']:,} in + {s['output_tokens']:,} out")
        print(f"    Cost:  ${s['cost_usd']:.6f}  /  ₹{s['cost_inr']:.4f}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _aggregate(entries: list[CostEntry], label: str) -> dict:
        return {
            "label": label,
            "call_count": len(entries),
            "live_calls": sum(1 for e in entries if not e.fallback),
            "fallback_calls": sum(1 for e in entries if e.fallback),
            "input_tokens": sum(e.input_tokens for e in entries),
            "output_tokens": sum(e.output_tokens for e in entries),
            "cost_usd": round(sum(e.cost_usd for e in entries), 8),
            "cost_inr": round(sum(e.cost_inr for e in entries), 4),
            "by_call_type": _group_by(entries, key=lambda e: e.call_type),
            "by_model":     _group_by(entries, key=lambda e: e.model),
        }


def _group_by(entries: list[CostEntry], *, key) -> dict:
    groups: dict[str, dict] = {}
    for e in entries:
        k = key(e)
        g = groups.setdefault(k, {"calls": 0, "cost_usd": 0.0, "cost_inr": 0.0})
        g["calls"] += 1
        g["cost_usd"] = round(g["cost_usd"] + e.cost_usd, 8)
        g["cost_inr"] = round(g["cost_inr"] + e.cost_inr, 4)
    return groups
