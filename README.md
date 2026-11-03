# Wynncraft daily raids → Discord

Posts the daily raid runs from the Wynncraft leaderboards to a Discord channel:
for each chosen raid a guild table and a player table with Daily Rank, Rank,
Score Δ, Comp Δ, Gambit Δ and Ratio, plus a line saying which day it is for
(for example `Daily raids for 2026-11-03`).

- Only **TNA** by default; any of TNA, NOTG, TCC, NOL and TWP can be turned on
- A raid day resets at **18:00 Dutch time**, and the overview is posted right after the reset
- Tracks the **top 1000 players** and **top 100 guilds** per raid
- Posts the **top 50 players** and **top 20 guilds** of the day
- Free: GitHub Actions runs it, a Discord webhook posts it, and your PC can stay off

## Setup (about 10 minutes)

### 1. Create a Discord webhook
1. In Discord, open the channel settings: ⚙️ **Edit Channel → Integrations → Webhooks**
2. **New Webhook**, pick a name and avatar (for example "Raid Stats")
3. Click **Copy Webhook URL** and keep it somewhere safe. It's a secret: anyone who has it can post in that channel.

You need the "Manage Webhooks" permission on the server.

### 2. Put the files on GitHub
1. Create a free account on [github.com](https://github.com) if you don't have one
2. **New repository**, for example `wynn-daily-raids`
   - **Public**: unlimited free minutes. The webhook URL stays secret because it's stored as a secret.
   - **Private** works too: 2000 free minutes per month, and this uses roughly 2 minutes per day.
3. In the empty repository, click **uploading an existing file** and drag in *everything* from this folder, including the `.github` folder. Then click **Commit changes**.
4. Check that `.github/workflows/daily-raids.yml` is in the repository.
   (If the `.github` folder didn't come along: **Add file → Create new file**, name it `.github/workflows/daily-raids.yml` and paste the contents in.)

### 3. Add the webhook as a secret
**Settings → Secrets and variables → Actions → New repository secret**
- Name: `DISCORD_WEBHOOK_URL`
- Secret: the webhook URL from step 1

### 4. Start it
1. Open the **Actions** tab (if GitHub asks, click "I understand my workflows, go ahead and enable them")
2. Click **Daily raids** on the left, then **Run workflow**
3. About a minute later Discord shows *"First snapshot saved…"*

From then on it posts every day shortly after **18:00** Dutch time. GitHub usually starts scheduled runs a few minutes late, sometimes up to half an hour. The first overview covers the time from that first snapshot to the next 18:00.

## Settings

Everything is in the `env:` block of `.github/workflows/daily-raids.yml`:

| Setting | Meaning |
|---|---|
| `RAIDS` | Which raids to post, comma separated. `TNA` = only TNA, `TNA,NOTG` = TNA and NOTG. Choose from `TNA`, `NOTG`, `TCC`, `NOL`, `TWP`. |
| `TIMEZONE` | Your time zone, for example `Europe/Amsterdam` or `Europe/Brussels` |
| `RESET_HOUR` | Hour (0–23, your time) at which a new raid day starts |
| `PLAYER_LIMIT` | Number of players tracked per raid (max 1000) |
| `GUILD_LIMIT` | Number of guilds tracked per raid (max 1000) |
| `SHOW_PLAYERS`, `SHOW_GUILDS` | Number of rows in the images |

**If you change `RESET_HOUR` or `TIMEZONE`**, also change the `cron` line at the top. GitHub only knows UTC, so it has to fire in the UTC hour of your reset in both summer and winter time, plus one hour after as a backup. For 18:00 Dutch time that's `"0 16,17,18 * * *"`. The extra runs do nothing when the day has already been posted.

## Good to know
- The date in the message is the day that **ends** at the reset. The post at 18:00 on 2026-11-03 covers 2026-11-02 18:00 → 2026-11-03 18:00, and says `Daily raids for 2026-11-03`.
- **Run workflow by hand** posts the results *so far* for the running day. It does **not** change the daily snapshot, so you can test as often as you like.
- A player or guild only shows up if they were in the tracked top at both the previous and the current snapshot. Newcomers count from the next day. Hidden profiles (shown as "redacted" on the leaderboard) can't be tracked.
- If a run fails (Wynncraft or Discord down), GitHub emails you and the snapshot isn't saved. The next post then covers both days and shows a date range, for example `2026-11-02 – 2026-11-03`.
- `data/snapshot.json` is the snapshot from the last reset. The workflow commits it every day, and you never need to touch it.
- If GitHub ever emails that the scheduled workflow was disabled (after a long time without activity), go to **Actions → Daily raids → Enable workflow**.

## Testing locally (optional)
```bash
pip install -r requirements.txt
python daily_raids.py --dry-run
```
This fetches the data and writes the images to `out/` without posting anything.
