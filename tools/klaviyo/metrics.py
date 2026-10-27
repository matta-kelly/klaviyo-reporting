"""Klaviyo metric calculations.

Totals + rates from campaign and flow dataframes. Returns dicts keyed
by metric name (see markdown.METRIC_LABELS for human labels).
"""
import pandas as pd

from connectors.klaviyo import REPORT_DAY_TZ


def _send_days(send_time):
    """Count distinct send days on Klaviyo's reporting clock (send_time is UTC)."""
    # format="ISO8601" rather than the default: the API returns send_time with
    # fractional seconds on some campaigns and without on others, and the
    # default infers one format from the first value then fails on the rest.
    local = (pd.to_datetime(send_time, format="ISO8601", utc=True)
             .dt.tz_convert(REPORT_DAY_TZ))
    return local.dt.date.nunique()


def campaign_metrics(df):
    """Compute totals + rates from a campaigns dataframe."""
    if df is None or df.empty:
        return None
    recipients = df["recipients"].sum()
    has_concepts = "campaign_concept" in df.columns
    has_opens = "opens_unique" in df.columns
    return {
        "recipients": recipients,
        "delivered": df["delivered"].sum(),
        "opens_unique": df["opens_unique"].sum() if has_opens else 0,
        "clicks_unique": df["clicks_unique"].sum(),
        "conversions": df["conversion_uniques"].sum(),
        "revenue": df["conversion_value"].sum(),
        "campaigns_sent": len(df),
        "unique_concepts": df["campaign_concept"].nunique() if has_concepts else len(df),
        "unique_days": _send_days(df["send_time"]),
        "open_rate": df["opens_unique"].sum() / recipients * 100 if recipients and has_opens else 0,
        "click_rate": df["clicks_unique"].sum() / recipients * 100 if recipients else 0,
        "conversion_rate": df["conversion_uniques"].sum() / recipients * 100 if recipients else 0,
        "rev_per_recipient": df["conversion_value"].sum() / recipients if recipients else 0,
        "avg_rev_per_campaign": df["conversion_value"].sum() / len(df) if len(df) else 0,
    }


def flow_metrics(df):
    """Compute totals + rates from a flows dataframe."""
    if df is None or df.empty:
        return None
    delivered = df["delivered"].sum()
    has_opens = "opens_unique" in df.columns
    return {
        "recipients": df["recipients"].sum() if "recipients" in df.columns else 0,
        "delivered": delivered,
        "opens_unique": df["opens_unique"].sum() if has_opens else 0,
        "clicks_unique": df["clicks_unique"].sum(),
        "conversions": df["conversion_uniques"].sum(),
        "revenue": df["conversion_value"].sum(),
        "flow_messages": len(df),
        "open_rate": df["opens_unique"].sum() / delivered * 100 if delivered and has_opens else 0,
        "click_rate": df["clicks_unique"].sum() / delivered * 100 if delivered else 0,
        "conversion_rate": df["conversion_uniques"].sum() / delivered * 100 if delivered else 0,
        "rev_per_delivered": df["conversion_value"].sum() / delivered if delivered else 0,
    }
