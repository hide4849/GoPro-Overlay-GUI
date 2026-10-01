import unittest
from dji_hud import mode_letter, signal_level, stick_position, stick_axes, DJI_ITEMS


class HUDTests(unittest.TestCase):
    def test_signal_five_bars_and_unknown(self):
        self.assertEqual([signal_level(v) for v in (0, 1, 20, 21, 40, 80, 100)], [0, 1, 1, 2, 2, 4, 5])
        self.assertIsNone(signal_level(None))

    def test_stick_center_extremes_and_clipping(self):
        self.assertEqual([stick_position(v) for v in (364, 1024, 1684, 0, 2048)], [-1, 0, 1, -1, 1])
        self.assertIsNone(stick_position(None))
        self.assertEqual(stick_axes(2), (('rudder', 'throttle'), ('aileron', 'elevator')))
        self.assertEqual(stick_axes(1)[1][1], 'throttle')
        self.assertEqual(stick_axes(3)[0], ('aileron', 'elevator'))

    def test_modes_and_exact_selection(self):
        self.assertEqual([mode_letter(v) for v in ('GPS_ATTI', 'GPS_SPORT', 'TRIPOD', 'MANUAL')], list('NSCM'))
        self.assertEqual(mode_letter('ASSISTED_TAKEOFF'), '--')
        self.assertEqual(len(DJI_ITEMS), 14)
        self.assertNotIn('dji_capacity', DJI_ITEMS)


if __name__ == '__main__':
    unittest.main()
