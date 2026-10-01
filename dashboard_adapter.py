"""Checked integration points for the unmodified upstream dashboard script.

Only transform in memory. Unknown upstream changes fail before execution.
"""
import hashlib
import json
from importlib.metadata import version
from pathlib import Path


def hardware_output_options(encoder):
    """Keep software defaults; target 7.5Mbps for all hardware encoders."""
    codecs = {
        "amf": ["-c:v", "h264_amf", "-quality", "quality", "-rc", "vbr_peak"],
        "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr"],
        "qsv": ["-c:v", "h264_qsv"],
        "vt": ["-c:v", "h264_videotoolbox"],
    }
    if encoder == "cpu":
        return None
    if encoder not in codecs:
        raise ValueError(f"Unknown encoder: {encoder}")
    return codecs[encoder] + ["-b:v", "7500k", "-maxrate", "15000k",
                             "-bufsize", "15000k", "-pix_fmt", "yuv420p"]


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Upstream dashboard integration point changed: " + old.strip()[:100])
    return source.replace(old, new, 1)


def adapt_source(source):
    source = _replace_once(
        source, '    args = gopro_dashboard_arguments()',
        '    from dashboard_extensions import TelemetrySession, process_segment_deltas\n'
        '    gui_telemetry = TelemetrySession(\n'
        '        globals().get("GOPRO_OVERLAY_TELEMETRY_SEGMENTS"),\n'
        '        globals().get("GOPRO_OVERLAY_FALLBACK_GPX"), log, globals().get("GOPRO_OVERLAY_DJI_OPTIONS"))\n'
        '    args = gopro_dashboard_arguments()')
    source = _replace_once(
        source, '                    gopro = loader.load(inputpath)',
        '                    gopro = gui_telemetry.load(loader, ffmpeg_gopro, inputpath)')
    start = '                frame_meta.process(timeseries_process.process_ses('
    end = '                frame_meta.process(timeseries_process.filter_locked())'
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError("Upstream telemetry processing block changed")
    first, last = source.index(start), source.index(end) + len(end)
    block = source[first:last]
    if block.count("frame_meta.process_deltas(") != 2:
        raise ValueError("Upstream delta processing integration points changed")
    block = block.replace("frame_meta.process_deltas(",
                          "process_segment_deltas(frame_meta, ")
    # Keep upstream processing operations, but apply them separately to each
    # recording so smoothing and speed calculations never cross boundaries.
    wrapped = ('                for frame_meta in gui_telemetry.frames(frame_meta):\n'
               '                    packets_per_second = frame_meta.packets_per_second()\n'
               + '                    if not gui_telemetry.is_dji:\n'
               + '\n'.join('        ' + line for line in block.splitlines()) + '\n'
               '                frame_meta = gui_telemetry.combine(frame_meta)\n'
               '                packets_per_second = frame_meta.packets_per_second()')
    source = source[:first] + wrapped + source[last:]
    source = _replace_once(
        source, '                if args.profile:',
        '                gui_output = globals().get("GOPRO_OVERLAY_OUTPUT_OPTIONS")\n'
        '                if gui_output is not None:\n'
        '                    from gopro_overlay.ffmpeg_overlay import FFMPEGOptions\n'
        '                    ffmpeg_options = FFMPEGOptions(output=gui_output)\n'
        '                elif args.profile:')
    compile(source, '<adapted-dashboard>', 'exec')
    return source


def validate_vendor(path, check_installed=False):
    path = Path(path)
    manifest = json.loads(path.with_name('upstream.json').read_text())
    # Git's Windows checkout may convert line endings without changing source.
    data = path.read_bytes().replace(b'\r\n', b'\n')
    if hashlib.sha256(data).hexdigest() != manifest['sha256']:
        raise ValueError('Upstream dashboard checksum mismatch; restore the matching vendor files')
    if check_installed and version('gopro-overlay') != manifest['version']:
        raise ValueError('Dashboard/library version mismatch; install requirements.txt and rebuild')
    return adapt_source(data.decode('utf-8'))


def run_dashboard(path, init_globals):
    source = validate_vendor(path, check_installed=True)
    namespace = dict(init_globals, __name__='__main__', __file__=str(path), __package__=None)
    exec(compile(source, str(path), 'exec'), namespace)
