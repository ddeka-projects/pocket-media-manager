# Pocket Media Manager PC Helper

This helper runs on the Windows PC that stores your media. It scans your media
folder, chooses a weighted recommendation using your existing rules, serves the
chosen file over the local network, and accepts simple phone feedback.

Playback itself is delegated to VLC (phone) or mpv (PC). The helper is the
recommendation and streaming bridge, not a custom video player.

## How The Pieces Connect

1. Your phone opens the helper home page and taps `Recommend`.
2. The helper scans `MEDIA_ROOT`, loads `_mpv_prefs.json`, and picks a video.
3. The helper records play count and `last_played` immediately.
4. The helper creates a temporary stream token.
5. The chosen player opens: VLC on the phone via deep link, or mpv locally on
   the PC via subprocess. The browser stays on a feedback page.
6. After watching, you switch back to the browser and choose Like, Dislike,
   Pending, Skip, or Something Else.

Plex can stay installed for normal library playback, but this workflow does not
depend on Plex. It exists to preserve your custom recommendation behavior.

## Setup

From Git Bash:

```bash
cd C:\Dev\pocket-media-manager\pc-helper
./scripts/bootstrap.sh
```

The bootstrap script creates `.venv`, installs dependencies, and creates `.env`
from `.env.example` if needed.

Edit `.env`:

```text
MEDIA_ROOT=E:\Hobby Disk
PUBLIC_BASE_URL=http://192.168.1.50:8787
SERVER_HOST=0.0.0.0
SERVER_PORT=8787
STREAM_FOLDER=_stream
SUPPORTED_EXTENSIONS=.mp4,.mkv,.mov,.avi,.webm
MPV_PATH=mpv
```

`PUBLIC_BASE_URL` must use the PC's LAN IP, not `localhost`, because the phone
needs to reach the PC.

The player is chosen from the home page, not from `.env`. A toggle at the top
of the home page lets you switch between **VLC (Phone)** and **mpv (PC)**.
VLC is the default. When VLC is selected, the browser opens a `vlc://` deep
link that launches VLC on the phone. When mpv is selected, the server launches
mpv as a local subprocess on the PC, pointing directly at the file. The
feedback flow is identical for both players.

`MPV_PATH` sets the path to the mpv executable. Defaults to `mpv`, which works
when mpv is on the system PATH. Set the full path (e.g.
`MPV_PATH=C:\Program Files\mpv\mpv.exe`) when mpv is not on PATH — this is
especially important for the scheduled startup task, which runs as SYSTEM and
does not inherit your user PATH.

`STREAM_FOLDER` names a direct-play folder inside `MEDIA_ROOT`. With the
default value, the folder is `MEDIA_ROOT\_stream`. Files in this folder are for
Stream mode only: they are playable from the `Stream` button but are excluded
from recommendations, Explore, Scoreboard, and `_mpv_prefs.json` updates.

The helper stores recommendation preferences in `MEDIA_ROOT\_mpv_prefs.json`.
If that file does not exist, it is created the first time preferences are saved.

The browser/app icon is served from `pc-helper\pocket-manager-icon.png`. The
current 128x128 PNG is linked as the favicon, Apple touch icon, and web
manifest icon. A 180x180 version can be substituted later if you want the
sharpest possible iOS home-screen icon.

Find the LAN IP from Windows:

```text
ipconfig
```

Look for the IPv4 address on the active Wi-Fi or Ethernet adapter.

## Everyday Windows Startup

For normal phone use, run the helper as a Windows scheduled task instead of
keeping a development terminal open. This starts the server when Windows boots,
before you sign in, and keeps it available while the PC is awake.

From an elevated PowerShell in `pc-helper`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

The task runs `scripts\run-server.ps1`, which starts Uvicorn without
development reload mode. The scheduled task runs as Windows `SYSTEM`, so it does
not depend on your account being signed in. It reads `SERVER_HOST` and
`SERVER_PORT` from `.env`, defaulting to:

```text
http://0.0.0.0:8787
```

Logs are written to:

```text
pc-helper\.tmp\server.log
```

The log file is overwritten each time the server starts, so it only contains
output from the current session. Check it after a problem to see what happened.

If you edit `.env` after the startup task is installed, restart the task from an
elevated PowerShell so the running helper loads the new settings:

```powershell
Stop-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
Start-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
```

If you want to stop the always-on helper before development work, remove the
scheduled task from an elevated PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
```

Install it again with `install-startup-task.ps1` when you want to return to the
always-on phone workflow.

You can also run the normal no-reload server manually:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-server.ps1
```

## Development Run

Use the development server when changing the app code:

```bash
./scripts/run-dev.sh
```

This starts Uvicorn with reload enabled, which is useful while editing code but
is not the preferred mode for daily phone playback.

Useful checks:

```text
http://localhost:8787/
http://localhost:8787/health
http://localhost:8787/next
http://localhost:8787/last
```

If the phone cannot reach `/health`, allow Python/Uvicorn through Windows
Firewall for private networks and confirm the phone is on the same Wi-Fi. If the
boot task starts but recommendations find no media, confirm that `MEDIA_ROOT` is
a local drive available before sign-in; mapped network drives usually belong to a
signed-in user session and are not visible to `SYSTEM`.

## Phone Browser Flow

The main phone workflow is the helper home page:

```text
http://<PC_LAN_IP>:8787/
```

Choose **VLC (Phone)** or **mpv (PC)** from the toggle at the top of the home
page, then tap `Recommend`. The helper selects a video, records the play, opens
the chosen player, and leaves the browser on a feedback page.

Tap `Stream` when you want to browse `MEDIA_ROOT\STREAM_FOLDER` directly
without entering the recommendation and feedback cycle. Stream looks like
Explore, with sticky `Back` and `Home` controls, but tapping a media file only
opens the chosen player. The browser stays on the Stream listing, so you
can continue with the next episode. Stream plays supported media files only
and does not update `_mpv_prefs.json`, play count, last played time, likes,
dislikes, pending, or Something Else feedback.

Tap `Explore` when you want to browse `MEDIA_ROOT` manually. Explore shows
folders that contain supported media somewhere below them and shows only files
with extensions from `SUPPORTED_EXTENSIONS`. Choosing a file records play
metadata, opens the chosen player, and then uses the same feedback flow as
recommendations. The top of Explore shows the full `MEDIA_ROOT` path at the
root and relative folder names inside subfolders. Use the sticky `Home` button
to leave Explore; subfolders also show a sticky `Back` button. Files with
unresolved Something Else feedback are hidden from Explore, and the configured
Stream folder is hidden because it has its own direct-play workflow. After you
submit feedback for an explored file, the browser returns to the same Explore
folder that contained that file so you can continue browsing from there.

Tap `Scoreboard` to see a read-only ranking of recommend-able media files by
their current numerical recommendation score. It shows only the media filename
and score, excludes files with unresolved Something Else feedback, and keeps a
sticky `Back` button at the top while you scroll.

Tap `Clean Up` when you want to remove orphan records from
`MEDIA_ROOT\_mpv_prefs.json`. The page lists preference records whose saved
file path is no longer present under `MEDIA_ROOT`, such as an older lower
quality copy you deleted after replacing it. Each row shows the top-level
folder as a tag and the media filename. Select the orphan rows you want removed
and tap `Clean`; `Cancel` returns home without changing the preference file.

Tap `Recommend with Selections` when you want to limit one recommendation to
specific top-level folders under `MEDIA_ROOT`. The selection page shows the
configured media root at the top, then only shows top-level folders that contain
at least one supported media file. Files are found recursively inside those
selected folders. For example, selecting `Anime` can still recommend files under
`Anime\Attack on Titan`, while `Attack on Titan` itself is not shown as a
checkbox. This is also the way to avoid folders you do not want included in a
given recommendation. The top controls stay visible while you scroll: use
`Recommend` to start from the checked folders, `Cancel` to return home, and
`Select All` or `Deselect All` to toggle the folder checkboxes in one tap.

After feedback for a selected recommendation, the browser returns to the
selection page with the same eligible folders still checked. This lets you keep
asking for recommendations from the same subset. Use `Cancel` on the selection
page to clear that saved selection and return to the home page.

When you switch back to the browser, choose:

- `Like`
  Boosts that file in future recommendations. Use this when you enjoyed the
  item and are happy for it to come back.
- `Dislike`
  Strongly penalizes that file without banning it. A dislike carries more
  weight than a like in the opposite direction.
- `Pending`
  Marks it as something to revisit and gives it a smaller boost than Like.
- `Skip`
  Records no preference feedback. The play count and `last_played` timestamp
  from the recommendation are still already recorded.
- `Something Else`
  Opens `Describe Change Required`, where you must choose `Remake`, `Fix`,
  `Trim`, `Hold`, or `Cancel`. Choosing one of the four change types writes an
  entry to `other_feedback.jsonl` under `MEDIA_ROOT` without changing
  like/dislike/pending counts. `Cancel` returns to the normal feedback page.
  If the same file already has unresolved Something Else feedback, the action
  is withheld until that entry is addressed.

After feedback is completed, the browser returns to the home page and
`Recommend` is available again. If the recommendation came from selected
folders, it returns to the selection page with those folders still checked. If
the file was opened through Explore, it returns to that file's Explore folder.

Something Else entries are appended to:

```text
MEDIA_ROOT\other_feedback.jsonl
```

Entries use JSON Lines: one JSON object per line.

```json
{"path":"E:\\Hobby Disk\\Anime\\Steins Gate\\Steins Gate Opening.mp4","type":"remake","created_at":"2026-06-11T10:00:00"}
```

Supported `type` values are `remake`, `fix`, `trim`, and `hold`. While an entry
exists, that file is not eligible for `Recommend`, `Recommend with Selections`,
or `Explore`.

Use `Address Other Feedback` on the home page after reviewing Something Else
feedback. It groups items by `Remake`, `Fix`, `Trim`, and `Hold`. Each row
shows the top-level folder as a tag and the media filename, such as
`[Anime] Steins Gate Opening.mp4`. Checking items and saving removes those
entries from `other_feedback.jsonl`. Cancel returns home without changing the
file. Once an entry is removed here, that file becomes eligible again.

Recommendation is weighted random rather than a strict queue. Never-played
files receive the strongest boost so new discovery is prioritized. Likes raise
future weight, Pending gives a smaller positive nudge, and Dislikes strongly
lower weight. These weights stay in place until you change the preference data
or reset preferences; there is no fixed "played in the last 7 days" cooldown.
When otherwise equal-weight files are in the same recommendation pool,
`last_played` is used only as a tie-breaker so less recently played files are a
little more likely than recently played ones. The Scoreboard uses the same
numerical score for ranking. Ties are ordered from less recently played to more
recently played; never-played ties are ordered from older date-added to newer
date-added.

The home page also has `Reset Preferences`. It opens a confirmation page before
clearing `MEDIA_ROOT\_mpv_prefs.json` back to an empty preference database.
Use `Clean Up` when you only want to remove selected orphan records while
keeping the rest of your recommendation history.

## Optional Phone Shortcuts

You can still use Shortcuts if you want. Create a shortcut named
`Next Recommended Video`:

1. Get contents of URL:

```text
http://<PC_LAN_IP>:8787/next
```

2. Get `player_url` from the JSON response (VLC) or check `launched` (mpv).
3. Open the URL (VLC) or confirm mpv launched (mpv).

Pass `?player=vlc` (default) or `?player=mpv` to choose the player:

```text
http://<PC_LAN_IP>:8787/next?player=vlc
http://<PC_LAN_IP>:8787/next?player=mpv
```

Create feedback shortcuts:

```text
POST http://<PC_LAN_IP>:8787/feedback/like
POST http://<PC_LAN_IP>:8787/feedback/dislike
POST http://<PC_LAN_IP>:8787/feedback/pending
POST http://<PC_LAN_IP>:8787/feedback/skip
POST http://<PC_LAN_IP>:8787/feedback/other
POST http://<PC_LAN_IP>:8787/feedback/other/cancel
POST http://<PC_LAN_IP>:8787/feedback/other/save
```

Feedback applies to the last recommendation made by the running helper process.
If the helper restarts before feedback is sent, `/feedback/...` will not know
which item was last recommended.

## API

- `GET /health`
  Returns `{"ok": true}`.
- `GET /`
  Returns the minimal browser control page.
- `POST /recommend`
  Browser action that selects a recommendation, opens the chosen player (VLC
  deep link or mpv subprocess), and shows feedback buttons.
- `GET /explore`
  Shows a manual browser rooted at `MEDIA_ROOT`.
- `GET /explore?path=...`
  Shows a subfolder under `MEDIA_ROOT`.
- `POST /explore/play`
  Plays a selected supported media file from Explore, records play metadata, and
  shows feedback buttons.
- `GET /stream`
  Shows the direct-play Stream browser rooted at `MEDIA_ROOT\STREAM_FOLDER`.
- `GET /stream?path=...`
  Shows a subfolder under the Stream folder.
- `POST /stream/play`
  Creates a temporary playback token for a selected Stream file and opens the
  chosen player without recording recommendation metadata or showing feedback.
- `GET /scoreboard`
  Shows recommend-able media files ranked by current numerical score.
- `GET /cleanup`
  Shows orphan records from `_mpv_prefs.json` whose paths are no longer present
  under `MEDIA_ROOT`.
- `POST /cleanup`
  Removes selected orphan records from `_mpv_prefs.json` and returns to the
  home page. Existing media records are kept.
- `GET /next`
  Picks and records a recommendation, then returns `stream_url`, `player`,
  `player_url` (VLC) or `launched` (mpv), and feedback URLs. Accepts an
  optional `player` query param (`vlc` or `mpv`, default `vlc`).
- `GET /stream/{token}`
  Streams a token-mapped file. The helper keeps the latest 30 playback tokens
  in memory; older links naturally expire and return 404, the same as unknown
  tokens, missing files, and paths outside `MEDIA_ROOT`.
- `POST /feedback/like`
  Adds one like to the last recommended file.
- `POST /feedback/dislike`
  Adds one dislike to the last recommended file.
- `POST /feedback/pending`
  Adds one pending/save-for-later mark to the last recommended file.
- `POST /feedback/skip`
  Clears the feedback step without changing preference counts.
- `POST /feedback/other`
  Starts the required Something Else change-type step. Returns a conflict if
  the current file already has an unresolved Something Else entry.
- `GET /feedback/other`
  Shows the `Describe Change Required` page, unless the current file already
  has an unresolved Something Else entry.
- `POST /feedback/other/cancel`
  Leaves the Something Else step and returns to the normal feedback page.
- `POST /feedback/other/save`
  Saves a required `feedback_type` form field to
  `MEDIA_ROOT\other_feedback.jsonl` and clears the feedback step without
  changing preference counts. Valid values are `remake`, `fix`, `trim`, and
  `hold`. Duplicate unresolved Something Else feedback for the same file is
  rejected.
- `GET /feedback/addressed`
  Shows saved Something Else records grouped by change type.
- `POST /feedback/addressed`
  Removes the checked Something Else records and returns to the home page.
- `GET /reset`
  Shows a confirmation page for resetting preferences.
- `POST /reset`
  Clears `_mpv_prefs.json` and resets in-memory recommendation state.
- `GET /last`
  Returns the last recommended file and its preference metadata.

## Recommendation Data

Preferences are stored in `MEDIA_ROOT\_mpv_prefs.json`. The JSON shape matches
the original PC script:

```json
{
  "files": {
    "E:\\Hobby Disk\\Example\\video.mp4": {
      "likes": 0,
      "dislikes": 0,
      "pending": 0,
      "play_count": 0,
      "last_played": null,
      "last_feedback": null
    }
  }
}
```

If the preference file is missing, the helper creates it. If it is corrupted,
the helper recovers with an empty in-memory preference set and writes a clean
file the next time preferences are saved. Preference updates are protected by a
process-level lock and written through a temporary file that atomically replaces
the old file, so a failed write should leave the previous complete JSON file in
place.

Files under `MEDIA_ROOT\STREAM_FOLDER` are intentionally outside the preference
system. They are not scanned for recommendations, not shown in Explore or
Scoreboard, and not added to `_mpv_prefs.json` when played from Stream.

When media files are deleted or moved, their old preference records can remain
in `_mpv_prefs.json`. `Clean Up` lists those orphan records and removes only
the checked entries. It does not delete media files and it does not reset
preference records for files that still exist.

Something Else feedback is stored separately in `other_feedback.jsonl` under
`MEDIA_ROOT`. Each line has `path`, `type`, and `created_at`. It does not affect
like/dislike/pending counts, but unresolved entries remove those files from
recommendations and Explore. The helper appends new records when Something Else
feedback is saved and rewrites the file when addressed records are removed.
Those JSONL updates use the same lock plus atomic replace pattern as
preferences.

## Safety Notes

This server is for your private home network only. Do not expose it to the
public internet.

The helper does not accept arbitrary file paths from the phone. It only streams
files selected by the recommendation engine, behind opaque stream tokens, and
it verifies streamed files are under `MEDIA_ROOT`.

Playback tokens are temporary and in-memory. Only the latest 30 generated links
are kept, which prevents quick recommendation cycling from leaving an unlimited
pile of old stream URLs active.

## Validation

This personal helper no longer carries an automated test suite. For changes,
run the development server and try the affected workflow from your phone or
from the local browser. The key manual checks are that the home page opens,
recommendations launch the chosen player, feedback returns to the expected
page, and maintenance screens such as Scoreboard, Address Other Feedback, and
Clean Up show the expected data.
