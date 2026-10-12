# Pocket Media Manager PC Helper

The helper runs on the Windows PC holding your media. It provides a phone/PC
browser interface, one recommendation engine, VLC phone streaming, mpv desktop
playback, and feedback for revisiting favorite moments.

## Setup and Windows Startup

From Git Bash in this directory:

```bash
./scripts/bootstrap.sh
```

The script creates `.venv`, installs dependencies, and copies `.env.example` to
`.env` if needed. Edit `.env`:

```text
MEDIA_ROOT=E:\Hobby Disk
PUBLIC_BASE_URL=http://192.168.1.50:8787
SERVER_HOST=0.0.0.0
SERVER_PORT=8787
STREAM_FOLDER=_stream
SUPPORTED_EXTENSIONS=.mp4,.mkv,.mov,.avi,.webm,.ts
MPV_PATH=mpv
```

`MEDIA_ROOT` must be an existing accessible directory. Relative paths are
resolved against `pc-helper/`. The helper does not create a missing media root:
that could hide a typo or a disconnected drive. `STREAM_FOLDER` must be inside
the media root and cannot be the metadata folder. Directory and file symlinks
are omitted from catalog scans.

Use the PC's LAN address for `PUBLIC_BASE_URL`, not `localhost`. Find it with
`ipconfig`. Choose VLC or mpv on the home page; each browser remembers that
choice. Set `MPV_PATH` to the executable's full path if mpv is not on PATH.

For everyday use, run from an elevated PowerShell in this directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

The scheduled task starts the helper when your user signs in and runs in your
desktop session so mpv is visible. The PC must remain awake and reachable.
After changing `.env`, restart it:

```powershell
Stop-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
Start-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
```

For development, remove the always-on task before starting the reload server:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
```

```bash
./scripts/run-dev.sh
```

The normal server can also be run manually:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-server.ps1
```

Open `http://localhost:8787/` on the PC or `http://<PC_LAN_IP>:8787/` on your
phone. If `/health` is unreachable, check the private-network Windows Firewall
rule for Python/Uvicorn and that both devices are on the same network. This is
a private-home-network app; do not expose it to the public internet.

## Watching and Giving Feedback

**Recommend** picks a clip with the one recommendation engine. **Recommend with
Selections** restricts the pool to checked top-level folders, recursively.
Selections persist per browser and feedback returns to that page. Cancel clears
the selections. No recommendation profiles remain.

**Explore** shows catalogued folders and files alphabetically. Choosing a file
records an encounter and opens the player; feedback returns to the same folder.
There is no separate Clip Preferences screen or Test Playback mode. If you are
testing a file, open it normally and choose No feedback. That records the offer
but adds no enjoyment or negative rating; the usual brief repeat penalty applies.

**Stream** browses `MEDIA_ROOT\STREAM_FOLDER` directly. Files there are outside
the recommendation catalog and encounter history. Opening a file leaves the
browser on its listing so you can return for the next episode.

When the player opens, the browser displays feedback:

- **Favorite** is an independent toggle saved immediately, even if you never
  submit a response. It stays set until explicitly removed.
- **Enjoyed it** records a positive report for this encounter.
- **Too soon** pauses this clip's recommendations for 24 hours.
- **Not in the mood** withholds this clip for the rest of this browser's session.
- **Less often** sets a lasting reduced-frequency preference.
- **Not for me** excludes it from recommendations until you undo that preference.
  It remains accessible through Explore; Favorite does not override exclusion.
- **No feedback** closes the encounter without expressing an opinion.
- **Something Else** opens Remake, Fix, Trim, Hold, or Cancel. Saving a task
  closes the encounter without ordinary feedback and hides that file from
  recommendations and Explore until addressed.

The feedback page lets you change Favorite and, when relevant, offers
**Restore normal frequency** and **Allow recommendations again**. To reverse a
lasting preference, open the clip through Explore and use those controls before
submitting a response. They save immediately without closing the encounter.
Enjoyed it does not silently undo either lasting preference. The single engine
boosts Favorites and repeated positive reports (with a cap), reduces frequency
for Less often, and gives recently enjoyed clips breathing room. An unrated
playback offer gets only a brief repeat penalty, not a viewing count.

The current tuning is a starting point:

| Action | Current recommendation effect |
| --- | --- |
| Favorite | A fourfold base-weight boost while enabled; timing and exclusion rules still apply. |
| Enjoyed it | Builds lasting interest, capped at five positive reports. Recent enjoyment also reduces repeat priority, gradually returning to normal over 24 hours. |
| Too soon | A full 24-hour pause from the response time, including for Favorites. |
| Not in the mood | Excludes the clip for the current browser session, until session expiry or Start New Session. |
| Less often | Reduces its weight to one quarter until Restore normal frequency is used. |
| Not for me | Excludes it until Allow recommendations again is used. Explore remains available. |
| No feedback | Adds no rating or lasting preference. The ordinary offer still receives a brief five-minute repeat penalty. |
| Something Else | Excludes the file from recommendations and Explore until its task is addressed. |

Weights influence random selection, not a guaranteed number of appearances.
Enjoyed it discourages immediate repeats but does not impose the strict pause
used by Too soon. Restoring a lasting preference does not cancel other timing
or maintenance restrictions.

If nothing is currently eligible, the app explains why; it does not quietly
override your responses. You can manually choose a clip through Explore,
restore its preferences, wait for Too soon to expire, or start a new session
for session-only declines.

## Encounters, Sessions, and Restarts

An encounter is created before opening the player. It records the file,
selection time, browser, session, source, player, selected folders, return page,
playback request/failure, and eventually feedback. Launching mpv or sending a
VLC link does not prove the video was watched or completed. Watch duration and
completion are not measured.

Feedback forms carry the encounter ID. Repeating the same response is safe;
submitting a different response to a closed encounter is rejected. There is no
global "last recommendation" shared by devices.

A persistent browser cookie identifies its saved settings. Phone and PC
browsers have separate sessions, while the library and lasting preferences are
shared. A session begins with an encounter request and is reused while there
has been less than 30 minutes of inactivity. Encounter requests, timely feedback,
and Favorite changes during an active encounter extend activity. Page refreshes
do not. **Start New Session** resets this browser's sitting explicitly.

Late feedback stays with its original encounter and does not revive an expired
session. Restarting the helper alone does not end an active session. Unfinished
encounters remain open: Home offers Resume Feedback, Close without feedback,
and All Unfinished Feedback. You can also keep browsing without answering.
Resume does not automatically relaunch the player; use Open Player when needed.

Clearing cookies or changing browsers gives you a new browser identity and
does not expose the previous browser's unfinished feedback. Its records still
exist in the database. Cookies must be retained for API feedback as well.

## Catalog and Maintenance

A complete catalog scan runs at startup. Ordinary Recommend, Selections,
feedback, and Explore operations use SQLite rather than rescanning the media
tree or rewriting JSON documents. Use **Refresh Library** after adding,
moving, editing, or deleting files. Refresh is a full scan and can take time on
a large disk. There is no continuous filesystem watcher.

`available` means the last successful check found that file. It is separate from
Favorite, disinterest, or maintenance blocks. Before playback, the chosen file
is checked again. A changed file's old record is made unavailable; Refresh
Library creates the replacement record with fresh preferences. Moving or
renaming a file also starts a fresh record. An unchanged file returning to the
same path can reuse its existing record. Failed or partial scans preserve the
previous catalog instead of marking everything missing.

**Address Other Feedback** groups outstanding tasks by Remake/Fix/Trim/Hold.
Checking items and saving marks them resolved. Reopen the file through Explore
after resolving its task; choose No feedback if you only want to check playback.

**Clean Up** removes only selected unavailable records and their encounters,
never media files. Records with unresolved tasks are withheld from cleanup.
**Reset Preferences** confirms before clearing all Favorites, Less often,
Not for me, and encounters across browsers. The catalog, player/selections,
and Something Else tasks remain; session IDs are reset. Both operations save
a database backup first.

## Storage and Backups

All generated app metadata is in:

```text
MEDIA_ROOT\.pocket-media-manager\
  pocket-media.sqlite3
  backups\
  logs\
```

SQLite manages its journal/WAL companion files alongside the database. The
metadata folder is excluded from all media browsing and catalog scans. No
separate database server is needed. Four application tables are used:

| Table | Stored data |
| --- | --- |
| `media` | Relative path, file size, precise modification timestamp, discovery time, availability, Favorite, Less often, Not for me. |
| `encounters` | Browser/session, media record, selection and playback-request timestamps, source/context, launch outcome/error, one response, resolution, close time. |
| `other_feedback` | Media record, task type, creation time, resolution time. |
| `settings` | Per-browser player/selections/session activity, catalog refresh and backup bookkeeping. |

Times are stored in UTC and displayed in the browser's local timezone. File
modification is stored as nanoseconds (`modified_ns`) to detect in-place edits.
There are no separate preference, score, summary, or feedback JSON files.

This version starts fresh. Existing `_mpv_prefs.json` and `other_feedback.jsonl`
files are ignored and left untouched. They are not imported or updated.

SQLite changes are transactional. A damaged database is reported rather than
silently replaced with an empty one. Automatic snapshot backups are checked at
startup and hourly while running, with at most one automatic backup per UTC day.
The latest seven helper-generated backups are kept, including backups made
before reset/cleanup. Snapshots use SQLite's backup API, not a live file copy.

To restore, stop the helper first. Preserve the current metadata folder under
a different name, create a fresh `.pocket-media-manager` folder, and copy the
chosen backup into it as `pocket-media.sqlite3`. Restart the helper; it recreates
logs/backups and refreshes the catalog. Keeping the preserved folder also keeps
the original database and any WAL companion files together for recovery.

Logs are in `logs/helper.log` (rotated at 1 MB with two old files) and, when
using the PowerShell launcher, `logs/server.log` (current server transcript,
overwritten on restart).

## API and Shortcuts

The API has changed with this fresh start. Keep the same HTTP cookie jar for
recommendation and feedback requests; all feedback requires `encounter_id`.
Old like/dislike/pending/skip shortcuts need updating.

- `GET /health`: readiness check.
- `GET /next?player=vlc` or `?player=mpv`: choose a recommendation, create an
  encounter, and request playback. Player is optional and otherwise remembered.
  Returns `encounter_id`, `player`, `file_name`, `player_url`/`stream_url` for
  VLC or `launched` for mpv, feedback URLs, and the persistent feedback page.
  A failed request includes `launch_error`; its encounter remains resumable.
- `POST /feedback/{response}` with `encounter_id`: `enjoyed`, `too_soon`,
  `not_in_mood`, `less_often`, `not_for_me`, or `no_feedback`.
- `POST /feedback/favorite` with `encounter_id` and `favorite=true/false`.
- `POST /feedback/restore` with `encounter_id` and `preference=less_often` or
  `preference=not_for_me`: clear that lasting preference without closing feedback.
- `POST /feedback/other/save` with `encounter_id` and `feedback_type`:
  `remake`, `fix`, `trim`, or `hold`.
- `GET /feedback/{encounter_id}`: resume browser feedback without relaunching.
- `GET /last`: this browser's last encounter and current media preferences.
- `POST /library/refresh`: refresh the catalog; JSON clients receive media count.
- `POST /session/new`: begin a fresh session.
- `GET /stream/{token}`: serve a temporary playback link. The latest 30 links
  are retained in memory and expire on helper restart; resumed feedback can
  request a new link through Open Player.

## Validation and Limits

Validation remains ad-hoc, without a maintained test suite. After changes,
exercise Recommend, selected folders, Explore, both players, and Stream. Check
that Favorite survives No feedback, each response is attached to its encounter,
unresolved tasks hide clips until addressed, and two browsers have separate
unfinished feedback. Restart with an open encounter and resume it. Verify
Refresh Library discovers additions and marks deletions, and that metadata
and Stream files never enter recommendations.

Actual VLC playback and Windows mpv launching need a device/PC check. The helper
does not transcode files; codec support belongs to the player. `.ts` is in the
default extension list, but an existing `SUPPORTED_EXTENSIONS` override needs
`.ts` added explicitly. There is no automatic clip matching, legacy migration,
profile selector, separate preference/test screen, watch-completion tracking,
or continuously refreshed catalog.
