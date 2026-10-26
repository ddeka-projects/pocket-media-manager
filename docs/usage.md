# Pocket Media Manager usage guide

See [Windows setup and everyday commands](../README.md#windows-setup) first.
Configuration names and code responsibilities are in [design.md](design.md).

## Watching and Giving Feedback

**Recommend** picks a clip with the one recommendation engine. **Recommend with
Selections** restricts the pool to the top-level folders you select, recursively. Each folder
is a card showing its eligible clip count; tap it to select or deselect it.
Selections persist per browser and feedback returns to that page. Cancel clears
the selections. No recommendation profiles remain.

**Explore** shows catalogued folders and files alphabetically. Choosing a file
records an encounter and opens the player; feedback returns to the same folder.
Folders and clips appear in separate groups: blue folder cards have a folder
icon and a chevron; media cards have a play icon. Stream uses the same cards.
Inside a folder, the title is the folder's name and a grey trail beneath it
(for example Explore › Anime) links back to each level above. The library and
Stream folder locations are listed at the bottom of Home, under Maintenance.
Favorites use a light green background and border, while
Not for me clips use light red. Dark filenames remain readable, and text badges
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
- **Enjoyed it** records a positive report for this encounter.
- **Too soon** pauses this clip's recommendations for 3 days (72 hours), app-wide.
- **Not in the mood** pauses this clip's recommendations for 48 hours, app-wide.
- **Less often** sets a lasting reduced-frequency preference.
- **Not for me** removes Favorite and excludes it from recommendations until
  you undo that preference. It remains accessible through Explore. Choose Allow
  recommendations again before marking it Favorite again; restoring eligibility
  alone does not restore the old Favorite status.
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
| Too soon | A full 3-day (72-hour) pause from the response time across all devices, including for Favorites. |
| Not in the mood | A full 48-hour pause from the response time across all devices, including for Favorites. |
| Less often | Reduces its weight to one quarter until Restore normal frequency is used. |
| Not for me | Removes Favorite and excludes it until Allow recommendations again is used. Explore remains available. |
| No feedback | Adds no rating or lasting preference. The ordinary offer still receives a brief five-minute repeat penalty. |
| Something Else | Excludes the file from recommendations and Explore until its task is addressed. |

Weights influence random selection, not a guaranteed number of appearances.
Enjoyed it discourages immediate repeats but does not impose the strict pause
used by Too soon or Not in the mood. It has two separate effects: a lasting
positive-interest signal and a temporary reduction in repeat priority. For
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
visible until its next request, but its operations are rejected. **Release
Control** gives up the grant deliberately.

Ownership has no heartbeat, inactivity timeout, or background polling. Closing
a tab may leave its grant held; use Take Control elsewhere. Refreshing creates
a new page identity and may require takeover. The grant lives only in the
current document's memory, so browser cookies and duplicated tabs cannot share
control. Browser navigation inside the app retains that document and grant.
Restarting the server clears ownership and temporary playback links.

An encounter is created before opening a player. It records the file, selection
time, source, player, selected folders, return page, playback outcome, and
feedback. Launching VLC/mpv is not counted as a completed viewing.

Only one encounter can remain open. The controlling page returns to that
feedback screen instead of Home, Explore, Stream, or maintenance workflows.
Taking control elsewhere resumes the same encounter without reopening playback.
A response, No feedback, or a saved Something Else task closes it; Favorite and
restoration controls save without closing it. Cancelling Something Else leaves
it open. A failed launch still needs closure; No feedback is fine. Open Player
uses the encounter's original player.

Pending feedback, favorites, maintenance tasks, and cooldowns live in SQLite
and survive takeover, cleared cookies, and server restarts. A cookie remembers
only player and folder selections. Feedback submissions include the encounter
ID: identical retries are safe, while a different response to an already-closed
encounter is rejected. Database transactions prevent competing requests from
creating two open encounters. Existing VLC streams can keep serving bytes
without browser ownership until their link expires or the server restarts.

## Catalog and maintenance

A full scan runs at startup and through Refresh Library. Ordinary browsing and
recommendations use the stored catalog. There is no continuous file watcher.
Metadata and Stream folders and symlinks are excluded from recommendations.
Failed or partial scans preserve the previous catalog rather than marking
unreadable files missing.

Before playback, a file is checked again. A modified file's old record becomes
unavailable; Refresh Library creates a replacement with fresh preferences.
Renaming or moving a file also creates a fresh record. An unchanged file
returning at the same path can reuse its previous record.

Address Other Feedback groups Remake/Fix/Trim/Hold tasks. Marking them addressed
makes those files eligible again. Clean Up removes selected unavailable records
and encounters, never media files, and protects unresolved maintenance tasks.
Reset Preferences clears Favorites, Less often, Not for me, and encounters
(including cooldowns). It retains the catalog, browser choices, and maintenance
tasks. Both cleanup and reset save a database backup before changing data.

## Storage and backups

Generated metadata stays in:

```text
<media root>\.pocket-media-manager\
  pocket-media.sqlite3
  backups\
```

SQLite manages journal/WAL files alongside the database. Its schema version is
2; unsupported versions and damaged databases produce errors rather than an
empty replacement. Media files are never modified by this app. Old JSON
preference files, if present, are left untouched.

| Table | Stored data |
| --- | --- |
| `media` | Relative path, size, precise modification time, availability, and lasting preferences. |
| `encounters` | Selection/context, player launch outcome, feedback and closure timestamps. |
| `other_feedback` | Remake/Fix/Trim/Hold work and resolution time. |
| `settings` | Browser choices and scan/backup bookkeeping. |

Times are stored in UTC and displayed in the browser's timezone. Cooldowns are
derived from saved feedback timestamps; no timer files or extra sessions are
needed. Playback tokens and browser ownership are temporary process memory.

Backups use SQLite's backup API, run an integrity check, and atomically publish
the finished snapshot. Checks run at startup and hourly with at most one
automatic snapshot per UTC day. The latest seven generated snapshots are kept,
including snapshots saved before cleanup/reset. Failed snapshots are not
published or marked complete.

To restore, stop the task first. Preserve the entire current metadata directory
under another name, including any WAL files. Create a fresh
`.pocket-media-manager` folder and copy a verified backup there as
`pocket-media.sqlite3`. Start the task; it recreates the backups directory and
refreshes the catalog. Keep the preserved directory until you verify recovery.

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
Use a cookie jar only to retain player/folder choices. Every mutation requires
the same-origin `Origin` header; GET requests do not select or launch clips.

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
  `feedback_type=remake`, `fix`, `trim`, or `hold`.
- `GET /feedback/{encounter_id}`: feedback without automatic replay.
- `GET /last`: pending encounter or latest closed encounter and media preferences.
- `POST /library/refresh`: scan the catalog; JSON returns the media count.
- `GET /stream/{token}` and `HEAD`: media bytes, including range requests. The
  latest 30 links stay in memory until replaced or the server restarts. Open
  Player can create a new link for a pending encounter.

## Validation and limits

Run the maintained tests described in the README. On Windows, verify quiet
logon startup, foreground shutdown, duplicate-server rejection, UAC only when
needed, task stop/start, and the root restart command. On phone and desktop,
check Take Control, reload/takeover, back/forward navigation, pending feedback,
selected folders, Explore, and Stream. Verify real VLC/mpv playback and iPhone
return from the player. Codec support belongs to the player; there is no
transcoding or completion tracking.
