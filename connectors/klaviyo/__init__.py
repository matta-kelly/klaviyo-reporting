"""Klaviyo connector. Raw API access with retry handling."""
import sys
import time
import tomllib
from datetime import timedelta, timezone
from pathlib import Path

from klaviyo_api import KlaviyoAPI
from openapi_client.exceptions import ApiException

from connectors.credentials import get_klaviyo_credentials


_CONFIG_PATH = Path(__file__).parent / "config.toml"
with open(_CONFIG_PATH, "rb") as f:
    CONFIG = tomllib.load(f)

ACCOUNT_TIMEZONE = CONFIG["account"]["timezone"]
REPORT_DAY_TZ = timezone(timedelta(hours=CONFIG["account"]["report_day_utc_offset"]))
REPORT_DAY_LABEL = (f"Days run midnight to midnight UTC{CONFIG['account']['report_day_utc_offset']:+d}, "
                    "Klaviyo's reporting clock (11 PM to 11 PM Pacific during standard time)")
CONVERSION_METRIC_ID = CONFIG["api"]["conversion_metric_id"]
FLOW_BATCH_SIZE = CONFIG["api"]["flow_batch_size"]
STATS_CAMPAIGNS = CONFIG["api"]["stats"]["campaigns"]
STATS_FLOWS = CONFIG["api"]["stats"]["flows"]

RETRY_CODES = set(CONFIG["api"]["retry"]["codes"])
MAX_RETRIES = CONFIG["api"]["retry"]["max_retries"]
DEFAULT_WAIT = CONFIG["api"]["retry"]["default_wait_seconds"]


def log(msg):
    print(f"[klaviyo] {msg}", file=sys.stderr, flush=True)


def get_client(credentials=None):
    # max_retries=0 disables the SDK's silent internal retry so
    # retry_on_rate_limit is the only retry path. Without this, a 429
    # inside an SDK call sleeps Retry-After up to 3x with no logging.
    # credentials: optional {"api_key"} to inject (the OAuth/per-user lane);
    # defaults to the environment-backed resolver.
    creds = credentials or get_klaviyo_credentials()
    return KlaviyoAPI(creds["api_key"], max_retries=0)


def retry_on_rate_limit(fn, max_retries=None, default_wait=None):
    """Call fn(), retrying on transient errors with logging on every wait."""
    if max_retries is None:
        max_retries = MAX_RETRIES
    if default_wait is None:
        default_wait = DEFAULT_WAIT
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except ApiException as e:
            if e.status not in RETRY_CODES or attempt == max_retries:
                if e.status in RETRY_CODES:
                    log(f"giving up after {attempt} attempts (status {e.status})")
                raise
            wait = int((e.headers or {}).get("retry-after", default_wait))
            log(f"status {e.status}, sleeping {wait}s (attempt {attempt}/{max_retries})")
            time.sleep(wait)
