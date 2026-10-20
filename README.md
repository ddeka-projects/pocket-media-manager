# Pocket Media Manager

A mobile-friendly local web app for rediscovering your media collection,
playing clips in VLC on your phone or mpv on your PC, and remembering feedback.
The Windows PC stores your data and serves the app over your home network.

## Windows setup

Use Windows PowerShell 5.1+ and Python 3.11+. From ordinary PowerShell in the
repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1
```

Edit the root `.env`, using [.env.example](.env.example) as the reference:

```text
POCKET_MEDIA_MANAGER_MEDIA_ROOT=E:\Hobby Disk
POCKET_MEDIA_MANAGER_PUBLIC_BASE_URL=http://192.168.1.50:8787
POCKET_MEDIA_MANAGER_MPV_PATH=mpv
```

The media directory must already exist and use an absolute path outside this
repository. Use the PC's LAN address for phone playback. Set the full mpv
executable path if it is not on PATH. Real environment variables override `.env`;
Windows backslashes are read literally.

Install automatic startup from ordinary PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

The **Pocket Media Manager** task starts immediately, then starts at your user
logon with normal privileges and no terminal window. It runs in your desktop
session so mpv can appear. The PC must stay signed in and awake. Open
`http://localhost:8787/` on the PC or `http://<PC_LAN_IP>:8787/` on your phone.

For first-time phone access or a changed port, run this once from **PowerShell
as Administrator under the same Windows account**:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1 -ConfigureFirewall
```

This configures only the Private-network TCP rule for the local subnet.
Ordinary installation leaves firewall rules unchanged.

After updating code or `.env`, double-click **`restart-server.cmd`**. Run
`bootstrap-windows.ps1` first when requirements change. The restart script stops
and waits for the exact scheduled task, starts it, checks readiness, and shows
the result. It requests UAC only if Windows denies task access. Finish active
operations first, then reload your browser and take control again.

To remove startup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
# Optional from elevated PowerShell: also remove this app's firewall rule.
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1 -RemoveFirewall
```

Removal retains media, database, and feedback. Foreground troubleshooting uses
`scripts\run-server.ps1`; development uses `scripts\run-dev.ps1`, optionally
with `-Reload`. Stop the scheduled server first. A lifetime guard rejects
another server using the same media directory, including on a different port.

## Everyday use

The first page takes control automatically. Other tabs and devices show
**Take Control** and cannot browse or operate the app until you transfer
control. **Release Control** frees it deliberately. There is no ownership
polling or timeout. A refresh creates a new page and may require takeover.
A previous page discovers a transfer on its next request; the server rejects
its actions. Active saves and player launches finish before control transfers.

- **Recommend** chooses from the eligible library. **Recommend with Selections**
  limits that same engine to selected folders and descendants.
- **Explore** browses folders and deliberately opens a clip, including clips
  temporarily excluded by a cooldown. Favorite clips stand out in green;
  Not for me clips stand out in red, with text badges too.
- **Stream** browses the separate `_stream` folder without recommendations or
  feedback. Existing media playback continues when browser control transfers.
- **Favorite** is a lasting preference independent of a response. **Enjoyed it**
  reports enjoyment; **Too soon** pauses recommendations for 72 hours;
  **Not in the mood** pauses them for 48 hours. **Less often** and **Not for me**
  apply until restored. Not for me also removes Favorite. **No feedback** closes
  a clip without rating it.
- **Something Else** records Remake/Fix/Trim/Hold work and hides the clip until
  resolved through **Address Other Feedback**.
- **Refresh Library** checks additions, moves, edits, and deletions. Startup
  also scans; ordinary browsing uses the stored catalog.
- **Clean Up** deletes selected unavailable database records, never media files.
  **Reset Preferences** clears preferences and encounters while retaining the
  catalog and maintenance tasks. Both save a backup first.

Only one clip can await feedback. Taking control on another device resumes
that same feedback without reopening the player. Finish it with a response,
No feedback, or a saved Something Else task. Favorite alone leaves it open.
Pending feedback, preferences, and cooldowns survive restarts. A browser cookie
remembers only player and folder choices; it grants no control.

## Data, logs, and checks

SQLite and the latest seven verified backup snapshots live inside
`POCKET_MEDIA_MANAGER_MEDIA_ROOT\.pocket-media-manager`. Keep that directory
with its journal files when backing up stopped app data. Your media stays in
its existing folders. [Storage and restore details](docs/usage.md#storage-and-backups).

Startup and runtime logs are in repository-level `logs\server.log`, rotated
at 5 MiB with three previous files. `/health` reports readiness after the
startup scan. [Design and configuration](docs/design.md) describes the code
layout and operating conventions.

Run the maintained tests from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m black --check pocket_media_manager scripts tests
```

Tests cover configuration, browser takeover and concurrent operations,
request protections, durable feedback, snapshots, and streaming ranges/HEAD.
JavaScript behavior checks use Node.js or macOS JavaScriptCore when available;
PowerShell lifecycle checks use `powershell` or `pwsh` when available.
Windows Task Scheduler, UAC/firewall behavior, and real VLC/mpv playback still
need checks on the PC and phone. The app does not transcode or measure viewing
completion. [Usage guide](docs/usage.md).
