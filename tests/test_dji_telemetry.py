import tempfile
import unittest
from pathlib import Path
from dji_telemetry import load_srt, parse_home, add_layout
from xml.etree import ElementTree as ET


def sample(t, lat=35, rel=0):
    return (f'{t+1}\n00:00:{t:02},000 --> 00:00:{t+1:02},000\n'
            f'2026-09-27 16:29:{t:02}.000\n'
            f'[latitude: {lat}] [longitude: 139] [rel_alt: {rel} abs_alt: 90]\n\n')


class DJITests(unittest.TestCase):
    def load(self, text, home=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.SRT'
            path.write_text(text)
            return load_srt(path, home=home)

    def test_stationary_and_missing_log_values(self):
        f = self.load(sample(0) + sample(1))
        e = f.frames[f.min]
        self.assertEqual(e.speed.magnitude, 0)
        self.assertIsNone(e.dji_home_distance)
        self.assertIsNone(e.battery)
        self.assertIsNone(e.gpslock)
        self.assertEqual(e.dt.utcoffset().total_seconds(), 9 * 3600)

    def test_motion_home_and_vertical_speed(self):
        f = self.load(sample(0) + sample(1, 35.0001, 2), (35, 139))
        e = f.frames[f.max]
        self.assertAlmostEqual(e.speed.magnitude, 11.094, places=2)
        self.assertAlmostEqual(e.dji_home_distance.magnitude, 11.094, places=2)
        self.assertEqual(e.dji_vertical_speed.magnitude, 2)

    def test_moving_map_draws_interpolated_dji_heading(self):
        from PIL import Image, ImageDraw
        from gopro_overlay.widgets.map import MovingMap
        from gopro_overlay.point import Coordinate
        from gopro_overlay.timeunits import timeunits
        frames = self.load(sample(0) + sample(1, 34.9999))
        entry = frames.get(timeunits(seconds=0.5))
        # Exercise the actual map rotation without network tile downloads.
        widget = MovingMap(
            at=Coordinate(0, 0), location=lambda: entry.point,
            azimuth=lambda: entry.azi,
            renderer=lambda map: Image.new('RGBA', map.size, (40, 60, 80, 255)),
        )
        image = Image.new('RGBA', (256, 256))
        widget.draw(image, ImageDraw.Draw(image))
        self.assertIsNotNone(image.getbbox())
        self.assertAlmostEqual(entry.azi.to('degree').magnitude, 180, places=3)

    def test_invalid_input(self):
        for text in ('', sample(0), sample(0) + sample(0), sample(0, 100) + sample(1)):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)
        for home in ('nan,139', '91,0', '1', 'abc,2'):
            with self.assertRaises(ValueError):
                parse_home(home)

    def test_home_label_not_invented(self):
        root = ET.fromstring(add_layout('<layout/>'))
        self.assertNotIn('dji_home_distance', [e.get('name') for e in root])
        self.assertEqual(parse_home('35,139'), (35, 139))


if __name__ == '__main__':
    unittest.main()
