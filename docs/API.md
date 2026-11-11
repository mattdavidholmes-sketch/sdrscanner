# Data feeds (HTTP API)

The dashboard is one small web server (`dashboard2.py`, port 8765). Every page it serves - the desktop page, the phone page
`/m`, the small live window `/live` - is only a consumer of the feeds below. A native iPad / iPhone app can use the same feeds;
nothing in them is HTML.

Written from the code on 2026-11-11 (v188). All feeds are `GET` and return JSON unless noted. "Takes the radio" means the call
retunes the one dongle, so it interrupts whatever the radio was doing.

## Rules for any client

- **One radio.** A listen always wins over the background sweep, and the app refuses a second command while the dongle is still
  switching. Send one command at a time and wait for the answer.
- **Read-only feeds are safe to poll.** Anything marked *changes state* or *takes the radio* is a button, not a poll.
- **Local network is trusted, the internet is not.** A request from outside the private address ranges (or through a proxy) is
  sent to `/login` and needs the access password; the answer sets a cookie. Remote access is off unless a password is set.
- **Times** are unix seconds unless a field says otherwise; the server's clock and time zone are the reference.

## Status and lists (read-only)

| Feed | What it returns |
|---|---|
| `/api/state` | Everything the main list shows: `hits` (every banked channel with name, category, mode, signal, last heard, voice verdict), `bands` being swept, sweep state, player state, recordings count, dongle health. Poll every 2 to 4 s. |
| `/meter` | Lightweight "what is playing now": frequency, name, audio level, squelch open, monitor / scanner state, SDRTrunk state. Poll up to 4 times a second while listening. |
| `/api/spectrum?band=<key>` | Latest sweep of one band: `x` (MHz), `y` (dB), `floor`, `lo`, `hi`, `when`, `labels` (known channels in range). Band keys come from `bands[].key` in `/api/state`. |
| `/api/nets` | Ham nets: `now`, `next`, `week` (seven days), `events` (club dates), `clubs`, `sources`, `checked`, `stale`. Each item has start / end, frequency, offset, tone, club, note, confidence, and for a net that is on, whether a signal has been seen since it began. Worked out from `nets.json`. |
| `/api/directory` | Every known channel (airports, repeaters, FRS / GMRS, marine, ...) with programming details and net notes. |
| `/api/recordings` | Recent recordings: file, time, frequency, name, seconds, transcript. Audio at `/rec/<file>`. |
| `/api/recent_text` | Aircraft callsigns named in today's airband transcripts: callsign -> "HH:MM on <channel>". |
| `/api/aircraft` | Aircraft within range from the configured ADS-B source: position, altitude, type, route, phase, likely channel(s) and why, plus `voice` (minutes since speech was confirmed per airband channel). |
| `/api/aircraft/today` | Aircraft passes logged today. |
| `/api/history?mhz=<f>` | Activity history of one channel: 24 h series and 7 day hourly pattern. |
| `/info?mhz=<f>` | What is known about one frequency (licence lookup, service, notes). |
| `/api/map` | Map data: home position, range rings, pins. |
| `/api/radio` | Broadcast FM stations for the audio test. |
| `/api/digital` | Digital systems: P25 control channels and sites, DMR channels, the SDRTrunk playlist. |
| `/api/sdrt/calls` | Last 200 calls SDRTrunk decoded (talkgroup, time, channel). |
| `/api/sdrt/talkgroups`, `/api/sdrt/units` | Talkgroups and radio units heard. |
| `/api/sdrt/solo/state` | Which talkgroup is singled out ("hone in"), if any. |
| `/api/sirens/status` | State of the "I hear sirens" sequence. |
| `/api/scanner` | Scanner / monitor state (bank, hold, lockouts). |
| `/api/uv32` | Which memory channel the handheld has for each frequency (from the last CHIRP export). |
| `/api/sound` | Mac audio output state. |
| `/api/alerts` | Alert rules and the alert log. |
| `/debug` | Processes, flags and recent log lines (for the Debug tab). |

## Listening (takes the radio)

| Call | What it does |
|---|---|
| `/stream?raw=1&mhz=<f>&mode=<fm\|am\|monitor>&sq=<auto\|0-100>&voice=<0\|1>&name=<text>` | Tunes and streams audio for as long as the connection is open. With `raw=1` the body is 16-bit signed little-endian mono PCM at 24 000 samples per second, no header. `mode=monitor` scans a bank (`cat=<bank>`). Closing the connection stops the listen. |
| `/playmac?...` | Same parameters, but the audio plays through the Mac's own output. Local network only. |
| `/stop` | Stops the current listen and gives the radio back to the sweep. |
| `/scanner/hold`, `/scanner/resume`, `/scanner/skip`, `/scanner/lockout` | Scanner keys while a monitor listen is running. |
| `/scanner/autoscan?bank=<name>` | Scan a bank by itself whenever nobody is using the radio (empty = off). |
| `/api/sdrt/play?target=<p25:...\|dmrt:...>` | Starts SDRTrunk on a digital system. Its audio is at `/sdrt_audio` (MP3 stream relayed from the local Icecast). |
| `/api/sdrt/dmrscan?on=<0\|1>&kind=<p25\|dmr>&step=<s>` | Rotates SDRTrunk through all sites of a kind. |
| `/api/sdrt/stop` | Stops SDRTrunk. |
| `/api/sdrt/solo?tg=<id>` | Single out one talkgroup (0 = hear everything). Restarts SDRTrunk (about 3 s). |

## Other actions (change state)

| Call | What it does |
|---|---|
| `/api/scan?cat=<category>` | Starts a sweep of that category's bands (queued while someone is listening). |
| `/mode?m=<scan\|listen>` | Switches between sweeping and listening. |
| `/api/sirens` | Starts the "I hear sirens" sequence (fire / EMS first). |
| `/api/wide?mhz=<f>&on=<0\|1>` | "Open more" for one channel: wider squelch and longer hang. |
| `/api/forget?mhz=<f>` | Removes one channel from the bank. |
| `/api/dmr/alias?mhz=<f>&tg=<n>&name=<text>` | Names a DMR talkgroup (empty name = forget). |
| `/api/alerts?add=<mhz\|cat\|kw>&value=<text>` | Adds an alert rule (a frequency, a category or a keyword). Without parameters it only lists. |
| `/api/map/home?lat=<>&lon=<>` | Sets the home position. |
| `/api/chirp` | Builds and downloads the CHIRP file for the handheld (CSV). |
| `/api/playlist`, `/api/sdrtrunk` | Rebuild the SDRTrunk playlist / hand the dongle to SDRTrunk's own window. |
| `/api/macbeep` | Plays a test sound on the Mac. |
| `/api/restart?force=<0\|1>` | Restarts the dashboard, by default only once nobody is listening. |
| `/delrec?f=<file>` | Deletes one recording. |

## Pages and static files

`/` desktop page, `/m` phone page, `/live` small live window, `/login` password form, `/manifest.webmanifest` and the icon
files (`/apple-touch-icon.png`, `/icon-192.png`, `/icon-512.png`, `/favicon.ico`) so the page installs to a home screen with
the app's own icon and opens full screen.

## What a native app still needs from the backend

1. **Audio in a format iOS plays natively in the background.** `/stream` is raw PCM, which a web page can play only while it
   is in the foreground. A native app can play it with its own audio engine; a compressed stream (AAC or Opus) would cut the
   data use for listening away from home.
2. **One versioned prefix** (`/api/v1/...`) so the page and an app can evolve separately. Today the feeds are unversioned and a
   few older ones (`/meter`, `/stream`, `/stop`, `/mode`, `/info`, `/debug`) sit outside `/api/`.
3. **Push instead of poll** for state (`/api/state`, `/meter`): server-sent events or a WebSocket, which also saves battery.
4. **Sign-in for use away from home.** The password cookie exists; an app wants a token it can keep in the keychain, and the
   server needs HTTPS in front of it (a tunnel or reverse proxy) before it is exposed.
5. **Notifications** (a net starting, an alert rule firing) need a sender that runs when no page is open.
