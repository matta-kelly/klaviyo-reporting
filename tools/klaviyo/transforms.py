"""Klaviyo-domain transforms and report-ready loaders.

Pure transforms (normalize, classify) plus loaders that combine the
connector with those transforms. Shared across every Klaviyo report.
"""
import re

from tools.klaviyo.source import loaders


def normalize_campaign_name(name):
    """Collapse segment-level sends into a single campaign concept.

    Strips CLONE/RESEND prefixes, leading date parens, trailing segment
    parentheticals, and '- <X> Segment' suffixes.
    """
    if not name:
        return name
    name = re.sub(r"^(CLONE|RESEND:?)\s*", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"^\([^)]*\)\s*", "", name).strip()
    while re.search(r"\s*\([^)]*\)\s*$", name):
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = re.sub(r"\s*-\s*\w+\s+Segment\s*$", "", name, flags=re.IGNORECASE).strip()
    return name


def extract_segment(name):
    """Classify campaign into engagement segment from name."""
    lower = name.lower() if name else ""
    # "unen" also catches misspellings in live names ("unenaged", "unengaed").
    if "unen" in lower:
        return "unengaged"
    if "vip" in lower:
        return "vip"
    return "engaged"


def load_campaigns(start, end, channel):
    """Fetch campaigns for a channel. Filters to Sent, excludes wholesale.

    Email campaigns also get a 'segment' column from extract_segment.
    """
    get_campaign_report, _, _ = loaders()
    df = get_campaign_report(start, end, channel=channel)
    if df.empty:
        return df
    df = df[df["status"] == "Sent"]
    df = df[~df["campaign_name"].str.contains(r"\bWS\b", case=False, na=False)]
    df["campaign_concept"] = df["campaign_name"].apply(normalize_campaign_name)
    if channel == "email":
        df["segment"] = df["campaign_name"].apply(extract_segment)
    return df


def load_flows(start, end, channel):
    """Fetch flows for a channel. Filters to messages with deliveries."""
    _, get_flow_report, _ = loaders()
    df = get_flow_report(start, end, channel=channel)
    if df.empty:
        return df
    return df[df["delivered"] > 0]


def load_all(start, end):
    """Load all four data sets (email/sms × campaigns/flows) for a period."""
    return {
        "email_campaigns": load_campaigns(start, end, "email"),
        "email_flows": load_flows(start, end, "email"),
        "sms_campaigns": load_campaigns(start, end, "sms"),
        "sms_flows": load_flows(start, end, "sms"),
    }
