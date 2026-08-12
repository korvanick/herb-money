#!/usr/bin/env python3
"""Degrime profit dashboard for Old School RuneScape.

Buy grimy herbs off the Grand Exchange, clean a full inventory per cast with
Degrime (70 Magic, Lunar spellbook), sell the clean herbs back. Live prices
come from the RuneScape Wiki real-time prices API.
"""

import argparse
import json
import math
import os
import shutil
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple

# --- API -------------------------------------------------------------------
API = "https://prices.runescape.wiki/api/v1/osrs"
PROJECT_URL = "https://github.com/korvanick/herb-money"
CONTACT_ENV = "HERB_MONEY_CONTACT"
TIMEOUT = 20

# Jagex publishes skills but never a player's coins, so --capital stays a
# number you supply. Each game mode keeps its own board.
HISCORES = "https://secure.runescape.com/m={board}/index_lite.json?player={name}"
HISCORE_BOARDS = {
    "main": "hiscore_oldschool",
    "ironman": "hiscore_oldschool_ironman",
    "hardcore": "hiscore_oldschool_hardcore_ironman",
    "ultimate": "hiscore_oldschool_ultimate",
}


def user_agent(contact):
    """A descriptive, contactable User-Agent, which the wiki requires.

    It identifies the project rather than any person by default: a clone of
    this repo must not report a stranger's traffic under the author's name.
    Whoever runs it adds their own handle with --contact or $HERB_MONEY_CONTACT.
    """
    project = f"herb-money - Degrime profit dashboard (+{PROJECT_URL})"
    return f"{project} - {contact}" if contact else project

POLL_RATE = 60
# /5m and /1h only produce new data on their own cadence, so re-requesting them
# every poll is wasted load on the API for identical bytes.
LATEST_TTL = 0
FIVE_MIN_TTL = 300
HOUR_TTL = 3600
# One request per herb, so this is the expensive one. An hour-long swing does
# not meaningfully change in a minute.
TIMESERIES_TTL = 900
TIMESERIES_POINTS = 12          # 12 x 5m = the last hour

# --- Method ----------------------------------------------------------------
TICKS_PER_INVENTORY = 10        # one cast plus banking, played tick-perfect
SECONDS_PER_TICK = 0.6
HERBS_PER_INVENTORY = 27        # Degrime cleans the whole inventory per cast
NATURE_RUNES_PER_CAST = 2       # plus 4 earth, assumed free from an earth staff
MAGIC_XP_PER_CAST = 83
DEGRIME_MAGIC_LEVEL = 70
DEGRIME_XP_RATE = 0.5           # Degrime grants half the manual Herblore XP

CASTS_PER_HOUR = 3600 / (TICKS_PER_INVENTORY * SECONDS_PER_TICK)
HERBS_PER_HOUR = CASTS_PER_HOUR * HERBS_PER_INVENTORY

# Nobody holds tick-perfect input for a full hour. Used only when the player
# has not told us their own figure with --focus.
FOCUS_LOW = 0.67
FOCUS_HIGH = 0.80

# --- Grand Exchange --------------------------------------------------------
TAX_RATE = 0.02                 # raised from 1% on 2025-05-29
TAX_FREE_BELOW = 50
TAX_CAP = 5_000_000
NATURE_RUNE_ID = 561

LOW_VOLUME = 2000               # 1h clean-side trades below this are a trap
STALE_AFTER = 600               # a price older than 10 minutes is a guess

# (grimy id, clean id, name, Herblore level to clean, manual Herblore XP)
HERBS = (
    (199, 249, "Guam", 3, 2.5),
    (201, 251, "Marrentill", 5, 3.8),
    (203, 253, "Tarromin", 11, 5.0),
    (205, 255, "Harralander", 20, 6.3),
    (207, 257, "Ranarr", 25, 7.5),
    (3049, 2998, "Toadflax", 30, 8.0),
    (209, 259, "Irit", 40, 8.8),
    (211, 261, "Avantoe", 48, 10.0),
    (213, 263, "Kwuarm", 54, 11.3),
    (30094, 30097, "Huasca", 58, 11.8),
    (3051, 3000, "Snapdragon", 59, 11.8),
    (215, 265, "Cadantine", 65, 12.5),
    (2485, 2481, "Lantadyme", 67, 13.1),
    (217, 267, "Dwarf weed", 70, 13.8),
    (219, 269, "Torstol", 75, 15.0),
)

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GREY = "\033[90m"

HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

STALE_MARK = "*"                # price older than STALE_AFTER
COSTLY_MARK = "$"               # more capital than --capital allows
SUSPECT_MARK = "?"              # sell-high print below sell-low, so unusable

Column = namedtuple("Column", "key heading width right")

# A price keeps its own fixed field so the digits line up down the column;
# whatever trails it (a delta, a marker) hangs off to the right instead of
# shoving the number leftwards.
PRICE_WIDTH = 6
DELTA_WIDTH = 5
MARK_WIDTH = 1
PRICED_WIDTH = PRICE_WIDTH + 1 + DELTA_WIDTH
MARKED_WIDTH = PRICE_WIDTH + MARK_WIDTH


def split_cell(value, trailing, trailing_width=DELTA_WIDTH, gap=" "):
    return f"{value:>{PRICE_WIDTH}}{gap}{trailing:<{trailing_width}}"


def marked_cell(value, trailing):
    return split_cell(value, trailing, MARK_WIDTH, "")


COLUMNS = (
    Column("name", "Herb", 13, False),
    Column("level", "Lvl", 3, True),
    Column("buy", split_cell("Buy", "Δ"), PRICED_WIDTH, True),
    Column("sell_low", split_cell("Sell-L", "Δ"), PRICED_WIDTH, True),
    Column("sell_high", marked_cell("Sell-H", ""), MARKED_WIDTH, True),
    Column("prof_low", "Prof-L", 7, True),
    Column("prof_high", "Prof-H", 7, True),
    Column("hr_low", "GP/hr-L", 8, True),
    Column("hr_high", "GP/hr-H", 8, True),
    Column("swing", "1h Sw", 6, True),
    Column("age", "Age", 5, True),
    Column("xp_hr", "XP/hr", 6, True),
    Column("capital", "Cap/hr", 7, True),
    Column("roi", "ROI%", 6, True),
    Column("volume", marked_cell("1h Vol", ""), MARKED_WIDTH, True),
)
# Sacrificed first as the terminal narrows. Name and GP/hr-L always survive, so
# the table bottoms out at MIN_WIDTH instead of wrapping into mush. Price cells
# carry their own delta, so a narrow window never loses direction or magnitude.
DROP_ORDER = (
    "level", "xp_hr", "age", "capital", "roi", "sell_high", "prof_high",
    "hr_high", "prof_low", "swing", "buy", "sell_low", "volume",
)
GAP = "  "
SEPARATOR = "  ·  "
MIN_WIDTH = 23
FALLBACK_SIZE = (138, 24)

Row = namedtuple("Row", (
    "name level locked buy buy_dir sell_low sell_high sell_dir suspect "
    "prof_low prof_high hr_low hr_high capital roi swing age stale xp_hr volume"
))


class PriceFeed:
    """Fetches wiki endpoints no faster than each one actually updates."""

    def __init__(self, contact=None):
        self._cache = {}
        self._headers = {"User-Agent": user_agent(contact)}

    def get(self, path, ttl):
        now = time.monotonic()
        cached = self._cache.get(path)
        if cached and now - cached[0] < ttl:
            return cached[1]

        request = urllib.request.Request(f"{API}/{path}", headers=self._headers)
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode())["data"]

        self._cache[path] = (now, payload)
        return payload

    def swing(self, item_id):
        """Spread of the 5m buy price over the last hour, or None if unknown.

        This is the number that decides whether a margin is real: an offer
        placed at the price on screen only fills if the price stays there.
        """
        try:
            points = self.get(f"timeseries?timestep=5m&id={item_id}", TIMESERIES_TTL)
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            return None

        prices = [
            point["avgLowPrice"] for point in points[-TIMESERIES_POINTS:]
            if point.get("avgLowPrice")
        ]
        return max(prices) - min(prices) if len(prices) > 1 else None


def fetch_levels(username, mode, contact):
    """Skill levels from the OSRS hiscores as {skill: level}.

    Returns (levels, problem): exactly one is None. A hiscores outage must
    not take the dashboard down, so the caller falls back to the flags.
    """
    url = HISCORES.format(board=HISCORE_BOARDS[mode], name=urllib.parse.quote(username))
    request = urllib.request.Request(url, headers={"User-Agent": user_agent(contact)})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            skills = json.loads(response.read().decode())["skills"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            other = " or ".join(m for m in HISCORE_BOARDS if m != mode)
            return None, (f"{username} is not on the {mode} hiscores. Check the spelling, "
                          f"or try --mode {other}.")
        return None, f"Hiscores returned {error.code} for {username}; using the levels given."
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        return None, f"Could not reach the hiscores ({error}); using the levels given."

    # An unranked skill comes back as -1, which is not a level.
    return {skill["name"]: max(1, skill["level"]) for skill in skills}, None


def ge_tax(price):
    if price < TAX_FREE_BELOW:
        return 0
    return min(math.floor(price * TAX_RATE), TAX_CAP)


def net_profit(sell, buy, rune_cost):
    return sell - ge_tax(sell) - buy - rune_cost


def gp(value):
    magnitude = abs(value)
    # 999,700 would otherwise round to a nonsensical "1000k".
    if magnitude >= 999_500:
        return f"{value / 1_000_000:.2f}M"
    if magnitude >= 10_000:
        return f"{value / 1000:.0f}k"
    return f"{value:,.0f}"


def compact(value):
    """Tight form for deltas and swings, which share a cell with a price."""
    return f"{value / 1000:.1f}k" if abs(value) >= 1000 else f"{value:,.0f}"


def age_text(seconds):
    minutes = seconds / 60
    return f"{minutes:.0f}m" if minutes < 60 else f"{minutes / 60:.1f}h"


def trend(current, previous, rising_is_good):
    """Movement against the 5m average as (suffix, colour).

    The suffix rides on the price itself rather than occupying its own column,
    so both direction and magnitude survive in a narrow window.
    """
    if not previous or current == previous:
        return "", ""

    diff = current - previous
    rising = diff > 0
    good = rising if rising_is_good else not rising
    return f"{'↑' if rising else '↓'}{compact(abs(diff))}", GREEN if good else RED


def price_age(entry, now):
    stamps = [entry.get("highTime"), entry.get("lowTime")]
    newest = max((stamp for stamp in stamps if stamp), default=None)
    return now - newest if newest else None


def build_rows(feed, options):
    latest = feed.get("latest", LATEST_TTL)
    five_min = feed.get("5m", FIVE_MIN_TTL)
    hourly = feed.get("1h", HOUR_TTL)
    now = time.time()

    nature_price = latest.get(str(NATURE_RUNE_ID), {}).get("high") or 0
    rune_cost = NATURE_RUNES_PER_CAST * nature_price / HERBS_PER_INVENTORY
    herbs_per_hour = HERBS_PER_HOUR * options.focus

    rows = []
    for grimy_id, clean_id, name, level, clean_xp in HERBS:
        grimy = latest.get(str(grimy_id)) or {}
        clean = latest.get(str(clean_id)) or {}
        # Buy patiently at the instant-sell price; instant-buying this method
        # is a loss on most herbs, so it is deliberately not modelled.
        buy = grimy.get("low")
        sell_low, sell_high = clean.get("low"), clean.get("high")
        if not buy or not sell_low or not sell_high:
            continue

        prof_low = net_profit(sell_low, buy, rune_cost)
        prof_high = net_profit(sell_high, buy, rune_cost)
        capital = buy * herbs_per_hour

        # Volume that matters is the clean side: that is what you have to dump.
        volume_data = hourly.get(str(clean_id), {})
        volume = volume_data.get("highPriceVolume", 0) + volume_data.get("lowPriceVolume", 0)

        ages = [age for age in (price_age(grimy, now), price_age(clean, now)) if age]
        age = max(ages) if ages else None

        rows.append(Row(
            name=name,
            level=level,
            locked=level > options.herblore or (
                options.capital is not None and capital > options.capital),
            buy=buy,
            buy_dir=trend(buy, five_min.get(str(grimy_id), {}).get("avgLowPrice"), False),
            sell_low=sell_low,
            sell_high=sell_high,
            sell_dir=trend(sell_low, five_min.get(str(clean_id), {}).get("avgLowPrice"), True),
            # high and low are independent last-trade prints, not an ordered
            # pair, so an inverted one means the high price is stale.
            suspect=sell_high < sell_low,
            prof_low=prof_low,
            prof_high=prof_high,
            hr_low=prof_low * herbs_per_hour,
            hr_high=prof_high * herbs_per_hour,
            capital=capital,
            roi=prof_low / buy * 100,
            swing=feed.swing(grimy_id) if options.swing else None,
            age=age,
            stale=age is not None and age > STALE_AFTER,
            xp_hr=clean_xp * DEGRIME_XP_RATE * herbs_per_hour,
            volume=volume,
        ))

    key = (lambda row: (row.locked, -row.roi)) if options.sort == "roi" else (
        lambda row: (row.locked, -row.hr_low))
    rows.sort(key=key)
    return rows, nature_price


def profit_colour(value):
    return GREEN if value > 0 else RED if value < 0 else GREY


def realistic(value, focus):
    """What to actually expect per hour.

    With --focus the rate is already the player's own, so it stands alone;
    without it, the tick-perfect rate only means something as a band.
    """
    if focus == 1.0:
        return f"{gp(value * FOCUS_LOW)}-{gp(value * FOCUS_HIGH)}/hr"
    return f"{gp(value)}/hr"


def swing_colour(swing, margin):
    """A swing wider than the margin means the margin is noise."""
    if margin <= 0 or swing >= margin:
        return RED
    return YELLOW if swing >= margin / 2 else GREEN


def cells_for(row, options):
    thin = row.volume < LOW_VOLUME
    marks = (STALE_MARK if row.stale else "") + (
        COSTLY_MARK if options.capital is not None and row.capital > options.capital else "")

    buy_suffix, buy_colour = row.buy_dir
    sell_suffix, sell_colour = row.sell_dir
    return {
        "name": (row.name + marks, BOLD),
        "level": (str(row.level), ""),
        "buy": (split_cell(f"{row.buy:,}", buy_suffix), buy_colour),
        "sell_low": (split_cell(f"{row.sell_low:,}", sell_suffix), sell_colour),
        "sell_high": (marked_cell(f"{row.sell_high:,}",
                                  SUSPECT_MARK if row.suspect else ""),
                      YELLOW if row.suspect else ""),
        "prof_low": (f"{row.prof_low:,.1f}", profit_colour(row.prof_low)),
        "prof_high": (f"{row.prof_high:,.1f}",
                      YELLOW if row.suspect else profit_colour(row.prof_high)),
        "hr_low": (gp(row.hr_low), profit_colour(row.hr_low)),
        "hr_high": (gp(row.hr_high),
                    YELLOW if row.suspect else profit_colour(row.hr_high)),
        "capital": (gp(row.capital), ""),
        "roi": (f"{row.roi:.2f}", profit_colour(row.roi)),
        "swing": ((compact(row.swing), swing_colour(row.swing, row.prof_low))
                  if row.swing is not None else ("-", GREY)),
        "age": ((age_text(row.age), YELLOW if row.stale else "")
                if row.age is not None else ("-", GREY)),
        "xp_hr": (gp(row.xp_hr), CYAN),
        "volume": (marked_cell(f"{row.volume:,}", "!" if thin else ""),
                   YELLOW if thin else ""),
    }


def align(text, column):
    return f"{text:>{column.width}}" if column.right else f"{text:<{column.width}}"


def table_width(columns):
    return sum(column.width for column in columns) + len(GAP) * (len(columns) - 1)


def fit_columns(width, protected=(), excluded=()):
    """Drop the least important columns until the table fits the terminal.

    Whatever the table is sorted by is protected, so the ordering never looks
    arbitrary because its own column was dropped.
    """
    columns = [column for column in COLUMNS if column.key not in excluded]
    for key in DROP_ORDER:
        if table_width(columns) <= width:
            break
        if key in protected:
            continue
        columns = [column for column in columns if column.key != key]
    return columns


def format_row(cells, columns, locked):
    if locked:
        # A dimmed row carries no other colour, so pad first and dim the whole.
        return DIM + GAP.join(align(cells[c.key][0], c) for c in columns) + RESET
    parts = []
    for column in columns:
        text, colour = cells[column.key]
        padded = align(text, column)
        parts.append(f"{colour}{padded}{RESET}" if colour else padded)
    return GAP.join(parts)


def pack(segments, width):
    """Greedily pack (plain, rendered) segments into separator-joined lines."""
    lines, plain, rendered = [], "", ""

    def flush():
        nonlocal plain, rendered
        if rendered:
            lines.append(rendered)
        plain, rendered = "", ""

    for segment_plain, segment_rendered in segments:
        if len(segment_plain) > width:
            # Too long to share a line with anything; wrap it, losing its colour.
            flush()
            lines.extend(textwrap.wrap(segment_plain, width))
            continue
        if plain and len(plain) + len(SEPARATOR) + len(segment_plain) > width:
            flush()
        if plain:
            plain += SEPARATOR + segment_plain
            rendered += f"{GREY}{SEPARATOR}{RESET}" + segment_rendered
        else:
            plain, rendered = segment_plain, segment_rendered
    flush()
    return lines


def wrap(text, width, colour):
    return [f"{colour}{line}{RESET}" for line in textwrap.wrap(text, max(width, 20))]


def header_segments(options, nature_price):
    segments = [("DEGRIME PROFIT", f"{BOLD}DEGRIME PROFIT{RESET}")]
    if options.username and not options.notice:
        # Name the source, but only when the lookup actually supplied the
        # levels: the hiscores are a periodic snapshot, so a level read from
        # them can trail what you actually have.
        text = f"{options.username} ({options.mode})"
        segments.append((text, f"{CYAN}{options.username}{RESET} {GREY}({options.mode}){RESET}"))
    segments += [
        (f"Herblore {options.herblore}", f"Herblore {CYAN}{options.herblore}{RESET}"),
        (f"Magic {options.magic}", f"Magic {CYAN}{options.magic}{RESET}"),
    ]
    if options.focus != 1.0:
        text = f"Focus {options.focus:.0%}"
        segments.append((text, f"Focus {CYAN}{options.focus:.0%}{RESET}"))
    if options.capital is not None:
        text = f"Capital {gp(options.capital)}"
        segments.append((text, f"Capital {CYAN}{gp(options.capital)}{RESET}"))
    segments.append((f"Nature rune {nature_price:,}gp",
                     f"Nature rune {CYAN}{nature_price:,}gp{RESET}"))
    return segments


def footnotes(rows, options, shown):
    """Explain only markers that are both present and currently on screen.

    A marker's column can be dropped by the responsive layout, and explaining
    a symbol the reader cannot see is worse than saying nothing.
    """
    notes = []
    if any(row.stale for row in rows):
        notes.append(f"{STALE_MARK} price older than {STALE_AFTER // 60}m")
    if "sell_high" in shown and any(row.suspect for row in rows):
        notes.append(f"{SUSPECT_MARK} sell-high print is below sell-low, so Prof-H is unreliable")
    if options.capital is not None and any(row.capital > options.capital for row in rows):
        notes.append(f"{COSTLY_MARK} needs more than your capital")
    if "volume" in shown and any(row.volume < LOW_VOLUME for row in rows):
        notes.append(f"! under {LOW_VOLUME:,} clean trades in the last hour")
    return notes


def render(rows, nature_price, options, status, width):
    columns = fit_columns(
        width,
        protected=("roi",) if options.sort == "roi" else (),
        excluded=() if options.swing else ("swing",),
    )
    shown = {column.key for column in columns}
    rule = f"{GREY}{'─' * table_width(columns)}{RESET}"
    heading = GAP.join(align(column.heading, column) for column in columns)

    lines = pack(header_segments(options, nature_price), width)
    if options.notice:
        lines += wrap(options.notice, width, YELLOW)
    if options.magic < DEGRIME_MAGIC_LEVEL:
        lines += wrap(f"Degrime needs {DEGRIME_MAGIC_LEVEL} Magic - you have "
                      f"{options.magic}. Rates below are unreachable for now.", width, YELLOW)

    lines += [rule, f"{BOLD}{heading}{RESET}", rule]
    lines += [format_row(cells_for(row, options), columns, row.locked) for row in rows]
    lines.append(rule)

    unlocked = [row for row in rows if not row.locked]
    if unlocked:
        best = unlocked[0]
        # The table already carries the tick-perfect figures, so this line
        # gives only what to actually expect. Spelling out Low and High here
        # is what teaches the table's -L and -H suffixes.
        best_segments = [
            (f"Best: {best.name}", f"Best: {BOLD}{best.name}{RESET}"),
            (f"Low {realistic(best.hr_low, options.focus)}",) * 2,
        ]
        if not best.suspect:
            best_segments.append((f"High {realistic(best.hr_high, options.focus)}",) * 2)
        best_segments += [
            (f"{gp(best.capital)} capital/hr",) * 2,
            (f"{best.roi:.2f}% ROI",) * 2,
        ]
        lines += pack(best_segments, width)

    for note in footnotes(rows, options, shown):
        lines += wrap(note, width, GREY)

    stats = (
        f"{HERBS_PER_HOUR * options.focus:,.0f} herbs/hr = "
        f"{CASTS_PER_HOUR * options.focus:,.0f} casts x {HERBS_PER_INVENTORY} @ "
        f"{TICKS_PER_INVENTORY} ticks/inv. Magic "
        f"{MAGIC_XP_PER_CAST * CASTS_PER_HOUR * options.focus:,.0f} xp/hr. "
        f"GE tax {TAX_RATE:.0%}."
    )
    if "swing" in shown:
        stats += " 1h Sw is how far the buy price moved in the last hour."
    lines += wrap(stats, width, GREY)

    if options.focus == 1.0:
        disclaimer = (f"Profit assumes tick-perfect execution and patient buy offers; "
                      f"expect {FOCUS_LOW:.0%}-{FOCUS_HIGH:.0%} depending on focus "
                      f"(pass --focus to bake your own figure in).")
    else:
        disclaimer = (f"Profit is scaled to {options.focus:.0%} of tick-perfect and assumes "
                      f"patient buy offers.")
    lines += wrap(f"{disclaimer} Dimmed herbs are out of reach.", width, DIM)
    lines += wrap(
        f"Wiki API contact: {options.contact}." if options.contact else
        f"Wiki API contact: none set - export {CONTACT_ENV}=\"@you on Discord\" "
        f"so your requests are attributed to you.", width, DIM)

    if status:
        lines += wrap(status, width, RED)
    else:
        lines += wrap(f"Updating every {POLL_RATE}s · Ctrl-C to quit", width, GREY)
    return lines


def draw(lines):
    # Overwrite in place rather than clearing the screen, which flickers.
    sys.stdout.write("\033[H" + "\033[K\n".join(lines) + "\033[K\033[0J")
    sys.stdout.flush()


def parse_capital(text):
    """Accept 50m, 50M, 50,000,000 or 50000000."""
    cleaned = "".join(text.lower().replace(",", "").replace("gp", "").split())
    if not cleaned:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(cleaned[-1])
    if multiplier:
        cleaned = cleaned[:-1]
    try:
        return int(float(cleaned) * (multiplier or 1))
    except ValueError:
        raise argparse.ArgumentTypeError(f"could not read {text!r} as an amount of gp")


def ask_number(label, default, low, high):
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and low <= int(raw) <= high:
            return int(raw)
        print(f"Enter a number between {low} and {high}.")


def ask_capital():
    while True:
        raw = input("Capital, blank for unlimited []: ").strip()
        if not raw:
            return None
        try:
            return parse_capital(raw)
        except argparse.ArgumentTypeError:
            print("Enter an amount like 50m, 30000000 or blank.")


def parse_options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--herblore", type=int, help="Herblore level (1-99)")
    parser.add_argument("--magic", type=int, help="Magic level (1-99)")
    parser.add_argument("--focus", type=int,
                        help="percent of tick-perfect you actually sustain (1-100)")
    parser.add_argument("--capital", type=parse_capital,
                        help="gp you can tie up, e.g. 50m; herbs above it are dimmed")
    parser.add_argument("--sort", choices=("profit", "roi"), default="profit",
                        help="rank by hourly profit (default) or return on capital")
    parser.add_argument("--no-swing", dest="swing", action="store_false",
                        help="skip the per-herb timeseries requests")
    parser.add_argument("--contact", default=os.environ.get(CONTACT_ENV),
                        help=f"your contact for the wiki API's User-Agent, e.g. "
                             f"'@you on Discord'; defaults to ${CONTACT_ENV}")
    parser.add_argument("--username", help="look your Herblore and Magic up on the hiscores")
    parser.add_argument("--mode", choices=tuple(HISCORE_BOARDS), default="main",
                        help="which hiscore board --username sits on (default: main)")
    args = parser.parse_args()

    # Precedence: an explicit level always wins, so you can still ask what a
    # level you have not reached yet would look like.
    args.notice = None
    if args.username:
        levels, args.notice = fetch_levels(args.username, args.mode, args.contact)
        if levels:
            args.herblore = args.herblore or levels.get("Herblore")
            args.magic = args.magic or levels.get("Magic")

    # --username is an explicit instruction to fetch, so a failed lookup falls back
    # to the defaults with a visible notice rather than blocking on a prompt.
    interactive = (args.username is None and args.herblore is None
                   and args.magic is None and args.focus is None)
    if args.herblore is None:
        args.herblore = ask_number("Herblore level", 99, 1, 99) if interactive else 99
    if args.magic is None:
        args.magic = ask_number("Magic level", 99, 1, 99) if interactive else 99
    if args.focus is None:
        args.focus = ask_number("Focus %, 100 for tick-perfect", 100, 1, 100) if interactive else 100
    if args.capital is None and interactive:
        args.capital = ask_capital()

    args.herblore = max(1, min(99, args.herblore))
    args.magic = max(1, min(99, args.magic))
    args.focus = max(1, min(100, args.focus)) / 100
    return args


def main():
    options = parse_options()
    feed = PriceFeed(options.contact)
    rows, nature_price, status = [], 0, ""

    sys.stdout.write(HIDE_CURSOR + "\033[2J")
    try:
        while True:
            try:
                rows, nature_price = build_rows(feed, options)
                status = ""
            except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
                status = f"Update failed ({error}) - retrying in {POLL_RATE}s"

            # Re-read the size every frame so resizing reflows the table.
            width = max(shutil.get_terminal_size(FALLBACK_SIZE).columns, MIN_WIDTH)
            draw(render(rows, nature_price, options, status, width))
            time.sleep(POLL_RATE)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(SHOW_CURSOR + "\n")


if __name__ == "__main__":
    main()
