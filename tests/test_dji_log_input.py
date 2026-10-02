import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import patch
from dji_log_input import select_txt


class FolderLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.video = Path(self.folder.name) / 'clip.MP4'
        self.video.touch()
        self.video.with_suffix('.SRT').write_text('2026-09-27 16:29:55.329\n', encoding='utf-8')

    def header(self, path):
        minute = 29 if path.stem != 'other-flight' else 10
        return SimpleNamespace(details=SimpleNamespace(
            start_time=datetime(2026, 9, 27, 7, minute, 52, tzinfo=timezone.utc), total_time=390))

    def test_selects_matching_time_among_multiple_flights(self):
        for name in ('matching.txt', 'other-flight.txt'):
            (self.video.parent / name).touch()
        with patch('dji_log_input.inspect_log', self.header):
            self.assertEqual(select_txt(self.video).name, 'matching.txt')

    def test_requires_srt_and_txt_and_rejects_ambiguity(self):
        with self.assertRaises(ValueError):
            select_txt(self.video)
        for name in ('matching.txt', 'duplicate.txt'):
            (self.video.parent / name).touch()
        with patch('dji_log_input.inspect_log', self.header), self.assertRaisesRegex(ValueError, '複数'):
            select_txt(self.video)
        self.video.with_suffix('.SRT').unlink()
        with self.assertRaisesRegex(ValueError, 'SRT'):
            select_txt(self.video)


if __name__ == '__main__':
    unittest.main()
