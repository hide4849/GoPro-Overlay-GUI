"""GUI-owned telemetry extensions. Keep the vendored upstream script pristine."""
import bisect
from pathlib import Path
from types import SimpleNamespace
import gpxpy
from gopro_overlay import timeseries_process
from gopro_overlay.entry import Entry
from gopro_overlay.framemeta import FrameMeta
from gopro_overlay.gpmf import GPS_FIXED_VALUES, GPSFix
from gopro_overlay.point import Point
from gopro_overlay.timeseries import Timeseries
from gopro_overlay.timeunits import timeunits
from gopro_overlay.units import units

def process_segment_deltas(frame_meta, processor, skip=1, filter_fn=lambda e: True):
    """Avoid 0.134.0's negative indices in segments shorter than twice skip."""
    frame_meta.check_modified()
    if skip < 1:
        raise ValueError("skip must be positive")
    count = len(frame_meta.framelist)
    if count >= 2 * skip:
        return frame_meta.process_deltas(processor, skip=skip, filter_fn=filter_fn)
    pairs = [(i, i + skip, i) for i in range(max(0, count - skip))]
    pairs += [(i - skip, i, i) for i in range(max(skip, count - skip), count)]
    for first, last, target in pairs:
        a = frame_meta.frames[frame_meta.framelist[first]]
        b = frame_meta.frames[frame_meta.framelist[last]]
        if filter_fn(a) and filter_fn(b):
            updates = processor(a, b, skip)
            if updates:
                frame_meta.frames[frame_meta.framelist[target]].update(**updates)


class TelemetrySession:
    """Per-run state; no global library patches or retained recordings."""

    def __init__(self, segments=None, fallback_gpx=None, log=lambda message: None, dji_options=None):
        self.dji_options = dji_options or {}
        self.is_dji = False
        self.paths = segments
        self.fallback_gpx = fallback_gpx
        self.log = log
        self.segments = None

    def load(self, loader, ffmpeg_gopro, inputpath):
        from dji_telemetry import sidecar, load_srt
        paths = self.paths or [inputpath]
        subtitles = [sidecar(path) for path in paths] if self.dji_options.get("enabled") else []
        if self.dji_options.get("enabled") and not all(subtitles):
            raise ValueError("DJI mode requires a matching SRT beside every MP4")
        if any(subtitles):
            if not all(subtitles):
                raise ValueError("Cannot mix DJI SRT and GoPro telemetry in one merge")
            if self.fallback_gpx:
                raise ValueError("GPX fallback is only supported for GoPro inputs")
            self.is_dji = True
            self.segments = []
            for path, subtitle in zip(paths, subtitles):
                frames = load_srt(subtitle, self.dji_options.get('timezone', 'Asia/Tokyo'),
                                  self.dji_options.get('home'))
                recording = ffmpeg_gopro.find_recording(Path(path))
                if abs(frames.max.millis() - recording.video.duration.millis()) > 2000:
                    raise ValueError(f"SRT/video duration mismatch: {subtitle}")
                self.segments.append(SimpleNamespace(framemeta=frames, recording=recording))
                self.log(f"DJI SRT: {subtitle} ({len(frames)} samples)")
            return SimpleNamespace(framemeta=self.segments[0].framemeta,
                                   recording=ffmpeg_gopro.find_recording(inputpath))
        if self.paths:
            self.log(f"Loading {len(self.paths)} telemetry segments independently")
            self.segments = [loader.load(Path(path)) for path in self.paths]
            result = SimpleNamespace(
                framemeta=self.segments[0].framemeta,
                recording=ffmpeg_gopro.find_recording(inputpath),
            )
        else:
            result = loader.load(inputpath)
        if self.fallback_gpx:
            timeseries, sources = load_gps_logger_gpx(Path(self.fallback_gpx))
            self.log(f"Fallback GPX: {timeseries.min} -> {timeseries.max}")
            self.log("Fallback sources: " + ", ".join(
                f"{name}={count}" for name, count in sorted(sources.items())))
            totals = {"gopro": 0, "fallback": 0, "missing": 0}
            for frames in self.frames(result.framemeta):
                counts = merge_gps_logger_fallback(timeseries, frames)
                for name in totals:
                    totals[name] += counts[name]
            self.log(f"GPS source samples: GoPro={totals['gopro']}, "
                     f"GPX fallback={totals['fallback']}, unavailable={totals['missing']}")
        return result

    def frames(self, default):
        return [segment.framemeta for segment in self.segments] if self.segments else [default]

    def combine(self, default):
        return combine_segment_framemeta(self.segments) if self.segments else default


def combine_segment_framemeta(segment_gopros):
    """Combine independently processed recordings on one video timeline.

    GoPro STMP/SHUT clocks restart for every recording.  Feeding a stream-copy
    concat directly to gopro-overlay makes its joined-file clock correction
    distort the total telemetry duration.  Keeping each segment independent
    through filtering also prevents speed/Kalman calculations from crossing a
    recording boundary, while the combined FrameMeta still gives map widgets
    the complete journey.
    """
    combined = FrameMeta()
    offset = timeunits(seconds=0)

    for gopro in segment_gopros:
        segment = gopro.framemeta
        segment.check_modified()
        for timestamp in segment.framelist:
            shifted = timestamp + offset
            entry = segment.frames[timestamp]
            if entry.timestamp is not None:
                entry.update(
                    timestamp=units.Quantity(shifted.millis(), units.number)
                )
            combined.add(shifted, entry)

        # Match ffmpeg's concat video timeline. Metadata commonly ends a
        # fraction of a second before the final video frame.
        offset += gopro.recording.video.duration

    return combined


FALLBACK_GPX_MAX_GAP_SECONDS = 60


def load_gps_logger_gpx(filepath: Path):
    """Load GPSLogger-style GPX, including standard GPX 1.0 speed fields."""
    with filepath.open("r", encoding="utf-8-sig") as gpx_file:
        document = gpxpy.parse(gpx_file)

    timeseries = Timeseries()
    sources = {}
    for track in document.tracks:
        for segment in track.segments:
            for point in segment.points:
                if point.time is None or point.latitude is None or point.longitude is None:
                    continue
                source = point.source or "unknown"
                sources[source] = sources.get(source, 0) + 1
                timeseries.add(Entry(
                    point.time,
                    point=Point(point.latitude, point.longitude),
                    alt=(units.Quantity(point.elevation, units.m)
                         if point.elevation is not None else None),
                    speed=(units.Quantity(point.speed, units.mps)
                           if point.speed is not None else None),
                    dop=(units.Quantity(point.horizontal_dilution, units.number)
                         if point.horizontal_dilution is not None else None),
                    gpsfix=GPSFix.LOCK_3D.value,
                    gpslock=units.Quantity(GPSFix.LOCK_3D.value, units.number),
                ))

    if len(timeseries) == 0:
        raise ValueError(f"No timestamped track points found in GPX file: {filepath}")

    # Some GPSLogger network fixes do not include <speed>. Fill only across a
    # short, recorded interval; never derive speed across a logger outage.
    timeseries.check_modified()
    dates = timeseries.dates
    for index, point_date in enumerate(dates):
        entry = timeseries.entries[point_date]
        if entry.speed is not None:
            continue
        candidates = []
        if index > 0:
            candidates.append((dates[index - 1], point_date))
        if index + 1 < len(dates):
            candidates.append((point_date, dates[index + 1]))
        candidates.sort(key=lambda pair: (pair[1] - pair[0]).total_seconds())
        for start, end in candidates:
            seconds = (end - start).total_seconds()
            if seconds <= 0 or seconds > FALLBACK_GPX_MAX_GAP_SECONDS:
                continue
            first = timeseries.entries[start]
            second = timeseries.entries[end]
            distance, _ = timeseries_process.distance_azi_between(first.point, second.point)
            entry.update(speed=distance / units.Quantity(seconds, units.seconds))
            break

    return timeseries, sources


def merge_gps_logger_fallback(gpx_timeseries: Timeseries, frame_meta: FrameMeta):
    """Replace only invalid GoPro GPS samples with nearby logger samples."""
    gpx_timeseries.check_modified()
    dates = gpx_timeseries.dates
    counts = {"gopro": 0, "fallback": 0, "missing": 0}

    frame_meta.check_modified()
    for timestamp in frame_meta.framelist:
        gopro_entry = frame_meta.frames[timestamp]
        if gopro_entry.gpsfix in GPS_FIXED_VALUES:
            counts["gopro"] += 1
            continue

        point_date = gopro_entry.dt
        index = bisect.bisect_left(dates, point_date)
        if index < len(dates) and dates[index] == point_date:
            logger_entry = gpx_timeseries.entries[point_date]
        elif index == 0 or index == len(dates):
            counts["missing"] += 1
            continue
        else:
            before = dates[index - 1]
            after = dates[index]
            if (after - before).total_seconds() > FALLBACK_GPX_MAX_GAP_SECONDS:
                counts["missing"] += 1
                continue
            logger_entry = gpx_timeseries.get(point_date)

        if logger_entry.point is None:
            counts["missing"] += 1
            continue

        updates = {
            "point": logger_entry.point,
            "gpsfix": GPSFix.LOCK_3D.value,
            "gpslock": units.Quantity(GPSFix.LOCK_3D.value, units.number),
        }
        for field in ("alt", "speed", "dop"):
            value = getattr(logger_entry, field)
            if value is not None:
                updates[field] = value
        gopro_entry.update(**updates)
        counts["fallback"] += 1

    return counts
