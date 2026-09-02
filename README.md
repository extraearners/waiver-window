# Waiver Window

A personal roster tool for Yahoo Fantasy Football.

In my leagues, waiver claims lock at 11:59pm PT on Tuesday. Shortly after that
they process, and every player nobody claimed converts to a free agent — first
manager to add them gets them.

I make my waiver claims by hand, before the deadline. What I cannot do is be
awake for the free-agent window that opens minutes later: I live outside the
United States and it lands in the middle of my night. This tool watches that
window for me and adds a player from a list I wrote earlier the same evening.

**It never submits a waiver claim.** Adding a free agent costs nothing;
submitting against a player who is still on waivers would spend a waiver
claim I want to keep. So the tool only ever acts on a player Yahoo has
confirmed has ownership status `freeagents`, and refuses otherwise — including
when Yahoo does not report a status at all.

**It decides nothing.** Every add, and the drop it is paired with, is written
down by me in advance. The tool executes a fixed, ordered list and nothing else.

---

## Scope

- **Users:** 1 (the author)
- **Leagues:** 2 private Yahoo Fantasy Football leagues, my own teams only
- **Writes:** free-agent add/drop on my own roster. Nothing else.
- **Never:** waiver claims, trades, league settings, other managers' teams
- **Not distributed, not monetized, no third-party users, no data resale**
- **Runs roughly once per week** during the NFL regular season

---

## How it works

### 1. Pick list

Targets live in a small table — a local CSV, or a published Google Sheet so I
can edit it from my phone before the deadline.

| league | priority | add_player | drop_player | max_wait_min |
|---------|----------|--------------------|----------------|--------------|
| league_A | 1 | Jaylen Wright | Tyler Boyd | 10 |
| league_A | 2 | Bucky Irving | Tyler Boyd | 10 |
| league_B | 1 | Jaylen Wright | Zay Jones | 10 |

`priority` is a fallback order. If the first target is taken by another manager,
the tool moves to the next one for that league, reusing the same roster slot.

### 2. Preparation pass

Before the window opens, the tool:

- loads and validates the pick list
- resolves every player name to a Yahoo player key, requiring an exact match
- confirms each `drop_player` is actually on my roster
- refreshes the OAuth token

A row that fails any of these is reported and skipped. It is never guessed at.
Doing this early means a typo surfaces while I can still fix it, rather than
mid-race.

### 3. The window

Waivers do not process at a mathematically exact instant — Yahoo takes some
minutes and the lag varies week to week. So the tool does not fire at a fixed
timestamp. It polls its targets' ownership status and acts on the change.

```
for each target, in priority order:
    status == "waivers"     -> not mine to take yet, keep watching
    status == "team"        -> someone claimed them, drop this target
    status == "freeagents"  -> add immediately, dropping the paired player
        accepted            -> done for this league
        rejected (too slow) -> advance to the next priority
    max_wait_min elapsed    -> give up, log it
```

All targets in a league are checked each pass rather than blocking on the
first, so a lower-priority player who frees up early is not missed while
waiting on one that never clears.

Polling is rate-limited, backs off on any error response, and stops as soon as
the list resolves or the wait budget is spent. At most one successful add/drop
per league per run.

### 4. Reporting

A log line per attempt — timestamp, league, player, outcome — so I can see what
happened before I wake up.

---

## Two backends

The decision of *what* to do never depends on *how* it is done. Both paths take
the same validated pick list and pass through the same free-agent gate.

| | `--backend api` | `--backend browser` (default) |
|---|---|---|
| Reaches Yahoo via | Fantasy Sports API | a real signed-in browser session |
| Needs | `fspt-w`, granted by manual review | a session captured once by hand |
| Speed | ~200ms per action | ~1-2s per action |
| Fragility | stable, versioned | breaks when Yahoo changes markup |

The browser path exists because the API's write scope is granted by review and
may not arrive in time. It reuses cookies from a sign-in the author performed
themselves; no password is handled, requested, or stored by this code. The saved
session file is written 0600 and gitignored.

Selectors for the browser path live in `selectors.json`, not in code, and
`python -m waiver_window.probe` reports what they actually matched on the live
page so they can be corrected without guessing.

## Yahoo API usage

`fantasysports.yahooapis.com/fantasy/v2`

**Read**

| Purpose | Endpoint |
|---|---|
| Discover the current season's game id | `/users;use_login=1/games;game_keys=nfl/leagues` |
| My roster | `/team/{team_key}/roster` |
| Resolve a player name to a key | `/league/{league_key}/players;search=...` |
| Ownership status (the poll) | `/league/{league_key}/players;player_keys=...;out=ownership` |

**Write**

| Purpose | Endpoint |
|---|---|
| Add a confirmed free agent, dropping a named player | `POST /league/{league_key}/transactions` |

The transaction body is `add/drop` and carries no `faab_bid` — by design, since
the tool never participates in waivers.

### Why read/write access is required

Read alone covers the watching half but not the point. The tool exists to
complete an add at a time I cannot be awake for. Without `fspt-w` there is no
way to finish that action.

---

## Safeguards

- **Free-agent gate.** `transactions.assert_free_agent` refuses any player not
  confirmed as `freeagents`. An unknown or missing status is treated as "still
  on waivers" and declined — it fails closed.
- **Explicit pairing.** Every pickup names the player it replaces. The tool
  will not choose a drop.
- **Bounded writes.** At most one successful add/drop per league per run.
- **Request budget.** A hard cap on total API calls per run, with exponential
  backoff on `429` and `5xx`.
- **Dry run.** `--dry-run` walks the whole path — resolution, polling,
  matching — and logs the transaction it would have sent, without sending it.
- **Local credentials.** Tokens are stored at mode 0600, gitignored, and go
  nowhere except Yahoo's token endpoint.

---

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env             # Yahoo client id and secret
cp leagues.example.json leagues.json
cp picks.example.csv picks.csv

cp selectors.json selectors.json   # already present; edit if Yahoo changes

# Browser path - works today, no API approval needed
playwright install chromium
python -m waiver_window.login      # sign in yourself; cookies saved locally
python -m waiver_window.probe "Justin Jefferson"
python -m waiver_window.run --backend browser --dry-run -v

# API path - once Yahoo grants read/write
python -m waiver_window.auth
python -m waiver_window.leagues
python -m waiver_window.run --backend api --dry-run -v
```

Full notes in [docs/setup.md](docs/setup.md). Scheduling on macOS via `launchd`
and `pmset` in [docs/scheduling.md](docs/scheduling.md).

---

## Status

Active personal project. The pick list, name resolution, polling loop and
free-agent gate are implemented and exercised, and the browser backend runs
end to end. The API backend's submitting call is written but gated behind
read/write access, which is pending Yahoo's review.

## License

MIT — see [LICENSE](LICENSE).
