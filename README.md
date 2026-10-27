# Klaviyo Reporting

Email and SMS performance from a Klaviyo account, by day and by period, with optional comparison against an earlier period. Console output always, PDF on request. Read only: it never writes to Klaviyo.

## Setup

### Install

```
uv sync
cp .env.example .env     # then fill it in
```

- needs Python 3.13

### Credentials

- every variable is in `.env.example`, with what it is for
- two things worth knowing before you ask for access
	- the warehouse role only needs select on the `int` and `proc` schemas
	- you need a Klaviyo key as well as the database, even though the numbers come from the database, see Caveat 1

### Why it reads a warehouse, and what that costs you

- Klaviyo's reporting endpoints are rationed
	- 2 calls a minute steady
	- 225 calls a day for the whole account, shared with the Klaviyo UI and every other job
- the same numbers land in a Postgres warehouse on a schedule, where a year costs the same as a week, so that is the default
- the client holds itself to the rate rather than eating 429s, which means **an API call is 31 seconds of waiting**
	- so how you run it decides how long it takes
	- a single day off the warehouse is instant, 0 calls
	- any longer period spends about 6 calls, roughly 3 minutes, to fill the flow unique counts
	- `KLAVIYO_REPORT_SOURCE=api` spends about 12, so roughly 6 minutes
- the API path exists as the reference
	- when a warehouse figure is doubted, run the period both ways and diff them with `verify_sources`
- full detail in [Rate limits](#rate-limits)

### Layout

- `connectors/` one client per system, nothing above talks to a system directly
	- `credentials.py` the single seam where credentials resolve
	- `warehouse/` Postgres, read only, verified TLS
	- `klaviyo/`
		- `client.py` transport, auth, retry, pagination, pacing
		- `campaigns.py` campaign values, plus the names
		- `flows.py` flow values and flow series, with metadata cached per process
		- `config.toml` the reporting clock, the conversion metric, the statistics asked for
- `tools/klaviyo/` every verb that affects a number
	- `source.py` picks the warehouse or the API backend
	- `warehouse.py` the SQL and the day-to-period rollup
	- `daily.py` day-grain loaders on the reporting clock
	- `transforms.py` normalize names, classify engaged/unengaged, drop wholesale
	- `metrics.py` totals and rates
- `reporting/klaviyo/` the command line interface and the output
	- `daily_report.py` the CLI
	- `daily_render.py` the PDF, through WeasyPrint
	- `general_report.py` the period summary
	- `verify_sources.py` warehouse against API

## Running it

```
uv run python -m reporting.klaviyo.daily_report <start> <end>
```

- from the repo root
- modes
	- `<start> <end>` one period
	- `yoy <start> <end>` against the same weekdays 364 days earlier
		- 364, not 365, so every day pairs with the same weekday
	- `compare <start> <end> <prior_start> <prior_end>` against any other period
	- `full <start> <end>` against both the week before and a year before
- flags
	- `--pdf [PATH]` also write the PDF, default `reporting/klaviyo/reports/<title>.pdf`
	- `--title TEXT` override the report title
	- `--labels A,B` override the period labels
- example

```
uv run python -m reporting.klaviyo.daily_report yoy 2026-10-19 2026-10-25 --pdf
```

### The API escape hatch

- `KLAVIYO_REPORT_SOURCE=api` bypasses the warehouse entirely and reads everything from Klaviyo
	- slow, and it spends the day's call budget
	- but it needs no database

## What the numbers mean

### Definitions

- **reporting day**: midnight to midnight at a fixed UTC-7, all year
	- Klaviyo reads a timeframe's dates on that clock and ignores any offset in the string
	- that is Pacific during daylight time, and 11 PM to 11 PM Pacific during standard time
- **revenue**: Klaviyo-attributed Placed Order value
	- the metric ID is set in `connectors/klaviyo/config.toml`
- **campaign**: one send to one segment
	- the same email to five segments is five campaigns
- **concept**: a campaign name with its segment parenthetical stripped
	- so five segment versions of one email count as one idea
- **flow**: a standing rule that sends continuously, so it has no send date
	- flow figures are dated by the day of the event
- **unique opens**: people who opened, not total open events
- **engaged / unengaged**: read from the segment parenthetical in the campaign name
	- not from Klaviyo's segment definition
- **delivered**: recipients minus bounces
	- campaign rates are over recipients
	- flow rates are over delivered

### Caveat 1. Unique people do not add across days

- the warehouse holds one row per message per day
- someone who clicks an SMS link on Monday and again on Thursday is
	- one person to Klaviyo
	- two to a sum of the two days
- measured over September 2026, summing days read SMS flow unique clicks 8.1% high, while delivered and revenue matched exactly
- affected, and only on flows
	- `opens_unique`
	- `clicks_unique`
	- `conversion_uniques`
	- plus the rates built on them: flow open rate, click rate, conversion rate
- unaffected, these sum correctly
	- recipients, delivered, bounced, revenue, unsubscribes, spam
	- every campaign metric, because a campaign sends once and the warehouse holds one row for it
- the consequence
	- any period longer than one day needs a Klaviyo API key, not just a database connection
	- `tools/klaviyo/warehouse.py` leaves those three columns empty for a message that ran on more than one day
	- `tools/klaviyo/source.py` fills them with one API call per period
	- the fill raises rather than returning a gap: the columns are nullable, and a silent sum over them would quietly undercount

### Caveat 2. Revenue is still moving for about a week

- Klaviyo attributes orders to a send for several days after it goes out
- a report run inside seven days of the period end is reading numbers that will rise
- the report says so on the page

### Caveat 3. Older days are frozen

- the warehouse re-reads only a trailing window
- a day more than a week old holds whatever it was worth when it was last read
- against the API the number would still be catching up; here it has stopped for good

### Caveat 4. Flow recipients read about 1% low

- measured against the Klaviyo UI for August 2026
	- revenue matched to the cent on 11 of 13 flows
	- conversions matched on 12 of 13
	- seven flows were short on recipients by 0.2% to 2.1%
- recipients cannot change after a send, so those are message-days that never landed
- campaign figures matched exactly

### Caveat 5. Scope

- sent campaigns only
- wholesale (WS) excluded
- SMS has no open tracking, so SMS shows click rate only

### Caveat 6. Rates are never averaged together

- campaign and flow rates are reported separately
- blending them hides which one moved

### Checking it

```
uv run python -m reporting.klaviyo.verify_sources <start> <end> [tolerance]
```

- diffs every summary metric between the warehouse and the API
- exits non-zero on drift, default tolerance 0.5%
- costs a handful of reporting calls against the day's 225, all on the API side, so it is not for a schedule
- a single day is the control
	- at that grain the two sources are the same number by construction
	- so a day that disagrees means something other than the rollup is wrong

## Rate limits

- Klaviyo rations the API by family, and the families do not behave alike
- all of them sit behind one key, so every process using that key shares the budget

| Family | Method | Limit |
| --- | --- | --- |
| records (campaigns, flows, profiles) | GET, cursor paginated | hundreds a minute |
| reports (campaign values, flow values, flow series) | POST | **2 a minute, 225 a day** |
| counts (metric aggregates) | POST | 60 a minute |

- the report budget, not the amount of data, is what shapes everything here
- a report call costs the same whatever it returns, so the client asks for the widest window and every statistic at once rather than making more calls

### The client paces itself

- `connectors/klaviyo/client.py` holds
	- 31 seconds between report calls
	- 1.1 seconds between count calls
- it stays under the steady rate instead of discovering the limit through 429s
- waiting on purpose is faster than being throttled
	- a 429 costs whatever `Retry-After` says, usually 60 seconds
	- and the call still has to be made afterwards
- this is why a report that touches the API takes minutes rather than seconds
	- the time is the pacing, not the work
- retry still exists underneath, because pacing only controls this process
	- **429** waits out Klaviyo's own `Retry-After` header, not a fixed ladder
		- a short retry would land inside the same window and be refused again
	- **500, 502, 503, 504, 524** back off 5, 15, 30, 60, 120 seconds
	- **dropped sockets and timeouts** take the same ladder
		- a connection error never reaches a status code, so without this it escapes the retry path every other transient fault goes through, and kills the run
	- five attempts, then it raises

### One client per process

- `get_client()` returns a shared instance
- the pacing state lives on the client, so two of them would each hold two calls a minute and together break the limit
- the limit is per account, not per process
	- a scheduled job, someone running a report by hand, and the Klaviyo UI all draw on the same 225
	- if a report starts getting throttled despite the pacing, something else is spending the budget

### How many calls a report costs

A flow report is one call per 100 flows, because `contains-any` caps its id list
at 100. On an account with 220 flows that is 3 calls, and the unique fill runs
once for email and once for SMS, so it is 6 rather than 1.

| Run | Report calls | Wall clock |
| --- | --- | --- |
| warehouse, single day | 0 | instant |
| warehouse, any longer period | about 6, to fill the flow unique counts | about 3 minutes |
| warehouse, `yoy` or `compare` | about 12 | about 6 minutes |
| `KLAVIYO_REPORT_SOURCE=api`, one period | about 12 | about 6 minutes |
| `verify_sources` | the API cost above, for one period | |

- measured on an account with 220 flows; halve the flows and you halve the calls

### Silent failures to know about

- two ways this API answers wrongly without erroring, both of which have happened here
	- a flow report sent **without an explicit flow id filter** returns a subset rather than an error
		- measured once at about 77% short
		- every flow id is passed explicitly for this reason
	- a segment series report with the segment passed as a **relationship** instead of a filter ignores it, and answers with another segment's numbers
- report responses are parallel arrays, one entry per grouping
	- so a truncated answer looks exactly like a complete one: fewer groupings, every array still the right length, nothing to notice
	- the client follows continuations, and raises rather than returning a partial when a response says there is more and offers no cursor to reach it
	- a wrong number that looks right is worse than no number

### API revision

- Klaviyo pins behaviour to a dated revision sent on every request
- a revision stays stable for a year, then deprecates over the next year
- omitting it or sending an unknown value is an error, not a fallback
- the current one is `DEFAULT_REVISION` in `connectors/klaviyo/client.py`
	- `KlaviyoAPI(revision=...)` overrides it
