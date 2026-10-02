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
from dji_flightlog import LOG_FIELDS
from dji_hud import DJI_ITEMS

FIELDS = {
    'dji_elapsed': 'REC TIME (s)',
    'dji_relative_alt': 'REL ALT (m)',
    'dji_vertical_speed': 'VERT SPEED (m/s)',
    'dji_start_distance': 'FROM REC START (m)',
    'dji_home_distance': 'FROM HOME (m)',
}
FIELDS.update({key: spec[0] for key, spec in LOG_FIELDS.items()})


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
            fields['dji_log_alt'] = units.Quantity(float(values['rel_alt']), units.number)
        if 'rel_alt' in a[4] and 'rel_alt' in b[4]:
            fields['dji_vertical_speed'] = units.Quantity((float(b[4]['rel_alt']) - float(a[4]['rel_alt'])) / dt, units.mps)
            fields['dji_climb'] = units.Quantity(fields['dji_vertical_speed'].to('kph').magnitude, units.number)
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


def add_layout(xml_text, home=None, selected=None, flight_log=False, scale=1, stick_mode=2):
    root = ET.fromstring(xml_text)
    for speed_unit in root.iter('component'):
        if speed_unit.get('type') == 'metric_unit' and speed_unit.get('metric') == 'speed':
            speed_unit.set('type', 'text')
            for attribute in ('metric', 'units', 'format', 'dp'):
                speed_unit.attrib.pop(attribute, None)
            speed_unit.text = 'km/h'
    visible = []
    for key, label in FIELDS.items():
        if key not in DJI_ITEMS:
            continue
        if selected is not None and key not in selected:
            continue
        if key in LOG_FIELDS and not flight_log and key not in ('dji_log_alt', 'dji_climb'):
            continue
        if key == 'dji_home_distance' and home is None and not flight_log:
            continue
        visible.append((key, label))
    if flight_log:
        priority = list(DJI_ITEMS)
        visible.sort(key=lambda item: priority.index(item[0]) if item[0] in priority else len(priority))
    tile_index = 0
    altitude = next((e for e in root.iter() if e.get('name') == 'altitude'), None)
    altitude_y = float(altitude.get('y', 980 * scale)) if altitude is not None else 980 * scale
    battery_index = 0
    battery_fields = {
        'dji_battery': ('%', 0, 24),
        'dji_voltage': ('V', 2, 24),
        'dji_current': ('A', 2, 24),
        'dji_temperature': ('℃', 1, 32),
    }
    battery_keys = [key for key in battery_fields if any(item[0] == key for item in visible)]
    visible.sort(key=lambda item: battery_keys.index(item[0]) if item[0] in battery_keys else len(battery_keys))
    for key, label in visible:
        px = lambda value: str(round(value * scale))
        if key == 'dji_flight_time':
            clock = next((e for e in root.iter() if e.get('name') == 'date_and_time'), None)
            x = float(clock.get('x', 260 * scale)) + 40 * scale if clock is not None else 300 * scale
            y = float(clock.get('y', 30 * scale)) if clock is not None else 30 * scale
            group = ET.SubElement(root, 'composite', name=key, x=str(round(x)), y=str(round(y)))
            ET.SubElement(group, 'component', type='text', x='0', y='0', size=px(16)).text = 'FLIGHT TIME(s)'
            ET.SubElement(group, 'component', type='metric', metric=key, x='0', y=px(24), size=px(32), dp='1')
            continue
        if key == 'dji_log_alt':
            altitude = next((e for e in root.iter() if e.get('name') == 'altitude'), None)
            if altitude is not None:
                group = ET.fromstring(ET.tostring(altitude, encoding='unicode'))
                group.set('name', key)
                group.set('x', str(round(float(altitude.get('x', 0)) + 100 * scale)))
                for component in list(group):
                    if component.get('type') == 'icon':
                        group.remove(component)
                for component in group.iter('component'):
                    if component.get('type') == 'metric_unit':
                        component.set('type', 'text')
                        for attribute in ('metric', 'units', 'format', 'dp'):
                            component.attrib.pop(attribute, None)
                        component.text = 'REL ALT(m)'
                    elif component.get('type') == 'metric':
                        component.set('metric', key)
                        component.attrib.pop('units', None)
                root.append(group)
            else:
                group = ET.SubElement(root, 'composite', name=key, x=px(116), y=str(round(altitude_y)))
                ET.SubElement(group, 'component', type='text', x=px(70), y='0', size=px(16)).text = 'REL ALT(m)'
                ET.SubElement(group, 'component', type='metric', metric=key, x=px(70), y=px(18), size=px(32), dp='0')
            continue
        if key in ('dji_climb', 'dji_home_distance', 'dji_distance'):
            x, title, dp = {
                'dji_climb': (400, 'CLIMB (km/h)', 1),
                'dji_home_distance': (616, 'HOME(m)', 0),
                'dji_distance': (716, 'TOTAL(m)', 0),
            }[key]
            group = ET.SubElement(root, 'composite', name=key, x=px(x), y=str(round(altitude_y)))
            ET.SubElement(group, 'component', type='text', x='0', y='0', size=px(16)).text = title
            ET.SubElement(group, 'component', type='metric', metric=key, x='0', y=px(18), size=px(32), dp=str(dp))
            continue
        if key in battery_fields:
            unit, dp, unit_width = battery_fields[key]
            group = ET.SubElement(root, 'composite', name=key,
                                  x=px(1900), y=str(round(altitude_y + (22 - (len(battery_keys) - 1 - battery_index) * 38) * scale)))
            if battery_index == 0:
                ET.SubElement(group, 'component', type='text', x='0', y=px(-40),
                              size=px(28), align='right').text = 'Battery'
            ET.SubElement(group, 'component', type='metric', metric=key,
                          x=px(-unit_width), y='0', size=px(28), dp=str(dp), align='right')
            ET.SubElement(group, 'component', type='text', x='0', y='0',
                          size=px(28), align='right').text = unit
            battery_index += 1
            continue
        if key == 'dji_satellites':
            gps = next((e for e in root.iter() if e.get('name') == 'gps_info'), None)
            group = ET.SubElement(root, 'composite', name=key,
                                  x=gps.get('x', px(1644)) if gps is not None else px(1644),
                                  y=str(round(float(gps.get('y', 0) if gps is not None else 0) + 82 * scale)))
            ET.SubElement(group, 'component', type='text', x='0', y='0', size=px(16)).text = 'Satellites:'
            ET.SubElement(group, 'component', type='metric', metric=key,
                          x=px(100), y='0', size=px(16), dp='0')
            continue
        if key in ('dji_uplink', 'dji_downlink'):
            ET.SubElement(root, 'component', name=key, type='dji_signal', metric=key,
                          label='RC' if key == 'dji_uplink' else 'VTX',
                          x=px(1720), y=str(round(altitude_y + (-83.5 if key == 'dji_uplink' else -7.5) * scale)),
                          scale=str(scale * 0.75), size=px(18))
            continue
        if key == 'dji_mode':
            speed = next((e for e in root.iter() if e.get('name') == 'big_mph'), None)
            x = speed.get('x', px(16)) if speed is not None else px(16)
            y = float(speed.get('y', px(800))) if speed is not None else 800 * scale
            ET.SubElement(root, 'component', type='dji_mode', name=key, x=x,
                          y=str(round(y - 70 * scale)), scale=str(scale), size=px(34))
            continue
        if key == 'dji_sticks':
            ET.SubElement(root, 'component', type='dji_sticks', name=key,
                          x=px((1920-272)/2), y=px(890), scale=str(scale),
                          stick_mode=str(stick_mode), size=px(16))
            continue
        i = tile_index
        tile_index += 1
        group = ET.SubElement(root, 'frame', name=key,
                              x=px(24 + (i % 3) * 242), y=px(130 + (i // 3) * 80),
                              width=px(232), height=px(70), bg='12,22,34,190', cr=px(10))
        ET.SubElement(group, 'component', type='text', x=px(12), y=px(8),
                      size=px(14), rgb='146,173,193').text = label
        is_text = key in LOG_FIELDS and LOG_FIELDS[key][3] is None
        ET.SubElement(group, 'component', type='dji_text' if is_text else 'metric', x=px(12), y=px(29),
                      size=px(16 if is_text else 28), metric=key, dp=str(LOG_FIELDS[key][4] if key in LOG_FIELDS else 1),
                      rgb='104,226,231')
    return ET.tostring(root, encoding='unicode')
