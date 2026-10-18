#!/usr/bin/env python3
"""Sunpath: offline sun position, daylight, shading and panel-tilt planner.

Reads plain-text site files (name, latitude, longitude, UTC offset) and an
optional horizon file (azimuth range, obstacle elevation). Standard library only.
"""

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
J2000 = datetime(2000, 1, 1, 12, tzinfo=UTC)
REFRACTION_DEG = -0.833  # apparent sunrise/sunset: refraction plus solar radius
GOLDEN_DEG = 6.0  # golden hour spans the sun between the horizon and 6 degrees
SHADE_STEP_MIN = 5  # sampling step for sunlit-minute counts


class SunpathError(Exception):
    """Raised for usage errors that stop a command."""


@dataclass
class Site:
    name: str
    lat: float
    lon: float
    offset: float
    line: int = 0


@dataclass
class Day:
    day: date
    status: str  # "normal", "polar day" or "polar night"
    sunrise: datetime | None = None
    noon: datetime | None = None
    sunset: datetime | None = None
    noon_altitude: float = 0.0
    eot_minutes: float = 0.0
    golden_morning: tuple | None = None
    golden_evening: tuple | None = None

    @property
    def day_length(self):
        if self.sunrise is None:
            return None
        return self.sunset - self.sunrise


@dataclass
class Parsed:
    items: list = field(default_factory=list)
    problems: list = field(default_factory=list)  # (line, message)


# ---------------------------------------------------------------- parsing

def _rows(path):
    """Yield (line_number, fields) for each data line; skips blanks and # comments."""
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            text = raw.split("#", 1)[0].strip()
            if text:
                yield lineno, [part.strip() for part in text.split(",")]


def _number(text, lo, hi, what):
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what} '{text}' is not a number") from None
    if not lo <= value <= hi:  # also rejects NaN
        raise ValueError(f"{what} {text} is outside {lo:g}..{hi:g}")
    return value


def parse_sites(path):
    """Parse a site file, collecting every problem instead of stopping at the first."""
    result = Parsed()
    first_seen = {}
    for lineno, fields_ in _rows(path):
        if len(fields_) != 4:
            result.problems.append((lineno, f"expected 4 fields (name, lat, lon, utc_offset), got {len(fields_)}"))
            continue
        name, lat, lon, offset = fields_
        try:
            if not name:
                raise ValueError("site name is empty")
            if name.lower() in first_seen:
                raise ValueError(f"duplicate site '{name}' (first defined at line {first_seen[name.lower()]})")
            site = Site(
                name,
                _number(lat, -90, 90, "latitude"),
                _number(lon, -180, 180, "longitude"),
                _number(offset, -12, 14, "UTC offset"),
                lineno,
            )
        except ValueError as exc:
            result.problems.append((lineno, str(exc)))
            continue
        first_seen[name.lower()] = lineno
        result.items.append(site)
    if not result.items and not result.problems:
        result.problems.append((0, "no sites found"))
    return result


def parse_horizon(path):
    """Parse obstacle ranges: azimuth_start, azimuth_end, elevation_deg (ranges may wrap past 360)."""
    result = Parsed()
    for lineno, fields_ in _rows(path):
        if len(fields_) != 3:
            result.problems.append((lineno, f"expected 3 fields (az_start, az_end, elevation), got {len(fields_)}"))
            continue
        try:
            start = _number(fields_[0], 0, 360, "azimuth start")
            end = _number(fields_[1], 0, 360, "azimuth end")
            elev = _number(fields_[2], 0, 89, "obstacle elevation")
        except ValueError as exc:
            result.problems.append((lineno, str(exc)))
            continue
        result.items.append((start, end, elev))
    return result


def horizon_elevation(obstacles, azimuth):
    """Highest obstacle elevation covering this azimuth; 0 when the view is open."""
    best = 0.0
    for start, end, elev in obstacles:
        if start <= end:
            inside = start <= azimuth <= end
        else:  # range wraps through north, e.g. 330 to 30
            inside = azimuth >= start or azimuth <= end
        if inside:
            best = max(best, elev)
    return best


def _format_problems(path, problems):
    return [f"{path}:{ln}: {msg}" if ln else f"{path}: {msg}" for ln, msg in problems]


def _load_sites_or_fail(path):
    parsed = parse_sites(path)
    if parsed.problems:
        raise SunpathError("\n".join(_format_problems(path, parsed.problems)))
    return parsed.items


def _load_horizon_or_fail(path):
    if path is None:
        return []
    parsed = parse_horizon(path)
    if parsed.problems:
        raise SunpathError("\n".join(_format_problems(path, parsed.problems)))
    return parsed.items


# ---------------------------------------------------------------- solar math

def _solar(when):
    """Declination (rad), equation of time (minutes), right ascension (rad) and GMST (deg) at a UTC instant."""
    n = (when - J2000).total_seconds() / 86400.0
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = math.radians(mean_lon + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    diff = (mean_lon - math.degrees(ra) + 180.0) % 360.0 - 180.0
    eot = 4.0 * diff  # 4 minutes of clock time per degree
    gmst = (280.46061837 + 360.98564736629 * n) % 360.0
    return dec, eot, ra, gmst


def sun_position(lat_deg, lon_deg, when):
    """Return (altitude_deg, azimuth_deg measured clockwise from north) for a UTC instant."""
    dec, _, ra, gmst = _solar(when)
    hour_angle = math.radians(gmst + lon_deg - math.degrees(ra))
    phi = math.radians(lat_deg)
    sin_alt = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(hour_angle)
    altitude = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
    azimuth = math.degrees(
        math.atan2(-math.sin(hour_angle), math.tan(dec) * math.cos(phi) - math.sin(phi) * math.cos(hour_angle))
    ) % 360.0
    return altitude, azimuth


def _horizon_cos(lat_rad, dec, alt_deg):
    """cos(hour angle) at which the sun's centre sits at alt_deg; outside -1..1 means it never gets there."""
    alt = math.radians(alt_deg)
    return (math.sin(alt) - math.sin(lat_rad) * math.sin(dec)) / (math.cos(lat_rad) * math.cos(dec))


def _local(site, when_utc):
    return (when_utc + timedelta(hours=site.offset)).replace(tzinfo=None)


def compute_day(site, day):
    """Sunrise, solar noon, sunset, golden-hour windows and equation of time for a local date."""
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    noon = base + timedelta(hours=12)
    for _ in range(3):  # equation of time changes slowly; a few passes converge
        _, eot, _, _ = _solar(noon)
        noon = base + timedelta(hours=12 - site.lon / 15.0 - eot / 60.0)
    dec, eot, _, _ = _solar(noon)
    lat = math.radians(site.lat)
    noon_alt = 90.0 - abs(site.lat - math.degrees(dec))
    result = Day(day=day, status="normal", noon_altitude=noon_alt, eot_minutes=eot)

    x = _horizon_cos(lat, dec, REFRACTION_DEG)
    if x > 1:
        result.status = "polar night"
        result.noon = _local(site, noon)
        return result
    if x < -1:
        result.status = "polar day"
        result.noon = _local(site, noon)
        return result

    half = timedelta(hours=math.degrees(math.acos(x)) / 15.0)
    rise, sett = noon - half, noon + half
    result.sunrise = _local(site, rise)
    result.noon = _local(site, noon)
    result.sunset = _local(site, sett)

    x6 = _horizon_cos(lat, dec, GOLDEN_DEG)
    if -1 <= x6 <= 1:
        six = timedelta(hours=math.degrees(math.acos(x6)) / 15.0)
        result.golden_morning = (result.sunrise, _local(site, noon - six))
        result.golden_evening = (_local(site, noon + six), result.sunset)
    return result


def sunlit_minutes(site, day, obstacles):
    """Return (minutes with the sun visible past the obstacles, minutes with an open horizon)."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(hours=site.offset)
    lit = open_ = 0
    for k in range(24 * 60 // SHADE_STEP_MIN):
        when = start + timedelta(minutes=k * SHADE_STEP_MIN)
        altitude, azimuth = sun_position(site.lat, site.lon, when)
        if altitude > 0:
            open_ += 1
        if altitude > horizon_elevation(obstacles, azimuth):
            lit += 1
    return lit * SHADE_STEP_MIN, open_ * SHADE_STEP_MIN


def shadow_length(height_m, altitude_deg):
    """Shadow length cast by an object of height_m; None when the sun is at or below the horizon."""
    if altitude_deg <= 0:
        return None
    return height_m / math.tan(math.radians(altitude_deg))


def tilt_advice(lat):
    """Rule-of-thumb fixed-tilt angles for a PV panel at this latitude."""
    magnitude = abs(lat)
    return {
        "azimuth": 180.0 if lat >= 0 else 0.0,
        "year": magnitude,
        "winter": min(90.0, magnitude + 15.0),
        "summer": max(0.0, magnitude - 15.0),
    }


# ---------------------------------------------------------------- formatting

def _clock(dt):
    return dt.strftime("%H:%M") if dt else "--:--"


def _span(td):
    minutes = round(td.total_seconds() / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _window(win):
    return f"{_clock(win[0])}-{_clock(win[1])}" if win else "none"


def _parse_when(text):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise SunpathError(f"'{text}' is not a local time; use YYYY-MM-DD HH:MM")


def _parse_date(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise SunpathError(f"'{text}' is not a date; use YYYY-MM-DD") from None


def _pick_sites(sites, name):
    if name is None:
        return sites
    matches = [s for s in sites if s.name.lower() == name.lower()]
    if not matches:
        known = ", ".join(s.name for s in sites)
        raise SunpathError(f"no site named '{name}' (known: {known})")
    return matches


def _lint_warnings(site):
    warnings = []
    if abs(site.lat) > 66.5:
        warnings.append("polar latitude: polar day or night is possible in some seasons")
    if (site.offset * 4) % 1:
        warnings.append(f"unusual UTC offset {site.offset:g}; expected a multiple of 0.25 hours")
    return warnings


# ---------------------------------------------------------------- commands

def cmd_lint(args, out):
    parsed = parse_sites(args.sites)
    problems = _format_problems(args.sites, parsed.problems)
    if args.horizon:
        horizon = parse_horizon(args.horizon)
        problems += _format_problems(args.horizon, horizon.problems)
    for problem in problems:
        print(f"ERROR {problem}", file=out)
    for site in parsed.items:
        for warning in _lint_warnings(site):
            print(f"WARN  {args.sites}:{site.line}: {site.name}: {warning}", file=out)
    if problems:
        print(f"{len(problems)} error(s) found", file=out)
        return 1
    print(f"OK: {len(parsed.items)} site(s) valid", file=out)
    return 0


def cmd_day(args, out):
    sites = _pick_sites(_load_sites_or_fail(args.sites), args.site)
    obstacles = _load_horizon_or_fail(args.horizon)
    day = _parse_date(args.date) if args.date else date.today()
    for site in sites:
        info = compute_day(site, day)
        print(f"Sunpath - {site.name} (lat {site.lat:g}, lon {site.lon:g}, UTC{site.offset:+g})", file=out)
        print(f"{day.isoformat()}", file=out)
        if info.status != "normal":
            print(f"  Status          {info.status}", file=out)
        else:
            print(f"  Sunrise         {_clock(info.sunrise)}", file=out)
            print(f"  Solar noon      {_clock(info.noon)}", file=out)
            print(f"  Sunset          {_clock(info.sunset)}", file=out)
            print(f"  Day length      {_span(info.day_length)}", file=out)
            print(f"  Golden morning  {_window(info.golden_morning)}", file=out)
            print(f"  Golden evening  {_window(info.golden_evening)}", file=out)
        print(f"  Noon altitude   {info.noon_altitude:.1f} deg", file=out)
        print(f"  Equation of time {info.eot_minutes:+.1f} min", file=out)
        if obstacles:
            lit, open_ = sunlit_minutes(site, day, obstacles)
            print(f"  Sunlit, open    {_span(timedelta(minutes=open_))}", file=out)
            print(f"  Sunlit, shaded  {_span(timedelta(minutes=lit))}", file=out)
            print(f"  Shade loss      {_span(timedelta(minutes=open_ - lit))}", file=out)
        print("", file=out)
    return 0


def cmd_position(args, out):
    site = _pick_sites(_load_sites_or_fail(args.sites), args.site)[0]
    local = _parse_when(args.at)
    when = local.replace(tzinfo=UTC) - timedelta(hours=site.offset)
    altitude, azimuth = sun_position(site.lat, site.lon, when)
    print(f"{site.name} at {local:%Y-%m-%d %H:%M} (UTC{site.offset:+g})", file=out)
    print(f"  Altitude   {altitude:.2f} deg", file=out)
    print(f"  Azimuth    {azimuth:.2f} deg from north", file=out)
    if args.height is not None:
        shadow = shadow_length(args.height, altitude)
        if shadow is None:
            print("  Shadow     none (sun is below the horizon)", file=out)
        else:
            print(f"  Shadow     {shadow:.2f} m for a {args.height:g} m object", file=out)
    return 0


def cmd_year(args, out):
    site = _pick_sites(_load_sites_or_fail(args.sites), args.site)[0]
    days = []
    d = date(args.year, 1, 1)
    while d.year == args.year:
        days.append(compute_day(site, d))
        d += timedelta(days=1)
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["date", "status", "sunrise", "solar_noon", "sunset", "day_length_min", "noon_altitude_deg"])
            for info in days:
                length = info.day_length
                writer.writerow([
                    info.day.isoformat(),
                    info.status,
                    _clock(info.sunrise) if info.sunrise else "",
                    _clock(info.noon) if info.noon else "",
                    _clock(info.sunset) if info.sunset else "",
                    f"{length.total_seconds() / 60:.1f}" if length else "",
                    f"{info.noon_altitude:.2f}",
                ])
        print(f"Wrote {len(days)} rows to {args.csv}", file=out)
    normal = [info for info in days if info.day_length is not None]
    print(f"{site.name} {args.year}: {len(days)} days", file=out)
    if normal:
        longest = max(normal, key=lambda i: i.day_length)
        shortest = min(normal, key=lambda i: i.day_length)
        print(f"  Longest day   {longest.day.isoformat()} ({_span(longest.day_length)})", file=out)
        print(f"  Shortest day  {shortest.day.isoformat()} ({_span(shortest.day_length)})", file=out)
    polar = [info for info in days if info.status != "normal"]
    for status in sorted({info.status for info in polar}):
        count = sum(1 for info in polar if info.status == status)
        print(f"  {status.capitalize():<13} {count} day(s)", file=out)
    return 0


def cmd_tilt(args, out):
    sites = _pick_sites(_load_sites_or_fail(args.sites), args.site)
    for site in sites:
        advice = tilt_advice(site.lat)
        print(f"{site.name} (lat {site.lat:g})", file=out)
        print(f"  Face azimuth  {advice['azimuth']:.0f} deg (true {'south' if site.lat >= 0 else 'north'})", file=out)
        print(f"  Year-round    {advice['year']:.0f} deg tilt", file=out)
        print(f"  Winter-tuned  {advice['winter']:.0f} deg tilt", file=out)
        print(f"  Summer-tuned  {advice['summer']:.0f} deg tilt", file=out)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="sunpath", description="Offline sun position, daylight and shading planner.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="check a site file (and optional horizon file) for errors")
    p.add_argument("sites")
    p.add_argument("--horizon")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("day", help="sunrise, noon, sunset, golden hours and optional shading for one date")
    p.add_argument("sites")
    p.add_argument("--site")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--horizon", help="horizon file for shading loss")
    p.set_defaults(func=cmd_day)

    p = sub.add_parser("position", help="sun altitude, azimuth and shadow length at a local time")
    p.add_argument("sites")
    p.add_argument("--site", required=True)
    p.add_argument("--at", required=True, help='local time, "YYYY-MM-DD HH:MM"')
    p.add_argument("--height", type=float, help="object height in metres for shadow length")
    p.set_defaults(func=cmd_position)

    p = sub.add_parser("year", help="daily table for a whole year, with optional CSV export")
    p.add_argument("sites")
    p.add_argument("--site", required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--csv", help="write the daily table to this CSV file")
    p.set_defaults(func=cmd_year)

    p = sub.add_parser("tilt", help="fixed PV panel tilt and azimuth rules of thumb")
    p.add_argument("sites")
    p.add_argument("--site")
    p.set_defaults(func=cmd_tilt)
    return parser


def main(argv=None, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out)
    except SunpathError as exc:
        print(f"sunpath: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"sunpath: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
