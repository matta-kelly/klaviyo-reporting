# tools/klaviyo/source.py
"""Where report data comes from: the warehouse, or Klaviyo's API.

The warehouse is the default. It holds the same numbers, costs nothing to read,
and does not spend the account's 225 reporting calls a day — a single week's
report against the API takes a minute or two, most of it asleep against a
two-a-minute rate limit, and competes with every other report run that day.

The API path is kept because it is the reference. When a warehouse figure is
doubted, running the same period both ways and diffing is the check, and that
is only possible while both paths exist.

Set KLAVIYO_REPORT_SOURCE=api to use it.
"""
import os

WAREHOUSE = "warehouse"
API = "api"


def report_source():
    """Which backend the loaders should read. Defaults to the warehouse."""
    chosen = os.environ.get("KLAVIYO_REPORT_SOURCE", WAREHOUSE).strip().lower()
    if chosen not in (WAREHOUSE, API):
        raise ValueError(
            f"KLAVIYO_REPORT_SOURCE must be {WAREHOUSE!r} or {API!r}, got {chosen!r}")
    return chosen


def _with_period_uniques(warehouse_report):
    """Fill the unique columns the warehouse cannot answer, from the API.

    The warehouse holds one row per message per day, and unique people do not
    add across days, so it leaves those columns empty for any message that ran
    on more than one day. Klaviyo counts distinct profiles over whatever window
    it is asked for, so one call to its period report answers them all.

    One call per period, and only when a report spans more than a day. A week's
    report that happens to need it still leaves 224 of the day's 225 reporting
    calls. Raising on a gap that survives the fill is deliberate: the columns
    are nullable, and a silent sum over them would quietly undercount.
    """
    from tools.klaviyo.warehouse import FLOW_UNIQUE

    def report(start, end, channel="email"):
        frame = warehouse_report(start, end, channel)
        if frame.empty or not frame[FLOW_UNIQUE].isna().any().any():
            return frame
        from connectors.klaviyo.flows import get_flow_report as from_api
        truth = from_api(start, end, channel).set_index("flow_message_id")
        for column in FLOW_UNIQUE:
            filled = frame["flow_message_id"].map(truth[column])
            frame[column] = frame[column].fillna(filled.astype("Float64"))
        missing = frame.loc[frame[FLOW_UNIQUE].isna().any(axis=1), "flow_message_id"]
        if len(missing):
            raise RuntimeError(
                f"{len(missing)} flow messages have no unique counts for {start} to {end}: "
                f"the warehouse could not sum them and Klaviyo's period report does not "
                f"list them ({', '.join(missing.head(3))})")
        return frame

    return report


def loaders():
    """The three period/series readers, from whichever backend is selected.

    Imported inside the function so choosing the warehouse never imports the
    API client, and choosing the API never requires warehouse credentials.
    """
    if report_source() == API:
        from connectors.klaviyo.campaigns import get_campaign_report
        from connectors.klaviyo.flows import get_flow_report, get_flow_series
        return get_campaign_report, get_flow_report, get_flow_series

    from tools.klaviyo.warehouse import (
        get_campaign_report, get_flow_report, get_flow_series,
    )
    return get_campaign_report, _with_period_uniques(get_flow_report), get_flow_series
