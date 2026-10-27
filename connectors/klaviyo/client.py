"""Klaviyo API client. Transport, auth, retry, pagination and the call budget.

The key comes from connectors.credentials: a private API key, the `pk_`
form, read scopes only. The connection check needs `accounts:read` and the
reports need `campaigns:read` and `flows:read`.

API Docs: https://developers.klaviyo.com/en/reference/api_overview

Three families behind one key, and they do not behave alike:

  records  GET, cursor paginated, hundreds of calls a minute
  reports  POST, a pre-aggregated grid over a window, 2 a minute and 225 a day

The report budget, not the data volume, is what shapes anything built on it.
A call costs the same whatever it returns, so every request asks for the widest
window and every statistic at once, and `report` paces itself to the steady rate
rather than discovering the limit through 429s.

Two failures on this API are silent, and both have bitten us:

  * A flow report without an explicit flow id filter returns a subset rather
    than an error. Flow revenue came back about 77% short.
  * A segment series report with the segment passed as a relationship ignores it
    and answers with another segment's numbers.

Neither raises. The helpers here put those ids where they belong, and anything
built on this client should check one number against a known one before it is
believed.
"""
import sys
import time
import urllib.parse

import requests

from connectors.credentials import get_klaviyo_credentials


def log(msg):
    """One progress line on stderr, so a paced run shows why it is waiting."""
    print(f"[klaviyo] {msg}", file=sys.stderr, flush=True)


# Klaviyo pins behaviour to a dated revision sent on every request, so the
# version is a dated dependency of the same kind Meta's URL version is. A
# revision stays stable for a year, then deprecates for a year. Omitting the
# header or sending an unknown value is an error rather than a fallback, so this
# is never optional. The `revision` argument overrides it, which makes the
# periodic bump a one-line change.
# https://developers.klaviyo.com/en/docs/api_versioning_and_deprecation_policy
DEFAULT_REVISION = "2026-07-15"

# Klaviyo's cap on any list filter. Flow ids go in 100 at a time.
FILTER_BATCH = 100

# The widest window a daily series report answers in one call. A call costs the
# same whatever it returns, so a range longer than this is split rather than
# asked for at a coarser grain.
WINDOW_DAILY = 60


class KlaviyoAPI:
    """Klaviyo API client with private-key auth, retry, pagination and pacing."""

    BASE_URL = "https://a.klaviyo.com/api"
    MAX_RETRIES = 5
    RETRY_BACKOFF = [5, 15, 30, 60, 120]
    DEFAULT_RETRY_AFTER = 60
    REPORT_MIN_INTERVAL = 31.0   # seconds between report calls, holding 2/minute

    def __init__(self, credentials=None, revision=None):
        self.api_key = (credentials or get_klaviyo_credentials())["api_key"]
        self.revision = revision or DEFAULT_REVISION

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Klaviyo-API-Key {self.api_key}",
            "revision": self.revision,
            "accept": "application/vnd.api+json",
            "content-type": "application/vnd.api+json",
        })
        # None, not 0.0: time.monotonic()'s reference point is undefined, and
        # on a host with low uptime a 0.0 start makes the first call wait the
        # full interval for nothing.
        self._last_call = None

    # ---- transport -------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs) -> dict:
        """One call with retry.

        429 waits out Klaviyo's own Retry-After, in seconds, rather than a fixed
        ladder: the report endpoints run at 2/m steady, so a 5s retry lands
        inside the same window and 429s again.
        """
        if not url.startswith("http"):
            url = f"{self.BASE_URL}/{url.lstrip('/')}"

        name = url.rsplit("/", 1)[-1]
        last, last_response = None, None
        for attempt in range(1, self.MAX_RETRIES + 1):
            final = attempt == self.MAX_RETRIES
            try:
                response = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as failure:
                # A dropped socket never reaches a status code, so without this
                # it escapes the retry loop every other transient fault goes
                # through and takes the whole run with it.
                last, last_response = f"{type(failure).__name__} on {name}", None
                if final:
                    break
                wait = self._backoff(attempt)
                log(f"{last}, retrying in {wait}s (attempt {attempt}/{self.MAX_RETRIES})")
                time.sleep(wait)
                continue

            if response.status_code == 429:
                last, last_response = f"429 on {name}", response
                if final:
                    break
                wait = self._retry_after(response)
                log(f"{last}, waiting {wait}s (attempt {attempt}/{self.MAX_RETRIES})")
                time.sleep(wait)
                continue

            if response.status_code in (500, 502, 503, 504, 524):
                last, last_response = f"HTTP {response.status_code} on {name}", response
                if final:
                    break
                wait = self._backoff(attempt)
                log(f"{last}, retrying in {wait}s (attempt {attempt}/{self.MAX_RETRIES})")
                time.sleep(wait)
                continue

            if not response.ok:
                # A 4xx body names the real problem — a filter in the wrong
                # place, a required group_by left out, an unsupported operator.
                # raise_for_status hides it, so it is carried in the message.
                raise requests.HTTPError(
                    f"{response.status_code} on {url}: {response.text[:500]}",
                    response=response)

            return response.json()

        # The response rides along where there was one, so a caller can tell
        # rate-limit exhaustion from a host that never answered.
        raise requests.HTTPError(
            f"{url}: gave up after {self.MAX_RETRIES} attempts, last was {last}",
            response=last_response)

    def _backoff(self, attempt: int) -> int:
        """The wait for a transient fault, by attempt number (1-based)."""
        return self.RETRY_BACKOFF[min(attempt - 1, len(self.RETRY_BACKOFF) - 1)]

    def _retry_after(self, response) -> int:
        """Klaviyo's Retry-After in seconds.

        The header is allowed to be an HTTP-date, and an intermediary that
        throttles on Klaviyo's behalf may send one. Parsing that as an int
        raises out of the retry loop and turns a retryable throttle into a
        hard failure, so an unparseable value falls back to the default.
        """
        raw = response.headers.get("retry-after", self.DEFAULT_RETRY_AFTER)
        try:
            return int(raw)
        except (TypeError, ValueError):
            log(f"Retry-After was {raw!r}, not seconds; waiting "
                f"{self.DEFAULT_RETRY_AFTER}s instead")
            return self.DEFAULT_RETRY_AFTER

    def _pace(self) -> None:
        """Hold the report endpoints' steady rate instead of eating 429s.

        Retry still covers a burst that slips through (another process on the
        same key, a backfill overlapping a refresh); this keeps the normal path
        off the limit entirely.
        """
        if self._last_call is not None:
            wait = self.REPORT_MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.monotonic()

    # ---- records ---------------------------------------------------------

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        """One GET. For single objects and for the first page of a walk."""
        return self._request("GET", endpoint, params=params)

    def paged(self, endpoint: str, tag: str, params: dict | None = None):
        """Yield (data, included) a page at a time until the cursor runs out.

        Klaviyo pages by cursor and hands the next one back as a whole URL in
        `links.next`, so it is followed as-is: the cursor is opaque and
        re-encoding it only invites a mismatch. Params ride on the first request
        only, since the next URL already carries them.
        """
        url, page_no, total = endpoint, 0, 0

        while url:
            payload = self._request("GET", url, params=params if page_no == 0 else None)
            rows = payload.get("data") or []
            page_no += 1
            total += len(rows)
            log(f"{tag}: page {page_no}, {len(rows)} rows ({total} total)")

            yield rows, (payload.get("included") or [])

            url = (payload.get("links") or {}).get("next")

    # ---- reports ---------------------------------------------------------

    def _paged_post(self, endpoint: str, body: dict, attributes: dict) -> dict:
        """POST an endpoint and follow its continuation until there is none.

        A report answers in parallel arrays, one `results` entry per grouping,
        so a truncated answer is indistinguishable from a real one: fewer
        groupings, every array still the right length, nothing to notice.
        Measured: campaign values page at 1,000 rows.

        Klaviyo hands the continuation back as a ready-made GET URL in
        links.next, exactly as the record endpoints do, but these endpoints are
        POST only and answer 405 to that URL. The cursor has to come out of the
        link and go back in the body as page_cursor. Following the link
        directly is what broke production.
        """
        self._pace()
        payload = self._request("POST", endpoint, json=body)

        pages, guard = 1, 50
        while True:
            following = (payload.get("links") or {}).get("next")
            if not following:
                break

            if pages >= guard:
                raise requests.HTTPError(
                    f"{endpoint}: still paging after {guard} responses, which is past "
                    f"anything this API has been seen to do. Refusing to keep going "
                    f"rather than return a partial answer.")

            cursor = _page_cursor(following)
            if not cursor:
                raise requests.HTTPError(
                    f"{endpoint}: has more pages but its next link carries no "
                    f"page[cursor] to continue with: {following}. Refusing a "
                    f"partial answer.")

            self._pace()
            page = self._request("POST", endpoint, json={"data": {
                "type": body["data"]["type"],
                "attributes": {**attributes, "page_cursor": cursor},
            }})
            _merge_results(payload, page)
            payload["links"] = page.get("links") or {}
            pages += 1

        if pages > 1:
            log(f"{endpoint} answered over {pages} responses")

        _refuse_unfollowable(endpoint, payload)
        return payload

    def report(self, endpoint: str, attributes: dict) -> dict:
        """POST one report and return its payload, whole.

        `endpoint` is the plural resource path, e.g. campaign-values-reports.
        The body's type is the singular of it, which Klaviyo validates.

        A report answers with parallel arrays — one `results` entry per
        grouping, each holding a value per bucket — so a truncated answer is
        indistinguishable from a real one: fewer groupings, every array still
        the right length, nothing to notice. This follows a continuation if one
        is offered and refuses to return a partial if one is offered in a form
        it cannot follow. Everything measured so far comes back in one response,
        so this is the guard for the case that has not happened yet rather than
        a path that runs.
        """
        body = {"data": {"type": endpoint.rstrip("/")[:-1], "attributes": attributes}}
        return self._paged_post(endpoint, body, attributes)

# ---- report continuation -------------------------------------------------

def _page_cursor(link: str) -> str | None:
    """The page[cursor] value out of a next link.

    The link is a ready-made GET URL, which is useless on a POST-only endpoint,
    so only the cursor inside it is worth anything.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    for key in ("page[cursor]", "page%5Bcursor%5D"):
        if query.get(key):
            return query[key][0]
    return None


#: What a POST response calls its list of groupings. A report uses `results`;
#: Klaviyo's other POST family uses `data`. The two are not interchangeable,
#: and merging the wrong key drops every continuation row without a word, which
#: is the failure this whole path exists to prevent.
GROUPING_KEYS = ("results", "data")


def _merge_results(payload: dict, page: dict) -> None:
    """Append a continuation's groupings onto the first response's.

    Only the groupings grow. The bucket axis (`date_times`) is identical on
    every page, so appending it would misalign every array that follows.

    Keyed off what the continuation carried, not off what the first response
    holds: a report's first page always has `results`, so testing that instead
    would call every continuation merged, including one that arrived in a shape
    this cannot append and would otherwise drop in silence.
    """
    first = payload.setdefault("data", {}).setdefault("attributes", {})
    more = (page.get("data") or {}).get("attributes") or {}

    grew = False
    for key in GROUPING_KEYS:
        if key in more:
            first.setdefault(key, []).extend(more[key] or [])
            grew = True
    if not grew:
        raise requests.HTTPError(
            f"a continuation arrived in a shape with no groupings to merge: "
            f"{sorted(more)}. Refusing to drop it silently.")


def _refuse_unfollowable(endpoint: str, payload: dict) -> None:
    """Raise when a response says there is more and offers no way to fetch it.

    An undercount from this API is silent by construction, so the only safe
    answer to "there is more and I cannot reach it" is to fail the run. A wrong
    number that looks right is worse than no number.
    """
    attributes = (payload.get("data") or {}).get("attributes") or {}
    returned = sum(len(attributes.get(key) or []) for key in GROUPING_KEYS)
    for key in ("total", "total_count", "count"):
        claimed = attributes.get(key)
        if isinstance(claimed, int) and claimed > returned:
            raise requests.HTTPError(
                f"{endpoint}: says {key}={claimed} but returned {returned} groupings "
                f"and offered no next link. Refusing a partial report.")


def id_batches(ids: list[str], size: int = FILTER_BATCH):
    """Chunk ids for a `contains-any` filter, which caps at 100 per list."""
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def quoted(ids: list[str]) -> str:
    """Render ids for a filter list: contains-any(flow_id,["a","b"])."""
    return ",".join(f'"{i}"' for i in ids)
