import os
import re
import sys
import time
import shutil
import threading
import traceback
import tempfile
import subprocess
import webbrowser
from pathlib import Path

# --- Tk/Tcl init guard (PyInstaller on macOS) ---
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    _base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    _tcl = os.path.join(_base, "_tcl_data")
    _tk  = os.path.join(_base, "_tk_data")
    if os.path.isdir(_tcl):
        os.environ.setdefault("TCL_LIBRARY", _tcl)
    if os.path.isdir(_tk):
        os.environ.setdefault("TK_LIBRARY", _tk)

IS_WIN = (os.name == "nt")
IS_MAC = (sys.platform == "darwin")

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkinterdnd2 import TkinterDnD, DND_FILES
import runpy

# ============================================================
#  GoPro Overlay Tool (D&D)
#   Mode A: Concat + Overlay (+ optional Timelapse)
#   Mode B: Batch Overlay (.mp4 + .360 pair -> extract GPMD -> attach -> overlay)
# ============================================================

APP_TITLE = "GoPro Overlay GUI Tool v1.1"
DEFAULT_WIDTH_2K = 1920

# --- Encode settings ---
X264_PRESET = "veryfast"
X264_CRF = "22"

# Apple macOS hardware (VideoToolbox)
VT_Q = "55"
VT_BITRATE = "7000k"

# nVIDIA NVENC
NVENC_PRESET = "p5"
NVENC_CQ = "22"

# Intel QSV
QSV_Q = "22"

# --- Overlay components（地図必須） ---
INCLUDE = [
    "date_and_time",
    "gps_info",
    "gps-lock",
    "big_mph",
    "altitude",
    "moving_map",
    "journey_map",
]

# --- GPS sanity caps ---
GPS_SPEED_MAX = "200"
GPS_SPEED_MAX_UNITS = "kph"
UNITS_SPEED = "kph"


# =============================
# Assets (EXE単体運用想定)
# =============================
def rpath(rel: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / rel

# ffmpeg / ffprobe のファイル名をOSで切替
ASSET_FFMPEG  = rpath("ffmpeg.exe"  if IS_WIN else "ffmpeg")
ASSET_FFPROBE = rpath("ffprobe.exe" if IS_WIN else "ffprobe")

# macOS: もし同梱バイナリが無ければ PATH の ffmpeg/ffprobe を使う（開発時の実行用）
if not IS_WIN:
    import shutil as _shutil
    if not ASSET_FFMPEG.exists():
        p = _shutil.which("ffmpeg")
        if p:
            ASSET_FFMPEG = Path(p)
    if not ASSET_FFPROBE.exists():
        p = _shutil.which("ffprobe")
        if p:
            ASSET_FFPROBE = Path(p)

# Font (common: Roboto)
ASSET_FONT    = rpath("third_party/Roboto/Roboto-Regular.ttf")
DASHBOARD_PY  = rpath("gopro-dashboard.py")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# =============================
# Runtime fixes for frozen EXE (maps/https/cache)
# =============================
def setup_https_and_cache_for_frozen():
    cache_dir = Path(tempfile.gettempdir()) / "gopro_tiles_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_dir))
    os.environ.setdefault("TMPDIR", str(cache_dir))
    os.environ.setdefault("TEMP", str(cache_dir))
    os.environ.setdefault("TMP", str(cache_dir))

    # HTTPS（requests/urllib3）に certifi のCAを使わせる
    try:
        import certifi
        cafile = certifi.where()
        os.environ["SSL_CERT_FILE"] = cafile
        os.environ["REQUESTS_CA_BUNDLE"] = cafile
    except Exception:
        pass

if getattr(sys, "frozen", False):
    setup_https_and_cache_for_frozen()


# =============================
# Helpers
# =============================
def ensure_naked_binaries(log=None):
    """Create extensionless ffmpeg/ffprobe next to ffmpeg.exe/ffprobe.exe for libs that call '.../ffmpeg'."""
    if os.name != "nt":
        return
    base = rpath(".")
    for exe_name, naked_name in [("ffmpeg.exe", "ffmpeg"), ("ffprobe.exe", "ffprobe")]:
        src = base / exe_name
        dst = base / naked_name
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)
            if log:
                log(f"Created naked binary: {dst.name}")



def detect_hw_encoders(ffmpeg: Path) -> tuple[bool, bool, bool]:
    """Detect availability of H.264 hardware encoders."""
    p = subprocess.run(
        [str(ffmpeg), "-hide_banner", "-encoders"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    txt = p.stdout or ""
    av_nvenc = ("h264_nvenc" in txt)
    av_qsv = ("h264_qsv" in txt)
    av_vt = ("h264_videotoolbox" in txt)
    return av_nvenc, av_qsv, av_vt


def parse_drop_files(data: str) -> list[Path]:
    paths = []
    cur = ""
    in_brace = False
    for ch in data:
        if ch == "{":
            in_brace = True
            cur = ""
        elif ch == "}":
            in_brace = False
            if cur:
                paths.append(cur)
                cur = ""
        elif ch.isspace() and not in_brace:
            if cur:
                paths.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        paths.append(cur)
    return [Path(p) for p in paths]


def uniq_preserve(seq):
    seen = set()
    out = []
    for x in seq:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def ffconcat_line(p: Path) -> str:
    """
    ffmpeg concat demuxer list line.
    Using -safe 0, absolute paths are OK.
    Use POSIX-style slashes (works well on Windows ffmpeg too).
    Escape single quotes.
    """
    s = p.resolve().as_posix()
    s = s.replace("'", r"'\''")
    return f"file '{s}'"


def build_pairs(files: set[Path]) -> list[tuple[Path, Path, str]]:
    mp4 = {}
    s360 = {}
    for f in files:
        if not f.exists() or not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext == ".mp4":
            mp4[f.stem] = f
        elif ext == ".360":
            s360[f.stem] = f
    stems = sorted(set(mp4) & set(s360))
    return [(mp4[s], s360[s], s) for s in stems]


def detect_gpmd_stream_index(ffprobe: Path, s360: Path) -> str:
    p = subprocess.run(
        [str(ffprobe), "-hide_banner", "-i", str(s360)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    txt = p.stdout or ""
    m = re.search(r"Stream #0:(\d+).*gpmd", txt, re.IGNORECASE)
    return m.group(1) if m else ""


# =============================
# Logging stream for runpy
# =============================
class _LogStream:
    def __init__(self, log):
        self._log = log
        self._buf = ""

    def write(self, s):
        if not s:
            return 0
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._log(line)
        return len(s)

    def flush(self):
        if self._buf.strip():
            self._log(self._buf.rstrip())
        self._buf = ""

    def isatty(self):
        return False


# =============================
# Command runner (progress)
# =============================
def run_cmd(cmd: list[str], log, check=True, stop_event=None, on_proc=None, progress_time_scale: float = 1.0):
    log(f"\nCMD>> {' '.join(str(x) for x in cmd)}\n")

    # total duration estimation (best effort)
    total_sec = None
    for i, part in enumerate(cmd):
        if part == "-i" and i + 1 < len(cmd):
            input_file = cmd[i + 1]
            try:
                p0 = subprocess.run(
                    [str(cmd[0]), "-hide_banner", "-i", input_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                m0 = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.\d+)", p0.stdout or "")
                if m0:
                    h0, m0_, s0 = m0.groups()
                    total_sec = int(h0) * 3600 + int(m0_) * 60 + float(s0)
            except Exception:
                total_sec = None
            break

    start_time = time.time()

    p = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if on_proc:
        on_proc(p)

    last_percent = -1

    for line in p.stdout:
        if stop_event and stop_event.is_set():
            try:
                p.terminate()
            except Exception:
                pass
            raise RuntimeError("Stopped by user")

        parts = (line or "").replace("\r", "\n").split("\n")
        for part in parts:
            part = part.strip()
            if not part:
                continue

            if "time=" in part:
                m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", part)
                if m and total_sec:
                    h, m_, s = m.groups()
                    current_sec_raw = int(h) * 3600 + int(m_) * 60 + float(s)
                    current_sec = current_sec_raw * float(progress_time_scale)
                    percent = int((current_sec / total_sec) * 100)
                    percent = max(0, min(100, percent))

                    time_str = f"{int(h):02d}:{int(m_):02d}:{float(s):05.2f}"

                    sm = re.search(r"size=\s*([0-9]+)\s*([a-zA-Z]+)", part)
                    size = "-"
                    if sm:
                        num = int(sm.group(1))
                        unit = sm.group(2)
                        if unit in ("kB", "KiB"):
                            size = str(num // 1024)
                        elif unit in ("MB", "MiB"):
                            size = str(num)
                        elif unit == "B":
                            size = str(num // (1024 * 1024))
                        else:
                            size = f"{num}{unit}"

                    speed_str = "-"
                    speed_match = re.search(r"speed=\s*([0-9\.]+)x", part)
                    if speed_match:
                        speed_val = float(speed_match.group(1))
                        speed_str = f"{speed_val:.2f}x"

                    elapsed_sec = int(time.time() - start_time)
                    eta_str = "--:--:--"
                    if current_sec > 0 and total_sec:
                        remaining = max(0, int(total_sec) - int(current_sec))
                        eta = int(elapsed_sec * (remaining / current_sec))
                        eh = eta // 3600
                        em = (eta % 3600) // 60
                        es = eta % 60
                        eta_str = f"{eh:02d}:{em:02d}:{es:02d}"

                    if percent != last_percent:
                        last_percent = percent
                        log(
                            f"{percent:3d}% | "
                            f"time={time_str} | "
                            f"size={size:>5}MB | "
                            f"speed={speed_str:>6} | "
                            f"ETA={eta_str}"
                        )
            else:
                if "Error" in part or "error" in part:
                    log(part)

    rc = p.wait()
    if on_proc:
        on_proc(None)

    if check and rc != 0:
        raise RuntimeError(f"Command failed ({rc})")
    return rc


# =============================
# Overlay (in-process, hard)
# =============================
def run_dashboard_overlay(input_mp4: Path, output_mp4: Path, log):
    ensure_naked_binaries(log)

    if not DASHBOARD_PY.exists():
        raise RuntimeError(f"Missing asset: {DASHBOARD_PY}")
    if not ASSET_FONT.exists():
        raise RuntimeError(f"Missing asset: {ASSET_FONT}")
    if not ASSET_FFMPEG.exists() or not ASSET_FFPROBE.exists():
        raise RuntimeError("Missing ffmpeg/ffprobe assets")

    mei_dir = str(rpath("."))

    argv = [
        str(DASHBOARD_PY),
        "--show-ffmpeg",
        "--ffmpeg-dir", mei_dir,
        "--include", *INCLUDE,
        "--units-speed", UNITS_SPEED,
        "--gps-speed-max", GPS_SPEED_MAX,
        "--gps-speed-max-units", GPS_SPEED_MAX_UNITS,
        "--font", str(ASSET_FONT),
        str(input_mp4),
        str(output_mp4),
    ]
    log("\nCMD>> " + " ".join(argv) + "\n")

    old_argv, old_stdout, old_stderr = sys.argv, sys.stdout, sys.stderr
    sys.argv = argv
    sys.stdout = _LogStream(log)
    sys.stderr = _LogStream(log)

    try:
        runpy.run_path(str(DASHBOARD_PY), run_name="__main__")
    except Exception:
        log("[gopro-dashboard] Exception occurred:")
        log(traceback.format_exc())
        raise
    finally:
        sys.argv, sys.stdout, sys.stderr = old_argv, old_stdout, old_stderr

    if not output_mp4.exists():
        raise RuntimeError("Overlay failed: output mp4 was not created.")


# ============================================================
# GUI
# ============================================================
class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1120x680")

        # hard assets
        self.ffmpeg = ASSET_FFMPEG
        self.ffprobe = ASSET_FFPROBE

        # Detect encoders
        self.av_nvenc, self.av_qsv, self.av_vt = detect_hw_encoders(self.ffmpeg)

        # Shared options
        default_enc = "cpu"
        if IS_MAC:
            if self.av_vt:
                default_enc = "vt"
        else:
            if self.av_qsv:
                default_enc = "qsv"
            elif self.av_nvenc:
                default_enc = "nvenc"
        self.encoder_var = tk.StringVar(value=default_enc)
        self.resolution_var = tk.StringVar(value="2k")  # default 2K

        # Mode switch (radio)  ← ここが「押したらGUIも切り替え」
        self.mode_var = tk.StringVar(value="concat")  # concat / batch

        # Concat mode states
        self.concat_files: list[Path] = []
        self.concat_files_info = tk.StringVar(value="Files: 0")

        # Concat-only: Timelapse
        self.timelapse_var = tk.StringVar(value="1")  # x1 / x5 / x10


        # Cleanup intermediate files
        self.cleanup_var = tk.BooleanVar(value=False)
        # Batch mode states
        self.batch_files: set[Path] = set()
        self.batch_info = tk.StringVar(value="Files loaded: 0   |   Pairs found: 0")
        self.pair_map: dict[str, tuple[Path, Path]] = {}

        # Output dir
        default_out = Path.cwd()
        if IS_MAC:
            desk = Path.home() / "Desktop"
            if desk.exists():
                default_out = desk
        self.out_dir = tk.StringVar(value=str(default_out))

        # Threading / stop
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.current_proc = None

        self._build_ui()

        # initial refresh
        self.refresh_concat_list()
        self.refresh_batch_pairs()

        # trace for UI switching
        self.mode_var.trace_add("write", lambda *_: self._switch_mode())

        self._switch_mode()

    # ---------- UI ----------
    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Output folder:").pack(side="left")
        ttk.Entry(top, textvariable=self.out_dir, width=76).pack(side="left", padx=6)
        ttk.Button(top, text="Browse", command=self.browse_out).pack(side="left")
        ttk.Button(top, text="About", command=self.show_about).pack(side="right")

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=10, pady=10)

        mid.columnconfigure(0, weight=1)  # ← 左
        mid.columnconfigure(1, weight=4)  # ← 右（ログを広めに）

        mid.rowconfigure(0, weight=1)

        left = ttk.Frame(mid)
        left.grid(row=0, column=0, sticky="nsew")

        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))


        # ---- LEFT: Mode + Shared Options ----
        mode_box = ttk.LabelFrame(left, text="Mode")
        mode_box.pack(anchor="w", pady=(0, 8))

        ttk.Radiobutton(
            mode_box,
            text="Merge + Overlay",
            variable=self.mode_var,
            value="concat",
        ).pack(side="left", padx=10, pady=4)

        ttk.Radiobutton(
            mode_box,
            text="Batch Overlay (.mp4 + .360)",
            variable=self.mode_var,
            value="batch",
        ).pack(side="left", padx=10, pady=4)

        opts_row = ttk.Frame(left)
        opts_row.pack(anchor="w", pady=(0, 8))

        # Encoder
        enc = ttk.LabelFrame(opts_row, text="Encoder")
        enc.grid(row=0, column=0, sticky="nw", padx=(0, 10))

        rb_cpu = ttk.Radiobutton(enc, text="Software", variable=self.encoder_var, value="cpu")
        rb_cpu.pack(anchor="w", padx=10, pady=2)

        if IS_MAC:
            rb_vt = ttk.Radiobutton(enc, text="Apple HW (VideoToolbox)", variable=self.encoder_var, value="vt")
            rb_vt.pack(anchor="w", padx=10, pady=2)
            if not self.av_vt:
                rb_vt.state(["disabled"])
        else:
            rb_qsv = ttk.Radiobutton(enc, text="Intel QSV", variable=self.encoder_var, value="qsv")
            rb_nv  = ttk.Radiobutton(enc, text="nVIDIA NVENC", variable=self.encoder_var, value="nvenc")
            rb_qsv.pack(anchor="w", padx=10, pady=2)
            rb_nv.pack(anchor="w", padx=10, pady=2)

            if not self.av_qsv:
                rb_qsv.state(["disabled"])
            if not self.av_nvenc:
                rb_nv.state(["disabled"])

        # Resolution
        res = ttk.LabelFrame(opts_row, text="Output Resolution")
        res.grid(row=0, column=1, sticky="nw", padx=(0, 10))

        ttk.Radiobutton(
            res,
            text="4K (Original)",
            variable=self.resolution_var,
            value="4k",
        ).pack(anchor="w", padx=10, pady=2)

        ttk.Radiobutton(
            res,
            text="2K (1920x1080)",
            variable=self.resolution_var,
            value="2k",
        ).pack(anchor="w", padx=10, pady=2)

        # Timelapse (Concat/Batch 共通)
        self.tl_box = ttk.LabelFrame(opts_row, text="Timelapse")
        self.tl_box.grid(row=0, column=2, sticky="nw")

        rb_tl1 = ttk.Radiobutton(self.tl_box, text="x1", variable=self.timelapse_var, value="1")
        rb_tl5 = ttk.Radiobutton(self.tl_box, text="x5",        variable=self.timelapse_var, value="5")
        rb_tl10 = ttk.Radiobutton(self.tl_box, text="x10",      variable=self.timelapse_var, value="10")
        rb_tl1.pack(anchor="w", padx=10, pady=2)
        rb_tl5.pack(anchor="w", padx=10, pady=2)
        rb_tl10.pack(anchor="w", padx=10, pady=2)

        self.tl_rbs = [rb_tl1, rb_tl5, rb_tl10]

        # ---- STACK AREA (mode-specific UI) ----
        self.stack = ttk.Frame(left)
        self.stack.pack(fill="both", expand=True)

        self.concat_frame = ttk.Frame(self.stack)
        self.batch_frame = ttk.Frame(self.stack)
        for f in (self.concat_frame, self.batch_frame):
            f.grid(row=0, column=0, sticky="nsew")
        self.stack.rowconfigure(0, weight=1)
        self.stack.columnconfigure(0, weight=1)

        self._build_concat_ui(self.concat_frame)
        self._build_batch_ui(self.batch_frame)

        # ---- RIGHT: Log ----
        ttk.Label(right, text="Log").pack(anchor="w")
        self.logbox = tk.Text(right)
        self.logbox.pack(fill="both", expand=True, pady=(0, 2))

    def _build_concat_ui(self, parent: ttk.Frame):
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(0, 6))

        ttk.Button(btns, text="Add MP4", command=self.concat_add_files_dialog).pack(side="left")
        self.concat_start_btn = ttk.Button(btns, text="Start", command=self.start)
        self.concat_start_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Clear", command=self.concat_clear_files).pack(side="left")

        ttk.Checkbutton(btns, text="Delete Temp Files", variable=self.cleanup_var).pack(side="left", padx=10)
        ttk.Label(parent, textvariable=self.concat_files_info).pack(anchor="w")

        self.concat_tree = ttk.Treeview(parent, columns=("file", "status"), show="headings", height=16)
        self.concat_tree.heading("file", text="Merge files (order)")
        self.concat_tree.heading("status", text="Status")
        self.concat_tree.column("file", width=100)
        self.concat_tree.column("status", width=50, anchor="center")
        self.concat_tree.pack(fill="both", expand=True, pady=6)

        self.concat_tree.drop_target_register(DND_FILES)
        self.concat_tree.dnd_bind("<<Drop>>", self.concat_on_drop)
        self.concat_tree.bind("<Delete>", self.concat_delete_selected)

    def _build_batch_ui(self, parent: ttk.Frame):
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(0, 6))

        ttk.Button(btns, text="Add .mp4 / .360", command=self.batch_add_files_dialog).pack(side="left")
        self.batch_start_btn = ttk.Button(btns, text="Start", command=self.start)
        self.batch_start_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Clear", command=self.batch_clear_files).pack(side="left")

        ttk.Checkbutton(btns, text="Delete Temp Files", variable=self.cleanup_var).pack(side="left", padx=10)
        ttk.Label(parent, textvariable=self.batch_info).pack(anchor="w")

        self.batch_tree = ttk.Treeview(parent, columns=("mp4", "s360", "status"), show="headings", height=18)
        self.batch_tree.heading("mp4", text="MP4")
        self.batch_tree.heading("s360", text="360")
        self.batch_tree.heading("status", text="Status")
        self.batch_tree.column("mp4", width=50)
        self.batch_tree.column("s360", width=50)
        self.batch_tree.column("status", width=30, anchor="center")
        self.batch_tree.pack(fill="both", expand=True, pady=6)

        self.batch_tree.drop_target_register(DND_FILES)
        self.batch_tree.dnd_bind("<<Drop>>", self.batch_on_drop)
        self.batch_tree.bind("<Delete>", self.batch_delete_selected_pairs)

    # ---------- Mode switch ----------
    def _switch_mode(self):
        mode = self.mode_var.get()
        if mode == "concat":
            self.concat_frame.tkraise()
            self.concat_start_btn.config(text="Start", command=self.start)
        else:
            self.batch_frame.tkraise()
            self.batch_start_btn.config(text="Start", command=self.start)

    # ---------- Common UI helpers ----------

    def _set_current_proc(self, p):
        self.current_proc = p

    def log(self, msg: str):
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.update_idletasks()

    def browse_out(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get())
        if d:
            self.out_dir.set(d)




    def show_about(self):
        # Clickable About dialog (URL opens in browser)
        win = tk.Toplevel(self)
        win.title("About")
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        frm = ttk.Frame(win, padding=12)
        frm.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frm, text="GoPro Overlay GUI Tool", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frm, text="version 1.1").grid(row=1, column=0, sticky="w")
        ttk.Label(frm, text="Copyright © 2026 by Hidenori Saka").grid(row=2, column=0, sticky="w")

        # Link-like style (blue + underline)
        try:
            s = ttk.Style(win)
            s.configure("Link.TLabel", foreground="blue")
        except Exception:
            pass

        url = "https://enpitusya.jp"
        link = ttk.Label(frm, text=url, cursor="hand2", style="Link.TLabel", font=("Segoe UI", 11, "underline"))
        link.grid(row=3, column=0, sticky="w", pady=(0, 8))

        def _open(_evt=None):
            try:
                webbrowser.open(url)
            except Exception:
                pass

        link.bind("<Button-1>", _open)

        ttk.Separator(frm).grid(row=4, column=0, sticky="ew", pady=8)

        ttk.Label(frm, text="gopro-dashboard-overlay: Copyright © by time4tea").grid(row=5, column=0, sticky="w")
        ttk.Label(frm, text=("FFmpeg: Build from evermeet.cx" if IS_MAC else "FFmpeg: Build from www.gyan.dev")).grid(row=6, column=0, sticky="w")
        ttk.Label(frm, text="ROBOTO: Copyright © 2011 The Roboto Project Authors").grid(row=7, column=0, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="OK", command=win.destroy).grid(row=0, column=0)

        win.update_idletasks()
        # Center on parent
        try:
            px = self.winfo_rootx()
            py = self.winfo_rooty()
            pw = self.winfo_width()
            ph = self.winfo_height()
            ww = win.winfo_width()
            wh = win.winfo_height()
            x = px + (pw - ww) // 2
            y = py + (ph - wh) // 2
            win.geometry(f"+{x}+{y}")
        except Exception:
            pass



    def _sanity_check_assets(self) -> bool:
        for p in [self.ffmpeg, self.ffprobe, ASSET_FONT, DASHBOARD_PY]:
            if not p.exists():
                messagebox.showerror("Missing asset", f"File not found:\n{p}")
                return False
        return True


    def _has_audio_stream(self, mp4: Path) -> bool:
        """Best-effort detection of audio stream."""
        try:
            p = subprocess.run(
                [str(self.ffprobe), "-hide_banner", "-i", str(mp4)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            txt = p.stdout or ""
            return ("Audio:" in txt)
        except Exception:
            # If probing fails, assume audio exists (safer default)
            return True

    def apply_timelapse(self, src_mp4: Path, out_dir: Path, base_stem: str):
        """Create a timelapse (speed-up) version of src_mp4 according to timelapse_var."""
        tl_mode = self.timelapse_var.get()
        if tl_mode == "1":
            self.log("■ not Timelapse (x1)")
            return None

        self.log("\n" + "=" * 90)
        self.log(f"■ Timelapse x{tl_mode}")

        tl_output = out_dir / f"{base_stem}_output_timelapse_x{tl_mode}.mp4"
        pts_factor = 1 / float(tl_mode)

        mode = self.encoder_var.get()

        # validate HW availability
        if IS_MAC:
            if mode == "vt" and not self.av_vt:
                self.log(">> VideoToolbox not available; fallback to Software")
                mode = "cpu"
        else:
            if mode == "nvenc" and not self.av_nvenc:
                self.log(">> NVENC not available; fallback to Software")
                mode = "cpu"
            if mode == "qsv" and not self.av_qsv:
                self.log(">> QSV not available; fallback to Software")
                mode = "cpu"

        def _run_vt(cmd_base):
            try:
                run_cmd(cmd_base + ["-c:v", "h264_videotoolbox", "-q:v", VT_Q, str(tl_output)],
                        self.log, stop_event=self.stop_event, on_proc=self._set_current_proc, progress_time_scale=float(tl_mode))
            except Exception:
                run_cmd(cmd_base + ["-c:v", "h264_videotoolbox", "-b:v", VT_BITRATE, str(tl_output)],
                        self.log, stop_event=self.stop_event, on_proc=self._set_current_proc, progress_time_scale=float(tl_mode))

        def _run_win_hw(cmd_base):
            if mode == "nvenc":
                run_cmd(cmd_base + ["-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-cq", NVENC_CQ, str(tl_output)],
                        self.log, stop_event=self.stop_event, on_proc=self._set_current_proc, progress_time_scale=float(tl_mode))
            elif mode == "qsv":
                run_cmd(cmd_base + ["-c:v", "h264_qsv", "-global_quality", QSV_Q, str(tl_output)],
                        self.log, stop_event=self.stop_event, on_proc=self._set_current_proc, progress_time_scale=float(tl_mode))
            else:
                run_cmd(cmd_base + ["-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF, str(tl_output)],
                        self.log, stop_event=self.stop_event, on_proc=self._set_current_proc, progress_time_scale=float(tl_mode))

        if self._has_audio_stream(src_mp4):
            # atempo: 0.5〜2.0 制限 → 分割
            atempo_filters = []
            remaining = float(tl_mode)
            while remaining > 2.0:
                atempo_filters.append("atempo=2.0")
                remaining /= 2.0
            atempo_filters.append(f"atempo={remaining}")
            atempo_chain = ",".join(atempo_filters)

            base = [
                str(self.ffmpeg), "-y",
                "-i", str(src_mp4),
                "-filter_complex",
                f"[0:v]setpts={pts_factor}*PTS[v];[0:a]{atempo_chain}[a]",
                "-map", "[v]",
                "-map", "[a]",
                "-c:a", "aac",
                "-b:a", "160k",
            ]
            if IS_MAC and mode == "vt":
                _run_vt(base)
            else:
                _run_win_hw(base)
        else:
            base = [
                str(self.ffmpeg), "-y",
                "-i", str(src_mp4),
                "-vf", f"setpts={pts_factor}*PTS",
                "-an",
            ]
            if IS_MAC and mode == "vt":
                _run_vt(base)
            else:
                _run_win_hw(base)

        self.log(f"■ Timelapse Finish: {tl_output.name}")
        return tl_output

    def _cleanup_paths(self, paths):
        #Delete intermediate files when cleanup_var is enabled.
        for p in paths:
            if not p:
                continue
            try:
                pp = Path(p)
                if pp.exists() and pp.is_file():
                    pp.unlink()
                    self.log(f"■ Deleted: {pp.name}")
            except Exception as e:
                self.log(f"■ Delete failed: {p} ({e})")


    def _set_start_button_stop(self):
        mode = self.mode_var.get()
        if mode == "concat":
            self.concat_start_btn.config(text="Stop", command=self.stop)
        else:
            self.batch_start_btn.config(text="Stop", command=self.stop)

    def _set_start_button_start(self):
        self.concat_start_btn.config(text="Start", command=self.start)
        self.batch_start_btn.config(text="Start", command=self.start)

    # ============================================================
    # Concat mode UI actions
    # ============================================================
    def concat_add_files_dialog(self):
        files = filedialog.askopenfilenames(
            title="Select MP4 files to concatenate",
            filetypes=[("MP4 files", "*.mp4"), ("All files", "*.*")]
        )
        if not files:
            return
        self.concat_files.extend(Path(f) for f in files)
        self.concat_files = uniq_preserve([p for p in self.concat_files if p.exists() and p.is_file() and p.suffix.lower() == ".mp4"])
        self.refresh_concat_list()

    def concat_on_drop(self, event):
        dropped = [p for p in parse_drop_files(event.data) if p.exists() and p.is_file()]
        dropped = [p for p in dropped if p.suffix.lower() == ".mp4"]
        self.concat_files.extend(dropped)
        self.concat_files = uniq_preserve(self.concat_files)
        self.refresh_concat_list()

    def concat_clear_files(self):
        self.concat_files = []
        self.refresh_concat_list()

    def refresh_concat_list(self):
        if not hasattr(self, "concat_tree"):
            return
        for i in self.concat_tree.get_children():
            self.concat_tree.delete(i)

        for idx, p in enumerate(self.concat_files):
            self.concat_tree.insert("", "end", iid=str(idx), values=(p.name, "Ready"))

        self.concat_files_info.set(f"Files: {len(self.concat_files)}")

    def concat_delete_selected(self, event=None):
        sel = self.concat_tree.selection()
        if not sel:
            return
        idxs = sorted((int(i) for i in sel), reverse=True)
        for i in idxs:
            if 0 <= i < len(self.concat_files):
                self.concat_files.pop(i)
        self.refresh_concat_list()

    def concat_set_row_status(self, row_i: int, status: str):
        def _update():
            iid = str(row_i)
            if self.concat_tree.exists(iid):
                self.concat_tree.set(iid, "status", status)
        self.after(0, _update)

    def concat_set_all_status(self, status: str):
        for i in range(len(self.concat_files)):
            self.concat_set_row_status(i, status)

    # ============================================================
    # Batch mode UI actions
    # ============================================================
    def batch_add_files_dialog(self):
        files = filedialog.askopenfilenames(
            title="Select .mp4 and .360 files",
            filetypes=[("GoPro files", "*.mp4 *.360"), ("All files", "*.*")]
        )
        for f in files:
            self.batch_files.add(Path(f))
        self.refresh_batch_pairs()

    def batch_on_drop(self, event):
        for p in parse_drop_files(event.data):
            self.batch_files.add(p)
        self.refresh_batch_pairs()

    def batch_clear_files(self):
        self.batch_files.clear()
        self.refresh_batch_pairs()

    def refresh_batch_pairs(self):
        if not hasattr(self, "batch_tree"):
            return
        for i in self.batch_tree.get_children():
            self.batch_tree.delete(i)

        self.pair_map = {}
        pairs = build_pairs(self.batch_files)
        for mp4, s360, stem in pairs:
            self.pair_map[stem] = (mp4, s360)
            self.batch_tree.insert("", "end", iid=stem, values=(mp4.name, s360.name, "Ready"))

        self.batch_info.set(f"Files loaded: {len(self.batch_files)}   |   Pairs found: {len(pairs)}")

    def batch_delete_selected_pairs(self, event=None):
        sel = self.batch_tree.selection()
        if not sel:
            return
        for stem in sel:
            mp4_s360 = self.pair_map.get(stem)
            if not mp4_s360:
                continue
            mp4, s360 = mp4_s360
            self.batch_files.discard(mp4)
            self.batch_files.discard(s360)
        self.refresh_batch_pairs()

    def batch_set_pair_status(self, stem: str, status: str):
        def _update():
            if self.batch_tree.exists(stem):
                mp4, s360 = self.pair_map.get(stem, (None, None))
                if mp4 and s360:
                    self.batch_tree.item(stem, values=(mp4.name, s360.name, status))
        self.after(0, _update)

    # ============================================================
    # Start/Stop
    # ============================================================
    def start(self):
        mode = self.mode_var.get()
        out_dir = Path(self.out_dir.get()).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        if not self._sanity_check_assets():
            return

        self.log(f"Mode: {mode}")
        self.log(f"Output folder: {out_dir}")
        self.log(f"ffmpeg:  {self.ffmpeg}")
        self.log(f"ffprobe: {self.ffprobe}")
        self.log(f"font:    {ASSET_FONT}")
        self.log("Starting...")

        self.stop_event.clear()
        self._set_start_button_stop()

        if mode == "concat":
            files_snapshot = list(self.concat_files)
            if len(files_snapshot) < 1:
                messagebox.showwarning("No files", "結合するMP4をD&Dまたは Add MP4... で追加してください。")
                self._set_start_button_start()
                return
            self.worker_thread = threading.Thread(
                target=self.worker_concat,
                args=(files_snapshot, out_dir),
                daemon=True
            )
        else:
            pairs = build_pairs(self.batch_files)
            if not pairs:
                messagebox.showwarning("No pairs", "同名の .mp4 と .360 のペアがありません。")
                self._set_start_button_start()
                return
            self.worker_thread = threading.Thread(
                target=self.worker_batch,
                args=(pairs, out_dir),
                daemon=True
            )

        self.worker_thread.start()

    def stop(self):
        self.log("\n>> Stop requested")
        self.stop_event.set()
        if self.current_proc and self.current_proc.poll() is None:
            try:
                self.current_proc.terminate()
            except Exception:
                pass

    # ============================================================
    # Concat mode worker
    # ============================================================
    def worker_concat(self, files: list[Path], out_dir: Path):
        try:
            self.concat_set_all_status("Processing")
            self.process_concat(files, out_dir)
            self.concat_set_all_status("Finish")
            self.log("\nALL DONE")
        except Exception as e:
            self.concat_set_all_status("Fail")
            self.log(f"\nFATAL ERROR: {e}")
        finally:
            self._set_start_button_start()
            self.current_proc = None
            self.stop_event.clear()

    def process_concat(self, files: list[Path], out_dir: Path):
        self.log("\n" + "=" * 90)
        self.log("Concat list:")
        for i, f in enumerate(files, 1):
            self.log(f"  {i:02d}. {f}")
        self.log("=" * 90)

        base_stem = files[0].stem if files else "merge"
        files_txt = out_dir / f"{base_stem}_files.txt"
        merged_mp4 = out_dir / f"{base_stem}_merge.mp4"
        proxy_mp4  = out_dir / f"{base_stem}_1080p.mp4"
        out_mp4    = out_dir / f"{base_stem}_output.mp4"

        # 1) concat
        self.log("■ CONCAT")
        txt = "\n".join(ffconcat_line(p) for p in files) + "\n"
        files_txt.write_text(txt, encoding="utf-8")

        run_cmd([
            str(self.ffmpeg), "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(files_txt),
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-map", "0:d:0?",
            "-c", "copy",
            str(merged_mp4)
        ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)

        # 2) transcode if 2K
        mode_res = self.resolution_var.get()
        if mode_res == "4k":
            self.log("\n" + "=" * 90)
            self.log("■ 4K Mode（without Encode）")
            proxy_in = merged_mp4
        else:
            self.log("\n" + "=" * 90)
            self.log("■ 2K Mode (with Encode)")
            vf = f"format=yuv420p,scale={DEFAULT_WIDTH_2K}:-2"

            mode = self.encoder_var.get()

            # validate HW availability
            if IS_MAC:
                if mode == "vt" and not self.av_vt:
                    self.log(">> VideoToolbox not available; fallback to Software")
                    mode = "cpu"
            else:
                if mode == "nvenc" and not self.av_nvenc:
                    self.log(">> NVENC not available; fallback to Software")
                    mode = "cpu"
                if mode == "qsv" and not self.av_qsv:
                    self.log(">> QSV not available; fallback to Software")
                    mode = "cpu"

            if IS_MAC and mode == "vt":
                base = [
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a:0?", "-map", "0:d:0?",
                    "-vf", vf,
                    "-c:a", "copy",
                    "-c:d", "copy",
                ]
                try:
                    run_cmd(base + ["-c:v", "h264_videotoolbox", "-q:v", VT_Q, str(proxy_mp4)],
                            self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)
                except Exception:
                    run_cmd(base + ["-c:v", "h264_videotoolbox", "-b:v", VT_BITRATE, str(proxy_mp4)],
                            self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)

            elif mode == "nvenc":
                run_cmd([
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a:0?", "-map", "0:d:0?",
                    "-vf", vf,
                    "-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-cq", NVENC_CQ,
                    "-c:a", "copy",
                    "-c:d", "copy",
                    str(proxy_mp4)
                ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)
            elif mode == "qsv":
                run_cmd([
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a:0?", "-map", "0:d:0?",
                    "-vf", vf,
                    "-c:v", "h264_qsv",
                    "-global_quality", QSV_Q,
                    "-c:a", "copy",
                    "-c:d", "copy",
                    str(proxy_mp4)
                ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)
            else:
                run_cmd([
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a:0?", "-map", "0:d:0?",
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF,
                    "-c:a", "copy",
                    "-c:d", "copy",
                    str(proxy_mp4)
                ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)

            proxy_in = proxy_mp4

        # 3) overlay
        self.log("\n" + "=" * 90)
        self.log("■ Overlay")
        run_dashboard_overlay(proxy_in, out_mp4, self.log)
        self.log(f"■ Overlay Finish: {out_mp4.name}")

        # 4) timelapse (optional)
        self.apply_timelapse(out_mp4, out_dir, base_stem)


        # 5) cleanup (optional)
        if self.cleanup_var.get():
            self.log("■ Cleanup intermediate files")
            self._cleanup_paths([
                files_txt,
                merged_mp4,
                proxy_mp4 if proxy_mp4.exists() else None,
            ])

    # ============================================================
    # Batch mode worker
    # ============================================================
    def worker_batch(self, pairs: list[tuple[Path, Path, str]], out_dir: Path):
        try:
            for mp4, s360, stem in pairs:
                if self.stop_event.is_set():
                    raise RuntimeError("Stopped by user")

                self.batch_set_pair_status(stem, "Processing")
                try:
                    ok = self.process_one_pair(mp4, s360, stem, out_dir)
                    self.batch_set_pair_status(stem, "Finish" if ok else "Skip")
                except Exception:
                    self.batch_set_pair_status(stem, "Fail")
                    raise

            self.log("\nALL DONE")
        except Exception as e:
            self.log(f"\nFATAL ERROR: {e}")
        finally:
            self._set_start_button_start()
            self.current_proc = None
            self.stop_event.clear()

    def process_one_pair(self, mp4: Path, s360: Path, stem: str, out_dir: Path) -> bool:
        self.log("\n" + "=" * 90)
        self.log(f"Processing: {stem}")
        self.log(f"MP4 : {mp4}")
        self.log(f"360 : {s360}")
        self.log("=" * 90)

        gpmd_mp4   = out_dir / f"{stem}_gpmd.mp4"
        merged_mp4 = out_dir / f"{stem}_merge.mp4"
        proxy_mp4  = out_dir / f"{stem}_1080p.mp4"
        out_mp4    = out_dir / f"{stem}_output.mp4"

        idx = detect_gpmd_stream_index(self.ffprobe, s360)
        if not idx:
            self.log(f"❌ No GPMD stream found in {s360.name} (skipping)")
            return False

        # 1) Extract GPMD from .360
        self.log("■ Extraction GPMD from .360")
        run_cmd(
            [str(self.ffmpeg), "-y", "-i", str(s360), "-map", f"0:{idx}", "-c", "copy", str(gpmd_mp4)],
            self.log,
            stop_event=self.stop_event,
            on_proc=self._set_current_proc
        )

        # 2) Attach GPMD to MP4
        self.log("=" * 90)
        self.log("■ Concat MP4 and GPMD")
        run_cmd([
            str(self.ffmpeg), "-y",
            "-i", str(mp4),
            "-i", str(gpmd_mp4),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-map", "1:d:0",
            "-c", "copy",
            str(merged_mp4)
        ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)

        # 3) transcode if 2K
        mode_res = self.resolution_var.get()
        if mode_res == "4k":
            self.log("\n" + "=" * 90)
            self.log("■ 4K Mode（without Encode）")
            proxy_in = merged_mp4
        else:
            self.log("\n" + "=" * 90)
            self.log("■ 2K Mode (with Encode)")
            vf = f"format=yuv420p,scale={DEFAULT_WIDTH_2K}:-2"

            mode = self.encoder_var.get()

            # validate HW availability
            if IS_MAC:
                if mode == "vt" and not self.av_vt:
                    self.log(">> VideoToolbox not available; fallback to Software")
                    mode = "cpu"
            else:
                if mode == "nvenc" and not self.av_nvenc:
                    self.log(">> NVENC not available; fallback to Software")
                    mode = "cpu"
                if mode == "qsv" and not self.av_qsv:
                    self.log(">> QSV not available; fallback to Software")
                    mode = "cpu"

            if IS_MAC and mode == "vt":
                base = [
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a?", "-map", "0:d:0",
                    "-vf", vf,
                    "-c:a", "copy",
                    "-c:d", "copy",
                ]
                try:
                    run_cmd(base + ["-c:v", "h264_videotoolbox", "-q:v", VT_Q, str(proxy_mp4)],
                            self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)
                except Exception:
                    run_cmd(base + ["-c:v", "h264_videotoolbox", "-b:v", VT_BITRATE, str(proxy_mp4)],
                            self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)

            elif mode == "nvenc":
                run_cmd([
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a?", "-map", "0:d:0",
                    "-vf", vf,
                    "-c:v", "h264_nvenc", "-preset", NVENC_PRESET, "-cq", NVENC_CQ,
                    "-c:a", "copy",
                    "-c:d", "copy",
                    str(proxy_mp4)
                ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)
            elif mode == "qsv":
                run_cmd([
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a?", "-map", "0:d:0",
                    "-vf", vf,
                    "-c:v", "h264_qsv",
                    "-global_quality", QSV_Q,
                    "-c:a", "copy",
                    "-c:d", "copy",
                    str(proxy_mp4)
                ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)
            else:
                run_cmd([
                    str(self.ffmpeg), "-y", "-i", str(merged_mp4),
                    "-map", "0:v:0", "-map", "0:a?", "-map", "0:d:0",
                    "-vf", vf,
                    "-c:v", "libx264", "-preset", X264_PRESET, "-crf", X264_CRF,
                    "-c:a", "copy",
                    "-c:d", "copy",
                    str(proxy_mp4)
                ], self.log, stop_event=self.stop_event, on_proc=self._set_current_proc)

            proxy_in = proxy_mp4

        # 4) Overlay
        self.log("\n" + "=" * 90)
        self.log("■ Overlay")
        run_dashboard_overlay(proxy_in, out_mp4, self.log)

        self.log(f"■ Overlay Finish: {out_mp4.name}")

        # 5) timelapse (optional)
        self.apply_timelapse(out_mp4, out_dir, stem)

        # 6) cleanup (optional)
        if self.cleanup_var.get():
            self.log("■ Cleanup intermediate files")
            self._cleanup_paths([
                gpmd_mp4,
                merged_mp4,
                proxy_mp4 if proxy_mp4.exists() else None,
            ])

        return True


if __name__ == "__main__":
    App().mainloop()
