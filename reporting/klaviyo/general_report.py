import sys
from datetime import date, timedelta
import pandas as pd

from tools.klaviyo.transforms import load_campaigns, load_flows
from tools.klaviyo.metrics import campaign_metrics, flow_metrics


# --- Data loading ---
#
# Console report. Shares the fetch/filter/normalize/metric logic with every
# other Klaviyo report via tools.klaviyo. The one local choice is that this
# console view does NOT split out a `vip` segment: vip-named campaigns are
# folded back into `engaged`. This preserves data parity with the original
# data-ops general_report (whose extract_segment had no vip bucket). The
# weekly markdown report, by contrast, does split vip — also matching data-ops.

def load_email_campaigns(start, end):
    """Email campaigns via the shared loader, vip folded into engaged."""
    df = load_campaigns(start, end, "email")
    if not df.empty:
        df["segment"] = df["segment"].replace("vip", "engaged")
    return df


def load_email_flows(start, end):
    """Email flows via the shared loader."""
    return load_flows(start, end, "email")


# --- Output ---

def fmt(val, kind="num"):
    if kind == "dollar":
        return f"${val:,.2f}"
    if kind == "pct":
        return f"{val:.2f}%"
    if kind == "num":
        return f"{val:,}"
    return str(val)


def print_section(title, metrics, is_flow=False):
    """Print a metrics block."""
    print(f"\n  [{title}]")

    # Totals
    if is_flow:
        print(f"    Flow messages:        {fmt(metrics['flow_messages'])}")
        print(f"    Delivered:            {fmt(metrics['delivered'])}")
    else:
        print(f"    Campaigns sent:       {fmt(metrics['campaigns_sent'])}")
        print(f"    Unique concepts:      {fmt(metrics['unique_concepts'])}")
        print(f"    Unique days sent:     {fmt(metrics['unique_days'])}")
        print(f"    Recipients:           {fmt(metrics['recipients'])}")
        print(f"    Delivered:            {fmt(metrics['delivered'])}")

    print(f"    Revenue:              {fmt(metrics['revenue'], 'dollar')}")
    if not is_flow and metrics.get('avg_rev_per_campaign'):
        print(f"    Avg rev/campaign:     {fmt(metrics['avg_rev_per_campaign'], 'dollar')}")

    # Efficiency
    print(f"    Open rate:            {fmt(metrics['open_rate'], 'pct')}")
    print(f"    Click rate:           {fmt(metrics['click_rate'], 'pct')}")
    print(f"    Conversion rate:      {fmt(metrics['conversion_rate'], 'pct')}")
    if is_flow:
        print(f"    Rev/delivered:        {fmt(metrics['rev_per_delivered'], 'dollar')}")
    else:
        print(f"    Rev/recipient:        {fmt(metrics['rev_per_recipient'], 'dollar')}")


def print_report(campaigns, flows, start, end):
    """Print the full structured report."""
    c_metrics = campaign_metrics(campaigns) if not campaigns.empty else None
    f_metrics = flow_metrics(flows) if not flows.empty else None

    total_revenue = (c_metrics["revenue"] if c_metrics else 0) + (f_metrics["revenue"] if f_metrics else 0)

    # --- 1. Top-line ---
    print(f"\n{'='*60}")
    print(f"  EMAIL PERFORMANCE: {start} to {end}")
    print(f"{'='*60}")
    print(f"\n  TOTAL REVENUE:          {fmt(total_revenue, 'dollar')}")
    if c_metrics:
        print(f"    Campaign revenue:     {fmt(c_metrics['revenue'], 'dollar')}")
    if f_metrics:
        print(f"    Flow revenue:         {fmt(f_metrics['revenue'], 'dollar')}")

    # --- 2. Flows ---
    print(f"\n{'='*60}")
    print(f"  FLOWS")
    print(f"{'='*60}")

    if f_metrics:
        print_section("FLOWS", f_metrics, is_flow=True)
    else:
        print("\n  [FLOWS] No data")

    # --- 3. Campaigns, all then by segment ---
    print(f"\n{'='*60}")
    print(f"  CAMPAIGNS — ALL")
    print(f"{'='*60}")

    if not campaigns.empty:
        print_section("ALL", campaign_metrics(campaigns))
    else:
        print("\n  No campaign data.")

    print(f"\n{'='*60}")
    print(f"  CAMPAIGNS — ENGAGED")
    print(f"{'='*60}")

    if not campaigns.empty:
        engaged_df = campaigns[campaigns["segment"] == "engaged"]
        if not engaged_df.empty:
            print_section("ENGAGED", campaign_metrics(engaged_df))
        else:
            print("\n  No engaged campaign data.")
    else:
        print("\n  No campaign data.")

    print(f"\n{'='*60}")
    print(f"  CAMPAIGNS — UNENGAGED")
    print(f"{'='*60}")

    if not campaigns.empty:
        unengaged_df = campaigns[campaigns["segment"] == "unengaged"]
        if not unengaged_df.empty:
            print_section("UNENGAGED", campaign_metrics(unengaged_df))
        else:
            print("\n  No unengaged campaign data.")
    else:
        print("\n  No campaign data.")


# --- YoY comparison ---

def _day_range_label(start_str, end_str):
    """Return day-of-week range string, e.g. 'Thu-Sat'."""
    s = date.fromisoformat(start_str)
    e = date.fromisoformat(end_str)
    days = []
    d = s
    while d <= e:
        days.append(d.strftime("%a"))
        d += timedelta(days=1)
    if len(days) == 1:
        return days[0]
    return f"{days[0]}-{days[-1]}"


def _pct_change(current, prior):
    if prior == 0:
        return "N/A"
    change = (current - prior) / prior * 100
    arrow = "+" if change >= 0 else ""
    return f"{arrow}{change:.1f}%"


def _compare_metrics(label, current, prior, keys, is_flow=False):
    """Print a side-by-side comparison of two metric dicts."""
    print(f"\n  [{label}]")
    print(f"    {'Metric':<22} {'Prior':>14} {'Current':>14} {'Change':>10}")
    print(f"    {'-'*60}")

    for key in keys:
        c_val = current.get(key, 0) if current else 0
        p_val = prior.get(key, 0) if prior else 0

        if key in ("open_rate", "click_rate", "conversion_rate"):
            kind = "pct"
        elif key in ("revenue", "rev_per_recipient", "rev_per_delivered", "avg_rev_per_campaign"):
            kind = "dollar"
        else:
            kind = "num"

        print(f"    {key:<22} {fmt(p_val, kind):>14} {fmt(c_val, kind):>14} {_pct_change(c_val, p_val):>10}")


def print_yoy_report(curr_campaigns, curr_flows, prior_campaigns, prior_flows,
                     curr_start, curr_end, prior_start, prior_end):
    """Print YoY comparison report."""
    curr_day_label = _day_range_label(curr_start, curr_end)
    prior_day_label = _day_range_label(prior_start, prior_end)

    print(f"\n{'='*60}")
    print(f"  YoY EMAIL COMPARISON")
    print(f"{'='*60}")
    print(f"  Current:  {curr_start} to {curr_end} ({curr_day_label})")
    print(f"  Prior:    {prior_start} to {prior_end} ({prior_day_label})")

    # Compute all metric sets
    cc = campaign_metrics(curr_campaigns) if not curr_campaigns.empty else None
    cf = flow_metrics(curr_flows) if not curr_flows.empty else None
    pc = campaign_metrics(prior_campaigns) if not prior_campaigns.empty else None
    pf = flow_metrics(prior_flows) if not prior_flows.empty else None

    curr_total = (cc["revenue"] if cc else 0) + (cf["revenue"] if cf else 0)
    prior_total = (pc["revenue"] if pc else 0) + (pf["revenue"] if pf else 0)
    curr_c_rev = cc["revenue"] if cc else 0
    prior_c_rev = pc["revenue"] if pc else 0
    curr_f_rev = cf["revenue"] if cf else 0
    prior_f_rev = pf["revenue"] if pf else 0

    # --- Top-line ---
    print(f"\n{'='*60}")
    print(f"  TOP-LINE")
    print(f"{'='*60}")
    print(f"    {'':>22} {'Prior':>14} {'Current':>14} {'Change':>10}")
    print(f"    {'-'*60}")
    print(f"    {'Total revenue':<22} {fmt(prior_total, 'dollar'):>14} {fmt(curr_total, 'dollar'):>14} {_pct_change(curr_total, prior_total):>10}")
    print(f"    {'Campaign revenue':<22} {fmt(prior_c_rev, 'dollar'):>14} {fmt(curr_c_rev, 'dollar'):>14} {_pct_change(curr_c_rev, prior_c_rev):>10}")
    print(f"    {'Flow revenue':<22} {fmt(prior_f_rev, 'dollar'):>14} {fmt(curr_f_rev, 'dollar'):>14} {_pct_change(curr_f_rev, prior_f_rev):>10}")

    # --- Flows ---
    print(f"\n{'='*60}")
    print(f"  FLOWS")
    print(f"{'='*60}")
    flow_keys = ["flow_messages", "delivered", "revenue", "open_rate", "click_rate",
                 "conversion_rate", "rev_per_delivered"]
    _compare_metrics("FLOWS", cf, pf, flow_keys, is_flow=True)

    # --- Campaigns, all then by segment ---
    for segment in ["all", "engaged", "unengaged"]:
        print(f"\n{'='*60}")
        print(f"  CAMPAIGNS — {segment.upper()}")
        print(f"{'='*60}")

        def select(df):
            if df.empty:
                return pd.DataFrame()
            return df if segment == "all" else df[df["segment"] == segment]

        curr_seg = select(curr_campaigns)
        prior_seg = select(prior_campaigns)

        cs = campaign_metrics(curr_seg) if not curr_seg.empty else None
        ps = campaign_metrics(prior_seg) if not prior_seg.empty else None

        campaign_keys = ["campaigns_sent", "unique_concepts", "unique_days", "recipients",
                         "delivered", "revenue", "avg_rev_per_campaign", "open_rate",
                         "click_rate", "conversion_rate", "rev_per_recipient"]
        _compare_metrics(segment.upper(), cs, ps, campaign_keys)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        # Single report
        start = f"{sys.argv[1]}T00:00:00Z"
        end = f"{sys.argv[2]}T23:59:59Z"
        campaigns = load_email_campaigns(start, end)
        flows = load_email_flows(start, end)
        print_report(campaigns, flows, sys.argv[1], sys.argv[2])

    elif len(sys.argv) == 4 and sys.argv[1] == "yoy":
        # YoY comparison: general_report yoy <start> <end>
        # Matches same day-of-week from prior year (not calendar date)
        curr_start_str = sys.argv[2]
        curr_end_str = sys.argv[3]
        curr_start = date.fromisoformat(curr_start_str)
        curr_end = date.fromisoformat(curr_end_str)
        # Go back 52 weeks to land on the same day of the week
        prior_start = curr_start - timedelta(weeks=52)
        prior_end = curr_end - timedelta(weeks=52)
        prior_start_str = prior_start.isoformat()
        prior_end_str = prior_end.isoformat()

        print(f"Fetching current period ({curr_start_str} to {curr_end_str})...")
        curr_campaigns = load_email_campaigns(f"{curr_start_str}T00:00:00Z", f"{curr_end_str}T23:59:59Z")
        curr_flows = load_email_flows(f"{curr_start_str}T00:00:00Z", f"{curr_end_str}T23:59:59Z")

        print(f"Fetching prior period ({prior_start_str} to {prior_end_str})...")
        prior_campaigns = load_email_campaigns(f"{prior_start_str}T00:00:00Z", f"{prior_end_str}T23:59:59Z")
        prior_flows = load_email_flows(f"{prior_start_str}T00:00:00Z", f"{prior_end_str}T23:59:59Z")

        print_yoy_report(curr_campaigns, curr_flows, prior_campaigns, prior_flows,
                         curr_start_str, curr_end_str, prior_start_str, prior_end_str)

    else:
        print("Usage:")
        print("  Single:  python -m reporting.klaviyo.general_report <start> <end>")
        print("  YoY:     python -m reporting.klaviyo.general_report yoy <start> <end>")
        print("")
        print("  e.g. python -m reporting.klaviyo.general_report yoy 2026-04-03 2026-04-05")
        sys.exit(1)
