import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dashboard_adapter import validate_vendor, hardware_output_options
from dashboard_extensions import (
    process_segment_deltas, combine_segment_framemeta, merge_gps_logger_fallback,
)
from gopro_overlay.entry import Entry
from gopro_overlay.framemeta import FrameMeta
from gopro_overlay.timeseries import Timeseries
from gopro_overlay.point import Point
from gopro_overlay.timeunits import timeunits
from gopro_overlay.gpmf import GPSFix

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def frames(count):
    result = FrameMeta()
    for i in range(count):
        result.add(timeunits(seconds=i), Entry(START + timedelta(seconds=i)))
    return result


class CompatibilityTests(unittest.TestCase):
    def test_adapter_and_installed_version(self):
        path = Path(__file__).resolve().parents[1] / "third_party/gopro-dashboard/gopro-dashboard.py"
        source = validate_vendor(path, check_installed=True)
        self.assertEqual(source.count("process_segment_deltas(frame_meta,"), 2)
        self.assertIn("gui_telemetry.load(", source)

    def test_delta_boundaries_and_tail(self):
        for count in (0, 1, 2, 3, 4, 5, 6, 12):
            with self.subTest(count=count):
                data = frames(count)
                pairs = []
                def processor(a, b, skip):
                    self.assertEqual((b.dt - a.dt).total_seconds(), skip)
                    pairs.append((a.dt, b.dt))
                    return {"speed": 123}
                process_segment_deltas(data, processor, skip=3)
                self.assertEqual(len(pairs), 2 * min(3, max(0, count - 3)) + max(0, count - 6))
                if count > 3:
                    self.assertEqual(data.frames[timeunits(seconds=count - 1)].speed, 123)

    def test_segment_boundaries(self):
        a, b = frames(2), frames(2)
        combined = combine_segment_framemeta([
            SimpleNamespace(framemeta=a, recording=SimpleNamespace(video=SimpleNamespace(duration=timeunits(seconds=5)))),
            SimpleNamespace(framemeta=b, recording=SimpleNamespace(video=SimpleNamespace(duration=timeunits(seconds=2)))),
        ])
        combined.check_modified()
        self.assertEqual([t.millis() for t in combined.framelist], [0, 1000, 5000, 6000])

    def test_gpx_fallback_preserves_valid_and_rejects_long_gap(self):
        gps = Timeseries()
        for second in (0, 10, 100):
            gps.add(Entry(START + timedelta(seconds=second), point=Point(35 + second / 10000, 139)))
        data = FrameMeta()
        for second, fix in ((0, GPSFix.LOCK_3D.value), (5, 0), (50, 0), (110, 0)):
            data.add(timeunits(seconds=second), Entry(START + timedelta(seconds=second), gpsfix=fix, point=Point(1, 2)))
        self.assertEqual(merge_gps_logger_fallback(gps, data), {"gopro": 1, "fallback": 1, "missing": 2})
        self.assertEqual(data.frames[timeunits(seconds=0)].point, Point(1, 2))

    def test_encoders(self):
        self.assertIsNone(hardware_output_options("cpu"))
        for name in ("vt", "amf", "nvenc", "qsv"):
            self.assertIn("7500k", hardware_output_options(name))


if __name__ == "__main__":
    unittest.main()
