# Pocket Media Manager

Pocket Media Manager helps you revisit moments in your local media collection.
A small Windows PC helper chooses a clip, opens VLC on your phone or mpv on your
PC, and lets you express how that recommendation felt.

There is one recommendation engine. **Favorite** is your lasting seal of
approval, separate from **Enjoyed it**, **Too soon**, **Not in the mood**,
**Less often**, and **Not for me**. **No feedback** covers testing or simply
moving on. **Something Else** keeps the Remake/Fix/Trim/Hold workflow for files
that need attention.

## Quick Start

From Git Bash in `pc-helper/`:

```bash
./scripts/bootstrap.sh
```

Edit `pc-helper/.env`:

```text
MEDIA_ROOT=E:\Hobby Disk
PUBLIC_BASE_URL=http://192.168.1.50:8787
MPV_PATH=mpv
```

Use the PC's LAN address so your phone can reach it. `MEDIA_ROOT` must already
exist. The helper initializes its SQLite database, backups, and logs inside
`MEDIA_ROOT\.pocket-media-manager`. Your videos remain in their existing folders.
No database server or cloud account is needed.

For development:

```bash
./scripts/run-dev.sh
```

For everyday Windows use, run once from an elevated PowerShell in `pc-helper/`:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-startup-task.ps1
```

The helper starts when you sign in, under your account, so mpv opens on your
desktop. Open `http://localhost:8787/` on the PC, or
`http://<PC_LAN_IP>:8787/` on your phone on the same network.

After editing `.env`, restart the scheduled task:

```powershell
Stop-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
Start-ScheduledTask -TaskName "Pocket Media Manager PC Helper"
```

## Everyday Use

- **Recommend** chooses from the eligible library.
- **Recommend with Selections** restricts that same engine to selected top-level
  folders and their descendants. Your selections remain checked after feedback.
- **Explore** browses the catalog and lets you pick a clip directly. Feedback
  returns you to its folder. All preference controls are on the feedback page;
  it offers restoration controls when Less often or Not for me is set. Use
  No feedback when testing a file. There is no separate preferences or test page.
- **Stream** browses the separate `_stream` folder. It records no encounters or
  preference feedback and leaves you on the listing for the next episode.
- **Address Other Feedback** resolves outstanding Remake/Fix/Trim/Hold tasks,
  making those files eligible again.
- **Refresh Library** discovers new files and checks for moves, edits, and
  deletions. A full scan also runs at startup; everyday navigation and
  recommendations use the stored catalog.
- **Start New Session** starts a fresh sitting, including clearing the
  session-only effect of Not in the mood. Too soon still applies for 24 hours.
- **Clean Up** removes selected unavailable database records and their history,
  never media files. Records with outstanding maintenance tasks are protected.
- **Reset Preferences** confirms before clearing Favorites, lasting preferences,
  and encounters. It retains the catalog and Something Else tasks and saves a
  database backup first.

Each browser remembers its player and selections and has its own 30-minute
session. Favorites and lasting clip preferences are shared. Feedback belongs
to an explicit encounter, so another browser or recommendation cannot steal it.
Unfinished feedback survives restarts and can be resumed or closed without a
rating. Opening a player is not counted as a completed viewing.

## Fresh Start and Current Limits

This version starts fresh: `_mpv_prefs.json` and `other_feedback.jsonl` are
ignored and left untouched. There is no legacy import or recommendation profile
selector. Renamed or adjusted media can become new records without inherited
preferences.

The helper does not measure video completion or watch duration. VLC/mpv handle
playback; the PC must be awake and reachable for phone streaming. Library changes
appear after startup or Refresh Library. `.ts` is now included in the default
extension list; existing `.env` extension overrides still need it added.

For this personal app, validation remains ad-hoc rather than a maintained test
suite. See [the helper README](pc-helper/README.md) for storage details, backup
and restore, API changes, and the checks to run after changes.
