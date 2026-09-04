# Running it on a waiver night

## Before you start

Confirm the saved Yahoo session still works. Sessions expire; find out on a
quiet evening, not at midnight.

```bash
.venv/bin/python -m waiver_window.probe --diagnose
```

Redirected to sign-in? Re-run `python -m waiver_window.login`.

## Tuesday, before 11:59pm PT

1. **Make your waiver claims by hand, in Yahoo.** The tool does not make claims
   and will refuse to. It only covers the free-agent window afterwards.

2. **Fill in `picks.csv`.** It is deliberately left empty between weeks so a
   stale list can never fire.

   ```csv
   league,priority,add_player,drop_player,max_wait_min
   league_A,1,Some Player,Player To Drop,25
   league_A,2,Backup Target,Player To Drop,25
   league_B,1,Another Player,,25
   ```

   - `priority` — fallback order within a league. First one that comes free wins.
   - `drop_player` — leave blank only if that roster genuinely has an open slot.
   - `max_wait_min` — 25 covers a start at 11:50 plus lag after midnight.
   - Names must match Yahoo's spelling. `prepare` checks this and tells you.

   To edit from a phone instead, put the same columns in a Google Sheet,
   `File > Share > Publish to web > CSV`, and set `PICKS_SOURCE` in `.env` to
   the published URL.

3. **Dry run it.** Resolves every name, walks the whole path, submits nothing.

   ```bash
   .venv/bin/python -m waiver_window.run --backend browser --dry-run -v
   ```

   Every row should reach `Ready:` with an apid, and a dpid unless it is an open
   slot. A row that does not appear was skipped — the log says why. Fix the
   sheet and run it again.

## At 11:50pm PT

```bash
.venv/bin/python -m waiver_window.run --backend browser --headed --fast-from 00:00 -v
```

Polls every 15s until 11:59, then every 400ms. Adds on the flip to free agent.
Stops after `max_wait_min`. `--headed` lets you watch; drop it once you trust it.

**This makes real roster moves.** There is no `--dry-run` on that line.

## Afterwards

`logs/waiver-window.log` has a line per attempt. Outcomes:

| | |
|---|---|
| `WON` | added |
| `LOST` | someone was faster; it moved to your next priority |
| `CLAIMED` | won on waivers by another manager, never became free |
| `TIMEOUT` | never came free inside the window |
| `BLOCKED` | a gate refused — it would have been a waiver claim |

## Unattended

Only once you have watched it work at least once. See `docs/scheduling.md` for
the `launchd` job and the `pmset` wake.
