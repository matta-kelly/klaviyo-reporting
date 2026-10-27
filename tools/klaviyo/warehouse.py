# tools/klaviyo/warehouse.py
"""Klaviyo report data, read from the warehouse instead of the API.

Drop-in replacements for the three connector calls the daily and period reports
are built on. Each returns a frame with the same columns the connector returned,
so the transforms, rollups and printers above them are untouched.

Why this exists. The reporting endpoints are rationed to 225 calls a day for the
whole account and two a minute steady, so a single week's report spends a minute
or two mostly asleep and competes with every other report run that day. The same
numbers already land in the warehouse on a schedule, where reading them costs
nothing and a year is as cheap as a week.

What differs from the API, and it is worth knowing before trusting a number:

  * flow recipients read about 1% low. Measured against the UI for August 2026:
    revenue matched to the cent on 11 of 13 flows and conversions on 12 of 13,
    but seven flows were short on recipients by 0.2% to 2.1%. Recipients cannot
    change after a send, so those are message-days that never landed. Campaign
    figures matched exactly.
  * the warehouse re-reads only a trailing window, so a day more than a week old
    is frozen at whatever it was worth when it was last read. Revenue accrues
    for five days after a send, so a report run within a week of the period is
    reading numbers that are still moving — the same as against the API, but
    here the number stops moving for good rather than catching up.

Days are Klaviyo reporting days throughout, cut at a fixed UTC-7, because that
is what the warehouse models already store.
"""
import pandas as pd

from connectors.warehouse import get_connection

#: Stat columns the campaign report hands back, in the connector's order.
CAMPAIGN_STATS = ["recipients", "delivered", "opens_unique", "clicks_unique", "bounced",
                  "unsubscribe_uniques", "spam_complaints", "conversion_uniques",
                  "conversion_value"]

#: Stat columns the flow reports hand back.
FLOW_STATS = ["recipients", "delivered", "opens_unique", "clicks_unique",
              "conversion_uniques", "conversion_value"]

#: Counts of people, not of events, so they do not add across days: someone who
#: taps an SMS link on Monday and again on Thursday is one person to Klaviyo and
#: two to a sum of the two days. Measured over September 2026, summing days read
#: SMS flow unique clicks 8.1% high while delivered and revenue matched exactly.
FLOW_UNIQUE = ["opens_unique", "clicks_unique", "conversion_uniques"]

#: Everything else is a count of events or an amount, which does add.
FLOW_ADDITIVE = [c for c in FLOW_STATS if c not in FLOW_UNIQUE]

_CAMPAIGNS = """
    select m.parent_id            as campaign_id,
           m.message_id           as campaign_message_id,
           m.channel              as send_channel,
           c.name                 as campaign_name,
           c.send_time            as send_time,
           c.status               as status,
           m.recipients, m.delivered, m.opens_unique, m.clicks_unique, m.bounced,
           m.unsubscribe_uniques, m.spam_complaints, m.conversion_uniques,
           m.revenue              as conversion_value
    from int.int_klaviyo__message_day m
    join proc.proc_klaviyo__campaigns c on c.campaign_id = m.parent_id
    where m.source_type = 'campaign'
      and m.channel = %(channel)s
      and m.date between %(start)s and %(end)s
"""

# One row per message per day. The period report sums these to message grain;
# the daily report keeps the day.
_FLOWS = """
    select m.parent_id      as flow_id,
           m.message_id     as flow_message_id,
           m.channel        as send_channel,
           m.date           as date,
           f.name           as flow_name,
           f.status         as status,
           m.recipients, m.delivered, m.opens_unique, m.clicks_unique,
           m.conversion_uniques,
           m.revenue        as conversion_value
    from int.int_klaviyo__message_day m
    join proc.proc_klaviyo__flows f on f.flow_id = m.parent_id
    where m.source_type = 'flow'
      and m.channel = %(channel)s
      and m.date between %(start)s and %(end)s
"""


def _read(sql, start, end, channel):
    """Run one query and hand back a frame, or an empty one for no rows."""
    params = {"start": str(start), "end": str(end), "channel": channel}
    conn = get_connection()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def get_campaign_report(start, end, channel="email"):
    """Campaign performance over a period, one row per campaign.

    A campaign runs once, so the warehouse already holds one row per campaign
    and no summing is needed. Mirrors connectors.klaviyo.campaigns.
    """
    df = _read(_CAMPAIGNS, start, end, channel)
    if df.empty:
        return pd.DataFrame()
    return df


def get_flow_report(start, end, channel="email"):
    """Flow performance over a period, one row per flow message.

    A flow message sends continuously, so its days are summed to message grain
    here, the same shape the API's period report returns. Name and status come
    from the dimension and are constant per message, so they survive the group.

    The unique columns are left empty for any message that ran on more than one
    day in the window, because a sum of daily unique people is not the period's
    unique people. A message that ran on a single day keeps its uniques, which
    are exact. `tools.klaviyo.source` fills the gaps from the API, so an empty
    column here is a request for one call, not a missing number.
    """
    df = _read(_FLOWS, start, end, channel)
    if df.empty:
        return pd.DataFrame()
    keys = ["flow_id", "flow_message_id", "send_channel", "flow_name", "status"]
    report = df.groupby(keys, dropna=False, as_index=False)[FLOW_STATS].sum()
    spanning = (df.groupby("flow_message_id")["date"].nunique() > 1)
    report[FLOW_UNIQUE] = report[FLOW_UNIQUE].astype("Float64")
    report.loc[report["flow_message_id"].map(spanning).fillna(False), FLOW_UNIQUE] = pd.NA
    return report


def get_flow_series(start, end, channel="email", interval="daily"):
    """Flow performance per message per day.

    `interval` exists to match the connector's signature. Only daily is stored,
    so anything else is refused rather than silently answered at the wrong
    grain.
    """
    if interval != "daily":
        raise ValueError(
            f"the warehouse stores flow series at daily grain only, not {interval!r}")
    df = _read(_FLOWS, start, end, channel)
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df
