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
| `service.py` | Coordinate recommendations, browser choices, and pending feedback. |
| `presentation.py`, `static/` | Server-rendered HTML, styles, and browser navigation. |
| `playback.py`, `state.py` | mpv launch and bounded temporary VLC links. |
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
identity. The browser preference cookie contains no ownership grant.

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
