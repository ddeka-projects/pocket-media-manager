# Pocket Media Manager

Pocket Media Manager is a personal Windows PC to phone media recommender.

The current helper is intentionally small: it keeps your custom weighted
recommendation logic on the PC, serves the selected local media file over your
home Wi-Fi, and gives your phone a tiny browser page for launching and recording
feedback.

The project does not replace Plex as a full media library. It exists because
Plex can play files well, but it does not know your custom recommendation
preferences such as unseen boosts, soft dislike penalties, pending items, and
Something Else blocks.

## What Is In This Repo

- `pc-helper/`
  FastAPI helper that scans a Windows media folder, picks a recommendation,
  streams the selected file, and records like/dislike/pending feedback.

## Quick Start

Open Git Bash in `pc-helper/` and run:

```bash
./scripts/bootstrap.sh
```

Then edit `pc-helper\.env` so `PUBLIC_BASE_URL` uses this PC's LAN IP address:

```text
PUBLIC_BASE_URL=http://192.168.1.50:8787
```

The player is chosen from the home page UI (VLC for phone, mpv for PC), not
from `.env`.

For everyday phone use, install the Windows startup task once from an elevated
PowerShell opened in `pc-helper`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

After that, the helper starts automatically when you sign in. It runs under your
user account so programs it launches (such as mpv) appear on your desktop.

When you edit `pc-helper\.env`, restart the scheduled task from an elevated
PowerShell so the running helper loads the new settings:

```powershell
Stop-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
Start-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
```

For active development, stop that always-on task and then run the development
server:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\remove-startup-task.ps1
```

```bash
./scripts/run-dev.sh
```

You can also run the normal no-reload server manually:

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\run-server.ps1
```

Test locally:

```text
http://localhost:8787/
http://localhost:8787/health
```

Test from your phone on the same Wi-Fi:

```text
http://<PC_LAN_IP>:8787/
```

For this personal app, validation is intentionally ad-hoc: run the helper and
try the workflow from your phone instead of maintaining an automated test
suite.

Choose **VLC (Phone)** or **mpv (PC)** from the player toggle on the home page.
Below it, choose a recommendation profile: **Balanced** (default), **Surprise
Me**, **Comfort Zone**, or **Pending Review**. Then tap `Recommend` to open the
chosen player. Use `Recommend with Selections` when you want to choose one or
more top-level folders under the configured media root for a single
recommendation. Use `Stream` when you want to browse the configured
`STREAM_FOLDER` under the media root and play files directly without
recommendation scoring or feedback. Use `Explore` when you want to browse
folders and pick a supported media file directly. Use `Clean Up` to remove
selected orphan preference records for media files that no longer exist under
the media root. The selection page shows the media root at the top. When you
switch back to the browser after a selected recommendation, feedback returns
you to the selection page with the same eligible folders checked. Use `Cancel`
on that page to clear the selection and return home. After choosing a file
through `Explore`, submitting feedback returns you to the Explore folder that
contained the file. Use `Something Else` when the file needs Remake, Fix, Trim,
or Hold handling without changing its like/dislike/pending score. Files with
unresolved Something Else feedback are hidden from recommendations and Explore
until they are cleared through `Address Other Feedback`.

See `pc-helper/README.md` for the full setup and phone workflow.
