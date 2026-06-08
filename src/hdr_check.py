#!/usr/bin/env python3
"""
hdr_check.py — Video metadata + YouTube-HDR diagnostics.

Reads a video file with ffprobe, prints all relevant metadata, and then
diagnoses why YouTube might NOT be tagging the upload as HDR.

Requires: ffmpeg/ffprobe installed and on PATH (https://ffmpeg.org/download.html).
No third-party Python packages needed (standard library only).

Usage:
    python hdr_check.py "/path/to/video.mp4"
    python hdr_check.py "/path/to/video.mp4" --json   # raw ffprobe JSON only

If ffprobe is not on your PATH, point at the executable directly:
    python hdr_check.py "C:\\videos\\clip.mp4" --ffprobe "C:\\ffmpeg\\bin\\ffprobe.exe"
Or set an environment variable once (used as a fallback):
    set FFPROBE=C:\\ffmpeg\\bin\\ffprobe.exe       (Windows cmd)
    $env:FFPROBE="C:\\ffmpeg\\bin\\ffprobe.exe"    (PowerShell)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap

# ---- terminal colors (degrade gracefully if not a tty) ----------------------
_USE_COLOR = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s
def bold(s):  return _c("1", s)
def green(s): return _c("32", s)
def yellow(s):return _c("33", s)
def red(s):   return _c("31", s)
def dim(s):   return _c("2", s)

PASS = green("PASS")
WARN = yellow("WARN")
FAIL = red("FAIL")


def resolve_ffprobe(candidate):
    """Turn a name or path into a usable ffprobe command, or None.

    Accepts a full path to ffprobe(.exe) or a bare name to look up on PATH.
    """
    if candidate:
        # An explicit path (e.g. C:\ffmpeg\bin\ffprobe.exe) — use it as-is.
        if os.path.isfile(candidate):
            return candidate
        # A bare name or something on PATH.
        found = shutil.which(candidate)
        if found:
            return found
        return None
    return shutil.which("ffprobe")


def run_ffprobe(path, ffprobe_exe="ffprobe"):
    """Return (format+streams dict, first-frames dict). Exits on hard failure."""
    exe = resolve_ffprobe(ffprobe_exe)
    if exe is None:
        sys.exit(red(
            f"Could not find ffprobe (looked for: {ffprobe_exe!r}).\n"
            "Pass the full path with --ffprobe \"C:\\path\\to\\ffprobe.exe\", "
            "set the FFPROBE environment variable, or add it to PATH.\n"
            "Download: https://ffmpeg.org/download.html"))

    base = [exe, "-v", "quiet", "-print_format", "json"]

    try:
        meta = json.loads(subprocess.check_output(
            base + ["-show_format", "-show_streams", path]))
    except subprocess.CalledProcessError:
        sys.exit(red(f"ffprobe could not read the file: {path}"))
    except FileNotFoundError:
        sys.exit(red(f"File not found: {path}"))

    # Read the first few frames to surface HDR side-data (mastering display,
    # content light level, Dolby Vision RPU) which is per-frame, not per-stream.
    frames = {"frames": []}
    try:
        frames = json.loads(subprocess.check_output(
            base + ["-show_frames", "-read_intervals", "%+#5",
                    "-select_streams", "v:0", path]))
    except subprocess.CalledProcessError:
        pass  # not fatal; some files won't expose this

    return meta, frames


def video_stream(meta):
    for s in meta.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def collect_side_data(frames):
    found = {}
    for fr in frames.get("frames", []):
        for sd in fr.get("side_data_list", []):
            t = sd.get("side_data_type", "")
            found.setdefault(t, sd)
    return found


def bit_depth(vs):
    """Best-effort bit depth from bits_per_raw_sample or pixel format name."""
    bpr = vs.get("bits_per_raw_sample")
    if bpr and str(bpr).isdigit():
        return int(bpr)
    pix = (vs.get("pix_fmt") or "").lower()
    for n in (16, 14, 12, 10):
        if f"p{n}" in pix or f"{n}le" in pix or f"{n}be" in pix:
            return n
    return 8 if pix else None


# ----------------------------------------------------------------------------
def print_metadata(meta, vs, side):
    fmt = meta.get("format", {})
    print(bold("\n=== FILE ==="))
    print(f"  Container : {fmt.get('format_long_name', fmt.get('format_name','?'))}")
    print(f"  Duration  : {fmt.get('duration','?')} s")
    print(f"  Size      : {fmt.get('size','?')} bytes")
    print(f"  Bitrate   : {fmt.get('bit_rate','?')} bps")

    if not vs:
        print(red("\nNo video stream found."))
        return

    print(bold("\n=== VIDEO STREAM ==="))
    rows = [
        ("Codec",          vs.get("codec_long_name", vs.get("codec_name"))),
        ("Profile",        vs.get("profile")),
        ("Resolution",     f"{vs.get('width')}x{vs.get('height')}"),
        ("Pixel format",   vs.get("pix_fmt")),
        ("Bit depth",      bit_depth(vs)),
        ("Color range",    vs.get("color_range")),
        ("Color primaries",vs.get("color_primaries")),
        ("Transfer (TRC)", vs.get("color_transfer")),
        ("Matrix",         vs.get("color_space")),
        ("Frame rate",     vs.get("r_frame_rate")),
    ]
    for k, v in rows:
        print(f"  {k:<16}: {v if v not in (None,'') else dim('(unset)')}")

    print(bold("\n=== HDR SIDE-DATA (first frames) ==="))
    if not side:
        print(dim("  none found"))
    for t, sd in side.items():
        print(f"  {t}:")
        for k, v in sd.items():
            if k == "side_data_type":
                continue
            print(f"      {k}: {v}")

    if vs.get("tags"):
        print(bold("\n=== STREAM TAGS ==="))
        for k, v in vs["tags"].items():
            print(f"  {k}: {v}")


# ----------------------------------------------------------------------------
def diagnose(vs, side):
    """Apply YouTube's HDR detection rules and report status + fixes."""
    print(bold("\n=== YOUTUBE HDR DIAGNOSIS ==="))
    if not vs:
        print(red("No video stream to analyze."))
        return

    results = []  # (status, message)

    depth = bit_depth(vs)
    prim  = (vs.get("color_primaries") or "").lower()
    trc   = (vs.get("color_transfer") or "").lower()
    mtx   = (vs.get("color_space") or "").lower()
    codec = (vs.get("codec_name") or "").lower()

    is_pq  = trc in ("smpte2084", "smpte-st-2084")
    is_hlg = trc in ("arib-std-b67", "arib-std-b-67")
    hdr_trc = is_pq or is_hlg

    # 1) bit depth
    if depth and depth >= 10:
        results.append((PASS, f"Bit depth is {depth}-bit (10-bit minimum met)."))
    else:
        results.append((FAIL, f"Bit depth is {depth or 'unknown'}-bit. YouTube treats "
                        "8-bit as SDR — re-export at 10-bit (e.g. yuv420p10le)."))

    # 2) transfer characteristics — the field that most often breaks HDR
    if is_pq:
        results.append((PASS, "Transfer function is PQ (SMPTE ST 2084)."))
    elif is_hlg:
        results.append((PASS, "Transfer function is HLG (ARIB STD-B67)."))
    else:
        results.append((FAIL, f"Transfer function is '{trc or 'unset'}', not a supported "
                        "HDR curve. YouTube only detects HDR with PQ (smpte2084) or "
                        "HLG (arib-std-b67). This is the single most common cause of "
                        "HDR not being recognized."))

    # 3) color primaries
    if prim == "bt2020":
        results.append((PASS, "Color primaries are BT.2020."))
    else:
        results.append((FAIL, f"Color primaries are '{prim or 'unset'}', expected bt2020."))

    # 4) matrix coefficients
    if mtx in ("bt2020nc", "bt2020_ncl"):
        results.append((PASS, "Matrix is BT.2020 non-constant luminance."))
    elif mtx in ("bt2020c", "bt2020_cl"):
        results.append((WARN, "Matrix is BT.2020 *constant* luminance; YouTube expects "
                        "non-constant (bt2020nc)."))
    else:
        results.append((WARN, f"Matrix is '{mtx or 'unset'}', expected bt2020nc."))

    # 5) mastering display + content light level (required for PQ/HDR10)
    has_mdcv = "Mastering display metadata" in side
    has_cll  = "Content light level metadata" in side
    has_dovi = any("DOVI" in k or "Dolby" in k for k in side)

    if is_pq:
        if has_mdcv:
            results.append((PASS, "SMPTE ST 2086 mastering display metadata present."))
        else:
            results.append((WARN, "PQ signaled but no SMPTE ST 2086 mastering display "
                            "metadata found. YouTube usually still accepts PQ, but "
                            "proper HDR10 wants it (and MaxCLL/MaxFALL)."))
        results.append((PASS if has_cll else WARN,
                        "Content light level (MaxCLL/MaxFALL) present."
                        if has_cll else
                        "No MaxCLL/MaxFALL content light level metadata found."))
    if has_dovi:
        results.append((WARN, "Dolby Vision configuration detected. YouTube relies on the "
                        "HDR10 base layer — make sure PQ + BT.2020 + 10-bit are all "
                        "correctly tagged independently of the DV layer."))

    # 6) codec advisory
    good_codecs = {"hevc", "vp9", "av1", "prores"}
    if codec in good_codecs:
        results.append((PASS, f"Codec '{codec}' can carry HDR metadata."))
    elif codec == "h264":
        results.append((WARN, "Codec is H.264. 8-bit H.264 cannot carry HDR metadata and "
                        "will be treated as SDR. Prefer HEVC, VP9 Profile 2, or AV1."))
    else:
        results.append((WARN, f"Codec '{codec or 'unknown'}' — verify it can carry HDR "
                        "metadata (HEVC / VP9 Profile 2 / AV1 recommended)."))

    # ---- the key gotcha: BT.2020 primaries WITHOUT an HDR transfer curve ----
    if prim == "bt2020" and not hdr_trc:
        results.append((FAIL, "BT.2020 primaries are tagged but the transfer curve is NOT "
                        "PQ/HLG. In this exact case YouTube re-tags the video to BT.709 "
                        "8-bit SDR to avoid banding — so it will never show the HDR badge. "
                        "Fix the transfer function tag."))

    for status, msg in results:
        print(f"  [{status}] " + textwrap.fill(msg, width=88,
              subsequent_indent=" " * 9))

    # ---- verdict ----
    hard_fail = (not (depth and depth >= 10)) or (not hdr_trc) or (prim != "bt2020")
    print()
    if hard_fail:
        print(red(bold("  VERDICT: This file will most likely NOT be detected as HDR.")))
        print(dim("  Address every [FAIL] above (transfer function and 10-bit are the "
                  "usual culprits) and re-upload."))
    else:
        print(green(bold("  VERDICT: Core HDR signaling looks correct.")))
        print(dim("  If YouTube still shows SDR, check any [WARN] items and confirm the "
                  "metadata survived your final export/transcode step."))


def main():
    ap = argparse.ArgumentParser(description="Diagnose YouTube HDR metadata in a video.")
    ap.add_argument("path", help="Path to the video file")
    ap.add_argument("--json", action="store_true",
                    help="Print raw ffprobe JSON instead of the report")
    ap.add_argument("--ffprobe", default=os.environ.get("FFPROBE", "ffprobe"),
                    help="Path to ffprobe(.exe) if it is not on your PATH "
                         "(or set the FFPROBE environment variable).")
    args = ap.parse_args()

    meta, frames = run_ffprobe(args.path, args.ffprobe)

    if args.json:
        print(json.dumps({"meta": meta, "frames": frames}, indent=2))
        return

    vs = video_stream(meta)
    side = collect_side_data(frames)
    print_metadata(meta, vs, side)
    diagnose(vs, side)


if __name__ == "__main__":
    main()
