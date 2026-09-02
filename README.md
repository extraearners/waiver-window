# Waiver Window

A personal roster-management tool for Yahoo Fantasy Football.

Waiver Window reads my roster, league settings, and free-agent pool; ranks
available players against my current lineup using historical and projected
stats; and then executes the add/drop transactions I have pre-approved, at the
time my league's waivers clear.

I live outside the United States. My leagues clear waivers at a time that falls
in the middle of the night locally. This tool exists so that the pickups I have
already decided on get submitted at that time without me being awake for it.

**It does not decide anything on its own.** Every add, and the drop it is paired
with, is written down by me in advance. The tool executes a fixed, ordered list
and nothing else.

---

## Scope

- **Users:** 1 (the author)
- **Leagues:** 2 private Yahoo Fantasy Football leagues, my own teams only
- **Not distributed, not monetized, no third-party users**
- **No data resale, no bulk collection, no redistribution of Yahoo data**
- **Runs roughly once per week** during the NFL regular season

---

## How it works

### 1. Pick list

Targets live in a small table (a published Google Sheet, or a local CSV). I fill
it in before the waiver deadline, from my phone if I'm away from my computer.

| league | priority | add_player | drop_player | max_wait_min |
|---------|----------|--------------------|----------------|--------------|
| league_A | 1 | Jaylen Wright | Tyler Boyd | 10 |
| league_A | 2 | Bucky Irving | Tyler Boyd | 10 |
| league_B | 1 | Jaylen Wright | Zay Jones | 10 |

`priority` is a fallback order. If the first target is claimed by another manager
before the tool reaches it, it moves to the next one for that league, reusing the
same roster slot.

### 2. Preparation pass

Shortly before the waiver clear time, the tool:

- loads the pick list
- resolves each player name to a Yahoo player key
- refreshes the OAuth token
- verifies that every named `drop_player` is actually on my roster
- verifies that each `add_player` is currently unavailable (i.e. still on waivers)

If a row fails validation it is reported and skipped. It is never guessed at.

### 3. Execution

Waivers do not clear at a mathematically exact instant — Yahoo's processing takes
some minutes and the lag varies week to week. So the tool does not fire blindly
at a fixed timestamp. It polls each target's ownership status and submits the
transaction the moment that status changes to free agent.

```
poll  ->  status still "waivers"     ->  wait 250ms, poll again
poll  ->  status now "freeagents"    ->  submit add/drop
      ->  success                    ->  log, move to next league
      ->  rejected (someone else won) ->  advance to next priority
      ->  max_wait_min elapsed        ->  give up, log, notify
```

Polling is rate-limited, backs off on any error response, and stops entirely once
the list is resolved or the wait budget is spent.

### 4. Reporting

Every run writes a log line per attempt — timestamp, league, player, outcome —
and sends a summary notification so I can see what happened before I wake up.

---

## Yahoo API usage

The tool talks to the Yahoo Fantasy Sports API (`fantasysports.yahooapis.com/fantasy/v2`).

**Read endpoints used**

| Purpose | Endpoint |
|---|---|
| League settings, waiver rules | `/league/{league_key}/settings` |
| My roster | `/team/{team_key}/roster` |
| Free agent / waiver pool, ownership status | `/league/{league_key}/players;status=A` |
| Player metadata and season stats | `/league/{league_key}/players;player_keys=...;out=stats` |

**Write endpoint used**

| Purpose | Endpoint |
|---|---|
| Submit a pre-approved add/drop | `POST /league/{league_key}/transactions` |

Writes are limited to `add/drop` transactions on my own roster. The tool does not
propose trades, does not modify league settings, and does not act on any team
other than mine.

### Why read/write access is required

Read-only access covers the analysis half of this tool but not its purpose. The
reason the tool exists is to submit a transaction at a time I cannot be awake
for. Without `fspt-w` there is no way to complete that action, and the tool
reduces to a projections viewer I have no particular need for.

---

## Safeguards

- Credentials and OAuth tokens are stored locally and are never transmitted
  anywhere except to Yahoo.
- The tool refuses to run without a validated pick list. An empty or malformed
  list is a no-op, not a fallback to autonomous behaviour.
- Every write is bounded: at most one successful add/drop per league per run.
- A hard cap on total requests per run, with exponential backoff on `4xx`/`5xx`.
- A dry-run mode (`--dry-run`) that performs the full polling and matching path
  and logs the transaction it *would* submit, without submitting it.

---

## Configuration

```bash
cp .env.example .env      # add Yahoo client ID and secret
cp picks.example.csv picks.csv
python -m waiver_window.auth        # one-time OAuth consent
python -m waiver_window.run --dry-run
```

Scheduling on macOS is handled by a `launchd` job; see [docs/scheduling.md](docs/scheduling.md).

---

## Status

Active personal project. Read-only functionality is implemented against the
Yahoo Fantasy Sports API. The transaction-submitting path is written but gated
behind read/write API access, which is pending approval.

## License

MIT — see [LICENSE](LICENSE).
