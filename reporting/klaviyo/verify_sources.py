"""Check the warehouse against Klaviyo for a period, metric by metric.

    python -m reporting.klaviyo.verify_sources <start> <end> [tolerance]

Exits non-zero if any summary metric drifts past the tolerance, 0.5% by default,
so it can be run before trusting a report that covers unfamiliar ground.

Why it exists. The warehouse holds one row per message per day and a report asks
for a period, so every figure on a report is a roll up. Most of them add. Unique
people do not, and the gap only appears over windows long enough for the same
person to act twice, which is why a week looked clean while a month was 8% out on
SMS flow clicks. A single day is the control: at that grain the two sources are
the same number by construction, so a day that disagrees means something else is
wrong.

Costs a handful of reporting calls against the day's 225, all of them on the API
side. Not for a schedule.
"""
import sys

from reporting.klaviyo.daily_report import SUMMARY_SECTIONS, fmt, load_period
from tools.klaviyo import source

DEFAULT_TOLERANCE = 0.005


def summary(start, end, backend):
    """One period's summary figures, read through the named backend."""
    source.os.environ["KLAVIYO_REPORT_SOURCE"] = backend
    return load_period(start, end, backend)["summary"]


def compare(start, end, tolerance=DEFAULT_TOLERANCE):
    """Print every metric that drifts, and return how many did."""
    warehouse, api = summary(start, end, source.WAREHOUSE), summary(start, end, source.API)
    drifted = 0
    for _, rows in SUMMARY_SECTIONS:
        for label, key in rows:
            held, truth = warehouse[key], api[key]
            if truth:
                gap = abs(held - truth) / abs(truth)
            else:
                gap = 0.0 if held == truth else 1.0
            if gap > tolerance:
                drifted += 1
                print(f"  {label:38}{fmt(held, key):>14}{fmt(truth, key):>14}"
                      f"{(held - truth) / truth * 100:>+9.2f}%")
    return drifted


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    begin, finish = sys.argv[1], sys.argv[2]
    limit = float(sys.argv[3]) if len(sys.argv) == 4 else DEFAULT_TOLERANCE

    print(f"{begin} to {finish}, warehouse against Klaviyo, tolerance {limit:.1%}")
    print(f"  {'Metric':38}{'Warehouse':>14}{'Klaviyo':>14}{'Gap':>10}")
    count = compare(begin, finish, limit)
    print(f"\n{count} metrics outside tolerance" if count else "\nEvery metric agrees.")
    sys.exit(1 if count else 0)
