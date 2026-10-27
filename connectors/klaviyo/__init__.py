"""Klaviyo connector. Raw API access, paced to the account's call budget.

`client.py` holds the transport: auth, retry, pagination and the pacing that
keeps reporting calls under two a minute. Everything here is configuration read
from `config.toml`, plus the one clock fact every caller needs.

Read only. Nothing in this package writes to Klaviyo.
"""
import tomllib
from datetime import timedelta, timezone
from pathlib import Path

from connectors.klaviyo.client import (
    FILTER_BATCH, WINDOW_DAILY, KlaviyoAPI, id_batches, log, quoted,
)

_CONFIG_PATH = Path(__file__).parent / "config.toml"
with open(_CONFIG_PATH, "rb") as f:
    CONFIG = tomllib.load(f)

ACCOUNT_TIMEZONE = CONFIG["account"]["timezone"]
REPORT_DAY_TZ = timezone(timedelta(hours=CONFIG["account"]["report_day_utc_offset"]))
REPORT_DAY_LABEL = (f"Days run midnight to midnight UTC{CONFIG['account']['report_day_utc_offset']:+d}, "
                    "Klaviyo's reporting clock (11 PM to 11 PM Pacific during standard time)")
CONVERSION_METRIC_ID = CONFIG["api"]["conversion_metric_id"]
STATS_CAMPAIGNS = CONFIG["api"]["stats"]["campaigns"]
STATS_FLOWS = CONFIG["api"]["stats"]["flows"]

_client = None


def get_client(credentials=None):
    """The shared client, built on first use.

    One instance per process on purpose: the pacing state lives on the client,
    so two of them would each hold two calls a minute and together break the
    limit. Passing credentials builds a separate one rather than reusing the
    shared instance.
    """
    global _client
    if credentials is not None:
        return KlaviyoAPI(credentials)
    if _client is None:
        _client = KlaviyoAPI()
    return _client


__all__ = [
    "KlaviyoAPI", "get_client", "log", "id_batches", "quoted",
    "FILTER_BATCH", "WINDOW_DAILY",
    "ACCOUNT_TIMEZONE", "REPORT_DAY_TZ", "REPORT_DAY_LABEL",
    "CONVERSION_METRIC_ID", "STATS_CAMPAIGNS", "STATS_FLOWS",
]
