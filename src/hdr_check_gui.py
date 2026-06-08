#!/usr/bin/env python3
"""
hdr_check_gui.py — GUI front-end for the HDR metadata + YouTube-HDR diagnostics.

Dependencies:
    pip install customtkinter tkinterdnd2 pyinstaller

Build:
    pyinstaller --onefile --windowed --name "HDRAnalyser" hdr_check_gui.py
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

# Suppress console windows when spawning subprocesses on Windows
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

import customtkinter as ctk

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except ImportError:
    _HAS_DND = False


# ── mastering display presets ─────────────────────────────────────────────────
# primaries use HEVC SEI units (chromaticity * 50000)
# luminance uses HEVC SEI units (nit * 10000)

PRESETS = {
    "P3-D65  |  1000 nit  (most common)": {
        "primaries": "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)",
        "peak_nit": "1000", "min_nit": "0.005",
        "max_cll": "1000", "max_fall": "400",
    },
    "P3-D65  |  4000 nit": {
        "primaries": "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)",
        "peak_nit": "4000", "min_nit": "0.005",
        "max_cll": "4000", "max_fall": "400",
    },
    "P3-D65  |  10000 nit": {
        "primaries": "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)",
        "peak_nit": "10000", "min_nit": "0.001",
        "max_cll": "10000", "max_fall": "400",
    },
    "BT.2020  |  1000 nit": {
        "primaries": "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)",
        "peak_nit": "1000", "min_nit": "0.005",
        "max_cll": "1000", "max_fall": "400",
    },
    "BT.2020  |  4000 nit": {
        "primaries": "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)",
        "peak_nit": "4000", "min_nit": "0.005",
        "max_cll": "4000", "max_fall": "400",
    },
    "BT.2020  |  10000 nit": {
        "primaries": "G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)",
        "peak_nit": "10000", "min_nit": "0.001",
        "max_cll": "10000", "max_fall": "400",
    },
    "Custom": {
        "primaries": "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)",
        "peak_nit": "1000", "min_nit": "0.005",
        "max_cll": "1000", "max_fall": "400",
    },
}
_PRESET_NAMES = list(PRESETS.keys())


# ── ffprobe / analysis logic ──────────────────────────────────────────────────

def _bundled_dir():
    """Return the folder containing this exe when running as a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return None


def _resource_path(relative_path):
    """Resolve asset paths for both dev mode and PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def resolve_ffprobe(candidate="ffprobe"):
    # Bundled copy next to the exe takes priority
    bd = _bundled_dir()
    if bd:
        sibling = os.path.join(bd, "ffprobe.exe")
        if os.path.isfile(sibling):
            return sibling
    if candidate and os.path.isfile(candidate):
        return candidate
    return shutil.which(candidate or "ffprobe")


def resolve_ffmpeg(ffprobe_path="ffprobe"):
    """Locate ffmpeg: bundled copy first, then PATH, then same dir as ffprobe."""
    bd = _bundled_dir()
    if bd:
        sibling = os.path.join(bd, "ffmpeg.exe")
        if os.path.isfile(sibling):
            return sibling
    found = shutil.which("ffmpeg")
    if found:
        return found
    if ffprobe_path and os.path.isfile(ffprobe_path):
        sibling = os.path.join(os.path.dirname(ffprobe_path), "ffmpeg.exe")
        if os.path.isfile(sibling):
            return sibling
    return None


def run_ffprobe(path, ffprobe_exe="ffprobe"):
    exe = resolve_ffprobe(ffprobe_exe)
    if exe is None:
        raise FileNotFoundError(
            f"Could not find ffprobe (tried: {ffprobe_exe!r}).\n"
            "Install ffmpeg or browse to ffprobe.exe."
        )
    base = [exe, "-v", "quiet", "-print_format", "json"]
    try:
        meta = json.loads(subprocess.check_output(
            base + ["-show_format", "-show_streams", path],
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        ))
    except subprocess.CalledProcessError:
        raise RuntimeError(f"ffprobe could not read the file:\n{path}")
    frames = {"frames": []}
    try:
        frames = json.loads(subprocess.check_output(
            base + ["-show_frames", "-read_intervals", "%+#5",
                    "-select_streams", "v:0", path],
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        ))
    except subprocess.CalledProcessError:
        pass
    return meta, frames


def _video_stream(meta):
    for s in meta.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _collect_side_data(frames):
    found = {}
    for fr in frames.get("frames", []):
        for sd in fr.get("side_data_list", []):
            t = sd.get("side_data_type", "")
            found.setdefault(t, sd)
    return found


def _bit_depth(vs):
    bpr = vs.get("bits_per_raw_sample")
    if bpr and str(bpr).isdigit():
        return int(bpr)
    pix = (vs.get("pix_fmt") or "").lower()
    for n in (16, 14, 12, 10):
        if f"p{n}" in pix or f"{n}le" in pix or f"{n}be" in pix:
            return n
    return 8 if pix else None


def build_report(meta, frames):
    vs   = _video_stream(meta)
    side = _collect_side_data(frames)
    fmt  = meta.get("format", {})

    file_rows = [
        ("Container", fmt.get("format_long_name", fmt.get("format_name", "?"))),
        ("Duration",  f"{fmt.get('duration', '?')} s"),
        ("Size",      f"{fmt.get('size', '?')} bytes"),
        ("Bitrate",   f"{fmt.get('bit_rate', '?')} bps"),
    ]

    stream_rows = []
    if vs:
        stream_rows = [
            ("Codec",           vs.get("codec_long_name", vs.get("codec_name"))),
            ("Profile",         vs.get("profile")),
            ("Resolution",      f"{vs.get('width')}x{vs.get('height')}"),
            ("Pixel format",    vs.get("pix_fmt")),
            ("Bit depth",       _bit_depth(vs)),
            ("Color range",     vs.get("color_range")),
            ("Color primaries", vs.get("color_primaries")),
            ("Transfer (TRC)",  vs.get("color_transfer")),
            ("Matrix",          vs.get("color_space")),
            ("Frame rate",      vs.get("r_frame_rate")),
        ]

    # Always surface MDCV and CLL explicitly, even when absent
    mdcv_sd  = side.get("Mastering display metadata", {})
    cll_sd   = side.get("Content light level metadata", {})
    has_mdcv = bool(mdcv_sd)
    has_cll  = bool(cll_sd)

    # Build a clean human-readable summary for each
    def _mdcv_summary():
        if not mdcv_sd:
            return None
        parts = []
        for k, v in mdcv_sd.items():
            if k != "side_data_type":
                parts.append(f"{k}: {v}")
        return "  |  ".join(parts) if parts else "(present but empty)"

    def _cll_summary():
        if not cll_sd:
            return None
        max_cll  = cll_sd.get("max_content", cll_sd.get("MaxCLL"))
        max_fall = cll_sd.get("max_average", cll_sd.get("MaxFALL"))
        if max_cll is not None or max_fall is not None:
            return f"MaxCLL: {max_cll}  |  MaxFALL: {max_fall}"
        parts = [f"{k}: {v}" for k, v in cll_sd.items() if k != "side_data_type"]
        return "  |  ".join(parts) if parts else "(present but empty)"

    hdr_meta_rows = [
        ("Mastering display",       _mdcv_summary()),
        ("Content light level",     _cll_summary()),
    ]

    # Other side data (Dolby Vision, etc.) minus the two above
    other_side_rows = []
    for t, sd in side.items():
        if t in ("Mastering display metadata", "Content light level metadata"):
            continue
        for k, v in sd.items():
            if k != "side_data_type":
                other_side_rows.append((f"{t} › {k}", v))

    diag_rows   = []
    verdict_pass = False

    if vs:
        depth = _bit_depth(vs)
        prim  = (vs.get("color_primaries") or "").lower()
        trc   = (vs.get("color_transfer")  or "").lower()
        mtx   = (vs.get("color_space")     or "").lower()
        codec = (vs.get("codec_name")      or "").lower()

        is_pq   = trc in ("smpte2084", "smpte-st-2084")
        is_hlg  = trc in ("arib-std-b67", "arib-std-b-67")
        hdr_trc = is_pq or is_hlg

        if depth and depth >= 10:
            diag_rows.append(("PASS", f"Bit depth is {depth}-bit (10-bit minimum met)."))
        else:
            diag_rows.append(("FAIL",
                f"Bit depth is {depth or 'unknown'}-bit. YouTube treats 8-bit as SDR "
                "— re-export at 10-bit (e.g. yuv420p10le)."))

        if is_pq:
            diag_rows.append(("PASS", "Transfer function is PQ (SMPTE ST 2084)."))
        elif is_hlg:
            diag_rows.append(("PASS", "Transfer function is HLG (ARIB STD-B67)."))
        else:
            diag_rows.append(("FAIL",
                f"Transfer function is '{trc or 'unset'}', not a supported HDR curve. "
                "YouTube only detects HDR with PQ (smpte2084) or HLG (arib-std-b67). "
                "This is the single most common cause of HDR not being recognized."))

        if prim == "bt2020":
            diag_rows.append(("PASS", "Color primaries are BT.2020."))
        else:
            diag_rows.append(("FAIL",
                f"Color primaries are '{prim or 'unset'}', expected bt2020."))

        if mtx in ("bt2020nc", "bt2020_ncl"):
            diag_rows.append(("PASS", "Matrix is BT.2020 non-constant luminance."))
        elif mtx in ("bt2020c", "bt2020_cl"):
            diag_rows.append(("WARN",
                "Matrix is BT.2020 *constant* luminance; YouTube expects non-constant (bt2020nc)."))
        else:
            diag_rows.append(("WARN",
                f"Matrix is '{mtx or 'unset'}', expected bt2020nc."))

        has_dovi = any("DOVI" in k or "Dolby" in k for k in side)
        if is_pq:
            diag_rows.append(("PASS" if has_mdcv else "WARN",
                "SMPTE ST 2086 mastering display metadata present." if has_mdcv else
                "PQ signaled but no SMPTE ST 2086 mastering display metadata found. "
                "YouTube usually still accepts PQ, but proper HDR10 wants it (and MaxCLL/MaxFALL)."))
            diag_rows.append(("PASS" if has_cll else "WARN",
                "Content light level (MaxCLL/MaxFALL) present." if has_cll else
                "No MaxCLL/MaxFALL content light level metadata found."))
        if has_dovi:
            diag_rows.append(("WARN",
                "Dolby Vision configuration detected. YouTube relies on the HDR10 base layer "
                "— make sure PQ + BT.2020 + 10-bit are all correctly tagged independently of the DV layer."))

        good_codecs = {"hevc", "vp9", "av1", "prores"}
        if codec in good_codecs:
            diag_rows.append(("PASS", f"Codec '{codec}' can carry HDR metadata."))
        elif codec == "h264":
            diag_rows.append(("WARN",
                "Codec is H.264. 8-bit H.264 cannot carry HDR metadata and will be treated as SDR. "
                "Prefer HEVC, VP9 Profile 2, or AV1."))
        else:
            diag_rows.append(("WARN",
                f"Codec '{codec or 'unknown'}' — verify it can carry HDR metadata "
                "(HEVC / VP9 Profile 2 / AV1 recommended)."))

        if prim == "bt2020" and not hdr_trc:
            diag_rows.append(("FAIL",
                "BT.2020 primaries are tagged but the transfer curve is NOT PQ/HLG. "
                "In this exact case YouTube re-tags the video to BT.709 8-bit SDR to avoid banding "
                "— so it will never show the HDR badge. Fix the transfer function tag."))

        fmt_name = (fmt.get("format_name") or "").lower()
        if "mp4" in fmt_name or "mov" in fmt_name:
            diag_rows.append(("PASS",
                "Container is MP4/MOV — compatible with YouTube, TikTok, and Instagram."))
        elif "matroska" in fmt_name or "webm" in fmt_name:
            diag_rows.append(("WARN",
                "Container is Matroska (MKV) or WebM. YouTube handles these natively, "
                "but TikTok and Instagram expect MP4. Remux to MP4 for broad platform compatibility."))
        elif "avi" in fmt_name:
            diag_rows.append(("WARN",
                "Container is AVI. AVI has limited support for modern HDR color metadata "
                "and is not accepted by TikTok or Instagram. Remux to MP4."))
        elif "mpegts" in fmt_name:
            diag_rows.append(("WARN",
                "Container is MPEG-TS. Accepted by YouTube for broadcast workflows "
                "but not by TikTok or Instagram. Remux to MP4 for social media delivery."))
        elif "hevc" in fmt_name:
            diag_rows.append(("WARN",
                "Raw HEVC bitstream with no container. "
                "Wrap in MP4 or MKV before uploading to any platform."))
        elif "mxf" in fmt_name:
            diag_rows.append(("WARN",
                "Container is MXF (professional broadcast format). "
                "Not accepted by TikTok or Instagram. Remux to MP4 for social media delivery."))
        elif fmt_name:
            diag_rows.append(("WARN",
                f"Container format '{fmt.get('format_name', fmt_name)}' may not be accepted by all platforms. "
                "MP4 is the safest choice for broad compatibility."))

        hard_fail = (not (depth and depth >= 10)) or (not hdr_trc) or (prim != "bt2020")
        verdict_pass = not hard_fail

    codec_name = (vs.get("codec_name") or "") if vs else ""

    return {
        "file":           file_rows,
        "stream":         stream_rows,
        "hdr_meta_rows":  hdr_meta_rows,
        "other_side":     other_side_rows,
        "diag":           diag_rows,
        "verdict_pass":   verdict_pass,
        "has_video":      vs is not None,
        "tags":           vs.get("tags", {}) if vs else {},
        "has_mdcv":       has_mdcv,
        "has_cll":        has_cll,
        "codec":          codec_name,
        "pix_fmt":        vs.get("pix_fmt", "yuv420p10le") if vs else "yuv420p10le",
    }


def report_as_text(meta, frames):
    r = build_report(meta, frames)
    lines = ["=== FILE ==="]
    for k, v in r["file"]:
        lines.append(f"  {k:<16}: {v if v not in (None, '') else '(unset)'}")

    lines.append("\n=== VIDEO STREAM ===")
    if r["has_video"]:
        for k, v in r["stream"]:
            lines.append(f"  {k:<16}: {v if v not in (None, '') else '(unset)'}")
        if r["tags"]:
            lines.append("\n=== STREAM TAGS ===")
            for k, v in r["tags"].items():
                lines.append(f"  {k}: {v}")
    else:
        lines.append("  No video stream found.")

    lines.append("\n=== HDR SIDE-DATA (first frames) ===")
    for k, v in r["hdr_meta_rows"]:
        lines.append(f"  {k:<24}: {v if v else '(not present)'}")
    for k, v in r["other_side"]:
        lines.append(f"  {k}: {v}")

    lines.append("\n=== HDR COMPATIBILITY DIAGNOSIS ===")
    for status, msg in r["diag"]:
        lines.append(f"  [{status}] {msg}")

    if r["has_video"]:
        lines.append("")
        if r["verdict_pass"]:
            lines.append("  VERDICT: Core HDR signaling looks correct.")
            lines.append("  If the platform still shows SDR, check any [WARN] items and confirm "
                         "the metadata survived your final export/transcode step.")
        else:
            lines.append("  VERDICT: This file will most likely NOT be detected as HDR.")
            lines.append("  Address every [FAIL] above (transfer function and 10-bit are the "
                         "usual culprits) and re-upload.")

    return "\n".join(lines)


# ── Fix Metadata dialog ───────────────────────────────────────────────────────

class FixMetadataDialog(ctk.CTkToplevel):
    """Modal dialog to inject MDCV / CLL metadata via x265 re-encode."""

    _SPEED_PRESETS = ["ultrafast", "veryfast", "faster", "fast", "medium", "slow", "slower"]

    def __init__(self, parent, file_path, codec, has_mdcv, has_cll,
                 ffprobe_path, on_complete, pix_fmt="yuv420p10le", duration=0.0):
        super().__init__(parent)
        self.title("Fix Missing Metadata")
        self.geometry("640x560")
        self.resizable(True, True)
        self.minsize(580, 480)
        self.lift()
        self.focus_force()

        self._file_path    = file_path
        self._codec        = codec.lower()
        self._has_mdcv     = has_mdcv
        self._has_cll      = has_cll
        self._ffprobe_path = ffprobe_path
        self._ffmpeg_path  = resolve_ffmpeg(ffprobe_path)
        self._on_complete  = on_complete
        self._pix_fmt      = pix_fmt or "yuv420p10le"
        self._duration     = float(duration) if duration else 0.0

        self._last_out_path = None
        self._encoding      = False
        self._proc          = None
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_dialog_close)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=0, pady=0)
        px = 16

        def sep():
            ctk.CTkFrame(scroll, height=1,
                         fg_color=("gray75", "gray35")).pack(fill="x", padx=px, pady=(8, 8))

        # ── header / missing items ──
        ctk.CTkLabel(scroll, text="Inject Missing HDR Metadata",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     anchor="w").pack(fill="x", padx=px, pady=(14, 4))

        missing = []
        if not self._has_mdcv:
            missing.append("Mastering Display (MDCV / SMPTE ST 2086)")
        if not self._has_cll:
            missing.append("Content Light Level (MaxCLL / MaxFALL)")
        if missing:
            body = "Missing from this file:\n" + "\n".join(f"  • {m}" for m in missing)
        else:
            body = "Both MDCV and CLL are already present — you can still overwrite them."
        ctk.CTkLabel(scroll, text=body, font=ctk.CTkFont(size=12),
                     text_color=("gray30", "gray70"),
                     justify="left", anchor="w").pack(fill="x", padx=px, pady=(0, 6))

        sep()

        # ── re-encode quality settings ──
        self._quality_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._quality_frame.pack(fill="x", padx=0)

        ctk.CTkLabel(self._quality_frame, text="Re-encode Quality",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=px)

        ctk.CTkLabel(self._quality_frame,
                     text="CRF 18 = visually lossless. Lower = better quality / larger file. "
                          "Higher = smaller file / slight quality reduction.",
                     font=ctk.CTkFont(size=10), text_color=("gray45", "gray60"),
                     wraplength=560, justify="left", anchor="w").pack(fill="x", padx=px, pady=(2, 4))

        qrow1 = ctk.CTkFrame(self._quality_frame, fg_color="transparent")
        qrow1.pack(fill="x", padx=px, pady=2)
        ctk.CTkLabel(qrow1, text="CRF (quality)", width=158, anchor="w",
                     font=ctk.CTkFont(size=12),
                     text_color=("gray40", "gray60")).pack(side="left")
        self._crf_var = tk.StringVar(value="18")
        ctk.CTkEntry(qrow1, textvariable=self._crf_var, width=80,
                     font=ctk.CTkFont(size=12)).pack(side="left", padx=4)

        qrow2 = ctk.CTkFrame(self._quality_frame, fg_color="transparent")
        qrow2.pack(fill="x", padx=px, pady=2)
        ctk.CTkLabel(qrow2, text="Encoding speed", width=158, anchor="w",
                     font=ctk.CTkFont(size=12),
                     text_color=("gray40", "gray60")).pack(side="left")
        self._speed_var = tk.StringVar(value="medium")
        ctk.CTkOptionMenu(qrow2, variable=self._speed_var,
                          values=self._SPEED_PRESETS, width=160,
                          font=ctk.CTkFont(size=12)).pack(side="left", padx=4)

        sep()

        # ── metadata preset ──
        ctk.CTkLabel(scroll, text="Metadata Preset", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=px)

        self._preset_var = tk.StringVar(value=_PRESET_NAMES[0])
        self._preset_menu = ctk.CTkOptionMenu(
            scroll, variable=self._preset_var,
            values=_PRESET_NAMES, width=420,
            command=self._on_preset_change,
        )
        self._preset_menu.pack(anchor="w", padx=px, pady=(4, 8))

        # ── custom fields (mastering display + CLL) — shown only when Custom selected ──
        self._custom_fields_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        # Not packed initially; shown via _on_preset_change when Custom is selected

        def cf_sep():
            ctk.CTkFrame(self._custom_fields_frame, height=1,
                         fg_color=("gray75", "gray35")).pack(fill="x", padx=px, pady=(8, 8))

        cf_sep()

        ctk.CTkLabel(self._custom_fields_frame, text="Mastering Display",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=px)

        self._peak_var = tk.StringVar(value=PRESETS[_PRESET_NAMES[0]]["peak_nit"])
        self._min_var  = tk.StringVar(value=PRESETS[_PRESET_NAMES[0]]["min_nit"])

        self._applying_preset = False
        self._peak_entry = self._add_field(self._custom_fields_frame, "Peak Luminance (nit)", self._peak_var, px)
        self._add_field(self._custom_fields_frame, "Min Luminance (nit)", self._min_var, px)

        # ── display primaries grid ──
        prim_outer = ctk.CTkFrame(self._custom_fields_frame, fg_color="transparent")
        prim_outer.pack(fill="x", padx=px, pady=(8, 4))

        ctk.CTkLabel(prim_outer, text="Display Primaries",
                     font=ctk.CTkFont(size=12, weight="bold"), anchor="w").pack(fill="x")
        ctk.CTkLabel(prim_outer, text="Chromaticity coordinates  (0 – 1 range)",
                     font=ctk.CTkFont(size=10), text_color=("gray45", "gray60"),
                     anchor="w").pack(fill="x")

        hdr_row = ctk.CTkFrame(prim_outer, fg_color="transparent")
        hdr_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(hdr_row, text="", width=90).pack(side="left")
        ctk.CTkLabel(hdr_row, text="x", width=105, anchor="center",
                     font=ctk.CTkFont(size=11), text_color=("gray40", "gray60")).pack(side="left")
        ctk.CTkLabel(hdr_row, text="y", width=105, anchor="center",
                     font=ctk.CTkFont(size=11), text_color=("gray40", "gray60")).pack(side="left")

        pv0 = self._parse_primaries(PRESETS[_PRESET_NAMES[0]]["primaries"]) or [0.0]*8
        self._prim_gx  = tk.StringVar(value=f"{pv0[0]:.4f}")
        self._prim_gy  = tk.StringVar(value=f"{pv0[1]:.4f}")
        self._prim_bx  = tk.StringVar(value=f"{pv0[2]:.4f}")
        self._prim_by  = tk.StringVar(value=f"{pv0[3]:.4f}")
        self._prim_rx  = tk.StringVar(value=f"{pv0[4]:.4f}")
        self._prim_ry  = tk.StringVar(value=f"{pv0[5]:.4f}")
        self._prim_wpx = tk.StringVar(value=f"{pv0[6]:.4f}")
        self._prim_wpy = tk.StringVar(value=f"{pv0[7]:.4f}")

        for _label, _xv, _yv in [
            ("Green",    self._prim_gx,  self._prim_gy),
            ("Blue",     self._prim_bx,  self._prim_by),
            ("Red",      self._prim_rx,  self._prim_ry),
            ("White Pt", self._prim_wpx, self._prim_wpy),
        ]:
            _row = ctk.CTkFrame(prim_outer, fg_color="transparent")
            _row.pack(fill="x", pady=2)
            ctk.CTkLabel(_row, text=_label, width=90, anchor="w",
                         font=ctk.CTkFont(size=12),
                         text_color=("gray40", "gray60")).pack(side="left")
            ctk.CTkEntry(_row, textvariable=_xv, width=100,
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
            ctk.CTkEntry(_row, textvariable=_yv, width=100,
                         font=ctk.CTkFont(size=12)).pack(side="left")

        cf_sep()

        ctk.CTkLabel(self._custom_fields_frame, text="Content Light Level",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=px)

        self._cll_var  = tk.StringVar(value=PRESETS[_PRESET_NAMES[0]]["max_cll"])
        self._fall_var = tk.StringVar(value=PRESETS[_PRESET_NAMES[0]]["max_fall"])

        self._add_field(self._custom_fields_frame, "MaxCLL  (nit)",  self._cll_var,  px)
        self._add_field(self._custom_fields_frame, "MaxFALL (nit)", self._fall_var, px)

        for _v in (self._peak_var, self._min_var, self._cll_var, self._fall_var,
                   self._prim_gx, self._prim_gy, self._prim_bx, self._prim_by,
                   self._prim_rx, self._prim_ry, self._prim_wpx, self._prim_wpy):
            _v.trace_add("write", self._on_field_edited)

        sep()

        # ── ffmpeg path ──
        ctk.CTkLabel(scroll, text="ffmpeg executable", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=px)
        self._ffmpeg_var = tk.StringVar(
            value=self._ffmpeg_path if self._ffmpeg_path else "ffmpeg not found — browse below"
        )
        ff_row = ctk.CTkFrame(scroll, fg_color="transparent")
        ff_row.pack(fill="x", padx=px, pady=(4, 0))
        ctk.CTkEntry(ff_row, textvariable=self._ffmpeg_var,
                     font=ctk.CTkFont(size=11)).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(ff_row, text="Browse…", width=90,
                      command=self._browse_ffmpeg).pack(side="right")

        # ── status ──
        self._dialog_status = tk.StringVar(value="")
        ctk.CTkLabel(scroll, textvariable=self._dialog_status,
                     font=ctk.CTkFont(size=11), text_color=("gray40", "gray60"),
                     anchor="w", wraplength=580).pack(fill="x", padx=px, pady=(8, 0))

        sep()

        # ── action buttons ──
        btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_row.pack(fill="x", padx=px, pady=(0, 16))
        ctk.CTkButton(btn_row, text="Save as New File", width=160,
                      command=lambda: self._run(overwrite=False)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Overwrite Original", width=160,
                      fg_color=("#c62828", "#b71c1c"), hover_color=("#b71c1c", "#8b0000"),
                      command=lambda: self._run(overwrite=True)).pack(side="left")
        self._open_folder_btn = ctk.CTkButton(btn_row, text="Open Folder", width=130,
                                              fg_color=("gray65", "gray35"),
                                              hover_color=("gray55", "gray45"),
                                              command=self._open_folder)
        # Revealed in _on_worker_done after successful encode

    def _add_field(self, parent, label, var, px):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=px, pady=2)
        ctk.CTkLabel(row, text=label, width=180, anchor="w",
                     font=ctk.CTkFont(size=12),
                     text_color=("gray40", "gray60")).pack(side="left")
        entry = ctk.CTkEntry(row, textvariable=var, width=140,
                             font=ctk.CTkFont(size=12))
        entry.pack(side="left", padx=4)
        return entry

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_preset_change(self, choice):
        if choice == "Custom":
            self._custom_fields_frame.pack(fill="x", padx=0,
                                           after=self._preset_menu)
            self.geometry("640x860")
            self.minsize(580, 660)
            self._peak_entry.focus_set()
            return
        self._custom_fields_frame.pack_forget()
        self.geometry("640x560")
        self.minsize(580, 480)
        self._applying_preset = True
        p = PRESETS[choice]
        self._peak_var.set(p["peak_nit"])
        self._min_var.set(p["min_nit"])
        pv = self._parse_primaries(p["primaries"]) or [0.0] * 8
        self._prim_gx.set(f"{pv[0]:.4f}");  self._prim_gy.set(f"{pv[1]:.4f}")
        self._prim_bx.set(f"{pv[2]:.4f}");  self._prim_by.set(f"{pv[3]:.4f}")
        self._prim_rx.set(f"{pv[4]:.4f}");  self._prim_ry.set(f"{pv[5]:.4f}")
        self._prim_wpx.set(f"{pv[6]:.4f}"); self._prim_wpy.set(f"{pv[7]:.4f}")
        self._cll_var.set(p["max_cll"])
        self._fall_var.set(p["max_fall"])
        self._applying_preset = False

    def _on_field_edited(self, *_):
        if not self._applying_preset:
            self._preset_var.set("Custom")

    def _browse_ffmpeg(self):
        path = filedialog.askopenfilename(
            title="Locate ffmpeg.exe",
            filetypes=[("Executable", "*.exe ffmpeg"), ("All files", "*.*")],
        )
        if path:
            self._ffmpeg_path = path
            self._ffmpeg_var.set(path)

    # ── run ───────────────────────────────────────────────────────────────────

    def _validate(self):
        try:
            peak = float(self._peak_var.get())
            mn   = float(self._min_var.get())
            cll  = float(self._cll_var.get())
            fall = float(self._fall_var.get())
        except ValueError:
            messagebox.showerror("Invalid input",
                                 "Peak luminance, min luminance, MaxCLL, and MaxFALL must all be numbers.",
                                 parent=self)
            return None

        if peak <= 0 or mn < 0 or cll < 0 or fall < 0:
            messagebox.showerror("Invalid input", "Luminance values must be positive.",
                                 parent=self)
            return None

        try:
            prim_vals = [float(v.get()) for v in (
                self._prim_gx, self._prim_gy, self._prim_bx, self._prim_by,
                self._prim_rx, self._prim_ry, self._prim_wpx, self._prim_wpy,
            )]
        except ValueError:
            messagebox.showerror("Invalid input", "All display primaries must be numbers.",
                                 parent=self)
            return None

        if any(not (0.0 <= v <= 1.0) for v in prim_vals):
            messagebox.showerror("Invalid input",
                                 "Display primaries must be between 0 and 1.",
                                 parent=self)
            return None

        prim = self._build_primaries_str(prim_vals)

        peak_u = int(round(peak * 10000))
        min_u  = int(round(mn   * 10000))
        cll_i  = int(round(cll))
        fall_i = int(round(fall))

        try:
            crf = int(self._crf_var.get())
            if not (0 <= crf <= 51):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid input", "CRF must be an integer between 0 and 51.",
                                 parent=self)
            return None

        return {
            "master_display": f"{prim}L({peak_u},{min_u})",
            "max_cll":        f"{cll_i},{fall_i}",
            "crf":            crf,
            "speed":          self._speed_var.get(),
        }

    def _run(self, overwrite):
        params = self._validate()
        if params is None:
            return

        ffmpeg_exe = self._ffmpeg_var.get().strip()
        if not ffmpeg_exe or "not found" in ffmpeg_exe:
            messagebox.showerror("ffmpeg not found",
                                 "Please browse to ffmpeg.exe before applying the fix.",
                                 parent=self)
            return

        if overwrite:
            if not messagebox.askyesno(
                "Overwrite original?",
                f"This will permanently overwrite:\n{self._file_path}\n\nContinue?",
                parent=self,
            ):
                return
            out_path = self._file_path
        else:
            ext     = os.path.splitext(self._file_path)[1]
            stem    = os.path.splitext(os.path.basename(self._file_path))[0]
            default = stem + "_hdr10" + ext
            out_path = filedialog.asksaveasfilename(
                title="Save fixed file",
                defaultextension=ext,
                initialfile=default,
                filetypes=[("Video files", f"*{ext}"), ("All files", "*.*")],
                parent=self,
            )
            if not out_path:
                return

        self._dialog_status.set("Re-encoding with ffmpeg…  (this may take a while)")
        self.update()
        threading.Thread(
            target=self._worker,
            args=(ffmpeg_exe, params, out_path, overwrite),
            daemon=True,
        ).start()

    @staticmethod
    def _parse_primaries(s):
        """Parse 'G(x,y)B(x,y)R(x,y)WP(x,y)' → list of 8 decimal chromaticities, or None."""
        try:
            parts = []
            for token in ("G(", "B(", "R(", "WP("):
                i = s.index(token)
                j = s.index(")", i)
                parts.extend(s[i + len(token):j].split(","))
            return [int(v) / 50000 for v in parts]
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _build_primaries_str(vals):
        """Convert list of 8 decimal chromaticities → 'G(x,y)B(x,y)R(x,y)WP(x,y)'."""
        u = lambda v: int(round(float(v) * 50000))
        gx, gy, bx, by, rx, ry, wpx, wpy = vals
        return f"G({u(gx)},{u(gy)})B({u(bx)},{u(by)})R({u(rx)},{u(ry)})WP({u(wpx)},{u(wpy)})"

    def _force_stop(self):
        if self._proc is not None:
            try:
                self._proc.kill()
            except Exception:
                pass

    def _on_dialog_close(self):
        self._force_stop()
        self.destroy()

    @staticmethod
    def _parse_out_time(val):
        """Parse ffmpeg out_time=HH:MM:SS.ffffff → seconds, or None on failure."""
        try:
            parts = val.strip().split(":")
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (ValueError, IndexError):
            pass
        return None

    def _worker(self, ffmpeg_exe, params, out_path, overwrite):
        ext      = os.path.splitext(out_path)[1]
        tmp_path = out_path + f".tmp{ext}" if overwrite else None
        dest     = tmp_path if overwrite else out_path
        self._worker_reencode(ffmpeg_exe, params, out_path, overwrite, dest, tmp_path)

    def _worker_reencode(self, ffmpeg_exe, params, out_path, overwrite, dest, tmp_path):
        self._encoding = True
        cmd = self._build_reencode_cmd(ffmpeg_exe, params, dest)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_NO_WINDOW,
            )
        except FileNotFoundError:
            self.after(0, lambda: self._on_worker_error(f"ffmpeg not found at: {ffmpeg_exe}"))
            return
        self._proc = proc

        # Drain stderr on a side thread so its buffer never fills and deadlocks
        stderr_buf = []
        def _drain_stderr():
            for ln in proc.stderr:
                stderr_buf.append(ln)
        threading.Thread(target=_drain_stderr, daemon=True).start()

        # Read progress from stdout (-progress pipe:1)
        for line in proc.stdout:
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if key == "out_time" and self._duration > 0:
                current_s = self._parse_out_time(val)
                if current_s is not None:
                    pct = min(99, int(current_s / self._duration * 100))
                    msg = f"Re-encoding… {pct}%"
                    self.after(0, lambda m=msg: self._dialog_status.set(m))

        proc.wait()
        if proc.returncode != 0:
            err = "".join(stderr_buf[-80:]) or "(no stderr)"
            self.after(0, lambda: self._on_worker_error(
                f"ffmpeg exited with code {proc.returncode}:\n\n{err}"))
            return

        self._finish(out_path, overwrite, tmp_path)

    def _finish(self, out_path, overwrite, tmp_path):
        if overwrite:
            try:
                os.replace(tmp_path, out_path)
            except OSError as e:
                self.after(0, lambda: self._on_worker_error(str(e)))
                return
        self.after(0, lambda: self._on_worker_done(out_path))

    def _build_reencode_cmd(self, ffmpeg_exe, params, dest):
        x265_params = (
            f"master-display={params['master_display']}"
            f":max-cll={params['max_cll']}"
            f":hdr10=1"
            f":colorprim=bt2020"
            f":transfer=smpte2084"
            f":colormatrix=bt2020nc"
            f":repeat-headers=1"
        )
        return [
            ffmpeg_exe, "-y",
            "-i", self._file_path,
            "-c:v", "libx265",
            "-crf", str(params["crf"]),
            "-preset", params["speed"],
            "-profile:v", "main10",
            "-pix_fmt", self._pix_fmt,
            "-x265-params", x265_params,
            "-color_primaries", "bt2020",
            "-color_trc", "smpte2084",
            "-colorspace", "bt2020nc",
            "-c:a", "copy",
            "-async", "1",
            "-progress", "pipe:1",
            dest,
        ]

    def _on_worker_done(self, out_path):
        self._encoding = False
        self._last_out_path = out_path
        self._dialog_status.set(f"Done: {os.path.basename(out_path)}")
        self._open_folder_btn.pack(side="left", padx=(8, 0))
        messagebox.showinfo("Done", f"Metadata injected successfully.\n\n{out_path}", parent=self)
        if self._on_complete:
            self._on_complete(out_path)

    def _open_folder(self):
        if self._last_out_path:
            os.startfile(os.path.dirname(os.path.abspath(self._last_out_path)))

    def _on_worker_error(self, msg):
        self._encoding = False
        self._dialog_status.set("Error — see dialog")
        messagebox.showerror("ffmpeg error", msg, parent=self)


# ── GUI ───────────────────────────────────────────────────────────────────────

if _HAS_DND:
    class _AppBase(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    _AppBase = ctk.CTk


class HDRApp(_AppBase):
    def __init__(self):
        super().__init__()

        self.title("NHNB HDR Analyser and Metadata Fixer")
        try:
            self.iconbitmap(_resource_path("icon.ico"))
        except Exception:
            pass
        self.geometry("960x760")
        self.minsize(720, 540)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self._mode         = "dark"
        self._ffprobe_path = resolve_ffprobe() or os.environ.get("FFPROBE", "ffprobe")
        self._current_file = None
        self._meta         = None
        self._frames       = None
        self._last_report  = None
        self._fix_dialog   = None

        self._build_ui()
        if _HAS_DND:
            self._setup_dnd()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        top = ctk.CTkFrame(self, height=50, corner_radius=0)
        top.pack(fill="x")
        top.pack_propagate(False)

        ctk.CTkLabel(top, text="NHNB HDR Analyser and Metadata Fixer",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(side="left", padx=16)
        self._theme_btn = ctk.CTkButton(top, text="Light Mode", width=120, height=32,
                                         command=self._toggle_theme)
        self._theme_btn.pack(side="right", padx=14, pady=9)

        self._drop_outer = ctk.CTkFrame(self, height=90, corner_radius=8)
        self._drop_outer.pack(fill="x", padx=12, pady=(10, 2))
        self._drop_outer.pack_propagate(False)

        self._drop_label = ctk.CTkLabel(
            self._drop_outer,
            text="Drag & drop a video file here, or use Browse",
            font=ctk.CTkFont(size=13),
            text_color=("gray50", "gray55"),
        )
        self._drop_label.pack(side="left", padx=20, expand=True)

        btn_col = ctk.CTkFrame(self._drop_outer, fg_color="transparent")
        btn_col.pack(side="right", padx=14)
        ctk.CTkButton(btn_col, text="Browse…", width=110, command=self._browse).pack(pady=(12, 4))
        self._analyse_btn = ctk.CTkButton(btn_col, text="Analyse", width=110, state="disabled",
                                           fg_color="#2e7d32", hover_color="#1b5e20",
                                           command=self._run_analysis)
        self._analyse_btn.pack(pady=(4, 12))

        self._path_var = tk.StringVar(value="No file selected")
        ctk.CTkLabel(self, textvariable=self._path_var,
                     font=ctk.CTkFont(size=11),
                     text_color=("gray45", "gray60"), anchor="w").pack(fill="x", padx=16, pady=(0, 4))

        self._tabs = ctk.CTkTabview(self)
        self._tabs.pack(fill="both", expand=True, padx=12, pady=(4, 4))

        report_tab = self._tabs.add("Report")
        json_tab   = self._tabs.add("Raw JSON")

        self._report_scroll = ctk.CTkScrollableFrame(report_tab)
        self._report_scroll.pack(fill="both", expand=True)
        ctk.CTkLabel(self._report_scroll,
                     text="Analyse a file to see results here.",
                     font=ctk.CTkFont(size=13),
                     text_color=("gray50", "gray55")).pack(pady=50)

        self._json_box = ctk.CTkTextbox(json_tab, font=ctk.CTkFont(family="Courier New", size=11))
        self._json_box.pack(fill="both", expand=True)
        self._json_box.insert("end", "No data yet.")
        self._json_box.configure(state="disabled")

        bottom = ctk.CTkFrame(self, height=44, corner_radius=0)
        bottom.pack(fill="x", padx=12, pady=(0, 8))
        bottom.pack_propagate(False)

        self._status_var = tk.StringVar(value="")
        ctk.CTkLabel(bottom, textvariable=self._status_var,
                     font=ctk.CTkFont(size=11), anchor="w").pack(side="left", padx=8)

        ctk.CTkButton(bottom, text="Save Report…", width=120,
                      command=self._save_report).pack(side="right", padx=4, pady=6)
        ctk.CTkButton(bottom, text="Copy to Clipboard", width=148,
                      command=self._copy_clipboard).pack(side="right", padx=4, pady=6)

    def _setup_dnd(self):
        for widget in (self, self._drop_outer, self._drop_label):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_drop(self, event):
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        self._set_file(raw)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select video file",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.mov *.avi *.ts *.mts *.m2ts *.webm *.hevc *.mxf"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._set_file(path)

    def _set_file(self, path):
        self._current_file = path
        self._drop_label.configure(text=os.path.basename(path))
        self._path_var.set(path)
        self._analyse_btn.configure(state="normal")

    def _toggle_theme(self):
        if self._mode == "dark":
            self._mode = "light"
            ctk.set_appearance_mode("light")
            self._theme_btn.configure(text="Dark Mode")
        else:
            self._mode = "dark"
            ctk.set_appearance_mode("dark")
            self._theme_btn.configure(text="Light Mode")

    # ── analysis ──────────────────────────────────────────────────────────────

    def _run_analysis(self):
        if not self._current_file:
            return
        self._analyse_btn.configure(state="disabled", text="Analysing…")
        self._status_var.set("Running ffprobe…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            meta, frames = run_ffprobe(self._current_file, self._ffprobe_path)
            self.after(0, lambda: self._render(meta, frames))
        except FileNotFoundError as exc:
            msg = str(exc)
            self.after(0, lambda: self._handle_missing_ffprobe(msg))
        except Exception as exc:
            msg = str(exc)
            self.after(0, lambda: self._show_error(msg))

    def _handle_missing_ffprobe(self, msg):
        self._analyse_btn.configure(state="normal", text="Analyse")
        self._status_var.set("ffprobe not found")
        if messagebox.askyesno("ffprobe not found", f"{msg}\n\nBrowse to ffprobe.exe now?"):
            path = filedialog.askopenfilename(
                title="Locate ffprobe.exe",
                filetypes=[("Executable", "*.exe ffprobe"), ("All files", "*.*")],
            )
            if path:
                self._ffprobe_path = path
                self._run_analysis()

    def _show_error(self, msg):
        self._analyse_btn.configure(state="normal", text="Analyse")
        self._status_var.set("Error")
        messagebox.showerror("Error", msg)

    # ── rendering ─────────────────────────────────────────────────────────────

    def _render(self, meta, frames):
        self._meta         = meta
        self._frames       = frames
        self._last_report  = build_report(meta, frames)
        report = self._last_report

        self._analyse_btn.configure(state="normal", text="Analyse")

        for w in self._report_scroll.winfo_children():
            w.destroy()

        px = 12

        def section_header(title):
            ctk.CTkLabel(self._report_scroll, text=title,
                         font=ctk.CTkFont(size=13, weight="bold"),
                         anchor="w").pack(fill="x", padx=px, pady=(16, 2))
            ctk.CTkFrame(self._report_scroll, height=1,
                         fg_color=("gray75", "gray35")).pack(fill="x", padx=px, pady=(0, 6))

        def kv_row(key, val, absent=False):
            row = ctk.CTkFrame(self._report_scroll, fg_color="transparent")
            row.pack(fill="x", padx=px + 4, pady=1)
            ctk.CTkLabel(row, text=f"{key}:", width=175, anchor="w",
                         font=ctk.CTkFont(size=12),
                         text_color=("gray40", "gray60")).pack(side="left")
            if absent or val in (None, ""):
                display = "(not present)" if absent else "(unset)"
                color   = ("gray45", "gray60")
            else:
                display = str(val)
                color   = ("gray10", "gray90")
            ctk.CTkLabel(row, text=display, anchor="w",
                         font=ctk.CTkFont(size=12), text_color=color,
                         wraplength=650).pack(side="left", padx=4)

        _badge_bg = {
            "PASS": ("#2e7d32", "#388e3c"),
            "WARN": ("#bf6900", "#e07b00"),
            "FAIL": ("#b71c1c", "#c62828"),
        }

        def diag_row(status, msg):
            bg  = _badge_bg.get(status, ("#555", "#888"))
            row = ctk.CTkFrame(self._report_scroll, fg_color="transparent")
            row.pack(fill="x", padx=px + 4, pady=3)
            ctk.CTkLabel(row, text=f" {status} ", width=52,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         fg_color=bg, corner_radius=4, text_color="white").pack(side="left", padx=(0, 10))
            ctk.CTkLabel(row, text=msg, anchor="w", justify="left",
                         font=ctk.CTkFont(size=12), wraplength=710).pack(side="left", fill="x", expand=True)

        # FILE
        section_header("FILE")
        for k, v in report["file"]:
            kv_row(k, v)

        # VIDEO STREAM
        section_header("VIDEO STREAM")
        if report["has_video"]:
            for k, v in report["stream"]:
                kv_row(k, v)
            if report["tags"]:
                section_header("STREAM TAGS")
                for k, v in report["tags"].items():
                    kv_row(k, v)
        else:
            ctk.CTkLabel(self._report_scroll, text="No video stream found.",
                         text_color=("#c62828", "#ef5350"),
                         anchor="w", font=ctk.CTkFont(size=12)).pack(fill="x", padx=px + 4)

        # HDR SIDE-DATA — always show MDCV and CLL explicitly
        section_header("HDR SIDE-DATA (first frames)")
        for k, v in report["hdr_meta_rows"]:
            kv_row(k, v, absent=(v is None))
        for k, v in report["other_side"]:
            kv_row(k, v)
        if not report["hdr_meta_rows"] and not report["other_side"]:
            ctk.CTkLabel(self._report_scroll, text="None found.",
                         text_color=("gray45", "gray60"),
                         anchor="w", font=ctk.CTkFont(size=12)).pack(fill="x", padx=px + 4)

        # DIAGNOSIS
        section_header("HDR COMPATIBILITY DIAGNOSIS")
        if report["has_video"]:
            for status, msg in report["diag"]:
                diag_row(status, msg)

            vpass         = report["verdict_pass"]
            v_fg          = ("#e8f5e9", "#1b5e20") if vpass else ("#ffebee", "#4a0000")
            v_bdr         = ("#388e3c", "#2e7d32") if vpass else ("#c62828", "#b71c1c")
            v_title       = ("VERDICT: Core HDR signaling looks correct."
                             if vpass else
                             "VERDICT: This file will most likely NOT be detected as HDR.")
            v_sub         = ("If the platform still shows SDR, check any WARN items and confirm "
                             "the metadata survived your final export/transcode step."
                             if vpass else
                             "Address every FAIL above (transfer function and 10-bit are the "
                             "usual culprits) and re-upload.")
            v_title_color = ("#1b5e20", "#4caf50") if vpass else ("#b71c1c", "#ef5350")

            card = ctk.CTkFrame(self._report_scroll, fg_color=v_fg,
                                border_color=v_bdr, border_width=1, corner_radius=8)
            card.pack(fill="x", padx=px, pady=(14, 6))
            ctk.CTkLabel(card, text=v_title,
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=v_title_color, anchor="w").pack(padx=14, pady=(10, 2), anchor="w")
            ctk.CTkLabel(card, text=v_sub,
                         font=ctk.CTkFont(size=11),
                         text_color=("gray30", "gray55"),
                         wraplength=840, justify="left", anchor="w").pack(padx=14, pady=(0, 10), anchor="w")

            # Fix Metadata button — shown when MDCV or CLL is missing
            if not report["has_mdcv"] or not report["has_cll"]:
                fix_row = ctk.CTkFrame(self._report_scroll, fg_color="transparent")
                fix_row.pack(fill="x", padx=px, pady=(4, 12))
                ctk.CTkLabel(fix_row,
                             text="Missing MDCV / MaxCLL metadata can be injected via x265 re-encode:",
                             font=ctk.CTkFont(size=12),
                             text_color=("gray35", "gray65"), anchor="w").pack(side="left")
                ctk.CTkButton(fix_row, text="Fix Metadata…", width=140,
                              command=self._open_fix_dialog).pack(side="right")
        else:
            ctk.CTkLabel(self._report_scroll, text="No video stream to analyse.",
                         text_color=("#c62828", "#ef5350"),
                         anchor="w", font=ctk.CTkFont(size=12)).pack(fill="x", padx=px + 4)

        # JSON tab
        raw = json.dumps({"meta": meta, "frames": frames}, indent=2)
        self._json_box.configure(state="normal")
        self._json_box.delete("0.0", "end")
        self._json_box.insert("end", raw)
        self._json_box.configure(state="disabled")

        verdict_word = "PASS" if report["verdict_pass"] else "FAIL"
        self._status_var.set(
            f"Done — {os.path.basename(self._current_file)} — Verdict: {verdict_word}"
        )

    # ── fix metadata ──────────────────────────────────────────────────────────

    def _open_fix_dialog(self):
        if not self._last_report:
            return
        if self._fix_dialog is not None and self._fix_dialog.winfo_exists():
            self._fix_dialog.lift()
            self._fix_dialog.focus_force()
            return
        r = self._last_report
        try:
            dur = float(self._meta.get("format", {}).get("duration", 0))
        except (TypeError, ValueError):
            dur = 0.0
        self._fix_dialog = FixMetadataDialog(
            parent=self,
            file_path=self._current_file,
            codec=r["codec"],
            has_mdcv=r["has_mdcv"],
            has_cll=r["has_cll"],
            ffprobe_path=self._ffprobe_path,
            on_complete=self._after_fix,
            pix_fmt=r.get("pix_fmt", "yuv420p10le"),
            duration=dur,
        )
        # Re-raise after CTkToplevel's deferred init finishes
        self.after(150, self._raise_fix_dialog)

    def _raise_fix_dialog(self):
        dlg = self._fix_dialog
        if dlg is not None and dlg.winfo_exists():
            dlg.lift()
            dlg.focus_force()

    def _after_fix(self, fixed_path):
        """Re-analyse the fixed file automatically."""
        self._set_file(fixed_path)
        self._run_analysis()

    def _on_close(self):
        dlg = self._fix_dialog
        if dlg is not None and dlg.winfo_exists() and getattr(dlg, "_encoding", False):
            self.lift()
            self.focus_force()
            if not messagebox.askyesno(
                "Re-encode in progress",
                "A re-encode is currently running.\n\n"
                "Closing will kill the encode — the partial output file will be left on disk.\n\n"
                "Close anyway?",
                parent=self,
            ):
                return
        self.destroy()

    def destroy(self):
        dlg = getattr(self, "_fix_dialog", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg._force_stop()
                    dlg.destroy()
            except Exception:
                pass
        super().destroy()

    # ── export ────────────────────────────────────────────────────────────────

    def _copy_clipboard(self):
        if self._meta is None:
            return
        self.clipboard_clear()
        self.clipboard_append(report_as_text(self._meta, self._frames))
        self._status_var.set("Report copied to clipboard.")

    def _save_report(self):
        if self._meta is None:
            return
        stem    = os.path.splitext(os.path.basename(self._current_file or "report"))[0]
        default = stem + "_hdr_report.txt"
        path    = filedialog.asksaveasfilename(
            title="Save report",
            defaultextension=".txt",
            initialfile=default,
            filetypes=[("Text file", "*.txt"), ("All files", "*.*")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report_as_text(self._meta, self._frames))
            self._status_var.set(f"Saved to {os.path.basename(path)}")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    app = HDRApp()
    app.mainloop()


if __name__ == "__main__":
    main()
