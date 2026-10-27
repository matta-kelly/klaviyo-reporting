# Klaviyo Reporting

Email and SMS performance from a Klaviyo account, by day and by period, with
optional comparison against an earlier period. Console output always, PDF on
request.

Read only. It never writes to Klaviyo.

## Why it reads a warehouse

Klaviyo's reporting endpoints are rationed to 225 calls a day for the whole
account and two a minute steady, so a single week's report against the API
spends a minute or two mostly asleep and competes with every other report run
that day.

This reads the same numbers out of a Postgres warehouse, where a year costs the
same as a week. The API path still exists as the reference: when a warehouse
figure is doubted, run the same period both ways and diff them with
`verify_sources`.

## Install

```
uv sync
cp .env.example .env     # then fill it in
```

Needs Python 3.13.

## Run

From the repo root.

```
uv run python -m reporting.klaviyo.daily_report <start> <end>
```

### Modes

```
<start> <end>                                      one period
yoy <start> <end>                                  vs the same weekdays 364 days earlier
compare <start> <end> <prior_start> <prior_end>    vs any other period
full <start> <end>                                 vs both the week before and a year before
```

364 days, not 365, so every day pairs with the same weekday.

### Flags

```
--pdf [PATH]        also write the PDF; default reporting/klaviyo/reports/<title>.pdf
--title TEXT        override the report title
--labels A,B        override the period labels
```

### Example

```
uv run python -m reporting.klaviyo.daily_report yoy 2026-10-19 2026-10-25 --pdf
```

## Access it needs

| Variable | Why |
| --- | --- |
| `WAREHOUSE_HOST`, `WAREHOUSE_USER`, `WAREHOUSE_DB`, `WAREHOUSE_PASSWORD` | Every number. A read-only role with select on the `int` and `proc` schemas is enough. |
| `WAREHOUSE_PORT` | Optional, defaults to 5432. |
| `KLAVIYO_API_KEY` | The flow unique counts, for any period longer than a day. Also the whole reference path. |

A database connection on its own will not run this report. See the first caveat.

## What it expects in the warehouse

One table and two dimensions, already modelled:

- `int.int_klaviyo__message_day`, one row per message per day, carrying
  `source_type`, `channel`, `date`, `parent_id`, `message_id`, and the stat
  columns (`recipients`, `delivered`, `opens_unique`, `clicks_unique`,
  `bounced`, `unsubscribe_uniques`, `spam_complaints`, `conversion_uniques`,
  `revenue`)
- `proc.proc_klaviyo__campaigns`, one row per campaign, with `name`,
  `send_time` and `status`
- `proc.proc_klaviyo__flows`, one row per flow, with `name` and `status`

Days are Klaviyo reporting days, cut at a fixed UTC-7.

## Definitions

| Term | Meaning |
| --- | --- |
| Reporting day | Midnight to midnight at a fixed UTC-7, all year. Klaviyo reads a timeframe's dates on that clock and ignores any offset in the string. That is Pacific during daylight time, and 11 PM to 11 PM Pacific during standard time. |
| Revenue | Klaviyo-attributed Placed Order value. The metric ID is set in `connectors/klaviyo/config.toml`. |
| Campaign | One send to one segment. The same email to five segments is five campaigns. |
| Concept | A campaign name with its segment parenthetical stripped, so five segment versions of one email count as one idea. |
| Flow | A standing rule that sends continuously, so it has no send date. Flow figures are dated by the day of the event. |
| Unique opens | People who opened, not total open events. |
| Engaged / unengaged | Read from the segment parenthetical in the campaign name, not from Klaviyo's segment definition. |
| Delivered | Recipients minus bounces. Campaign rates are over recipients. Flow rates are over delivered. |

## Caveats

### 1. Unique people do not add across days

The warehouse holds one row per message per day. Someone who clicks an SMS link
on Monday and again on Thursday is one person to Klaviyo and two to a sum of
the two days. Measured over September 2026, summing days read SMS flow unique
clicks 8.1% high while delivered and revenue matched exactly.

Three columns are affected, and only on flows:

```
opens_unique
clicks_unique
conversion_uniques
```

Plus the rates built on them: flow open rate, click rate, conversion rate.

Everything else sums correctly: recipients, delivered, bounced, revenue,
unsubscribes, spam. Every campaign metric is unaffected, because a campaign
sends once and the warehouse holds one row for it.

So any period longer than one day needs a Klaviyo API key, not just a database
connection. `tools/klaviyo/warehouse.py` leaves those three columns empty for a
message that ran on more than one day, and `tools/klaviyo/source.py` fills them
with one API call per period. The fill raises rather than returning a gap: the
columns are nullable, and a silent sum over them would quietly undercount.

### 2. Revenue is still moving for about a week

Klaviyo attributes orders to a send for several days after it goes out. A report
run inside seven days of the period end is reading numbers that will rise. The
report says so on the page.

### 3. Older days are frozen

The warehouse re-reads only a trailing window, so a day more than a week old
holds whatever it was worth when it was last read. Against the API the number
would still be catching up. Here it has stopped for good.

### 4. Flow recipients read about 1% low

Measured against the Klaviyo UI for August 2026: revenue matched to the cent on
11 of 13 flows and conversions on 12 of 13, but seven flows were short on
recipients by 0.2% to 2.1%. Recipients cannot change after a send, so those are
message-days that never landed. Campaign figures matched exactly.

### 5. Scope

Sent campaigns only. Wholesale (WS) is excluded. SMS has no open tracking, so
SMS shows click rate only.

### 6. Rates are never averaged together

Campaign and flow rates are reported separately. Blending them hides which one
moved.

## Checking it

```
uv run python -m reporting.klaviyo.verify_sources <start> <end> [tolerance]
```

Diffs every summary metric between the warehouse and the API and exits non-zero
on drift. Default tolerance 0.5%. Costs a handful of reporting calls against the
day's 225, all on the API side, so it is not for a schedule.

A single day is the control. At that grain the two sources are the same number
by construction, so a day that disagrees means something other than the rollup
is wrong.

## Layout

```
connectors/            one client per system; nothing above talks to a system directly
  credentials.py       the single seam where credentials resolve
  warehouse/           Postgres, read only, verified TLS
  klaviyo/             the API client, rate limited
tools/klaviyo/         every verb that affects a number
  source.py            picks the warehouse or the API backend
  warehouse.py         the SQL and the day-to-period rollup
  daily.py             day-grain loaders on the reporting clock
  transforms.py        normalize names, classify engaged/unengaged, drop wholesale
  metrics.py           totals and rates
reporting/klaviyo/     the command line interface and the output
  daily_report.py      the CLI
  daily_render.py      the PDF, through WeasyPrint
  general_report.py    the period summary
  verify_sources.py    warehouse against API
```

Set `KLAVIYO_REPORT_SOURCE=api` to bypass the warehouse entirely and read
everything from Klaviyo. Slow, and it spends the day's call budget, but it
needs no database.
