"""Standalone HTML dashboard as a quick status overview, without having to
open Excel. Colors follow the fixed status palette (good/warning/critical) -
status is never conveyed by color alone, always combined with an icon +
text label.
"""

from datetime import datetime

_STATUS_ROLE = {
    "OK": "good", "READY TO BUILD": "good",
    "PARTIAL": "warning", "PARTIAL BUILD": "warning",
    "MISSING": "critical", "BLOCKED": "critical",
}
_STATUS_ICON = {"good": "✓", "warning": "⚠", "critical": "✕"}
_STATUS_LABEL = {
    "OK": "OK", "READY TO BUILD": "Ready",
    "PARTIAL": "Partial", "PARTIAL BUILD": "Partial",
    "MISSING": "Missing", "BLOCKED": "Blocked",
}

_CSS = """
:root {
  --surface-1:  #fcfcfb; --page: #f9f9f7; --text-primary: #0b0b0b;
  --text-secondary: #52514e; --muted: #898781; --gridline: #e1e0d9;
  --border: rgba(11,11,11,0.10);
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface-1: #1a1a19; --page: #0d0d0d; --text-primary: #ffffff;
    --text-secondary: #c3c2b7; --muted: #898781; --gridline: #2c2c2a;
    --border: rgba(255,255,255,0.10);
    --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px; background: var(--page); color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
h1 { font-size: 22px; margin: 0 0 4px; }
.meta { color: var(--text-secondary); font-size: 13px; margin-bottom: 24px; }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.tile {
  background: var(--surface-1); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px 20px; min-width: 150px; flex: 1;
}
.tile .label { font-size: 12px; color: var(--text-secondary); font-weight: 600; }
.tile .value {
  font-size: 30px; font-weight: 700; font-variant-numeric: proportional-nums;
  margin-top: 4px;
}
.tile.good .value { color: var(--good); }
.tile.warning .value { color: var(--warning); }
.tile.critical .value { color: var(--critical); }
.meter {
  display: flex; height: 10px; border-radius: 5px; overflow: hidden;
  background: var(--gridline); margin: 8px 0 28px;
}
.meter span { height: 100%; }
.meter .good { background: var(--good); }
.meter .warning { background: var(--warning); }
.meter .critical { background: var(--critical); }
h2 { font-size: 16px; margin: 28px 0 10px; }
table { width: 100%; border-collapse: collapse; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 8px 12px; font-size: 13px; border-bottom: 1px solid var(--gridline); }
th { color: var(--text-secondary); font-weight: 600; background: var(--page); }
tr:last-child td { border-bottom: none; }
.badge {
  display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600;
  padding: 2px 8px; border-radius: 999px;
}
.badge.good { color: var(--good); background: color-mix(in srgb, var(--good) 15%, transparent); }
.badge.warning { color: var(--warning); background: color-mix(in srgb, var(--warning) 20%, transparent); }
.badge.critical { color: var(--critical); background: color-mix(in srgb, var(--critical) 15%, transparent); }
.footnote { color: var(--muted); font-size: 12px; margin-top: 24px; }
"""


def _badge(status):
    role = _STATUS_ROLE.get(status, "warning")
    label = _STATUS_LABEL.get(status, status)
    icon = _STATUS_ICON[role]
    return f'<span class="badge {role}">{icon} {label}</span>'


def _tile(label, value, role=""):
    cls = f"tile {role}".strip()
    return f'<div class="{cls}"><div class="label">{label}</div><div class="value">{value}</div></div>'


def build_html(stats, num_units, gap_df, max_gap_rows=25):
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    total_parts = max(stats["parts_total"], 1)
    ok_pct = 100 * stats["parts_ok"] / total_parts
    partial_pct = 100 * stats["parts_partial"] / total_parts
    missing_pct = 100 * stats["parts_missing"] / total_parts

    tiles_html = "".join([
        _tile("Total Parts", stats["parts_total"]),
        _tile("OK", stats["parts_ok"], "good"),
        _tile("Partially Available", stats["parts_partial"], "warning"),
        _tile("Missing", stats["parts_missing"], "critical"),
    ])
    tiles_html2 = "".join([
        _tile("Assemblies Ready", stats["asm_ready"], "good"),
        _tile("Assemblies Partial", stats["asm_partial"], "warning"),
        _tile("Assemblies Blocked", stats["asm_blocked"], "critical"),
        _tile("Critical Parts Missing", stats["critical_missing"], "critical"),
    ])

    meter_html = (
        f'<div class="meter">'
        f'<span class="good" style="width:{ok_pct:.1f}%"></span>'
        f'<span class="warning" style="width:{partial_pct:.1f}%"></span>'
        f'<span class="critical" style="width:{missing_pct:.1f}%"></span>'
        f'</div>'
    )

    rows_html = ""
    if gap_df is not None and len(gap_df) > 0:
        shown = gap_df.head(max_gap_rows)
        for _, r in shown.iterrows():
            rows_html += (
                "<tr>"
                f"<td>{r['Material_Number']}</td>"
                f"<td>{r.get('Title', '')}</td>"
                f"<td>{r.get('Gap', '')}</td>"
                f"<td>{_badge(r.get('Status', ''))}</td>"
                f"<td>{r.get('Order_Supplier', '')}</td>"
                f"<td>{r.get('Order_Delivery_Date', '')}</td>"
                "</tr>"
            )
        more = len(gap_df) - len(shown)
        note = f"<p class='footnote'>+{more} more rows in the Excel file (sheet \"2. ACTION REQUIRED\").</p>" if more > 0 else ""
    else:
        rows_html = "<tr><td colspan='6'>No open shortages.</td></tr>"
        note = ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Inventory Management Dashboard</title>
<style>{_CSS}</style>
</head>
<body>
  <h1>Inventory Management Dashboard</h1>
  <div class="meta">Units: {num_units} &nbsp;|&nbsp; Generated: {generated_at} &nbsp;|&nbsp;
    Storage sources: {stats['sources_used']}</div>

  <div class="tiles">{tiles_html}</div>
  {meter_html}
  <div class="tiles">{tiles_html2}</div>

  <h2>Action Required (Top {min(max_gap_rows, len(gap_df) if gap_df is not None else 0)})</h2>
  <table>
    <thead><tr><th>Material Number</th><th>Description</th><th>Shortage</th>
      <th>Status</th><th>Supplier</th><th>Delivery Date</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  {note}
  <p class="footnote">Full details, reference data, and all shortages are in the accompanying Excel file.</p>
</body>
</html>"""
    return html


def write_html(output_path, stats, num_units, gap_df, max_gap_rows=25):
    html = build_html(stats, num_units, gap_df, max_gap_rows=max_gap_rows)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
