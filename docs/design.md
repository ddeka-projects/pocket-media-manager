# Design

Pocket Media Manager is one independently runnable Python web app. A Windows
user-logon task runs it in the user's desktop session with normal privileges.
The browser provides the interface; server-side code scans the local library,
launches players, and commits feedback to SQLite.

## Code responsibilities

| Location | Responsibility |
| --- | --- |
| `__main__.py` | Small `python -m pocket_media_manager` entry point. |
| `app.py` | Assemble routes, request protections, ownership, startup/shutdown, and server CLI. |
| `config.py`, `env.py` | Frozen validated settings and literal `.env` parsing. |
| `api.py` | Translate HTTP requests into app operations. |
| `service.py` | Coordinate recommendations, shared choices, and pending feedback. |
| `presentation.py`, `static/` | Server-rendered HTML, styles, and browser navigation. |
| `playback.py` | mpv launch and stable path-based VLC links. |
| `storage.py` | SQLite transactions, scans, verification, and backups. |
| `recommender.py` | Recommendation eligibility and weights. |
| `controller.py` | Explicit per-page control with no expiry. |
| `process_lock.py` | OS-released lifetime guard for the app's media directory. |
| `logging_setup.py`, `errors.py` | Bounded logs and consistent request errors. |
| `scripts/` | PowerShell setup, foreground/background launch, tasks, and restart. |
| `tests/` | Maintained behavior and operating-convention tests. |

The browser shell keeps a random page ID and server-issued token in memory.
Internal links and forms fetch server-rendered pages into the same document,
keeping control through navigation. A duplicate tab or refresh creates a new
identity. Player and folder preferences are shared database settings; there is
no browser preference cookie. Taking control reloads the same saved choices.

Ordinary HTML and JSON requests have a 12-second deadline, including response
bodies. A timeout releases the busy indicator and reports an uncertain save
instead of retrying it automatically. This deadline does not expire ownership;
VLC media streaming keeps its own lifetime.

A shared asynchronous gate admits one protected request or control transfer
at a time. Takeover waits for an admitted operation to finish, then invalidates
the old token. SQLite still independently protects transactions and resolves
conflicting or repeated feedback safely. A process guard is acquired before
initialization or scanning, preventing a second app server on the same data,
even when configured on a different port.

## Catalog and stored state

The database uses schema 3 with three tables: `media`, `encounters`, and
`settings`. Older schemas are rejected; there is no migration. Start a fresh
metadata directory when upgrading from an older version.

`media.relative_path` is the primary key, using a consistent Windows path
representation and forward separators while retaining valid filenames. A
scan retains the first catalogued spelling when only Windows casing changes;
case-sensitive development filesystems keep distinct paths distinct. A
same-path edit or replacement keeps its row, preferences, encounters, and
maintenance flag. Moving or renaming a file creates a new identity and leaves
the old path eligible for cleanup. The row stores availability, Favorite,
Less often count, Not for me, and nullable Remake/Fix/Trim feedback. There is no
file-version identity or separate maintenance-task table.

Encounters use random UUID primary keys, a media-path reference, selection and
closure times, a closing response, return path, and launch status. Null response
and closure time mean pending. Ordinary feedback, No feedback, and maintenance
responses close the encounter; preference toggles do not. A transaction commits
the closing response and media changes together, so retries do not increment or
decrement a counter twice. Only one encounter can await feedback. The response
and closure time also supply enjoyment history and cooldowns to the recommender.

Each Less often response adds a count; each Enjoyed it removes one, stopping at
zero. Its weight multiplier is `2 ** -less_often_count`; elapsed time does not
clear the count. Restore normal frequency resets it. Not for me and outstanding
maintenance remain separate exclusions.

The settings table holds one player choice (initially VLC), one folder
selection (initially empty), and automatic-backup bookkeeping. It does not
identify browsers or duplicate the pending encounter.

Startup initializes storage and backups without scanning. Scan Library walks
the media tree explicitly and publishes catalog availability only after a
successful full scan. Clean Up checks only stored available paths, then offers
deletion of selected missing media and their encounters. Reset Library clears
all three tables. Both destructive actions save a verified snapshot first.
Home visibility uses table-existence checks, not eligibility or task counts.

VLC media addresses are derived from the configured origin and URL-encoded
media-root relative path: `/media/{relative_path}`. The same endpoint serves
Stream files without cataloguing them. There is no random-token registry or
stored URL; links stay stable across app restarts while origin and path stay
the same. mpv opens the local Windows path. File-serving checks still restrict
access to allowed media inside the root and exclude metadata and linked paths.
The app stores no playback positions and does not infer viewing completion.

## Interface conventions

The app uses a single dark theme. Every colour is a named variable at the top
of `static/styles.css`, so palette changes happen in one place. Screens share
one card and button vocabulary, so meaning stays consistent:

- **Blue** (`--accent`) marks the main action or a chosen item: the single
  primary button per screen, the Recommend card, selected folders, and folder
  cards. **Green** means Favorite and **red** means Not for me; neither is used
  for anything else. Filled red is reserved for a final destructive confirm.
- Buttons are neutral (dark grey) by default. `primary` is used at most once per
  screen; `quiet` is text-only for low-stakes exits such as No feedback or
  Cancel; `pill` is the compact Home/Back style in the sticky toolbar;
  `compact` is for secondary utilities such as the Maintenance group.
- Lists of things to open, play, or pick are cards (`explore-card`), built by
  the shared helpers in `presentation.py`.
- Buttons, cards, and breadcrumbs glow in their own colour (`--glow`) as soon
  as a finger or primary mouse button presses them. The highlight stays while
  held, then fades over 0.32s on release; a cancelled touch clears it. Keyboard
  activation also plays the glow. Navigation never waits for the effect, so a
  fast screen change can cut the fade short. Reduce Motion uses a static
  highlight instead.

## Configuration

Settings load from the root `.env`, then real environment values override it.
Quoted Windows backslashes stay literal. Invalid paths, ports, or values stop
startup with a logged explanation. Old unprefixed variable names are not used.

| Variable | Meaning / default |
| --- | --- |
| `POCKET_MEDIA_MANAGER_MEDIA_ROOT` | Required existing absolute media directory outside the repository. |
| `POCKET_MEDIA_MANAGER_HOST` | Listener address, `0.0.0.0`. |
| `POCKET_MEDIA_MANAGER_PORT` | TCP port, `8787`. |
| `POCKET_MEDIA_MANAGER_PUBLIC_BASE_URL` | LAN HTTP(S) origin used by VLC; defaults to loopback and the configured port. |
| `POCKET_MEDIA_MANAGER_STREAM_FOLDER` | Folder inside the media root, `_stream`; cannot be metadata. |
| `POCKET_MEDIA_MANAGER_SUPPORTED_EXTENSIONS` | `.mp4,.mkv,.mov,.avi,.webm,.ts`. |
| `POCKET_MEDIA_MANAGER_MPV_PATH` | mpv executable name or path, `mpv`. |

The listener accepts loopback/private-network peers, validates local Host
headers, and rejects cross-origin browser requests. Every mutation requires a
matching Origin; proxy headers are not trusted. These checks serve a single
user on a trusted home LAN. Ownership controls which page operates the app;
it is not an account system.

Startup and runtime errors share repository `logs/server.log`, rotated at
5 MiB with three old files. The scheduled launch uses `pythonw.exe` directly,
so closing a setup terminal has no effect on the server. Foreground launch
shows logs and supports deliberate development reload. Task Scheduler tracks
the server itself; stop/restart does not launch a detached server process.
