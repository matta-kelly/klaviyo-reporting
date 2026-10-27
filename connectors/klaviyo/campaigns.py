"""Campaign performance from Klaviyo's reporting API.

One paced report call for the numbers, then a records call for the names. The
report answers with ids only, so the second call is what makes the frame
readable; records run on a far higher rate limit than reports, so it is close
to free.
"""
import pandas as pd

from connectors.klaviyo import (
    CONVERSION_METRIC_ID, STATS_CAMPAIGNS, get_client, id_batches, log, quoted,
)


def _fetch_metadata(client, channel, ids):
    """Fetch name, send_time and status for the given campaign IDs only.

    Uses Klaviyo's documented `any(id,[...])` filter combined with the required
    `equals(messages.channel,...)` filter. Ids are batched because a filter
    list caps at 100.
    """
    if not ids:
        return {}

    campaigns = {}
    log(f"fetching metadata for {len(ids)} {channel} campaign ids")
    for batch in id_batches(ids):
        params = {
            "filter": f'and(equals(messages.channel,"{channel}"),any(id,[{quoted(batch)}]))'
        }
        for rows, _ in client.paged("campaigns", f"campaigns[{channel}]", params):
            for row in rows:
                attributes = row.get("attributes") or {}
                campaigns[row["id"]] = {
                    "campaign_name": attributes.get("name"),
                    "send_time": attributes.get("send_time"),
                    "status": attributes.get("status"),
                }
    return campaigns


def get_campaign_report(start, end, channel="email",
                        conversion_metric_id=CONVERSION_METRIC_ID):
    """Campaign performance over a date range, one row per campaign message.

    Args:
        start, end: ISO datetime strings. The offset in them is ignored:
            Klaviyo cuts the days on its own fixed reporting clock.
        channel: "email" or "sms".
        conversion_metric_id: the metric revenue is attributed from.

    Returns:
        A DataFrame with the groupings, every statistic in STATS_CAMPAIGNS, and
        campaign_name, send_time and status joined on. Empty if the period had
        no sends.

    Raises:
        requests.HTTPError: the API refused, or answered a partial report it
            offered no way to continue.
    """
    client = get_client()

    log(f"querying {channel} campaign values report ({start[:10]} to {end[:10]})")
    payload = client.report("campaign-values-reports", {
        "statistics": STATS_CAMPAIGNS,
        "timeframe": {"start": start, "end": end},
        "conversion_metric_id": conversion_metric_id,
        # Grouping by campaign_message_id is required; the API rejects a
        # request without it.
        "group_by": ["campaign_id", "campaign_message_id", "send_channel"],
        "filter": f'equals(send_channel,"{channel}")',
    })

    results = [
        {**(row.get("groupings") or {}), **(row.get("statistics") or {})}
        for row in payload["data"]["attributes"].get("results") or []
    ]
    log(f"  report returned {len(results)} rows")
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    ids = sorted({r["campaign_id"] for r in results if r.get("campaign_id")})
    metadata = _fetch_metadata(client, channel, ids)
    for column in ("campaign_name", "send_time", "status"):
        df[column] = df["campaign_id"].map(
            lambda cid: metadata.get(cid, {}).get(column))
    return df
