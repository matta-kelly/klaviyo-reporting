"""Daily email report: campaigns + flows per day, with SMS period totals.

Modes:
    <start> <end>                                      one period
    yoy <start> <end>                                  vs the same weekdays 364 days earlier
    compare <start> <end> <prior_start> <prior_end>    vs any other period, e.g. a sale on different dates
    full <start> <end>                                 vs both the week before and a year before

One period is the default because a reader handed three timeframes at once has
to work out which two any figure is about. `full` is there when the question is
whether a move is seasonal or new, and it is a deliberate ask rather than the
house style.

Any number of periods renders: the current period is last, and every period
before it gets its own change column against it.

Console output always. `--pdf` also writes the PDF: a summary page, then a
daily email page per period. Campaign and flow rates are reported separately
and never blended.
"""
import argparse
import re
from datetime import date, timedelta
from pathlib import Path

from connectors.klaviyo import REPORT_DAY_LABEL
from tools.klaviyo.daily import (
    timeframe, days_between, with_send_date, load_flows_daily, daily_rollup, rollup_row, sms_row,
)
from tools.klaviyo.transforms import load_campaigns, load_flows
from reporting.klaviyo.general_report import (
    load_email_campaigns, load_email_flows, print_report, print_yoy_report,
)

YOY_OFFSET = timedelta(days=364)
WOW_OFFSET = timedelta(days=7)
ACCRUING_DAYS = 7
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Daily tables: (title, [(column label, row key), ...]).
SINGLE_TABLES = [
    ("CAMPAIGNS", [
        ("Sends", "campaign_sends"), ("Concepts", "campaign_concepts"),
        ("Recipients", "campaign_recipients"), ("Uniq opens", "campaign_opens"),
        ("Open rate", "campaign_open_rate"), ("Click rate", "campaign_click_rate"),
        ("Revenue", "campaign_revenue"), ("Avg/send", "avg_rev_per_campaign"),
    ]),
    ("FLOWS", [
        ("Recipients", "flow_recipients"), ("Uniq opens", "flow_opens"),
        ("Open rate", "flow_open_rate"), ("Click rate", "flow_click_rate"),
        ("Revenue", "flow_revenue"),
    ]),
    ("TOTAL (CAMPAIGNS + FLOWS)", [
        ("Recipients", "total_recipients"), ("Uniq opens", "total_opens"),
        ("Revenue", "total_revenue"), ("Rev/recip", "rev_per_recipient"),
    ]),
]

# YoY day-pair tables: at most three column groups each, to stay readable in a terminal.
YOY_TABLES = [
    ("RECIPIENTS", [("Campaign", "campaign_recipients"), ("Flow", "flow_recipients"), ("Total", "total_recipients")]),
    ("UNIQUE OPENS", [("Campaign", "campaign_opens"), ("Flow", "flow_opens"), ("Total", "total_opens")]),
    ("OPEN RATE", [("Campaign", "campaign_open_rate"), ("Flow", "flow_open_rate")]),
    ("CLICK RATE", [("Campaign", "campaign_click_rate"), ("Flow", "flow_click_rate")]),
    ("REVENUE", [("Campaign", "campaign_revenue"), ("Flow", "flow_revenue"), ("Total", "total_revenue")]),
    ("CAMPAIGN SENDS", [("Sends", "campaign_sends"), ("Concepts", "campaign_concepts"), ("Avg rev/send", "avg_rev_per_campaign")]),
    ("REV / RECIPIENT", [("Total", "rev_per_recipient")]),
]

# Period summary, shared by console and PDF: (section, [(row label, summary key), ...]).
SUMMARY_SECTIONS = [
    ("Revenue", [
        ("Total revenue (email + SMS)", "all_revenue"),
        ("Email campaign revenue", "email_campaign_revenue"),
        ("Email flow revenue", "email_flow_revenue"),
        ("SMS campaign revenue", "sms_campaign_revenue"),
        ("SMS flow revenue", "sms_flow_revenue"),
    ]),
    ("Campaigns", [
        ("Email campaign sends", "email_campaign_sends"),
        ("Email unique campaigns (concepts)", "email_campaign_concepts"),
        ("Email $ average per send", "email_avg_rev_per_campaign"),
        ("SMS campaign sends", "sms_campaign_sends"),
        ("SMS $ average per send", "sms_avg_rev_per_campaign"),
    ]),
    ("Rates", [
        ("Email campaign open rate", "email_campaign_open_rate"),
        ("Email flow open rate", "email_flow_open_rate"),
        ("Email campaign click rate", "email_campaign_click_rate"),
        ("Email flow click rate", "email_flow_click_rate"),
        ("SMS campaign click rate", "sms_campaign_click_rate"),
        ("SMS flow click rate", "sms_flow_click_rate"),
    ]),
    ("Audience", [
        ("Email recipients", "email_total_recipients"),
        ("Email unique opens", "email_total_opens"),
        ("Email revenue per recipient", "email_rev_per_recipient"),
        ("SMS recipients", "sms_total_recipients"),
    ]),
]
STRONG_KEYS = {"all_revenue"}

STANDARD_NOTES = [
    "Campaign and flow rates are reported separately, not averaged together. "
    "Campaign rates are over recipients; flow rates are over delivered.",
    "SMS has no open tracking, so SMS shows click rate only.",
    "Campaigns are sent campaigns only, wholesale (WS) excluded. "
    "Revenue is Klaviyo-attributed Placed Order value.",
    "Campaign sends count every send; unique campaigns collapse segment versions of the same email into one.",
    "Unique opens are people who opened, not total open events.",
    f"{REPORT_DAY_LABEL}. Campaigns are dated by send date, flows by the day of the event.",
]


# --- Data ---

def load_period(start_date, end_date, label):
    """Fetch one period.

    Returns a dict with the raw email frames (for general_report), the daily
    rollup, and `summary`: a flat dict keyed as in SUMMARY_SECTIONS. Totals
    are computed from the whole period, not summed from the days, so unique
    concepts and flow unique opens are not double counted.
    """
    start, end = timeframe(start_date, end_date)
    campaigns = with_send_date(load_email_campaigns(start, end))
    flows = load_email_flows(start, end)
    daily = daily_rollup(campaigns, load_flows_daily(start_date, end_date),
                         days_between(start_date, end_date))
    email = rollup_row(campaigns, flows)
    sms = sms_row(load_campaigns(start, end, "sms"), load_flows(start, end, "sms"))

    summary = {f"email_{k}": v for k, v in email.items()}
    summary.update({f"sms_{k}": v for k, v in sms.items()})
    summary["all_revenue"] = email["total_revenue"] + sms["total_revenue"]
    return {"label": label, "start": start_date, "end": end_date, "campaigns": campaigns,
            "flows": flows, "daily": daily, "total": email, "summary": summary}


def report_notes(periods):
    """Notes printed with the summary: accrual warnings first, then the standard notes."""
    notes = []
    for p in periods:
        age = (date.today() - date.fromisoformat(p["end"])).days
        if age <= ACCRUING_DAYS:
            when = "today or later" if age <= 0 else ("1 day ago" if age == 1 else f"{age} days ago")
            notes.append(f"{p['label']} ended {when}, so its revenue is still accruing attribution and will rise.")
    return notes + STANDARD_NOTES


def range_label(start, end, with_year=False):
    """A date range as a column heading, e.g. `Sep 28-Oct 4`."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    text = f"{first.strftime('%b %d').replace(' 0', ' ')}-{last.strftime('%b %d').replace(' 0', ' ')}"
    return f"{text} {first.year}" if with_year else text


def default_labels(periods_dates):
    """Label periods by year when the years differ, otherwise by start date.

    Three periods are always date ranges: two of them share a year, so a year
    is not a label that tells them apart.
    """
    if len(periods_dates) > 2:
        current_year = periods_dates[-1][0][:4]
        return [range_label(s, e, with_year=(s[:4] != current_year)) for s, e in periods_dates]
    years = {s[:4] for s, _ in periods_dates}
    if len(years) == len(periods_dates):
        return [s[:4] for s, _ in periods_dates]
    return [s for s, _ in periods_dates]


# --- Output ---

def fmt(val, key):
    if key.endswith("_rate"):
        return f"{val:.2f}%"
    if key.endswith("rev_per_recipient"):
        return f"${val:,.3f}"
    if key.endswith("revenue") or key.endswith("avg_rev_per_campaign"):
        return f"${val:,.0f}"
    return f"{val:,.0f}"


def pct_change(current, prior):
    if prior == 0:
        return "N/A"
    return f"{(current - prior) / prior * 100:+.1f}%"


def day_label(d):
    return f"{d.isoformat()} {d.strftime('%a')}"


def print_header(title, periods, yoy=False):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    for p in periods:
        print(f"  {p['label']}: {p['start']} to {p['end']}")
    if yoy:
        print("  Prior days are the same weekdays 364 days earlier.")
    print(f"  {REPORT_DAY_LABEL}.")
    print("  Campaigns are dated by send date, flows by the day of the event.")
    print("  Campaign rates are over recipients, flow rates over delivered.")


def print_single_table(title, daily, total, cols):
    width = 15 + 12 * len(cols)
    print(f"\n  [{title}]")
    print("  " + f"{'Date':<15}" + "".join(f"{label:>12}" for label, _ in cols))
    print("  " + "-" * width)
    for d, row in daily.iterrows():
        print("  " + f"{day_label(d):<15}" + "".join(f"{fmt(row[k], k):>12}" for _, k in cols))
    print("  " + "-" * width)
    print("  " + f"{'TOTAL':<15}" + "".join(f"{fmt(total[k], k):>12}" for _, k in cols))


def print_period_daily(p):
    print(f"\n  ---- {p['label']} daily email ({p['start']} to {p['end']}) ----")
    for title, cols in SINGLE_TABLES:
        print_single_table(title, p["daily"], p["total"], cols)


def print_yoy_table(title, curr, prior, curr_total, prior_total, cols):
    """Rows are day pairs; each column group is prior/current/change for one key."""
    width = 28 + 35 * len(cols)
    print(f"\n  [{title}]")
    print("  " + " " * 28 + "".join(f"{label:^35}" for label, _ in cols))
    print("  " + f"{'Date':<15}{'Prior date':<13}"
          + "".join(f"{'Prior':>12}{'Current':>12}{'Chg':>11}" for _ in cols))
    print("  " + "-" * width)
    for (cd, crow), (pd_, prow) in zip(curr.iterrows(), prior.iterrows()):
        cells = "".join(f"{fmt(prow[k], k):>12}{fmt(crow[k], k):>12}{pct_change(crow[k], prow[k]):>11}"
                        for _, k in cols)
        print("  " + f"{day_label(cd):<15}{pd_.isoformat():<13}" + cells)
    print("  " + "-" * width)
    cells = "".join(f"{fmt(prior_total[k], k):>12}{fmt(curr_total[k], k):>12}"
                    f"{pct_change(curr_total[k], prior_total[k]):>11}" for _, k in cols)
    print("  " + f"{'TOTAL':<28}" + cells)


def print_summary(periods):
    """Email + SMS period summary.

    The current period is last. Every period before it gets a change column
    against it, so one prior reads as before/after and two read as this week
    against last week and against last year.
    """
    priors = periods[:-1]
    print(f"\n{'='*60}")
    print("  SUMMARY (EMAIL + SMS)")
    print(f"{'='*60}")
    width = max(14, max(len(p["label"]) for p in periods) + 2)
    change_width = width + 4
    print("  " + f"{'Metric':<36}" + "".join(f"{p['label']:>{width}}" for p in periods)
          + "".join(f"{'vs ' + p['label']:>{change_width}}" for p in priors))
    for section, rows in SUMMARY_SECTIONS:
        print(f"  {section.upper()}")
        for label, key in rows:
            vals = [p["summary"][key] for p in periods]
            line = f"    {label:<34}" + "".join(f"{fmt(v, key):>{width}}" for v in vals)
            line += "".join(f"{pct_change(vals[-1], v):>{change_width}}" for v in vals[:-1])
            print(line)
    print("\n  Notes:")
    for n in report_notes(periods):
        print(f"  - {n}")


def pdf_path(arg, title):
    if arg:
        return Path(arg).expanduser()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR / f"{re.sub(r'[/:]', ' ', title)}.pdf"


def parse_args():
    parser = argparse.ArgumentParser(
        prog="python -m reporting.klaviyo.daily_report",
        description="Daily Klaviyo email report with SMS period totals. Dates are inclusive "
                    f"ISO dates. {REPORT_DAY_LABEL}.",
        epilog="e.g. compare 2026-09-01 2026-09-07 2025-08-27 2025-09-02 "
               "--title 'Labor Day Email & SMS' --labels LD25,LD26 --pdf",
    )
    parser.add_argument("dates", nargs="+",
                        help="<start> <end> | yoy <start> <end> | full <start> <end> "
                             "| compare <start> <end> <prior_start> <prior_end>")
    parser.add_argument("--pdf", nargs="?", const="", default=None, metavar="PATH",
                        help="also write the PDF; default path is reporting/klaviyo/reports/<title>.pdf")
    parser.add_argument("--title", help="report title")
    parser.add_argument("--labels", help="period labels, prior first, comma separated (e.g. LD25,LD26)")
    args = parser.parse_args()

    d = args.dates
    if len(d) == 2:
        mode, ranges = "single", [(d[0], d[1])]
    elif len(d) == 3 and d[0] == "full":
        back = lambda offset: tuple((date.fromisoformat(x) - offset).isoformat() for x in d[1:])
        mode, ranges = "full", [back(YOY_OFFSET), back(WOW_OFFSET), (d[1], d[2])]
    elif len(d) == 3 and d[0] == "yoy":
        prior = tuple((date.fromisoformat(x) - YOY_OFFSET).isoformat() for x in d[1:])
        mode, ranges = "yoy", [prior, (d[1], d[2])]
    elif len(d) == 5 and d[0] == "compare":
        mode, ranges = "compare", [(d[3], d[4]), (d[1], d[2])]
    else:
        parser.error("dates must be: <start> <end> | yoy <start> <end> | full <start> <end> "
                     "| compare <start> <end> <prior_start> <prior_end>")
    for s, e in ranges:
        days_between(s, e)  # validates the dates

    labels = args.labels.split(",") if args.labels else default_labels(ranges)
    if len(labels) != len(ranges):
        parser.error(f"--labels needs {len(ranges)} comma-separated labels")
    return args, mode, ranges, labels


if __name__ == "__main__":
    args, mode, ranges, labels = parse_args()

    periods = []
    for (s, e), label in zip(ranges, labels):
        print(f"Fetching {label} ({s} to {e})...")
        periods.append(load_period(s, e, label))

    current = ranges[-1]
    if args.title:
        title = args.title
    elif len(periods) == 1:
        title = f"Email & SMS {current[0]} to {current[1]}"
    else:
        title = (f"Email & SMS {current[0]} to {current[1]} "
                 f"vs {' and '.join(labels[:-1])}")

    print_header(title, periods, yoy=(mode == "yoy"))
    if mode == "yoy":
        prior, curr = periods
        for t, cols in YOY_TABLES:
            print_yoy_table(t, curr["daily"], prior["daily"], curr["total"], prior["total"], cols)
    else:
        for p in periods:
            print_period_daily(p)
    print_summary(periods)

    if len(periods) == 1:
        p = periods[0]
        print_report(p["campaigns"], p["flows"], p["start"], p["end"])
    else:
        # The campaign level breakdown takes one pair, so it uses the nearest
        # prior period. The summary above already carries every comparison.
        prior, curr = periods[-2], periods[-1]
        print_yoy_report(curr["campaigns"], curr["flows"], prior["campaigns"], prior["flows"],
                         curr["start"], curr["end"], prior["start"], prior["end"])

    if args.pdf is not None:
        from reporting.klaviyo.daily_render import render_pdf
        out = pdf_path(args.pdf, title)
        render_pdf(title, periods, report_notes(periods), out)
        print(f"\nPDF written to {out}")
