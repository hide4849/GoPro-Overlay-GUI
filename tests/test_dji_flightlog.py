import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from dji_flightlog import FlightLog, load_flight_video, DEFAULT_LOG_FIELDS, RecordedText
from dji_telemetry import add_layout
from gopro_overlay.timeunits import timeunits


class FlightLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'log.json'
        self.rows = []
        for t in range(5):
            self.rows.append({'custom': {'dateTime': '1970-01-01T00:00:00+00:00' if t == 0 else f'2026-09-27T07:29:5{t}+00:00'},
                              'osd': {'flyTime': t, 'latitude': 35, 'longitude': 139,
                                      'height': 10 + t, 'hSpeed': 10, 'zSpeed': -2, 'yaw': 120},
                              'battery': {'chargeLevel': 90 - t, 'voltage': 8.0},
                              'home': {'isHomeRecord': True, 'latitude': 35, 'longitude': 139},
                              'rc': {'uplinkSignal': None}})
        self.write()

    def write(self):
        self.path.write_text(json.dumps({'frames': self.rows}))

    def test_sync_ignores_epoch_and_converts_speed(self):
        log = FlightLog(self.path)
        self.assertEqual(log.offset(datetime(2026, 9, 27, 7, 29, 52, tzinfo=timezone.utc)), 2)
        f = load_flight_video(self.path, 1, elapsed=2)
        e = f.frames[f.min]
        self.assertEqual(e.dji_battery.magnitude, 88)
        self.assertEqual(e.dji_speed.magnitude, 36)
        self.assertEqual(e.dji_climb.magnitude, 7.2)
        self.assertEqual(e.speed.to('kph').magnitude, 36)
        self.assertEqual(e.dji_home_distance.magnitude, 0)
        self.assertIsNone(e.dji_uplink)
        # Rendering holds the most recent recorded sample; no invented percentages.
        self.assertEqual(f.get(timeunits(seconds=0.5)).dji_battery.magnitude, 88)
        self.assertEqual(f.max.millis(), 1000)

    def test_missing_alignment_and_nonoverlap_rejected(self):
        for elapsed in (None, -1, float('nan'), 4):
            with self.subTest(elapsed=elapsed), self.assertRaises(ValueError):
                load_flight_video(self.path, 2, elapsed=elapsed)

    def test_dashboard_altitude_uses_srt_absolute_height(self):
        from gopro_overlay.entry import Entry
        from gopro_overlay.framemeta import FrameMeta
        from gopro_overlay.units import units
        from datetime import timedelta
        start = datetime(2026, 9, 27, 7, 29, 53, tzinfo=timezone.utc)
        srt = FrameMeta(packets_per_second=1)
        for t in range(3):
            srt.add(timeunits(seconds=t), Entry(start + timedelta(seconds=t),
                                               alt=units.Quantity(100 + t, units.m)))
        frames = load_flight_video(self.path, 2, srt_frames=srt)
        self.assertEqual(frames.get(timeunits(seconds=0)).alt.magnitude, 100)
        self.assertEqual(frames.get(timeunits(seconds=0)).dji_relative_alt.magnitude, 13)
        self.assertEqual(frames.get(timeunits(seconds=1)).alt.magnitude, 101)
        # Absolute altitude remains available after the short flight-log tail.
        self.assertEqual(frames.get(timeunits(seconds=2)).alt.magnitude, 102)
        self.assertIsNone(frames.get(timeunits(seconds=2)).dji_relative_alt)

    def test_gaps_and_duplicate_timestamps_rejected(self):
        self.rows[2]['osd']['flyTime'] = 1
        self.write()
        with self.assertRaises(ValueError):
            FlightLog(self.path)
        self.rows = [self.rows[0], self.rows[4]]
        self.write()
        with self.assertRaises(ValueError):
            load_flight_video(self.path, 2, elapsed=0)

    def test_short_tail_is_blank_without_repeating_battery(self):
        f = load_flight_video(self.path, 2, elapsed=3)
        self.assertEqual(f.get(timeunits(seconds=1)).dji_battery.magnitude, 86)
        self.assertIsNone(f.get(timeunits(seconds=1.5)).dji_battery)
        self.assertIsNone(f.frames[f.max].dji_mode)
        self.assertEqual(f.max.millis(), 2000)
        with self.assertRaises(ValueError):
            load_flight_video(self.path, 20, elapsed=3)

    def test_selection_changes_panel_without_empty_tiles(self):
        xml = add_layout('<layout/>', selected=DEFAULT_LOG_FIELDS, flight_log=True)
        root = ET.fromstring(xml)
        self.assertEqual({e.get('name') for e in root}, DEFAULT_LOG_FIELDS)
        self.assertEqual(len(root), len(DEFAULT_LOG_FIELDS))
        self.assertIn('CLIMB (km/h)', xml)
        self.assertNotIn('dji_battery', add_layout('<layout/>'))

    def test_discrete_state_holds_during_interpolation(self):
        from gopro_overlay.entry import Entry
        from datetime import timedelta
        dt = datetime(2026, 9, 27, tzinfo=timezone.utc)
        a = Entry(dt, dji_mode=RecordedText('GPS_ATTI'))
        b = Entry(dt + timedelta(seconds=1), dji_mode=RecordedText('GPS_SPORT'))
        self.assertEqual(a.interpolate(b, dt + timedelta(seconds=0.5)).dji_mode, 'GPS_ATTI')


if __name__ == '__main__':
    unittest.main()
