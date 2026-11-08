# Pocket Media Manager usage guide

See [Windows setup and everyday commands](../README.md#windows-setup) first.
Configuration names and code responsibilities are in [design.md](design.md).

## Watching and Giving Feedback

On first use, run **Scan Library** to populate the catalog. Startup only opens
the database; it does not scan your media. Home always shows Scan Library,
Stream, and Clean Up. Recommend, Recommend with Selections, Explore, and
Address Other Feedback appear when the media table has any records. Reset
Library appears when either media or encounters exist. These visibility checks
only test table emptiness; a page can still report that nothing is eligible or
that no maintenance work is outstanding.

Maintenance places Reset Library and Clean Up side by side, with Address Other
Feedback spanning the row below. When Clean Up is the only action, it fills the row.

The player and folder selections are shared across devices and browser
sessions. The initial player is VLC; choosing mpv saves that choice until you
change it or reset the library. Taking control restores the same saved choices.

**Recommend** picks a clip with the one recommendation engine. **Recommend with
Selections** restricts the pool to the top-level folders you select, recursively. Each folder
is a card showing its eligible clip count; tap it to select or deselect it.
Selections persist in the database and feedback returns to that page. Cancel clears
the selections. No recommendation profiles remain.

**Explore** shows catalogued folders and files alphabetically. Choosing a file
records an encounter and opens the player; feedback returns to the same folder.
Folders and clips appear in separate groups: blue folder cards have a folder
icon and a chevron; media cards have a play icon. Stream uses the same cards.
Inside a folder, the title is the folder's name and a grey trail beneath it
(for example Explore › Anime) links back to each level above. The library and
Stream folder locations are listed at the bottom of Home, under Maintenance.
Favorites use a green-tinted card with a green edge, while
Not for me clips use red. Filenames stay readable on both, and text badges
identify each preference without relying on color. Choosing Not for me removes
Favorite automatically, so the clip switches from green to red with only the
Not for me badge. Not for me clips can still be opened manually.

There is no separate Clip Preferences screen or Test Playback mode. If you are
testing a file, open it normally and choose No feedback. That records the offer
but adds no enjoyment or negative rating; the usual brief repeat penalty applies.

**Stream** browses the configured media root / Stream folder directly. Files there are outside
the recommendation catalog and encounter history. Opening a file leaves the
browser on its listing so you can return for the next episode. Like other app
workflows, starting a new Stream playback is unavailable while a library clip
awaits feedback. Existing playback links keep serving media bytes.

When the player opens, the browser displays feedback:

- **Favorite** is an independent toggle saved immediately, even if you never
  submit a response. It stays set until you remove it or choose Not for me.
- **Enjoyed it** records a positive report for this encounter and reduces its
  Less often count by one, never below zero.
- **Too soon** pauses this clip's recommendations for 3 days (72 hours), app-wide.
- **Not in the mood** pauses this clip's recommendations for 48 hours, app-wide.
- **Less often** increases a lasting frequency-penalty count. Each count halves
  the recommendation weight, so repeated responses reduce it further.
- **Not for me** removes Favorite and excludes it from recommendations until
  you undo that preference. It remains accessible through Explore. Choose Allow
  recommendations again before marking it Favorite again; restoring eligibility
  alone does not restore the old Favorite status.
- **No feedback** closes the encounter without expressing an opinion.
- **Something Else** opens Remake, Fix, Trim, or Cancel. Saving a task
  closes the encounter without ordinary feedback and hides that file from
  recommendations and Explore until addressed.

The feedback page lets you change Favorite and, when relevant, offers
**Restore normal frequency** and **Allow recommendations again**. To reverse a
lasting preference, open the clip through Explore and use those controls before
submitting a response. They save immediately without closing the encounter.
Restore normal frequency resets the Less often count to zero. Enjoyed it
removes one count but does not clear Not for me. The single engine
boosts Favorites and repeated positive reports (with a cap), reduces frequency
for Less often, and gives recently enjoyed clips breathing room. An unrated
playback offer gets only a brief repeat penalty, not a viewing count.

The current tuning is a starting point:

| Action | Current recommendation effect |
| --- | --- |
| Favorite | A fourfold base-weight boost while enabled; timing and exclusion rules still apply. |
| Enjoyed it | Removes one Less often count, stopping at zero, and builds lasting interest, capped at five positive reports. Recent enjoyment also reduces repeat priority, gradually returning to normal over 24 hours. |
| Too soon | A full 3-day (72-hour) pause from the response time across all devices, including for Favorites. |
| Not in the mood | A full 48-hour pause from the response time across all devices, including for Favorites. |
| Less often | Adds one count: the weight multiplier is 1/2, 1/4, 1/8, and so on. Counts persist until reduced by Enjoyed it or cleared with Restore normal frequency. |
| Not for me | Removes Favorite and excludes it until Allow recommendations again is used. Explore remains available. |
| No feedback | Adds no rating or lasting preference. The ordinary offer still receives a brief five-minute repeat penalty. |
| Something Else | Excludes the file from recommendations and Explore until its task is addressed. |

Weights influence random selection, not a guaranteed number of appearances.
Enjoyed it discourages immediate repeats but does not impose the strict pause
used by Too soon or Not in the mood. Alongside reducing one Less often count,
it adds a lasting positive-interest signal and a temporary reduction in repeat priority. For
example, Enjoyed it on Monday at 8 pm makes the clip less likely to repeat
immediately, but does not block it. That timing reduction gradually wears off
and is gone by Tuesday at 8 pm; the positive-interest signal remains. This
24-hour behavior is unchanged by the longer explicit feedback cooldowns.

Too soon and Not in the mood add no permanent weight penalty and do not change
Favorite or lasting preferences. When their cooldown expires, that restriction
disappears completely; other eligibility rules still apply. Selecting the same
response on a later encounter starts a new cooldown from that response, without
automatic escalation. Restoring a lasting preference does not cancel other
timing or maintenance restrictions.

If nothing is currently eligible, the app explains why; it does not quietly
override your responses. You can manually choose a clip through Explore,
restore its preferences, or wait for a cooldown to expire.

## Browser control and pending feedback

One live page controls the whole app, including browsing. The first page claims
control automatically if nobody holds it; another tab or device shows **Take
Control**. Taking control immediately revokes the previous grant after any
already-authorized save or player launch finishes. The old page can remain
visible until its next request, but its operations are rejected. There is no
manual release button.

Ownership has no heartbeat, inactivity timeout, or background polling. Closing
a tab may leave its grant held; use Take Control elsewhere. Refreshing creates
a new page identity and may require takeover. The grant lives only in the
current document's memory, so duplicated tabs cannot share
control. Browser navigation inside the app retains that document and grant.
Restarting the server clears ownership. Playback URLs remain the same.

An encounter is created before opening a player. It records the file's relative
path, selection time, return page, launch status, feedback, and closure time.
Its random UUID identifies the opening independently of its timestamp.
Launching VLC/mpv is not counted as a completed viewing.

Only one encounter can remain open. The controlling page returns to that
feedback screen instead of Home, Explore, Stream, or maintenance workflows.
Taking control elsewhere resumes the same encounter without reopening playback.
A response, No feedback, or a saved Something Else task closes it; Favorite and
restoration controls save without closing it. Cancelling Something Else leaves
it open. A failed launch still needs closure; No feedback is fine. Open Player
uses the shared saved player. Moving from phone to PC does not automatically
change it; you can close feedback even if that device cannot open the player.

Pending feedback, favorites, maintenance tasks, and cooldowns live in SQLite
and survive takeover, browser changes, and server restarts. Player and folder
selections also live in the database; no preference cookie is needed.
Feedback submissions include the encounter
ID: identical retries are safe, while a different response to an already-closed
encounter is rejected. Database transactions prevent competing requests from
creating two open encounters. Existing VLC streams can keep serving bytes
without browser ownership.

Catalog and Stream playback use the same stable path-based media endpoint.
For example, `Anime/aot-compilation.mp4` has the address
`http://<PC>:8787/media/Anime/aot-compilation.mp4`. Spaces, Unicode, and
punctuation are URL-encoded; filenames do not need a custom identifier.
VLC receives its launch link containing this HTTP address; mpv opens the
corresponding local file on the Windows PC. Open Player reuses the encounter
and the same file address. The URL survives server restarts while the host,
port, and relative path stay the same. Stream files use their media-root path,
including the configured `_stream` folder, without creating catalog records.
The app stores no URLs, playback tokens, or playback positions. Stable URLs
remove the changing-address issue; whether VLC on iPhone resumes must still be
checked on the device.

## Catalog and maintenance

A full scan runs only when you choose **Scan Library**, a primary Home action
above Maintenance. It inserts new paths, marks missing paths unavailable, and
restores availability when a file returns. Startup, ordinary browsing, and
recommendations use the stored catalog. There is no continuous file watcher.
Metadata and Stream folders and symlinks are excluded from recommendations.
Failed or partial scans preserve the previous catalog rather than marking
unreadable files missing.

The relative path is the file's primary key. Editing, trimming, remastering, or
replacing a file at that path retains the same preferences, encounter history,
and outstanding work. Size and modification time do not create a new record.
Renaming or moving a file creates a new identity when scanned; the old record
points to a missing path. A file returning at its old path reuses that record
unless it has been deleted through Clean Up. Files are checked again before
playback.

**Address Other Feedback** lists media with a current Remake/Fix/Trim flag.
Marking an item addressed clears that flag and makes the file eligible again
subject to availability, preferences, and cooldowns. Editing a file or scanning
does not automatically clear its flag. There is no separate task archive.

Opening **Clean Up** checks only the paths of catalog records currently marked
available. It does not walk the media directory or discover files; missing
paths become unavailable. The page then lists unavailable records and can
report that there is nothing to clean. Deleting selected entries removes their
media records and every associated encounter, including any maintenance state.
Media files are never deleted. A later scan of a deleted record's path starts
with fresh preferences and history.

**Reset Library** is a destructive operation with a warning. It clears all media,
encounters, and settings, including maintenance flags, preferences, cooldowns,
and shared player/folder choices. It leaves the schema ready for reuse, restores
the initial VLC/empty-folder defaults, and requires Scan Library to rebuild the
catalog. Media files and backup snapshots remain. Cleanup deletion and reset
both save a verified database backup before changing data.

## Storage and backups

Generated metadata stays in:

```text
<media root>\.pocket-media-manager\
  pocket-media.sqlite3
  backups\
```

SQLite manages journal/WAL files alongside the database. This version uses
schema 3 and intentionally provides no migration from older schemas.
Unsupported versions and damaged databases produce errors rather than an empty
replacement. To start fresh after upgrading, stop the server, move aside or
remove the old `.pocket-media-manager` directory, and start the server again.
It creates the new database; choose Scan Library to populate it. Existing
records are not imported. Media files are never modified by this app.

| Table | Stored data |
| --- | --- |
| `media` | `relative_path` primary key, `available`, `favorite`, `less_often_count`, `not_for_me`, nullable `other_feedback` (Remake/Fix/Trim). |
| `encounters` | UUID `id`, `media_path` reference, `selected_at`, nullable `feedback`, `closed_at`, `return_path`, `launch_status`. |
| `settings` | Shared player/folder choices and automatic-backup bookkeeping. |

Times are stored in UTC and displayed in the browser's timezone. Cooldowns are
derived from saved feedback timestamps; no timer files or extra sessions are
needed. An encounter with null feedback and closure time is pending; No feedback
and Remake/Fix/Trim are explicit closing responses. Browser ownership is
temporary process memory.

Backups use SQLite's backup API, run an integrity check, and atomically publish
the finished snapshot. Checks run at startup and hourly with at most one
automatic snapshot per UTC day. The latest seven generated snapshots are kept,
including snapshots saved before cleanup/reset. Failed snapshots are not
published or marked complete.

To restore, stop the task first. Preserve the entire current metadata directory
under another name, including any WAL files. Create a fresh
`.pocket-media-manager` folder and copy a verified backup there as
`pocket-media.sqlite3`. The backup must use schema 3. Start the task; it recreates
the backups directory and uses the restored catalog without scanning. Run Scan
Library when you want to reconcile it with the files currently on disk. Keep
the preserved directory until you verify recovery.

Application logs are separate: repository `logs/server.log`, with a 5 MiB
limit and three rotated copies. They include startup failures and player-launch
errors. The media directory is the data to preserve when replacing a checkout.

## API

The browser handles this automatically. Other clients must first send
`POST /api/controller/claim` with JSON `{"page_id":"unique-page-id"}` and the
server's exact `Origin` header. If occupied, explicitly call
`POST /api/controller/takeover` with the same body. Include `X-Page-Id` and
`X-Controller-Token` (the returned `token`) on all normal app requests. Release
with `POST /api/controller/release` and JSON `{"page_id":"...","token":"..."}`.
Player/folder choices are shared database settings. Every mutation requires the
same-origin `Origin` header; GET requests do not select, launch, or scan clips.

JSON errors have the shape
`{"ok":false,"error":{"code":"...","message":"..."}}`. Ownership rejection
uses `controller_occupied`, `controller_required`, or `controller_transferred`.
An open encounter blocks unrelated operations with HTTP 409 and
`error.code=feedback_required`, `error.encounter_id`, and `error.feedback_page`.
HTML navigation goes to its feedback screen. Responses containing app state
are not cached. Health, icons, browser assets, and existing playback links are
available without ownership.

- `GET /health`: readiness after startup completes, including app and process-instance identifiers.
- `POST /next`, optional form `player=vlc` or `player=mpv`: recommend, create an
  encounter, and request playback. Returns encounter ID, launch outcome,
  streaming/player URLs where relevant, and feedback URLs.
- `POST /feedback/{response}`, form `encounter_id`: `enjoyed`, `too_soon`,
  `not_in_mood`, `less_often`, `not_for_me`, or `no_feedback`.
- `POST /feedback/favorite`, forms `encounter_id`, `favorite=true/false`.
- `POST /feedback/restore`, forms `encounter_id`,
  `preference=less_often` or `preference=not_for_me`.
- `POST /feedback/other/save`, forms `encounter_id`,
  `feedback_type=remake`, `fix`, or `trim`.
- `GET /feedback/{encounter_id}`: feedback without automatic replay.
- `GET /last`: pending encounter or latest closed encounter and media preferences.
- `POST /library/scan`: scan the catalog; JSON returns the media count.
- `POST /cleanup/check`: check stored available paths, then open the cleanup list.
- `GET /cleanup`: display the stored unavailable records without another check.
- `POST /cleanup`, form `media_paths`: delete selected unavailable records and
  their encounters after saving a backup.
- `POST /reset`: reset all three database tables after saving a backup.
- `GET /media/{relative_path}` and `HEAD`: stable media bytes, including range
  requests, for both catalog and Stream files. The path is relative to the media
  root and URL-encoded. These addresses need no browser ownership grant.

## Validation and limits

Run the maintained tests described in the README. On Windows, verify quiet
logon startup, foreground shutdown, duplicate-server rejection, UAC only when
needed, task stop/start, and the root restart command. On phone and desktop,
check Take Control, reload/takeover, back/forward navigation, pending feedback,
selected folders, Explore, and Stream. Verify real VLC/mpv playback, iPhone
return from the player, and whether stable URLs let VLC resume after stopping
partway through a file. Codec support belongs to the player; there is no
transcoding or completion tracking.
