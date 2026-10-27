import pandas as pd
from . import get_client, retry_on_rate_limit, log, CONVERSION_METRIC_ID, FLOW_BATCH_SIZE, STATS_FLOWS
from openapi_client.models.flow_values_request_dto import FlowValuesRequestDTO
from openapi_client.models.flow_values_request_dto_resource_object import FlowValuesRequestDTOResourceObject
from openapi_client.models.flow_values_request_dto_resource_object_attributes import FlowValuesRequestDTOResourceObjectAttributes
from openapi_client.models.flow_series_request_dto import FlowSeriesRequestDTO
from openapi_client.models.flow_series_request_dto_resource_object import FlowSeriesRequestDTOResourceObject
from openapi_client.models.flow_series_request_dto_resource_object_attributes import FlowSeriesRequestDTOResourceObjectAttributes


def _fetch_metadata(client):
    """Fetch flow names and statuses via paginated get_flows."""
    flows = {}
    cursor = None
    while True:
        response = client.Flows.get_flows(page_cursor=cursor)
        for f in response.data:
            flows[f.id] = {
                "flow_name": f.attributes.name,
                "status": f.attributes.status,
            }
        if response.links and response.links.next:
            cursor = response.links.next
        else:
            break
    return flows


def _query_batch(client, flow_ids, start, end, channel, conversion_metric_id):
    """Query reporting API for a batch of flow IDs."""
    ids_str = ",".join(f'"{fid}"' for fid in flow_ids)
    filter_str = f'contains-any(flow_id,[{ids_str}]),equals(send_channel,"{channel}")'

    body = FlowValuesRequestDTO(
        data=FlowValuesRequestDTOResourceObject(
            type="flow-values-report",
            attributes=FlowValuesRequestDTOResourceObjectAttributes(
                statistics=STATS_FLOWS,
                timeframe={"start": start, "end": end},
                conversion_metric_id=conversion_metric_id,
                group_by=["flow_id", "flow_message_id", "send_channel"],
                filter=filter_str,
            ),
        )
    )

    results = []
    response = retry_on_rate_limit(lambda: client.Reporting.query_flow_values(body))
    for r in response.data.attributes.results:
        results.append({**r.groupings, **r.statistics})
    return results


def get_flow_report(start, end, channel="email", conversion_metric_id=CONVERSION_METRIC_ID):
    """Fetch flow performance report for a date range.

    Returns raw DataFrame from the API. No schema filtering or type coercion.
    """
    client = get_client()

    metadata = _fetch_metadata(client)
    all_ids = list(metadata.keys())

    results = []
    for i in range(0, len(all_ids), FLOW_BATCH_SIZE):
        batch = all_ids[i:i + FLOW_BATCH_SIZE]
        results.extend(_query_batch(client, batch, start, end, channel, conversion_metric_id))

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["flow_name"] = df["flow_id"].map(lambda fid: metadata.get(fid, {}).get("flow_name"))
    df["status"] = df["flow_id"].map(lambda fid: metadata.get(fid, {}).get("status"))

    return df


def _query_series_batch(client, flow_ids, start, end, channel, interval, conversion_metric_id):
    """Query the flow series report for a batch of flow IDs.

    Returns one dict per flow message per bucket. `bucket` is the bucket's
    calendar date; Klaviyo labels REPORT_DAY_TZ days as midnight UTC.
    """
    ids_str = ",".join(f'"{fid}"' for fid in flow_ids)
    filter_str = f'contains-any(flow_id,[{ids_str}]),equals(send_channel,"{channel}")'

    body = FlowSeriesRequestDTO(
        data=FlowSeriesRequestDTOResourceObject(
            type="flow-series-report",
            attributes=FlowSeriesRequestDTOResourceObjectAttributes(
                statistics=STATS_FLOWS,
                timeframe={"start": start, "end": end},
                interval=interval,
                conversion_metric_id=conversion_metric_id,
                group_by=["flow_id", "flow_message_id", "send_channel"],
                filter=filter_str,
            ),
        )
    )

    response = retry_on_rate_limit(lambda: client.Reporting.query_flow_series(body))
    attrs = response.data.attributes
    rows = []
    for r in attrs.results:
        for i, bucket in enumerate(attrs.date_times):
            rows.append({
                **r.groupings,
                "bucket": bucket.date(),
                **{stat: values[i] for stat, values in r.statistics.items()},
            })
    return rows


def get_flow_series(start, end, channel="email", interval="daily", conversion_metric_id=CONVERSION_METRIC_ID):
    """Fetch flow performance per time bucket for a date range.

    Daily interval allows up to 60 days per call. Returns a long DataFrame,
    one row per flow message per bucket, with a `bucket` date column plus
    the STATS_FLOWS statistics, flow_name and status. No filtering.
    """
    client = get_client()

    metadata = _fetch_metadata(client)
    all_ids = list(metadata.keys())
    batches = [all_ids[i:i + FLOW_BATCH_SIZE] for i in range(0, len(all_ids), FLOW_BATCH_SIZE)]

    log(f"querying {channel} flow series ({interval}, {start[:10]} to {end[:10]}), {len(all_ids)} flows")
    results = []
    for n, batch in enumerate(batches, 1):
        rows = _query_series_batch(client, batch, start, end, channel, interval, conversion_metric_id)
        log(f"  batch {n}/{len(batches)}: {len(rows)} rows")
        results.extend(rows)

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["flow_name"] = df["flow_id"].map(lambda fid: metadata.get(fid, {}).get("flow_name"))
    df["status"] = df["flow_id"].map(lambda fid: metadata.get(fid, {}).get("status"))

    return df
