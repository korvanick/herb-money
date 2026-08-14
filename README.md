# herb-money

A terminal dashboard that ranks Old School RuneScape herbs by how profitable they are to
clean with [Degrime](https://oldschool.runescape.wiki/w/Degrime), using live Grand Exchange
prices from the [RuneScape Wiki real-time prices API](https://prices.runescape.wiki/).

Buy grimy herbs, clean a full inventory per cast, sell the clean herbs back. The wiki's own
guides tell you which herb was best whenever the page was last updated; this tells you which
one is best right now, and what you would actually make rather than what a tick-perfect
player would.

There is a longer write-up about why it exists and how it gets used at
[korvanick.com/projects/herb-money](https://korvanick.com/projects/herb-money).

```
DEGRIME PROFIT  ·  Herblore 78  ·  Magic 85  ·  Focus 75%  ·  Capital 50.00M  ·  Nature rune 158gp
─────────────────────────────────────────────────────────────────────────────────────────────────
Herb              Buy Δ      Sell-L Δ      Sell-H    Prof-L   Prof-H   GP/hr-L   GP/hr-H  1h Vol
─────────────────────────────────────────────────────────────────────────────────────────────────
Kwuarm          2,051 ↓6      2,245 ↑45     2,200?    138.3     93.3     1.68M     1.13M  21,885
Cadantine       2,139 ↓1      2,319         2,320     122.3    123.3     1.49M     1.50M  12,207
Toadflax        2,013         2,185 ↓14     2,200     117.3    131.3     1.43M     1.60M  21,958
Torstol         3,100         3,247 ↑12     3,247      71.3     71.3      866k      866k  10,457
Avantoe         1,441 ↓25     1,544         1,554      61.3     70.3      745k      854k   8,138
Guam*             196           249           249      37.3     37.3      453k      453k   4,534
Irit            1,376 ↓14     1,414         1,480      -1.7     63.3      -21k      769k  11,892
Ranarr$         5,554         5,785 ↓1      5,800     104.3    118.3     1.27M     1.44M  23,764
Marrentill        145           219           225      58.3     64.3      708k      781k   1,438!
Huasca*$        5,761         5,825         5,863     -63.7    -26.7     -774k     -324k   1,578!
─────────────────────────────────────────────────────────────────────────────────────────────────
Best: Kwuarm  ·  Low 1.68M/hr  ·  24.92M capital/hr  ·  6.74% ROI
* price older than 10m
? sell-high print is below sell-low, so Prof-H is unreliable
$ needs more than your capital
! under 2,000 clean trades in the last hour, so ranked last and never picked as best
```

Marrentill is the shape of thing this is for. On paper it is the sixth best herb on
screen and better than Guam; in practice 1,438 clean trades an hour is not a market you
can dump 12,000 herbs into, so it sits at the bottom with everything else too thin to
sell rather than tempting you from mid-table.

## Running it

Python 3.8 or newer. No dependencies, no install, nothing to build.

```bash
python3 herb-money.py
```

With no arguments it asks for your levels, focus and capital, each with a default you can
accept by pressing enter. Everything can be passed instead:

```bash
python3 herb-money.py --username "your name" --focus 75 --capital 50m
```

| Option | What it does |
| --- | --- |
| `--username` | Looks your Herblore and Magic up on the OSRS hiscores |
| `--mode` | Which hiscore board that name is on: `main`, `ironman`, `hardcore`, `ultimate` |
| `--herblore`, `--magic` | Set levels directly. Overrides the lookup, so you can ask what a level you have not reached yet would look like |
| `--focus` | Percent of tick-perfect you actually sustain. Every rate is scaled to it |
| `--capital` | GP you can tie up, as `50m`, `1.5b`, `300k` or `50000000`. Herbs needing more are dimmed |
| `--sort` | `profit` (default) or `roi`, which ranks by return on capital instead |
| `--no-swing` | Skips the per-herb history requests and drops the `1h Sw` column |
| `--contact` | Your contact for the API's User-Agent. See below |

Only skills come from the hiscores. Jagex publishes no API for a player's coins, so
`--capital` is always a number you supply.

## Reading it

`-L` and `-H` are the two sell prices. **Low** is what you get dumping into an instant sell;
**High** is what you get waiting for a buyer. Ranking is on Low, because that is the number
you can count on. The buy side is always the patient price, since instant-buying grimy herbs
is a loss on most of them and is deliberately not modelled.

| Column | Meaning |
| --- | --- |
| `Buy Δ`, `Sell-L Δ` | Price, and how far it moved against the last 5m average. Colour says whether that movement helps you, so a rising buy price is red and a rising sell price is green |
| `Prof-L`, `Prof-H` | Profit per herb after tax and runes, dumping or waiting. `Prof-L` is bold, since most of the table is green and this is the figure to land on first |
| `GP/hr-L`, `GP/hr-H` | The same, per hour of cleaning |
| `Cap/hr` | GP tied up to sustain an hour of it |
| `ROI%` | Profit per gp invested. Ranks very differently from raw profit |
| `1h Sw` | How far the buy price moved over the last hour, coloured against your own margin. Red means the swing is wider than the margin, so the margin is noise |
| `Age` | How stale the price is |
| `1h Vol` | Clean-side trades in the last hour. That is the side you have to offload. Under 2,000 earns a `!`, which sinks the herb to the bottom of the table and takes it out of the running for best |

Dimmed rows are out of reach, either above your Herblore level or over your capital.

The table reflows to the terminal as you resize it, dropping the least important columns as
it narrows, down to a 23-column floor. Prices keep their deltas at every width. Resizing
redraws immediately and costs no requests; it does not wait for the next minute's prices.

## How profit is worked out

```
profit per herb = sell - GE tax - buy - nature runes
```

Tax is 2% of the sale, rounded down, exempt below 50gp and capped at 5M. Runes are two
natures per cast across 27 herbs, priced live; the four earth runes are assumed free from a
staff. Throughput comes from 10 ticks per inventory, so 600 casts and 16,200 herbs an hour
at tick-perfect, which nothing sustains for a full hour — expect 67-80% of it, or set
`--focus` to your own figure and have every number scaled for you.

## If you run this

The wiki asks that requests carry a descriptive User-Agent it can contact. By default this
identifies the project rather than any person, so a clone never reports your traffic under
someone else's name. Add your own:

```bash
export HERB_MONEY_CONTACT="@you on Discord"
```

Each endpoint is cached to its real update rate rather than refetched every poll, which is
the main thing keeping this polite. `--no-swing` drops the per-herb history requests if you
want a smaller footprint still.

## License

[MIT](LICENSE). Prices come from the RuneScape Wiki at runtime and are not redistributed
here; their content is under
[CC BY-NC-SA 3.0](https://oldschool.runescape.wiki/w/RuneScape:Copyrights).
