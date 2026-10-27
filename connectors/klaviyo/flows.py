"""Flow performance from Klaviyo's reporting API, as a period total or a series.

Every flow id is passed explicitly. A flow report sent without a flow id filter
returns a subset rather than an error: measured once at about 77% short, with
nothing in the response to say so. The id list is what makes the answer whole,
and it caps at 100, so wide accounts take several paced calls.

Flow metadata is fetched once per process and cached. It is a records call, not
a report, so it is cheap, but it pages through every flow in the account and
the names do not change between two reports in the same run.
"""
from datetime import date

import pandas as pd

from connectors.klaviyo import (
    CONVERSION_METRIC_ID, FILTER_BATCH, STATS_FLOWS, WINDOW_DAILY, get_client,
    id_batches, log, quoted,
)

def _fetch_metadata(client):
    """Flow names and statuses, keyed by flow id.

    Cached on the client, not in this module: a client built with other
    credentials belongs to another Klaviyo account, and handing it the first
    account's flow ids would filter a report down to ids that do not exist
    there. That answers empty rather than erroring, which is the silent
    undercount this module is about.
    """
    cached = getattr(client, "_flow_metadata", None)
    if cached is not None:
        return cached
    flows = {}
    for rows, _ in client.paged("flows", "flows"):
        for row in rows:
            attributes = row.get("attributes") or {}
            flows[row["id"]] = {
                "flow_name": attributes.get("name"),
                "status": attributes.get("status"),
            }
    client._flow_metadata = flows
    return flows


def _rows_from(payload):
    """Flatten a flow values report into one dict per grouping."""
    return [
        {**(row.get("groupings") or {}), **(row.get("statistics") or {})}
        for row in payload["data"]["attributes"].get("results") or []
    ]


def _with_metadata(results, metadata):
    """Build the frame and join the flow name and status onto it."""
    if not results:
        return pd.DataFrame()
    df = pd.DataFrame(results)
    for column in ("flow_name", "status"):
        df[column] = df["flow_id"].map(lambda fid: metadata.get(fid, {}).get(column))
    return df


def get_flow_report(start, end, channel="email",
                    conversion_metric_id=CONVERSION_METRIC_ID):
    """Flow performance over a date range, one row per flow message.

    Args:
        start, end: ISO datetime strings. The offset in them is ignored:
            Klaviyo cuts the days on its own fixed reporting clock.
        channel: "email" or "sms".
        conversion_metric_id: the metric revenue is attributed from.

    Returns:
        A DataFrame with the groupings, every statistic in STATS_FLOWS, and
        flow_name and status joined on. Empty if the period had no sends.

    Raises:
        requests.HTTPError: the API refused, or answered a partial report it
            offered no way to continue.
    """
    client = get_client()
    metadata = _fetch_metadata(client)
    batches = list(id_batches(list(metadata), FILTER_BATCH))

    log(f"querying {channel} flow values ({start[:10]} to {end[:10]}), "
        f"{len(metadata)} flows in {len(batches)} call(s)")
    results = []
    for n, batch in enumerate(batches, 1):
        payload = client.report("flow-values-reports", {
            "statistics": STATS_FLOWS,
            "timeframe": {"start": start, "end": end},
            "conversion_metric_id": conversion_metric_id,
            "filter": f'contains-any(flow_id,[{quoted(batch)}]),'
                      f'equals(send_channel,"{channel}")',
            "group_by": ["flow_id", "flow_message_id", "send_channel"],
        })
        rows = _rows_from(payload)
        log(f"  batch {n}/{len(batches)}: {len(rows)} rows")
        results.extend(rows)

    return _with_metadata(results, metadata)


def get_flow_series(start, end, channel="email", interval="daily",
                    conversion_metric_id=CONVERSION_METRIC_ID):
    """Flow performance per message per bucket over a date range.

    Args:
        start, end: ISO datetime strings, as for get_flow_report. A daily
            interval answers at most 60 days per call.
        channel: "email" or "sms".
        interval: "daily", "weekly" or "monthly".
        conversion_metric_id: the metric revenue is attributed from.

    Returns:
        A long DataFrame, one row per flow message per bucket, carrying a
        `bucket` date column alongside the statistics, flow_name and status.
        Empty if the period had no sends.

    Raises:
        requests.HTTPError: the API refused, or answered a partial report it
            offered no way to continue.
    """
    if interval == "daily":
        span = (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days + 1
        if span > WINDOW_DAILY:
            raise ValueError(
                f"a daily flow series answers at most {WINDOW_DAILY} days per call and "
                f"{start[:10]} to {end[:10]} is {span}. Klaviyo does not say it has "
                f"truncated, so this refuses rather than report a short series. Split "
                f"the range, or ask for a coarser interval.")

    client = get_client()
    metadata = _fetch_metadata(client)
    batches = list(id_batches(list(metadata), FILTER_BATCH))

    log(f"querying {channel} flow series ({interval}, {start[:10]} to {end[:10]}), "
        f"{len(metadata)} flows in {len(batches)} call(s)")
    results = []
    for n, batch in enumerate(batches, 1):
        payload = client.report("flow-series-reports", {
            "statistics": STATS_FLOWS,
            "timeframe": {"start": start, "end": end},
            "interval": interval,
            "conversion_metric_id": conversion_metric_id,
            "filter": f'contains-any(flow_id,[{quoted(batch)}]),'
                      f'equals(send_channel,"{channel}")',
            "group_by": ["flow_id", "flow_message_id", "send_channel"],
        })
        attributes = payload["data"]["attributes"]
        buckets = [d.date() if hasattr(d, "date") else pd.Timestamp(d).date()
                   for d in attributes.get("date_times") or []]
        rows = []
        for row in attributes.get("results") or []:
            series = row.get("statistics") or {}
            # A statistic array shorter than the bucket axis means the series
            # was truncated. Filling the tail with None would pass straight
            # through `.sum()`, which skips it, and lower the reported total
            # with nothing to show for it.
            short = [stat for stat, values in series.items()
                     if len(values) != len(buckets)]
            if short:
                raise ValueError(
                    f"flow series for {row.get('groupings')}: "
                    f"{', '.join(short)} do not have one value per bucket "
                    f"({len(buckets)} buckets). Refusing a partial series.")
            for i, bucket in enumerate(buckets):
                rows.append({
                    **(row.get("groupings") or {}),
                    "bucket": bucket,
                    **{stat: values[i] for stat, values in series.items()},
                })
        log(f"  batch {n}/{len(batches)}: {len(rows)} rows")
        results.extend(rows)

    return _with_metadata(results, metadata)
