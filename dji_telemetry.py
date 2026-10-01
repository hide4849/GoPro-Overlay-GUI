"""DJI SRT telemetry; never infer battery, radio status, or a home point."""
import bisect
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

from gopro_overlay.entry import Entry
from gopro_overlay.framemeta import FrameMeta
from gopro_overlay.point import Point
from gopro_overlay.timeunits import timeunits
from gopro_overlay.timeseries_process import distance_azi_between
from gopro_overlay.units import units

FIELDS = {
    'dji_elapsed': 'REC TIME (s)',
    'dji_relative_alt': 'REL ALT (m)',
    'dji_vertical_speed': 'VERT SPEED (m/s)',
    'dji_start_distance': 'FROM REC START (m)',
    'dji_home_distance': 'FROM HOME (m)',
}


def sidecar(video):
    path = Path(video)
    for suffix in ('.SRT', '.srt'):
        candidate = path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def parse_home(text):
    if not text.strip():
        return None
    try:
        lat, lon = map(float, text.split(','))
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError()
        return lat, lon
    except ValueError:
        raise ValueError('Home must be latitude,longitude (e.g. 37.748096,140.469722)') from None


def _seconds(value):
    h, m, s = value.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def load_srt(path, timezone_name='Asia/Tokyo', home=None):
    rows = []
    for block in re.split(r'\n\s*\n', Path(path).read_text(encoding='utf-8-sig')):
        timing = re.search(r'(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})', block)
        if not timing:
            continue
        date = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+', block)
        values = dict(re.findall(r'(latitude|longitude|rel_alt|abs_alt):\s*([-+\d.]+)', block))
        if not date or not {'latitude', 'longitude'} <= values.keys():
            raise ValueError(f'Missing DJI date/GPS data at {timing[1]} in {path}')
        t = _seconds(timing[1])
        end = _seconds(timing[2])
        lat, lon = float(values['latitude']), float(values['longitude'])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            raise ValueError(f'Invalid DJI GPS at {timing[1]}')
        if end <= t or (rows and t <= rows[-1][0]):
            raise ValueError('DJI subtitle times must be increasing')
        if rows and t - rows[-1][1] > 1:
            raise ValueError('DJI SRT has a telemetry gap longer than one second')
        rows.append((t, end, datetime.fromisoformat(date[0]).replace(tzinfo=ZoneInfo(timezone_name)), Point(lat, lon), values))
    if len(rows) < 2:
        raise ValueError('DJI SRT requires at least two timestamped GPS samples')
    result = FrameMeta(packets_per_second=30)
    times = [r[0] for r in rows]
    origin = rows[0][3]
    home_point = Point(*home) if home is not None else None
    odo = units.Quantity(0, units.m)
    # A centred two-second baseline suppresses repeated per-frame GPS fixes.
    for i, (t, end, date, point, values) in enumerate(rows):
        lo = max(0, bisect.bisect_right(times, t - 1) - 1)
        hi = min(len(rows) - 1, bisect.bisect_left(times, t + 1))
        a, b = rows[lo], rows[hi]
        dt = b[0] - a[0]
        distance, azi = distance_azi_between(a[3], b[3])
        if i:
            odo += distance_azi_between(rows[i - 1][3], point)[0]
        fields = dict(point=point, speed=distance / units.Quantity(dt, units.seconds),
                      azi=units.Quantity(azi, units.degree), odo=odo, timestamp=units.Quantity(t * 1000, units.number),
                      dji_elapsed=units.Quantity(t, units.seconds),
                      dji_start_distance=distance_azi_between(origin, point)[0])
        if 'abs_alt' in values:
            fields['alt'] = units.Quantity(float(values['abs_alt']), units.m)
        if 'rel_alt' in values:
            fields['dji_relative_alt'] = units.Quantity(float(values['rel_alt']), units.m)
        if 'rel_alt' in a[4] and 'rel_alt' in b[4]:
            fields['dji_vertical_speed'] = units.Quantity((float(b[4]['rel_alt']) - float(a[4]['rel_alt'])) / dt, units.mps)
        if home_point is not None:
            fields['dji_home_distance'] = distance_azi_between(home_point, point)[0]
        # Internal lock flag enables route widgets; never expose as measured GPS lock.
        fields['gpsfix'] = 3
        result.add(timeunits(seconds=t), Entry(date, **fields))
    # Keep the subtitle end on the video timeline, avoiding upstream stretching
    # the last sample time to the full clip duration.
    last = rows[-1]
    end_entry = Entry(last[2] + timedelta(seconds=last[1] - last[0]), **fields)
    end_entry.update(dji_elapsed=units.Quantity(last[1], units.seconds),
                     timestamp=units.Quantity(last[1] * 1000, units.number))
    result.add(timeunits(seconds=last[1]), end_entry)
    result.check_modified()
    return result


def add_layout(xml_text, home=None):
    root = ET.fromstring(xml_text)
    for i, (key, label) in enumerate(FIELDS.items()):
        if key == 'dji_home_distance' and home is None:
            continue
        group = ET.SubElement(root, 'composite', name=key, x='20', y=str(130 + i * 56))
        ET.SubElement(group, 'component', type='text', x='0', y='0', size='16').text = label
        ET.SubElement(group, 'component', type='metric', x='0', y='20', size='24', metric=key, dp='1')
    return ET.tostring(root, encoding='unicode')
