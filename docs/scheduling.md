# Scheduling on macOS

The tool needs to be running a couple of minutes before the league's waiver
clear time. Two pieces: wake the machine, then start the job.

## 1. Wake the Mac

`launchd` will not wake a sleeping machine on its own, so schedule a wake
separately. For a Wednesday 00:00 clear time, waking at 23:50 the night before:

```bash
sudo pmset repeat wake TW 23:50:00
```

Verify with `pmset -g sched`.

If the Mac is set to stay awake permanently this step is unnecessary, but it is
cheap insurance against a sleep setting changing later.

## 2. Schedule the job

Save as `~/Library/LaunchAgents/com.local.waiver-window.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.local.waiver-window</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>bash</string>
    <string>-lc</string>
    <string>cd ~/waiver-window &amp;&amp; .venv/bin/python -m waiver_window.run --backend browser</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>2</integer>
    <key>Hour</key><integer>23</integer>
    <key>Minute</key><integer>52</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>/tmp/waiver-window.out</string>
  <key>StandardErrorPath</key>
  <string>/tmp/waiver-window.err</string>
</dict>
</plist>
```

`Weekday 2` is Tuesday — the job starts Tuesday night and polls across midnight
into Wednesday.

```bash
launchctl load ~/Library/LaunchAgents/com.local.waiver-window.plist
launchctl list | grep waiver-window
```

## 3. Clock accuracy

Polling is self-correcting, but a badly skewed clock still wastes the head of
the window. Confirm NTP sync is on:

```bash
sudo systemsetup -getusingnetworktime
```

## 4. Test before you rely on it

Run a dry run at an arbitrary time first. It walks the full path — pick list,
key resolution, polling, match — and logs the transaction it would have sent.

```bash
python -m waiver_window.run --backend browser --dry-run -v
```
