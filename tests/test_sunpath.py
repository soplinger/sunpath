import contextlib
import io
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import sunpath  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SITES = os.path.join(ROOT, "examples", "sites.txt")
HORIZON = os.path.join(ROOT, "examples", "horizon.txt")
UTC = timezone.utc


def write_file(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        code = sunpath.main(argv, out=buf)
    return code, buf.getvalue()


class ParsingTests(unittest.TestCase):
    def test_example_site_file_is_valid(self):
        parsed = sunpath.parse_sites(SITES)
        self.assertEqual(parsed.problems, [])
        self.assertEqual([s.name for s in parsed.items][0], "Cottage North")
        self.assertEqual(len(parsed.items), 4)

    def test_every_bad_line_is_reported_with_its_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "sites.txt", "\n".join([
                "# header comment",
                "Good, 10, 10, 1",
                "Too far north, 91, 0, 0",
                "Glued, 5, 5",
                "Good, 1, 1, 1",
                "Typo, abc, 1, 0",
                "Offset, 1, 1, 15",
            ]) + "\n")
            parsed = sunpath.parse_sites(path)
        self.assertEqual([ln for ln, _ in parsed.problems], [3, 4, 5, 6, 7])
        self.assertIn("latitude 91 is outside", parsed.problems[0][1])
        self.assertIn("expected 4 fields", parsed.problems[1][1])
        self.assertIn("duplicate site 'Good'", parsed.problems[2][1])
        self.assertIn("not a number", parsed.problems[3][1])
        self.assertIn("UTC offset 15 is outside", parsed.problems[4][1])
        self.assertEqual([s.name for s in parsed.items], ["Good"])

    def test_crlf_endings_and_inline_comments_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "sites.txt", "A, 1, 2, 0  # first\r\n\r\nB, -3.5, 4, 5.5\r\n")
            parsed = sunpath.parse_sites(path)
        self.assertEqual(parsed.problems, [])
        self.assertEqual([s.name for s in parsed.items], ["A", "B"])
        self.assertAlmostEqual(parsed.items[1].offset, 5.5)

    def test_file_with_only_comments_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "sites.txt", "# nothing here\n")
            parsed = sunpath.parse_sites(path)
        self.assertEqual(len(parsed.problems), 1)
        self.assertEqual(parsed.items, [])

    def test_horizon_ranges_and_wrap_around(self):
        obstacles = [(330, 30, 8), (0, 90, 4)]
        self.assertEqual(sunpath.horizon_elevation(obstacles, 355), 8)
        self.assertEqual(sunpath.horizon_elevation(obstacles, 10), 8)
        self.assertEqual(sunpath.horizon_elevation(obstacles, 60), 4)
        self.assertEqual(sunpath.horizon_elevation(obstacles, 200), 0)

    def test_horizon_file_missing_fields_reports_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "hz.txt", "0, 90, 4\n90, 180\n")
            parsed = sunpath.parse_horizon(path)
        self.assertEqual(parsed.items, [(0.0, 90.0, 4.0)])
        self.assertEqual(parsed.problems[0][0], 2)


class SolarMathTests(unittest.TestCase):
    def test_sun_is_nearly_overhead_at_equator_on_equinox_noon(self):
        altitude, _ = sunpath.sun_position(0.0, 0.0, datetime(2026, 3, 20, 12, 0, tzinfo=UTC))
        self.assertGreater(altitude, 85)

    def test_sun_rises_due_east_at_equator_on_equinox(self):
        altitude, azimuth = sunpath.sun_position(0.0, 0.0, datetime(2026, 3, 20, 6, 0, tzinfo=UTC))
        self.assertLess(abs(altitude), 5)
        self.assertGreater(azimuth, 85)
        self.assertLess(azimuth, 95)

    def test_equator_equinox_day_is_about_twelve_hours(self):
        site = sunpath.Site("Equator", 0.0, 0.0, 0.0)
        info = sunpath.compute_day(site, date(2026, 3, 20))
        minutes = info.day_length.total_seconds() / 60
        self.assertGreater(minutes, 12 * 60 - 10)
        self.assertLess(minutes, 12 * 60 + 15)

    def test_midsummer_day_length_at_london_latitude(self):
        site = sunpath.Site("London", 51.5074, -0.1278, 0.0)
        info = sunpath.compute_day(site, date(2026, 6, 21))
        minutes = info.day_length.total_seconds() / 60
        self.assertGreater(minutes, 16 * 60 + 30)
        self.assertLess(minutes, 16 * 60 + 45)
        self.assertEqual(info.status, "normal")

    def test_polar_day_and_polar_night_are_detected(self):
        arctic = sunpath.Site("Arctic", 80.0, 0.0, 0.0)
        antarctic = sunpath.Site("Antarctic", -80.0, 0.0, 0.0)
        self.assertEqual(sunpath.compute_day(arctic, date(2026, 6, 21)).status, "polar day")
        self.assertEqual(sunpath.compute_day(antarctic, date(2026, 6, 21)).status, "polar night")
        self.assertIsNone(sunpath.compute_day(arctic, date(2026, 6, 21)).sunrise)

    def test_golden_morning_is_a_short_window_before_noon(self):
        site = sunpath.Site("London", 51.5074, -0.1278, 0.0)
        info = sunpath.compute_day(site, date(2026, 6, 21))
        start, end = info.golden_morning
        self.assertEqual(start, info.sunrise)
        self.assertLess(end, info.noon)
        golden = (end - start).total_seconds() / 60
        self.assertGreater(golden, 40)
        self.assertLess(golden, 70)

    def test_shading_reduces_sunlit_minutes(self):
        site = sunpath.Site("London", 51.5074, -0.1278, 0.0)
        day = date(2026, 6, 21)
        open_lit, open_total = sunpath.sunlit_minutes(site, day, [])
        self.assertEqual(open_lit, open_total)
        shaded_lit, _ = sunpath.sunlit_minutes(site, day, [(0.0, 360.0, 60.0)])
        self.assertLess(shaded_lit, open_total)
        self.assertGreater(shaded_lit, 0)

    def test_shadow_length_and_low_sun(self):
        self.assertAlmostEqual(sunpath.shadow_length(1.0, 45.0), 1.0, places=6)
        self.assertAlmostEqual(sunpath.shadow_length(2.0, 30.0), 2 / 0.5773502691896257, places=6)
        self.assertIsNone(sunpath.shadow_length(2.0, -5.0))

    def test_tilt_rules_of_thumb_for_both_hemispheres(self):
        north = sunpath.tilt_advice(40.0)
        self.assertEqual((north["azimuth"], north["year"], north["winter"], north["summer"]), (180.0, 40.0, 55.0, 25.0))
        south = sunpath.tilt_advice(-30.0)
        self.assertEqual(south["azimuth"], 0.0)
        self.assertEqual(sunpath.tilt_advice(5.0)["summer"], 0.0)


class CommandTests(unittest.TestCase):
    def test_day_report_includes_sun_times_and_shading(self):
        code, text = run_cli(["day", SITES, "--site", "Cottage North", "--date", "2026-06-21", "--horizon", HORIZON])
        self.assertEqual(code, 0)
        self.assertIn("Day length", text)
        self.assertIn("Golden morning", text)
        self.assertIn("Shade loss", text)
        self.assertNotIn("Harbour", text)

    def test_polar_site_reports_status_instead_of_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "sites.txt", "Svalbard, 78.2, 15.6, 1\n")
            code, text = run_cli(["day", path, "--date", "2026-06-21"])
        self.assertEqual(code, 0)
        self.assertIn("polar day", text)
        self.assertNotIn("Sunrise", text)

    def test_year_export_writes_one_row_per_day_of_a_leap_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "year.csv")
            code, text = run_cli(["year", SITES, "--site", "Desert Lab", "--year", "2028", "--csv", csv_path])
            with open(csv_path, encoding="utf-8") as fh:
                rows = fh.read().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 367)
        self.assertTrue(rows[0].startswith("date,status,sunrise"))
        self.assertIn("Longest day", text)

    def test_position_reports_altitude_azimuth_and_shadow(self):
        code, text = run_cli(["position", SITES, "--site", "Equator Camp", "--at", "2026-03-20 12:00", "--height", "2"])
        self.assertEqual(code, 0)
        self.assertIn("Altitude", text)
        self.assertIn("Azimuth", text)
        self.assertIn("Shadow", text)

    def test_position_without_height_omits_shadow(self):
        code, text = run_cli(["position", SITES, "--site", "Equator Camp", "--at", "2026-03-20 12:00"])
        self.assertEqual(code, 0)
        self.assertIn("Altitude", text)
        self.assertNotIn("Shadow", text)

    def test_tilt_command_lists_three_angles(self):
        code, text = run_cli(["tilt", SITES, "--site", "Harbour Studio"])
        self.assertEqual(code, 0)
        self.assertIn("Winter-tuned  75 deg tilt", text)
        self.assertIn("Face azimuth  180 deg", text)

    def test_lint_passes_on_examples_and_fails_on_bad_file(self):
        code, text = run_cli(["lint", SITES, "--horizon", HORIZON])
        self.assertEqual(code, 0)
        self.assertIn("OK: 4 site(s) valid", text)
        with tempfile.TemporaryDirectory() as tmp:
            bad = write_file(tmp, "sites.txt", "Ok, 1, 1, 0\nBroken, 1, 999, 0\n")
            code, text = run_cli(["lint", bad])
        self.assertEqual(code, 1)
        self.assertIn(":2: longitude 999 is outside", text)

    def test_lint_warns_about_polar_latitude(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_file(tmp, "sites.txt", "Polar, 70, 20, 1\n")
            code, text = run_cli(["lint", path])
        self.assertEqual(code, 0)
        self.assertIn("WARN", text)
        self.assertIn("polar latitude", text)

    def test_unknown_site_is_a_usage_error(self):
        code, _ = run_cli(["day", SITES, "--site", "Nowhere", "--date", "2026-06-21"])
        self.assertEqual(code, 2)

    def test_bad_date_is_a_usage_error(self):
        code, _ = run_cli(["day", SITES, "--date", "21/06/2026"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
