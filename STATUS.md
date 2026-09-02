# Where this stands

Last worked: 2026-09-01, evening PT.

## Working, verified against the live site

- Yahoo session capture (`login`) — cookies saved 0600, gitignored
- League map: `league_A` = 49039 team 6 (DidjaMothaTeachyaThoseMannas?),
  `league_B` = 833119 team 7
- Full player scan: 525 players over 22 pages via `all_players_page`
- Name → `apid` resolution (Cyrus Allen → 42773)
- Pick list, including a blank `drop_player` for an open roster slot
- Both refusal gates, exercised against a real waiver page:
  status read, and the page heading Yahoo serves
- Two-speed polling around `--fast-from`

## Not verified

**The submit control.** `_find_submit` falls back to `button:not([type])`
inside the acquisition form. Nobody has seen what that actually picks. No live
run should happen until the `--- what _find_submit would pick ---` section of
`probe --add` has been read. This is the one thing blocking a real pickup.

**The success path.** Everything proven so far is refusal. No add has ever been
submitted. Cannot be proven until a target is genuinely a free agent.

## Open problem

`available_page` (`status=A`) returned 25 players early in the evening and then
none, with `rendered=False` — the player links never appeared within 8s. Cause
unknown. Candidates: throttling after the dry run's several hundred requests,
an interstitial, a layout change, or a genuinely empty list.

`probe --diagnose` was written to tell these apart and **has not been run yet**.
That is the next command.

Note the race path does not depend on this page — `prepare` uses
`all_players_page` and polling uses each target's own acquisition page. But an
unexplained empty scan is not something to leave sitting there.

## Next steps, in order

1. `python -m waiver_window.probe --diagnose`
2. Fix whatever that reveals
3. `python -m waiver_window.probe --add "<a real free agent>"` — read the
   submit-control section, correct `submit_add` in `selectors.json`
4. Supervised live run: `run --backend browser --headed --fast-from 00:00 -v`
   on a player actually wanted. This is the first real write.
5. Only then: `launchd` + `pmset` scheduling per docs/scheduling.md

## Yahoo API

Read/write access applied for 2026-09-01. Yahoo said 1-2 weeks. Nothing has
been granted yet, not even read — so the browser backend is currently the only
working path, not a fallback. `--backend api` is written but untested against a
live token.

## Waiver mechanics, confirmed

Claims lock 11:59pm PT Tuesday and process after. Unclaimed players convert to
free agents and go first-come. This tool covers only that second window; claims
are made by hand. `W (Sep 2)` in the roster-status column means the player
becomes available at 12:00am PT on Sep 2.
