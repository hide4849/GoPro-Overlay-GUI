"""Offline import of decoded DJI flight-record JSON, aligned to original video."""
import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

# Only useful, understood values. Cell voltages and inferred health are excluded.
LOG_FIELDS = {
    'dji_speed': ('SPEED (km/h)', 'osd', 'hSpeed', 3.6, 1),
    'dji_battery': ('BATTERY (%)', 'battery', 'chargeLevel', 1, 0),
    'dji_temperature': ('BAT TEMP (C)', 'battery', 'temperature', 1, 1),
    'dji_voltage': ('VOLTAGE (V)', 'battery', 'voltage', 1, 2),
    'dji_current': ('CURRENT (A)', 'battery', 'current', 1, 1),
    'dji_flight_time': ('FLIGHT TIME (s)', 'osd', 'flyTime', 1, 1),
    'dji_log_alt': ('REL ALT (m)', 'osd', 'height', 1, 1),
    'dji_climb': ('CLIMB (km/h)', 'osd', 'zSpeed', -3.6, 1),
    'dji_distance': ('TRAVELLED (m)', 'osd', 'cumulativeDistance', 1, 0),
    'dji_satellites': ('GPS SATELLITES', 'osd', 'gpsNum', 1, 0),
    'dji_uplink': ('UPLINK (%)', 'rc', 'uplinkSignal', 1, 0),
    'dji_downlink': ('DOWNLINK (%)', 'rc', 'downlinkSignal', 1, 0),
    'dji_pitch': ('PITCH (deg)', 'osd', 'pitch', 1, 1),
    'dji_roll': ('ROLL (deg)', 'osd', 'roll', 1, 1),
    'dji_yaw': ('HEADING (deg)', 'osd', 'yaw', 1, 0),
    'dji_gimbal_pitch': ('GIMBAL PITCH (deg)', 'gimbal', 'pitch', 1, 1),
    'dji_capacity': ('CAPACITY (mAh)', 'battery', 'currentCapacity', 1, 0),
    'dji_full_capacity': ('FULL CAPACITY (mAh)', 'battery', 'fullCapacity', 1, 0),
    'dji_cycles': ('DISCHARGE COUNT', 'battery', 'numberOfDischarges', 1, 0),
    'dji_recording': ('RECORDING (0/1)', 'camera', 'isVideo', 1, 0),
    'dji_record_time': ('CAMERA REC (s)', 'camera', 'recordTime', 1, 0),
    'dji_rc_aileron': ('RC AILERON (raw)', 'rc', 'aileron', 1, 0),
    'dji_rc_elevator': ('RC ELEVATOR (raw)', 'rc', 'elevator', 1, 0),
    'dji_rc_throttle': ('RC THROTTLE (raw)', 'rc', 'throttle', 1, 0),
    'dji_rc_rudder': ('RC RUDDER (raw)', 'rc', 'rudder', 1, 0),
    'dji_sticks': ('STICKS', 'rc', 'aileron', 1, 0),
    'dji_mode': ('FLIGHT MODE', 'osd', 'flycState', None, 0),
    'dji_return_status': ('RETURN STATUS', 'osd', 'goHomeStatus', None, 0),
    'dji_warning': ('WARNING', 'app', 'warn', None, 0),
    'dji_notice': ('NOTICE', 'app', 'tip', None, 0),
}
from dji_hud import DJI_ITEMS
DEFAULT_LOG_FIELDS = set(DJI_ITEMS) - {'dji_home_distance'}


class RecordedText(str):
    """Hold discrete states when upstream interpolates an Entry."""
    def __sub__(self, other):
        return 0

    def __add__(self, other):
        return self if isinstance(other, (int, float)) else super().__add__(other)


def create_text_metric(widget_factory, element, entry, **kwargs):
    from gopro_overlay.layout_components import text
    from gopro_overlay.layout_xml import at, attrib, rgbattr
    key = attrib(element, 'metric')
    return text(at=at(element), value=lambda: (str(getattr(entry(), key) or '--').replace('_', ' ')[:24]),
                font=widget_factory._font(element, 'size', d=16),
                fill=rgbattr(element, 'rgb', d=(255, 255, 255)),
                stroke=(0, 0, 0), stroke_width=1)


def number(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


class FlightLog:
    def __init__(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        self.rows = data.get('frames', [])
        if len(self.rows) < 2:
            raise ValueError('Select decoded DJI JSON with frames (not TXT or a summary-only JSON).')
        self.times = [r.get('osd', {}).get('flyTime') for r in self.rows]
        if not all(number(t) and t >= 0 for t in self.times) or any(
                b <= a for a, b in zip(self.times, self.times[1:])):
            raise ValueError('Flight-log times must be finite and strictly increasing.')
        anchors = []
        for t, row in zip(self.times, self.rows):
            try:
                dt = datetime.fromisoformat(row.get('custom', {}).get('dateTime', ''))
                if dt.year >= 2020 and dt.tzinfo is not None:
                    anchors.append(dt.timestamp() - t)
            except (ValueError, TypeError):
                pass
        self.origin = median(anchors) if anchors else None
        if anchors and max(abs(a - self.origin) for a in anchors) > 1:
            raise ValueError('Flight-log clock is inconsistent; cannot safely synchronize video.')
        self.available = {key for key, (_, group, field, scale, _) in LOG_FIELDS.items()
                          if any((isinstance(r.get(group, {}).get(field), str) if scale is None
                                  else number(r.get(group, {}).get(field))) for r in self.rows)}

    def offset(self, start=None, elapsed=None):
        if elapsed is not None:
            if not number(elapsed) or elapsed < 0:
                raise ValueError('Video start must be a non-negative flight elapsed time in seconds.')
            return elapsed
        if start is None or self.origin is None:
            raise ValueError('Without SRT, enter video start as flight elapsed seconds.')
        return start.timestamp() - self.origin

    def row_at(self, elapsed):
        if elapsed < self.times[0] - 0.15 or elapsed > self.times[-1] + 0.15:
            raise ValueError('Video is outside the selected flight log. Check log and video start.')
        index = min(max(bisect.bisect_right(self.times, elapsed) - 1, 0), len(self.rows) - 1)
        if index + 1 < len(self.rows) and self.times[index + 1] - self.times[index] > 1:
            raise ValueError('Flight log has a gap longer than one second in this video.')
        return self.rows[index]


def load_flight_video(path, duration, srt_frames=None, elapsed=None, home=None):
    from gopro_overlay.entry import Entry
    from gopro_overlay.framemeta import FrameMeta
    from gopro_overlay.point import Point
    from gopro_overlay.timeseries_process import distance_azi_between
    from gopro_overlay.timeunits import timeunits
    from gopro_overlay.units import units

    flight = FlightLog(path)
    if not number(duration) or duration <= 0:
        raise ValueError('Video duration must be positive.')
    start = srt_frames.frames[srt_frames.min].dt if srt_frames is not None else None
    offset = flight.offset(start, elapsed)
    covered_start = max(0.0, flight.times[0] - offset)
    covered_end = min(duration, flight.times[-1] - offset)
    # Recording can continue briefly after the flight record closes. Do not
    # stretch flight telemetry or repeat its last values over missing footage.
    if covered_start >= covered_end or covered_start > 5 or duration - covered_end > 5:
        raise ValueError(
            f'動画と飛行ログの時刻が合いません。動画の飛行経過秒数: '
            f'{offset:.2f}～{offset + duration:.2f}秒、'
            f'ログの範囲: {flight.times[0]:.2f}～{flight.times[-1]:.2f}秒。'
            '対応するログ・SRT・動画開始秒数を確認してください。')
    result = FrameMeta(packets_per_second=10)
    samples = [0.0] + [t - offset for t in flight.times if 0 < t - offset < duration] + [duration]
    if covered_start > 0:
        samples.append(covered_start)
    if covered_end < duration:
        samples.extend([covered_end, min(duration, covered_end + 0.001)])
    samples = sorted(set(samples))
    origin_point = None
    for t in samples:
        if t < covered_start or t > covered_end:
            fields = dict(dji_elapsed=units.Quantity(t, units.seconds),
                          timestamp=units.Quantity(t * 1000, units.number))
            if srt_frames is not None:
                srt_entry = srt_frames.get(timeunits(seconds=t))
                for key in ('point', 'speed', 'alt', 'azi', 'odo', 'gpsfix',
                            'dji_relative_alt', 'dji_vertical_speed', 'dji_start_distance'):
                    value = getattr(srt_entry, key)
                    if value is not None:
                        fields[key] = value
            if flight.origin is None:
                raise ValueError('Flight log has no valid absolute timestamps.')
            dt = datetime.fromtimestamp(flight.origin + offset + t, start.tzinfo if start else timezone.utc)
            result.add(timeunits(seconds=t), Entry(dt, **fields))
            continue
        row = flight.row_at(offset + t)
        osd = row['osd']
        fields = {}
        # The original dashboard altitude uses absolute SRT altitude, whereas
        # flight-log height is relative to takeoff and belongs to the DJI HUD.
        if srt_frames is not None:
            altitude = srt_frames.get(timeunits(seconds=t)).alt
            if altitude is not None:
                fields['alt'] = altitude
        for key, (_, group, field, scale, _) in LOG_FIELDS.items():
            value = row.get(group, {}).get(field)
            if scale is None:
                if isinstance(value, str):
                    fields[key] = RecordedText(value)
                continue
            if number(value):
                measured = value * scale
                if key == 'dji_yaw':
                    measured %= 360
                fields[key] = units.Quantity(0 if measured == 0 else measured, units.number)
        lat, lon = osd.get('latitude'), osd.get('longitude')
        if number(lat) and number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
            point = Point(lat, lon)
            origin_point = origin_point or point
            fields.update(point=point, gpsfix=3,
                          dji_start_distance=distance_azi_between(origin_point, point)[0])
            home_data = row.get('home', {})
            home_pair = home
            if home_pair is None and home_data.get('isHomeRecord'):
                hl, hn = home_data.get('latitude'), home_data.get('longitude')
                if number(hl) and number(hn) and -90 <= hl <= 90 and -180 <= hn <= 180 and (hl or hn):
                    home_pair = (hl, hn)
            if home_pair:
                fields['dji_home_distance'] = distance_azi_between(Point(*home_pair), point)[0]
        if number(osd.get('hSpeed')):
            fields['speed'] = units.Quantity(osd['hSpeed'], units.mps)
        if number(osd.get('height')):
            fields['dji_relative_alt'] = units.Quantity(osd['height'], units.m)
        if number(osd.get('zSpeed')):
            fields['dji_vertical_speed'] = units.Quantity(-osd['zSpeed'], units.mps)
        if number(osd.get('yaw')):
            fields['azi'] = units.Quantity(osd['yaw'], units.degree)
        if number(osd.get('cumulativeDistance')):
            fields['odo'] = units.Quantity(osd['cumulativeDistance'], units.m)
        fields.update(dji_elapsed=units.Quantity(t, units.seconds),
                      timestamp=units.Quantity(t * 1000, units.number))
        if flight.origin is None:
            raise ValueError('Flight log has no valid absolute timestamps.')
        dt = datetime.fromtimestamp(flight.origin + offset + t, start.tzinfo if start else timezone.utc)
        result.add(timeunits(seconds=t), Entry(dt, **fields))
    result.check_modified()
    return result
