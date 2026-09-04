# Where this stands

Last worked: 2026-09-03, evening PT.

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

Also verified 2026-09-03, against a genuine free agent (Cyrus Allen, apid
42773, after he cleared waivers):

- `classify_page` returns `freeagents` on a real add page
- the full dry run reaches submit and reports
  `WON [league_A #1] +Cyrus Allen (open slot)`
- the empty-page problem is solved — it was never Yahoo. `wait_for_selector`
  defaults to waiting for *visibility*, and Yahoo's table has player links that
  are attached but not visible, so the wait timed out on a page that had loaded
  fine. Both waits now check attachment, and neither can empty a scan: pages
  are indexed regardless and judged by the players found.

## Not verified

**No add has ever been submitted.** Everything proven is either a refusal or a
dry run. The submit path itself is reasoned from the page's structure, not
observed working:

- the acquisition form has no submit control; Yahoo posts it from script, so
  the tool calls `form.submit()` directly. The form carries its own hidden
  `stage`, `crumb` and `apid`.
- drops are scripted trigger buttons over hidden inputs, so the trigger is
  clicked rather than the input checked.

Both are sound readings of the live DOM, and neither has actually run. The
first live run should be `--headed`, watched, on a player genuinely wanted.

## State it was left in

`picks.csv` is **empty on purpose**. The tool refuses to run without an
explicit list, so nothing can fire on a stale name. Fill it in on the night.

`max_scan_pages` raised 24 -> 40 after a scan stopped exactly on the old cap at
600 players — truncated, not exhausted, which would silently miss a target
further down the list. That now logs a warning when it happens.

## Next steps, in order

1. Follow `RUNBOOK.md` on a Tuesday evening
2. First live run `--headed` and watched
3. Only after that works: `launchd` + `pmset` per `docs/scheduling.md`

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
