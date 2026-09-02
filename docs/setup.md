# Setup

## Prerequisites

- Python 3.9+
- A Yahoo Developer Network application with Fantasy Sports access
  (read/write if you intend to submit transactions)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

```bash
cp .env.example .env
```

Fill in `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET` from your YDN application.

Then authorise against your own Yahoo account:

```bash
python -m waiver_window.auth
```

This stores `tokens.json` locally with mode 0600. It is gitignored.

## League keys

```bash
python -m waiver_window.leagues
```

Prints the league keys (`{game_id}.l.{league_id}`) and team keys for every
league on the authenticated account. Put the league keys in `LEAGUE_KEYS` and
map your friendly aliases — the names in the `league` column of the pick list —
to those keys.

## Pick list

```bash
cp picks.example.csv picks.csv
```

Edit before each waiver deadline. To edit from a phone instead, put the same
columns in a Google Sheet, use `File > Share > Publish to web > CSV`, and set
`PICKS_SOURCE` to the published URL. No credentials are needed for that path —
the sheet is fetched as plain CSV over HTTPS.

## Verify

```bash
python -m waiver_window.run --dry-run -v
```

Nothing is submitted. Check the log for resolved player keys and the
transaction the tool would have sent.

---

## Browser backend

Works without waiting on Yahoo's API review. Uses a session you authenticate
yourself.

```bash
pip install playwright
playwright install chromium
python -m waiver_window.login
```

`login` opens a visible browser at Yahoo Fantasy and waits. Sign in there, by
hand, then press Enter in the terminal. Cookies are saved to
`storage_state.json` (mode 0600, gitignored).

**The script never asks for or handles a password.** It reads the cookies Yahoo
issued to your own sign-in. Treat the saved file like a password — anyone with
it is logged in as you. Delete it to revoke.

### Calibrating selectors

Yahoo's markup changes without notice, so selectors live in `selectors.json`.
To see what they actually match:

```bash
python -m waiver_window.probe "Justin Jefferson"
```

Read-only — it clicks nothing. It prints each matched row, the raw status text,
and how that text was mapped. If names or statuses look wrong, edit
`selectors.json` and run it again. No code change needed.

### Session expiry

Yahoo sessions do not last forever. If `probe` reports a redirect to sign-in,
re-run `login`. Worth checking on a Sunday rather than discovering it at
midnight on Wednesday.
