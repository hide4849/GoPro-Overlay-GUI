"""Direct TXT input with local decoded-data caching; never persist SDK keys."""
import hashlib
import os
import tempfile
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from dji_flightlog import FlightLog


def folder_txts(video):
    return sorted(p for p in Path(video).parent.iterdir() if p.is_file() and p.suffix.lower() == '.txt')


def select_txt(video, timezone_name='Asia/Tokyo'):
    from dji_telemetry import sidecar
    video = Path(video)
    subtitle = sidecar(video)
    if subtitle is None:
        raise ValueError(f'{video.name}: 同じフォルダに同名のSRTが必要です。')
    with subtitle.open(encoding='utf-8-sig') as stream:
        match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+', stream.read(8192))
    if not match:
        raise ValueError(f'{subtitle.name}: SRTの撮影時刻を読み取れません。')
    start = datetime.fromisoformat(match[0]).replace(tzinfo=ZoneInfo(timezone_name)).timestamp()
    matches = []
    for path in folder_txts(video):
        try:
            log = inspect_log(path)
            origin = log.details.start_time.timestamp()
            if origin - 5 <= start <= origin + log.details.total_time + 5:
                matches.append(path)
        except (ValueError, OSError, AttributeError):
            continue
    if not matches:
        raise ValueError(f'{video.name}: 同じフォルダに撮影時刻に対応する飛行ログTXTがありません。')
    if len(matches) != 1:
        raise ValueError(f'{video.name}: 対応するTXTが複数あります。必要なログだけを同じフォルダに置いてください。')
    return matches[0]


def inspect_log(path):
    path = Path(path)
    if path.suffix.lower() == '.json':
        FlightLog(path)
        return None
    if path.suffix.lower() != '.txt':
        raise ValueError('DJI飛行ログのTXTを選択してください。')
    from pydjirecord import DJILog
    try:
        return DJILog.from_bytes(path.read_bytes())
    except Exception:
        raise ValueError('DJI飛行ログとして読み取れないTXTです。') from None


def decode_log(path, api_key='', cache_dir=None):
    path = Path(path)
    if path.suffix.lower() == '.json':
        FlightLog(path)
        return path
    payload = path.read_bytes()
    cache = Path(cache_dir or Path(tempfile.gettempdir()) / 'GoProOverlayGUI-flightlogs')
    target = cache / (hashlib.sha256(payload).hexdigest() + '.json')
    if target.is_file():
        try:
            FlightLog(target)
            return target
        except (ValueError, OSError):
            pass
    log = inspect_log(path)
    key = api_key.strip() or os.environ.get('DJI_API_KEY', '').strip()
    if log.version >= 13 and not key:
        raise ValueError('このTXTの初回読み込みにはDJIのSDK Keyが必要です。DJI設定欄に入力してください。')
    from pydjirecord.export.json import export_json
    try:
        keys = log.fetch_keychains(key, cache=False) if log.version >= 13 else None
        frames = log.frames(keys)
        cache.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.tmp')
        export_json(log, frames, output=temporary)
        FlightLog(temporary)
        temporary.replace(target)
    except Exception as exc:
        message = str(exc).replace(key, '[SDK Key]') if key else str(exc)
        raise ValueError('TXTの復号に失敗しました。SDK Keyと通信を確認してください。 ' + message) from None
    return target
