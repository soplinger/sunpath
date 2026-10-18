# ☀️ Sunpath

**Offline sun position, daylight, shading and panel-tilt planner, driven by a plain-text site file.**

Sunpath answers the questions you actually have about a place: when does the sun rise and set, when is golden hour, how long is the shadow of a 2 m post at 3 pm, how many hours does my roofline shade the panels, and what tilt should the array be. Describe each site once in a text file, then ask questions from the terminal. Nothing is fetched, nothing is cached on a server, and there are no API keys.

## Features

- **Solar position** – altitude and azimuth (clockwise from north) for any local date and time.
- **Daily sunrise, solar noon and sunset** – with day length and the equation of time.
- **Golden-hour windows** – the morning and evening spans where the sun sits between the horizon and 6°.
- **Polar day and polar night detection** – reported as a status rather than nonsense clock times.
- **Shadow length calculator** – the shadow cast by an object of any height at a chosen time.
- **Horizon shading** – a plain-text obstacle file (azimuth ranges with elevations, wrapping through north) gives sunlit minutes and the shade loss against an open horizon.
- **Full-year table** – one row per day, with a longest and shortest day summary and an optional CSV export.
- **Fixed PV tilt advice** – year-round, winter-tuned and summer-tuned tilt, plus the face azimuth for each hemisphere.
- **Multi-site support** – one site file can hold many places; every report can target one site by name or run across all of them.
- **Line-numbered validation** – a `lint` command that reports every bad line at once with `file:line`, and flags duplicate names and polar-latitude or unusual-offset warnings.
- **Local-time output** – every clock time is shown in the site's UTC offset, so you read what the wall clock says.

## Quick start

1. Describe your sites in a plain-text file. Lines are `name, latitude, longitude, utc_offset_hours`; `#` starts a comment.

   ```text
   # examples/sites.txt
   Cottage North, 51.5074, -0.1278, 0
   Harbour Studio, 59.9139, 10.7522, 1
   Desert Lab, 33.4484, -112.0740, -7
   Equator Camp, 0.0, 36.8219, 3
   ```

2. Check the file, then ask for the longest day at the cottage, with shading from the example horizon file:

   ```sh
   python3 sunpath.py lint examples/sites.txt --horizon examples/horizon.txt
   python3 sunpath.py day examples/sites.txt --site "Cottage North" --date 2026-06-21 --horizon examples/horizon.txt
   ```

3. Find the sun for a specific moment and the shadow of a 2 m post at that time:

   ```sh
   python3 sunpath.py position examples/sites.txt --site "Equator Camp" --at "2026-03-20 12:00" --height 2
   ```

4. Export a whole year to a spreadsheet:

   ```sh
   python3 sunpath.py year examples/sites.txt --site "Desert Lab" --year 2026 --csv desert-2026.csv
   ```

## Commands

| Command | What it does |
| --- | --- |
| `lint SITES [--horizon FILE]` | Validates both files, lists every error with its line number, and prints warnings. Exit code 1 on errors. |
| `day SITES [--site NAME] [--date YYYY-MM-DD] [--horizon FILE]` | Prints sunrise, noon, sunset, day length, golden hours, noon altitude and, with a horizon file, sunlit and shade-loss minutes. Date defaults to today. |
| `position SITES --site NAME --at "YYYY-MM-DD HH:MM" [--height M]` | Prints altitude, azimuth and optional shadow length at a local time. |
| `year SITES --site NAME --year Y [--csv FILE]` | Builds the daily table for a year and prints the longest and shortest days. |
| `tilt SITES [--site NAME]` | Prints face azimuth and year-round, winter and summer tilt rules of thumb. |

## File formats

**Site file** (`SITES`): four comma-separated fields per line.

- `name`: unique, case-insensitive, may contain spaces.
- `latitude`: −90 to 90, north positive.
- `longitude`: −180 to 180, east positive.
- `utc_offset_hours`: −12 to 14. This is the offset in effect for the dates you plan to look at. It is applied as a fixed offset, so for a daylight-saving period use the summer value.

**Horizon file** (`--horizon`): three fields per line.

- `azimuth_start`, `azimuth_end`: 0 to 360, clockwise from north. A range where start is greater than end wraps through north, so `330, 30, 8` covers the northern arc.
- `obstacle_elevation_deg`: 0 to 89, the height the obstacle reaches over that arc.

Where several obstacles cover the same azimuth, the highest one counts.

## Accuracy

Positions use the NOAA-style low-precision solar algorithm. Sunrise and sunset use the apparent altitude of −0.833°, which includes atmospheric refraction and the solar radius. Expect times within a minute or two of published almanacs. Golden-hour windows use the 6° altitude. Shading is sampled every 5 minutes, so shade figures are accurate to about that step.

## Requirements

Python 3.10 or newer, standard library only. Tests need `pytest`, though the suite also runs under `unittest`.

## How to run

```sh
python3 sunpath.py --help
python3 sunpath.py <command> --help
```

## How to test

```sh
python3 -m pytest tests
```

Or without pytest:

```sh
python3 -m unittest discover -s tests -v
```

The tests cover parsing with line-numbered errors (including CRLF endings, comments and duplicate names), wrap-around horizon ranges, solar geometry at known equinox and solstice conditions, polar day and night, golden-hour windows, shading, shadow length, tilt rules, and every CLI command's exit codes and output.

## Project layout

```text
sunpath.py              the whole program (one module, argparse CLI)
examples/sites.txt      sample site file
examples/horizon.txt    sample obstacle file
tests/test_sunpath.py   unit and CLI tests
```
