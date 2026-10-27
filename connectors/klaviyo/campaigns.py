import pandas as pd
from . import get_client, retry_on_rate_limit, log, CONVERSION_METRIC_ID, STATS_CAMPAIGNS
from openapi_client.models.campaign_values_request_dto import CampaignValuesRequestDTO
from openapi_client.models.campaign_values_request_dto_resource_object import CampaignValuesRequestDTOResourceObject
from openapi_client.models.campaign_values_request_dto_resource_object_attributes import CampaignValuesRequestDTOResourceObjectAttributes


def _fetch_metadata(client, channel, ids):
    """Fetch name/send_time/status for the given campaign IDs only.

    Uses Klaviyo's documented `any(id,[...])` filter combined with the
    required `equals(messages.channel,...)` filter.
    """
    if not ids:
        return {}
    quoted = ",".join(f'"{i}"' for i in ids)
    filter_str = f'and(equals(messages.channel,"{channel}"),any(id,[{quoted}]))'

    campaigns = {}
    cursor = None
    page = 0
    log(f"fetching metadata for {len(ids)} {channel} campaign ids")
    while True:
        page += 1
        response = retry_on_rate_limit(lambda: client.Campaigns.get_campaigns(
            filter=filter_str,
            page_cursor=cursor,
        ))
        for c in response.data:
            campaigns[c.id] = {
                "campaign_name": c.attributes.name,
                "send_time": c.attributes.send_time,
                "status": c.attributes.status,
            }
        log(f"  page {page}: {len(response.data)} ({len(campaigns)} total)")
        if response.links and response.links.next:
            cursor = response.links.next
        else:
            break
    return campaigns


def get_campaign_report(start, end, channel="email", conversion_metric_id=CONVERSION_METRIC_ID):
    """Fetch campaign performance report for a date range.

    Returns raw DataFrame from the API. No schema filtering or type coercion.
    """
    client = get_client()

    body = CampaignValuesRequestDTO(
        data=CampaignValuesRequestDTOResourceObject(
            type="campaign-values-report",
            attributes=CampaignValuesRequestDTOResourceObjectAttributes(
                statistics=STATS_CAMPAIGNS,
                timeframe={"start": start, "end": end},
                conversion_metric_id=conversion_metric_id,
                group_by=["campaign_id", "campaign_message_id", "send_channel"],
                filter=f'equals(send_channel,"{channel}")',
            ),
        )
    )

    log(f"querying {channel} campaign values report ({start[:10]} to {end[:10]})")
    results = []
    response = retry_on_rate_limit(lambda: client.Reporting.query_campaign_values(body))
    for r in response.data.attributes.results:
        results.append({**r.groupings, **r.statistics})
    log(f"  report returned {len(results)} rows")

    if not results:
        log("  no results, returning empty df")
        return pd.DataFrame()

    df = pd.DataFrame(results)

    ids = sorted({r["campaign_id"] for r in results if r.get("campaign_id")})
    metadata = _fetch_metadata(client, channel, ids)
    df["campaign_name"] = df["campaign_id"].map(lambda cid: metadata.get(cid, {}).get("campaign_name"))
    df["send_time"] = df["campaign_id"].map(lambda cid: metadata.get(cid, {}).get("send_time"))
    df["status"] = df["campaign_id"].map(lambda cid: metadata.get(cid, {}).get("status"))

    return df
