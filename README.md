# SightMap availability tracker — AVE Santa Clara

Small Python job that pulls the public [SightMap](https://sightmap.com) JSON for **AVE Santa Clara**, saves each run to **SQLite**, compares to the last run, and optionally posts to a **Discord webhook** when something changes.

Human-facing availability page: [aveliving.com/check-availability/santa-clara](https://www.aveliving.com/check-availability/santa-clara).

## What it detects

- New units listed vs previous snapshot  
- Units no longer listed  
- Price changes  
- `available_on` / move-in date changes  

First successful run creates a **baseline** snapshot only (no Discord diff alert by default).

## Setup

```bash
cd sightmap-tracker
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `DISCORD_WEBHOOK_URL` to your channel webhook (Discord → channel → Integrations → Webhooks). Leave it blank to only print and append `changes.log`.

Run manually:

```bash
python sightmap_tracker.py
```

Artifacts (default paths, same folder as the script):

- `sightmap.db` — snapshots  
- `changes.log` — appended summary each run  

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_WEBHOOK_URL` | _(empty)_ | Post the full report to Discord (see below) |
| `DISCORD_ONLY_ON_CHANGES` | `false` | If `true`, skip Discord when there is no diff (still prints + logs) |
| `SIGHTMAP_URL` | AVE Santa Clara SightMap API URL | Override if needed |
| `DB_PATH` | `sightmap.db` | SQLite file |
| `LOG_PATH` | `changes.log` | Append-only log |

**Each run** prints and logs: **(1)** header, **(2)** all listed units sorted by **floor then unit #** (plan, sq ft, rent, availability text), **(3)** a **Changes** section (baseline message, or diff vs previous snapshot). With `DISCORD_WEBHOOK_URL` set, the same report is sent to Discord (split into multiple messages if it exceeds Discord’s length limit). The **GitHub Actions** workflow sets `DISCORD_ONLY_ON_CHANGES=true` so Discord is notified **only when the diff is non-empty**; the full report is still in each **workflow run log**. For local runs, set `DISCORD_ONLY_ON_CHANGES=true` in `.env` for the same behavior.

## GitHub Actions (every 2 hours, UTC)

The repo includes [`.github/workflows/sightmap-tracker.yml`](.github/workflows/sightmap-tracker.yml). It:

- Runs on a schedule (`cron` every **2 hours**, UTC) and supports **Run workflow** manually.  
- Restores **`sightmap.db`** and **`changes.log`** from [Actions cache](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows) so the **previous snapshot** exists on the next run (first run after a cache miss is baseline only).  
- Passes `DISCORD_WEBHOOK_URL` from **repository secrets** (never commit the URL).

**One-time setup**

1. Push this project to a GitHub repo (private is fine).  
2. **Settings → Secrets and variables → Actions → New repository secret**  
   - Name: `DISCORD_WEBHOOK_URL`  
   - Value: your webhook URL (use a **new** webhook if an old one was ever pasted into chat or a ticket).  
3. Confirm the workflow appears under **Actions**; use **Run workflow** to test.  

Scheduled workflows only run on the **default** branch. Cron is **UTC** (not Pacific).

## macOS LaunchAgent (every 2 hours)

1. Install the venv and `.env` as above.  
2. Copy the example plist and **replace** placeholders with your real paths (use absolute paths):

   ```bash
   cp launchagents/com.sightmap.ave-santa-clara.plist.example \
      ~/Library/LaunchAgents/com.sightmap.ave-santa-clara.plist
   ```

   Edit the plist: `WorkingDirectory`, `ProgramArguments` (venv `python` + `sightmap_tracker.py`), `StandardOutPath` / `StandardErrorPath`, and either `DISCORD_WEBHOOK_URL` in the plist **or** rely on `.env` in `WorkingDirectory` (dotenv loads automatically).

3. Load the agent:

   ```bash
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sightmap.ave-santa-clara.plist
   ```

   Or on older macOS:

   ```bash
   launchctl load ~/Library/LaunchAgents/com.sightmap.ave-santa-clara.plist
   ```

4. Unload when needed:

   ```bash
   launchctl bootout gui/$(id -u)/com.sightmap.ave-santa-clara
   ```

`StartInterval` is **7200** seconds (2 hours). `RunAtLoad` runs once when you log in / load the agent.

**Note:** This runs on **your Mac**, not in the cloud. It only fires while the Mac is awake enough for LaunchAgent and has network. For 24/7 hosting, run the same script on a small VPS with `cron`.

## Security

Treat the Discord webhook URL like a password. It is in `.gitignore` via `.env`. If it leaks, delete the webhook in Discord and create a new one.

## Troubleshooting

- **No Discord:** Check `DISCORD_WEBHOOK_URL`, stderr in `sightmap-tracker.err.log`, and “Discord notify failed” lines in `changes.log`.  
- **Fetch errors:** SightMap may be down or blocking; the script exits without writing a snapshot so you do not get a false “all units removed” diff.  
- **Do Not Disturb / Focus:** macOS may suppress banner notifications for other apps; Discord alerts are independent and show wherever you use Discord.

## No Playwright

The public JSON endpoint is used directly. Playwright is not required unless that API stops working and you choose to scrape instead.
