"""Klaviyo day-grain loaders and rollup.

Days are Klaviyo reporting days: midnight to midnight at REPORT_DAY_TZ
(fixed UTC-7). Klaviyo reads a timeframe's dates on that clock and ignores
any offset, so `timeframe()` keeps the plain T00:00:00Z/T23:59:59Z form.
Campaigns are dated by send date: `send_time` is a true UTC instant and is
converted to REPORT_DAY_TZ, so a campaign lands on the same day Klaviyo
counts it in. Flows are dated by flow series bucket.
"""
from datetime import date, timedelta

import pandas as pd

from connectors.klaviyo import REPORT_DAY_TZ
from tools.klaviyo.source import loaders
from tools.klaviyo.metrics import campaign_metrics, flow_metrics

FLOW_STAT_COLUMNS = ["recipients", "delivered", "opens_unique", "clicks_unique",
                     "conversion_uniques", "conversion_value"]


def timeframe(start_date, end_date):
    """Return the (start, end) timeframe strings covering whole days start_date..end_date.

    Args: ISO date strings or date objects. Returns a tuple of ISO datetime strings.
    """
    return f"{start_date}T00:00:00Z", f"{end_date}T23:59:59Z"


def days_between(start_date, end_date):
    """Return every date from start_date to end_date inclusive.

    Args: ISO date strings or date objects. Raises ValueError if end precedes start.
    """
    s = date.fromisoformat(str(start_date))
    e = date.fromisoformat(str(end_date))
    if e < s:
        raise ValueError(f"end {e} precedes start {s}")
    return [s + timedelta(days=i) for i in range((e - s).days + 1)]


def with_send_date(campaigns):
    """Add a `send_date` column (REPORT_DAY_TZ date of send_time) to a campaigns frame.

    Returns a copy; an empty frame is returned unchanged.
    """
    if campaigns.empty:
        return campaigns
    df = campaigns.copy()
    local = pd.to_datetime(df["send_time"], utc=True).dt.tz_convert(REPORT_DAY_TZ)
    df["send_date"] = local.dt.date
    return df


def load_flows_daily(start_date, end_date, channel="email"):
    """Fetch flow stats per message per day for start_date..end_date.

    Keeps messages with delivered > 0 over the period, matching
    transforms.load_flows, and drops rows where every stat is zero. Returns a
    long DataFrame with a `date` column; empty when the API returns nothing.

    Unique opens and clicks summed across days count a person once per day,
    so period totals come from the flow values report, not from this frame.
    """
    start, end = timeframe(start_date, end_date)
    _, _, get_flow_series = loaders()
    df = get_flow_series(start, end, channel=channel, interval="daily")
    if df.empty:
        return df
    df = df.rename(columns={"bucket": "date"})
    period_delivered = df.groupby("flow_message_id")["delivered"].transform("sum")
    df = df[period_delivered > 0]
    return df[(df[FLOW_STAT_COLUMNS] != 0).any(axis=1)]


def rollup_row(campaigns, flows):
    """Compute one report row from a campaigns frame and a flows frame.

    Campaign rates are over recipients and flow rates over delivered, as in
    metrics.py. Campaign and flow rates are never blended; only counts and
    revenue are added into totals. Empty frames contribute zeros.
    """
    c = campaign_metrics(campaigns) or {}
    f = flow_metrics(flows) or {}
    row = {
        "campaign_sends": c.get("campaigns_sent", 0),
        "campaign_concepts": c.get("unique_concepts", 0),
        "campaign_recipients": c.get("recipients", 0),
        "campaign_opens": c.get("opens_unique", 0),
        "campaign_open_rate": c.get("open_rate", 0),
        "campaign_click_rate": c.get("click_rate", 0),
        "campaign_revenue": c.get("revenue", 0),
        "avg_rev_per_campaign": c.get("avg_rev_per_campaign", 0),
        "flow_recipients": f.get("recipients", 0),
        "flow_opens": f.get("opens_unique", 0),
        "flow_open_rate": f.get("open_rate", 0),
        "flow_click_rate": f.get("click_rate", 0),
        "flow_revenue": f.get("revenue", 0),
    }
    row["total_recipients"] = row["campaign_recipients"] + row["flow_recipients"]
    row["total_opens"] = row["campaign_opens"] + row["flow_opens"]
    row["total_revenue"] = row["campaign_revenue"] + row["flow_revenue"]
    row["rev_per_recipient"] = row["total_revenue"] / row["total_recipients"] if row["total_recipients"] else 0
    return row


def daily_rollup(campaigns, flows_daily, days):
    """Build one rollup_row per day.

    Args:
        campaigns: frame from with_send_date(); may be empty.
        flows_daily: frame from load_flows_daily(); may be empty.
        days: list of dates to report; days with no activity are zero-filled.
    Returns a DataFrame indexed by date.
    """
    rows = []
    for d in days:
        c = campaigns[campaigns["send_date"] == d] if not campaigns.empty else campaigns
        f = flows_daily[flows_daily["date"] == d] if not flows_daily.empty else flows_daily
        rows.append(rollup_row(c, f))
    return pd.DataFrame(rows, index=pd.Index(days, name="date"))


def sms_row(campaigns, flows):
    """Compute SMS period totals from SMS campaign and flow frames.

    SMS has no open tracking, so rates are click rates only. Campaign click
    rate is over recipients, flow click rate over delivered. Empty frames
    contribute zeros.
    """
    c = campaign_metrics(campaigns) or {}
    f = flow_metrics(flows) or {}
    row = {
        "campaign_sends": c.get("campaigns_sent", 0),
        "campaign_recipients": c.get("recipients", 0),
        "campaign_click_rate": c.get("click_rate", 0),
        "campaign_revenue": c.get("revenue", 0),
        "avg_rev_per_campaign": c.get("avg_rev_per_campaign", 0),
        "flow_recipients": f.get("recipients", 0),
        "flow_click_rate": f.get("click_rate", 0),
        "flow_revenue": f.get("revenue", 0),
    }
    row["total_recipients"] = row["campaign_recipients"] + row["flow_recipients"]
    row["total_revenue"] = row["campaign_revenue"] + row["flow_revenue"]
    return row
