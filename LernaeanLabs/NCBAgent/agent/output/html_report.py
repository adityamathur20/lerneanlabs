"""
Consolidated HTML report — all substances, all anomalies, stock summary for a given month.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from agent.config_loader import ClientProfile
from agent.models import DailyRegisterEntry, SubstanceRegister

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def generate_html_report(
    registers: dict[str, SubstanceRegister],
    profile: ClientProfile,
    year: int,
    month: int,
    output_path: Path,
    generated_at: date | None = None,
) -> None:
    generated_at = generated_at or date.today()
    month_name = MONTH_NAMES[month]
    month_entries: dict[str, list[DailyRegisterEntry]] = {
        sub: reg.entries_for_month(year, month) for sub, reg in registers.items()
    }

    html = _build_html(profile, registers, month_entries, year, month, month_name, generated_at)
    output_path.write_text(html, encoding="utf-8")
    print(f"[HTML]  Written: {output_path}")


def _build_html(
    profile: ClientProfile,
    registers: dict[str, SubstanceRegister],
    month_entries: dict[str, list[DailyRegisterEntry]],
    year: int,
    month: int,
    month_name: str,
    generated_at: date,
) -> str:

    flagged_entries = [
        (sub, e) for sub, entries in month_entries.items()
        for e in entries if e.requires_human_review
    ]
    nil_count = sum(
        1 for entries in month_entries.values() for e in entries if e.nil_transaction
    )
    total_working_days = max((len(e) for e in month_entries.values()), default=0)

    sections = [_css(), _header(profile, month_name, year, generated_at),
                _summary_table(registers, month_entries, year, month, total_working_days, nil_count)]

    if flagged_entries:
        sections.append(_anomalies_section(flagged_entries))

    for substance, entries in month_entries.items():
        if entries:
            sections.append(_register_table(substance, entries, profile))

    sections.append(_footer())

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NCB Compliance Report — {profile.client_name} — {month_name} {year}</title>
</head>
<body>
{"".join(sections)}
</body>
</html>"""


def _css() -> str:
    return """<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Arial', sans-serif; font-size: 11px; color: #1a1a1a; background: #f5f7fa; }
  .page { max-width: 1200px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 18px; color: #1a3a5c; }
  h2 { font-size: 13px; color: #1a3a5c; margin: 16px 0 8px 0; border-bottom: 2px solid #1a3a5c; padding-bottom: 4px; }
  h3 { font-size: 12px; color: #2c5282; margin: 12px 0 6px 0; }
  .header-block { background: #1a3a5c; color: white; padding: 16px 20px; margin-bottom: 16px; }
  .header-block h1 { color: white; margin-bottom: 6px; }
  .header-meta { display: flex; gap: 40px; font-size: 11px; opacity: 0.9; }
  .meta-item span { font-weight: bold; }
  .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 16px; }
  .stat-card { background: white; border: 1px solid #dde; border-radius: 6px; padding: 12px; }
  .stat-label { font-size: 10px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-value { font-size: 20px; font-weight: bold; color: #1a3a5c; margin-top: 4px; }
  .stat-sub { font-size: 10px; color: #888; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 10px; background: white; margin-bottom: 16px; }
  th { background: #1a3a5c; color: white; padding: 6px 8px; text-align: left; font-weight: 600; }
  th.center, td.center { text-align: center; }
  th.right, td.right { text-align: right; }
  td { padding: 5px 8px; border-bottom: 1px solid #eee; vertical-align: top; }
  tr:nth-child(even) td { background: #f8fafc; }
  tr.nil-row td { color: #999; font-style: italic; }
  tr.flagged-row td { background: #fff3cd !important; }
  tr.negative-row td { background: #fde8e8 !important; }
  .badge { display: inline-block; padding: 2px 6px; border-radius: 10px; font-size: 9px; font-weight: bold; }
  .badge-ok { background: #d4edda; color: #155724; }
  .badge-nil { background: #e2e3e5; color: #383d41; }
  .badge-flag { background: #fff3cd; color: #856404; }
  .badge-error { background: #fde8e8; color: #721c24; }
  .anomaly-list { background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 12px; margin-bottom: 16px; }
  .anomaly-item { margin-bottom: 8px; padding: 6px; background: white; border-radius: 4px; border-left: 3px solid #ffc107; }
  .anomaly-date { font-weight: bold; color: #1a3a5c; }
  .anomaly-reason { color: #856404; margin-top: 2px; }
  .sub-section { background: white; border: 1px solid #dde; border-radius: 6px; padding: 12px; margin-bottom: 16px; }
  .footer { text-align: center; color: #888; font-size: 10px; padding: 16px; border-top: 1px solid #dde; margin-top: 20px; }
  .txn-sub { font-size: 9px; color: #555; }
  .highlight-open { font-weight: bold; color: #2c5282; }
  .highlight-close { font-weight: bold; color: #1a6b1a; }
  .highlight-loss { color: #c0392b; }
</style>"""


def _header(profile: ClientProfile, month_name: str, year: int, generated_at: date) -> str:
    return f"""<div class="page">
<div class="header-block">
  <h1>NCB Compliance Report — {month_name} {year}</h1>
  <div class="header-meta">
    <div class="meta-item">Client: <span>{profile.client_name}</span></div>
    <div class="meta-item">URN: <span>{profile.urn}</span></div>
    <div class="meta-item">Zonal Unit: <span>{profile.zonal_unit}</span></div>
    <div class="meta-item">Generated: <span>{generated_at.strftime('%d %b %Y')}</span></div>
  </div>
</div>"""


def _summary_table(
    registers: dict[str, SubstanceRegister],
    month_entries: dict[str, list[DailyRegisterEntry]],
    year: int,
    month: int,
    total_working_days: int,
    nil_count: int,
) -> str:

    rows = ""
    total_flagged = 0
    for substance, entries in month_entries.items():
        if not entries:
            continue
        reg = registers[substance]
        opening = entries[0].opening_kg if entries else Decimal("0")
        closing = entries[-1].closing_kg if entries else Decimal("0")
        received = sum((e.total_received_kg for e in entries), Decimal("0"))
        dispatched = sum((e.total_dispatched_kg for e in entries), Decimal("0"))
        flagged = sum(1 for e in entries if e.requires_human_review)
        total_flagged += flagged
        nil = sum(1 for e in entries if e.nil_transaction)
        flag_badge = f'<span class="badge badge-flag">{flagged} flagged</span>' if flagged else '<span class="badge badge-ok">Clean</span>'
        rows += f"""<tr>
          <td><strong>{substance}</strong></td>
          <td class="right">{opening:.3f}</td>
          <td class="right">{received:.3f}</td>
          <td class="right">{dispatched:.3f}</td>
          <td class="right highlight-close">{closing:.3f}</td>
          <td class="center">{nil}</td>
          <td class="center">{flag_badge}</td>
        </tr>"""

    return f"""<h2>Stock Summary — {MONTH_NAMES[month]} {year}</h2>
<div class="sub-section">
<table>
  <thead><tr>
    <th>Substance</th>
    <th class="right">Opening (kg)</th>
    <th class="right">Received (kg)</th>
    <th class="right">Dispatched (kg)</th>
    <th class="right">Closing (kg)</th>
    <th class="center">Nil Days</th>
    <th class="center">Status</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>"""


def _anomalies_section(flagged: list[tuple[str, DailyRegisterEntry]]) -> str:
    items = ""
    for substance, entry in flagged:
        for reason in entry.review_reasons:
            items += f"""<div class="anomaly-item">
  <div class="anomaly-date">{entry.date.strftime('%d %b %Y')} — {substance}</div>
  <div class="anomaly-reason">&#9888; {reason}</div>
</div>"""
    return f"""<h2>&#9888; Anomalies Requiring Human Review ({len(flagged)} entries)</h2>
<div class="anomaly-list">{items}</div>"""


def _register_table(
    substance: str,
    entries: list[DailyRegisterEntry],
    profile: ClientProfile,
) -> str:

    rows = ""
    for entry in entries:
        row_class = ""
        if entry.closing_kg < Decimal("0"):
            row_class = "negative-row"
        elif entry.requires_human_review:
            row_class = "flagged-row"
        elif entry.nil_transaction:
            row_class = "nil-row"

        status_badge = (
            '<span class="badge badge-error">&#10060; Review</span>' if entry.requires_human_review
            else '<span class="badge badge-nil">Nil</span>' if entry.nil_transaction
            else '<span class="badge badge-ok">&#10003;</span>'
        )

        # Receipts sub-rows
        if entry.receipts:
            receipts_cell = "".join(
                f'<div class="txn-sub">{t.voucher_no} | {t.counterparty} | {t.quantity_kg:.3f} kg | {t.form_g_no}</div>'
                for t in entry.receipts
            )
        else:
            receipts_cell = '<span style="color:#bbb">—</span>'

        # Dispatches sub-rows
        if entry.dispatches:
            dispatches_cell = "".join(
                f'<div class="txn-sub">{t.voucher_no} | {t.counterparty} | {t.quantity_kg:.3f} kg | {t.form_g_no}</div>'
                for t in entry.dispatches
            )
        else:
            dispatches_cell = '<span style="color:#bbb">—</span>'

        rows += f"""<tr class="{row_class}">
  <td class="center">{entry.serial_no}</td>
  <td>{entry.date.strftime('%d %b')}</td>
  <td class="right highlight-open">{entry.opening_kg:.3f}</td>
  <td>{receipts_cell}</td>
  <td class="right">{entry.total_received_kg:.3f}</td>
  <td>{dispatches_cell}</td>
  <td class="right">{entry.total_dispatched_kg:.3f}</td>
  <td class="right highlight-loss">{entry.handling_loss_kg:.3f}</td>
  <td class="right highlight-close">{entry.closing_kg:.3f}</td>
  <td class="center">{status_badge}</td>
</tr>"""

    return f"""<h2>Form D — Daily Register: {substance}</h2>
<div class="sub-section">
<p style="font-size:10px;color:#666;margin-bottom:8px;">
  Registration No: {profile.urn} &nbsp;|&nbsp;
  Compliance Officer: {profile.compliance_officer_name} &nbsp;|&nbsp;
  Address: {profile.address}
</p>
<table>
  <thead>
    <tr>
      <th class="center">Sl No</th>
      <th>Date</th>
      <th class="right">Opening (kg)</th>
      <th>Receipts (Voucher | Party | Qty | Form-G)</th>
      <th class="right">Total Recv (kg)</th>
      <th>Dispatches (Voucher | Party | Qty | Form-G)</th>
      <th class="right">Total Disp (kg)</th>
      <th class="right">Loss (kg)</th>
      <th class="right">Closing (kg)</th>
      <th class="center">Status</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
</div>"""


def _footer() -> str:
    return """<div class="footer">
  Generated by NCBAgent &nbsp;|&nbsp; LernaeanLabs &nbsp;|&nbsp;
  This report is a preparation tool only. The compliance officer is legally responsible
  for all submissions to NCB Pre-Register portal. NCBAgent does not provide legal compliance certification.
</div>
</div>"""
