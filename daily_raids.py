"""Post daily Wynncraft raid stats (players + guilds) to a Discord webhook.

Once a day, after the reset hour (18:00 Europe/Amsterdam by default), this
fetches the raid leaderboards from the Wynncraft API, compares them with the
snapshot saved at the previous reset and posts the difference per raid as two
table images. GitHub Actions runs it on a schedule.

    python daily_raids.py            # fetch, post to Discord, save snapshot
    python daily_raids.py --dry-run  # fetch and write the images to out/ only

Settings are environment variables, see .github/workflows/daily-raids.yml.
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

# short name -> (full name, leaderboard prefix in the Wynncraft API)
ALL_RAIDS = {
    "TNA": ("The Nameless Anomaly", "nameless"),
    "NOTG": ("Nest of the Grootslangs", "grootslang"),
    "TCC": ("The Canyon Colossus", "colossus"),
    "NOL": ("Orphion's Nexus of Light", "orphion"),
    "TWP": ("The Wartorn Palace", "fruma"),
}


def env_int(name, default):
    return int(os.environ.get(name) or default)


def env_raids():
    names = [n.strip().upper() for n in (os.environ.get("RAIDS") or "TNA").split(",") if n.strip()]
    unknown = [n for n in names if n not in ALL_RAIDS]
    if unknown or not names:
        sys.exit(f"Invalid RAIDS setting {os.environ.get('RAIDS')!r}. Choose from: {', '.join(ALL_RAIDS)}")
    return [(n, *ALL_RAIDS[n]) for n in names]


RAIDS = env_raids()  # raids to post, e.g. RAIDS="TNA" or RAIDS="TNA,NOTG"
TZ = ZoneInfo(os.environ.get("TIMEZONE") or "Europe/Amsterdam")
RESET_HOUR = env_int("RESET_HOUR", 18)  # local hour at which a new raid day starts
PLAYER_LIMIT = env_int("PLAYER_LIMIT", 1000)  # players tracked per raid (max 1000)
GUILD_LIMIT = env_int("GUILD_LIMIT", 100)  # guilds tracked per raid (max 1000)
SHOW_PLAYERS = env_int("SHOW_PLAYERS", 50)  # rows in the player image
SHOW_GUILDS = env_int("SHOW_GUILDS", 20)  # rows in the guild image

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "data" / "snapshot.json"
OUT_DIR = ROOT / "out"
API = "https://api.wynncraft.com/v3/leaderboards/{board}?resultLimit={limit}"
USER_AGENT = "wynn-daily-raids (github.com/actions)"


# ---------------------------------------------------------------- Wynncraft API

def fetch_board(board, limit):
    """Return {uuid: {name, rank, score, comp, gambits}} for one leaderboard."""
    url = API.format(board=board, limit=limit)
    for attempt in range(5):
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("ratelimit-reset", 30)) + 1)
            continue
        if r.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        entries = {}
        for rank, e in r.json().items():
            if e.get("restricted"):  # hidden profiles show up as "redacted", can't be tracked
                continue
            meta = e.get("metadata") or {}
            entries[e["uuid"]] = {
                "name": e["name"],
                "rank": int(rank),
                "score": e.get("score", 0),
                "comp": meta.get("completions", 0),
                "gambits": meta.get("gambits", 0),
            }
        return entries
    raise RuntimeError(f"Wynncraft API keeps failing for {board}")


def daily_rows(today, previous, show):
    """Players/guilds that did runs since the previous snapshot, best first."""
    rows = []
    for uuid, cur in today.items():
        old = previous.get(uuid)
        if old is None:  # was not in yesterday's tracked top, no baseline
            continue
        score, comp, gambits = cur["score"] - old[0], cur["comp"] - old[1], cur["gambits"] - old[2]
        if comp <= 0:
            continue
        rows.append({"rank": cur["rank"], "name": cur["name"], "score": score, "comp": comp, "gambits": gambits})
    rows.sort(key=lambda r: (-r["comp"], -r["score"], r["name"].lower()))
    return rows[:show]


# ---------------------------------------------------------------- raid days

def last_reset(now):
    """The most recent RESET_HOUR:00 in local time, at or before now."""
    local = now.astimezone(TZ)
    reset = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    return reset if local >= reset else reset - timedelta(days=1)


def reset_due(previous, now):
    """True when no snapshot was taken since the last reset."""
    return datetime.fromisoformat(previous["taken_at"]) < last_reset(now)


def period_label(previous_iso, now, interim):
    """Name a period after the local date of the reset that ends it (YYYY-MM-DD)."""
    start = datetime.fromisoformat(previous_iso).astimezone(TZ)
    first = start.date() + timedelta(days=1) if start.hour >= RESET_HOUR else start.date()
    if interim:  # the day is still running
        return first.isoformat()
    last = last_reset(now).date()
    if last <= first:
        return first.isoformat()
    # a run was missed, so this covers more than one day
    return f"{first} – {last}"


# ---------------------------------------------------------------- image

BG = (30, 31, 34)
TITLE_BG = (43, 45, 49)
ROW_COLORS = [(54, 56, 61), (30, 31, 34)]
LINE = (205, 205, 210)
ROW_LINE = (95, 97, 104)
TEXT = (220, 221, 222)
WHITE = (255, 255, 255)

FONTS = {
    False: ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "segoeui.ttf", "arial.ttf"],
    True: ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf"],
}


def font(size, bold=False):
    for name in FONTS[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


HEADERS = ["Daily Rank", "Rank", "Name", "Score Δ", "Comp Δ", "Gambit Δ", "Ratio"]
NAME_COL = 2


def table_cells(rows):
    return [
        [
            str(i),
            str(r["rank"]),
            r["name"],
            f"{r['score']:,}",
            f"{r['comp']:,}",
            f"{r['gambits']:,}",
            f"{r['gambits'] / r['comp']:.2f}",
        ]
        for i, r in enumerate(rows, 1)
    ]


def render_table(title, rows):
    """Draw a dark table like the usual raid leaderboard screenshots; returns PNG bytes."""
    f_title, f_head, f_cell = font(38), font(30, bold=True), font(30)
    pad, row_h, title_h = 16, 64, 78
    cells = table_cells(rows)

    widths = [
        max([f_head.getlength(h)] + [f_cell.getlength(row[i]) for row in cells]) + 2 * pad
        for i, h in enumerate(HEADERS)
    ]
    widths[NAME_COL] += 24  # a bit of air between the name and the numbers
    title_w = f_title.getlength(title) + 60
    if title_w > sum(widths):
        widths[NAME_COL] += title_w - sum(widths)
    widths = [int(w) for w in widths]
    width = sum(widths)
    height = title_h + row_h * (len(cells) + 1)

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, title_h], fill=TITLE_BG)
    d.text((width / 2, title_h / 2), title, font=f_title, fill=WHITE, anchor="mm")
    d.rectangle([0, title_h - 2, width, title_h + 1], fill=LINE)

    def draw_row(y, values, fnt, color):
        x = 0
        for i, (value, w) in enumerate(zip(values, widths)):
            if i == NAME_COL:
                d.text((x + pad, y + row_h / 2), value, font=fnt, fill=color, anchor="lm")
            else:
                d.text((x + w - pad, y + row_h / 2), value, font=fnt, fill=color, anchor="rm")
            x += w

    y = title_h
    draw_row(y, HEADERS, f_head, WHITE)
    y += row_h
    d.rectangle([0, y - 2, width, y + 1], fill=LINE)
    for i, values in enumerate(cells):
        d.rectangle([0, y + 2, width, y + row_h], fill=ROW_COLORS[i % 2])
        draw_row(y, values, f_cell, TEXT)
        y += row_h
        d.line([0, y, width, y], fill=ROW_LINE, width=1)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- Discord

def post_to_discord(webhook, content, images):
    """images: list of (filename, png bytes); Discord allows 10 per message."""
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    files = {f"files[{i}]": (name, png, "image/png") for i, (name, png) in enumerate(images)}
    for _ in range(5):
        if files:
            r = requests.post(webhook, data={"payload_json": json.dumps(payload)}, files=files, timeout=60)
        else:
            r = requests.post(webhook, json=payload, timeout=60)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return
    raise RuntimeError("Discord keeps rate limiting the webhook")


# ---------------------------------------------------------------- main

def build_messages(boards, previous, now, interim):
    label = period_label(previous["taken_at"], now, interim)
    slug = label.replace(" – ", "_to_")
    since = datetime.fromisoformat(previous["taken_at"]).astimezone(TZ)
    messages = []
    for short, full, key in RAIDS:
        images = []
        guilds = daily_rows(boards[f"{key}SrGuilds"], previous["boards"].get(f"{key}SrGuilds", {}), SHOW_GUILDS)
        players = daily_rows(boards[f"{key}SrPlayers"], previous["boards"].get(f"{key}SrPlayers", {}), SHOW_PLAYERS)
        if guilds:
            title = f"{short} Guild Raids {label}: Top {len(guilds)}"
            images.append((f"{short}_guilds_{slug}.png", render_table(title, guilds)))
        if players:
            title = f"{short} Player Raids {label}: Top {len(players)}"
            images.append((f"{short}_players_{slug}.png", render_table(title, players)))
        if images:
            if interim:
                line = f"Daily raids for **{label}** so far (since {since:%Y-%m-%d %H:%M}, manual run)"
            else:
                line = f"Daily raids for **{label}**"
            messages.append((f"## {full} ({short})\n{line}", images))
    if not messages:
        names = ", ".join(short for short, _, _ in RAIDS)
        messages.append((f"No {names} raid runs found for **{label}**{' so far' if interim else ''}.", []))
    return messages


def main():
    dry_run = "--dry-run" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook and not dry_run:
        sys.exit("DISCORD_WEBHOOK_URL is not set (add it as a GitHub Actions secret)")
    now = datetime.now(timezone.utc)
    previous = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else None

    # A manual "Run workflow" click posts the numbers so far and keeps the daily baseline,
    # unless a reset is due: then it does the daily post the schedule would have done.
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    due = previous is not None and reset_due(previous, now)
    interim = manual and not due

    # The schedule fires several times around the reset (GitHub can delay or drop runs);
    # only the first run after the reset posts.
    if previous and not manual and not dry_run and not due:
        taken = datetime.fromisoformat(previous["taken_at"]).astimezone(TZ)
        print(f"Nothing to do: last snapshot {taken:%Y-%m-%d %H:%M} is newer than the last reset "
              f"({last_reset(now):%Y-%m-%d %H:%M} {TZ.key}).")
        return

    boards = {}
    for short, _, key in RAIDS:
        boards[f"{key}SrPlayers"] = fetch_board(f"{key}SrPlayers", PLAYER_LIMIT)
        boards[f"{key}SrGuilds"] = fetch_board(f"{key}SrGuilds", GUILD_LIMIT)
        print(f"{short}: {len(boards[f'{key}SrPlayers'])} players, {len(boards[f'{key}SrGuilds'])} guilds")

    if previous is None:
        messages = [(
            "First snapshot saved. From now on the daily raids are posted here every day "
            "after the gambit reset.",
            [],
        )]
    else:
        messages = build_messages(boards, previous, now, interim)

    if dry_run:
        OUT_DIR.mkdir(exist_ok=True)
        for content, images in messages:
            print(content)
            for name, png in images:
                (OUT_DIR / name).write_bytes(png)
                print(f"  wrote out/{name} ({len(png) // 1024} KB)")
        return

    for content, images in messages:
        post_to_discord(webhook, content, images)
        time.sleep(1)

    if previous is None or not interim:
        snapshot = {
            "taken_at": now.isoformat(timespec="seconds"),
            "boards": {
                board: {uuid: [e["score"], e["comp"], e["gambits"]] for uuid, e in entries.items()}
                for board, entries in boards.items()
            },
        }
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
        print(f"Saved snapshot {snapshot['taken_at']}")


if __name__ == "__main__":
    main()
