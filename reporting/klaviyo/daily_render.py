"""PDF rendering for the daily Klaviyo report.

Workflow-private: input is the period dicts from daily_report.load_period.
Layout: page 1 is the email + SMS summary (one period, or prior vs current
with change) beside the notes; then one page of daily email tables per period.
"""
from html import escape

from weasyprint import HTML

from reporting.klaviyo.daily_report import (
    REPORT_DAY_LABEL, SINGLE_TABLES, SUMMARY_SECTIONS, STRONG_KEYS, fmt, pct_change, day_label,
)

CSS = """
@page { size: letter landscape; margin: 0.5in; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #1f2328; }
h1 { font-size: 18pt; margin: 0 0 2pt; }
h2 { font-size: 13pt; margin: 0 0 8pt; }
h3 { font-size: 10pt; margin: 7pt 0 2pt; color: #444; text-transform: uppercase; letter-spacing: .04em; }
.sub { color: #57606a; margin-bottom: 12pt; }
.page { page-break-before: always; }
table { border-collapse: collapse; }
th, td { padding: 3pt 8pt; border-bottom: 0.5pt solid #d0d7de; }
th { text-align: left; background: #f2f4f7; font-weight: 600; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.summary { width: 62%; float: left; }
.summary td:first-child { width: 45%; }
tr.sec td { background: #eef1f5; font-weight: 700; padding-top: 6pt; }
tr.strong td { font-weight: 700; }
.pos { color: #1a7f37; }
.neg { color: #cf222e; }
.notes { width: 33%; float: right; font-size: 9pt; color: #333; }
.notes li { margin-bottom: 5pt; }
table.daily { width: 100%; page-break-inside: avoid; }
.page { font-size: 8.5pt; }
.page th, .page td { padding: 1.5pt 8pt; }
.page h3 { margin: 5pt 0 2pt; }
"""


def _change_cell(current, prior):
    v = pct_change(current, prior)
    cls = "neg" if v.startswith("-") else ("pos" if v.startswith("+") else "")
    return f'<td class="num {cls}">{v}</td>'


def _summary_table(periods):
    """The current period is last; each earlier one gets its own change column."""
    priors = periods[:-1]
    head = "".join(f'<th class="num">{escape(p["label"])}</th>' for p in periods)
    head += "".join(f'<th class="num">vs {escape(p["label"])}</th>' for p in priors)
    span = len(periods) + len(priors) + 1
    rows = []
    for section, items in SUMMARY_SECTIONS:
        rows.append(f'<tr class="sec"><td colspan="{span}">{section}</td></tr>')
        for label, key in items:
            vals = [p["summary"][key] for p in periods]
            cls = ' class="strong"' if key in STRONG_KEYS else ""
            cells = "".join(f'<td class="num">{fmt(v, key)}</td>' for v in vals)
            cells += "".join(_change_cell(vals[-1], v) for v in vals[:-1])
            rows.append(f"<tr{cls}><td>{label}</td>{cells}</tr>")
    return f'<table class="summary"><tr><th>Metric</th>{head}</tr>{"".join(rows)}</table>'


def _daily_table(title, daily, total, cols):
    head = "".join(f'<th class="num">{label}</th>' for label, _ in cols)
    body = "".join(
        f"<tr><td>{day_label(d)}</td>" + "".join(f'<td class="num">{fmt(row[k], k)}</td>' for _, k in cols) + "</tr>"
        for d, row in daily.iterrows()
    )
    foot = ('<tr class="strong"><td>Total</td>'
            + "".join(f'<td class="num">{fmt(total[k], k)}</td>' for _, k in cols) + "</tr>")
    return f'<h3>{title.title()}</h3><table class="daily"><tr><th>Date</th>{head}</tr>{body}{foot}</table>'


def _daily_page(p):
    tables = "".join(_daily_table(t, p["daily"], p["total"], cols) for t, cols in SINGLE_TABLES)
    return (f'<section class="page"><h2>{escape(p["label"])}: daily email '
            f'({p["start"]} to {p["end"]})</h2>{tables}</section>')


def build_html(title, periods, notes):
    """Return the report as one HTML string (print layout)."""
    ranges = ". ".join(f'{escape(p["label"])}: {p["start"]} to {p["end"]}' for p in periods)
    notes_html = "".join(f"<li>{escape(n)}</li>" for n in notes)
    pages = "".join(_daily_page(p) for p in periods)
    return f"""<html><head><style>{CSS}</style></head><body>
<h1>{escape(title)}</h1>
<div class="sub">Klaviyo. {ranges}.</div>
{_summary_table(periods)}
<div class="notes"><b>Notes</b><ul>{notes_html}</ul></div>
{pages}
</body></html>"""


def render_pdf(title, periods, notes, output_path):
    """Write the report PDF to output_path."""
    HTML(string=build_html(title, periods, notes)).write_pdf(str(output_path))
