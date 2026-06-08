# NHNB HDR Analyser and Metadata Fixer

A Windows GUI tool for inspecting video HDR metadata and diagnosing HDR compatibility across platforms including YouTube, TikTok, and Instagram. When required HDR metadata is missing, it can inject it via a full x265 re-encode.

---

## Getting Started

Download the latest release from the [Releases](../../releases) page. No installation is required. All necessary components — including `ffmpeg.exe` and `ffprobe.exe` — are included in the same folder as `NHNBHDRAnalyser.exe`. Simply run the exe and the application will locate them automatically.

---

## Analysing a File

Load a file using one of two methods:

- **Drag and drop** a video file onto the drop zone at the top of the window.
- Click **Browse…** to open a file picker. Supported formats include `.mp4`, `.mkv`, `.mov`, `.avi`, `.ts`, `.mts`, `.m2ts`, `.webm`, `.hevc`, and `.mxf`.

Once a file is selected, click **Analyse**. ffprobe runs in the background — the button will read "Analysing…" while it works. Results appear in the Report tab when complete.

---

## The Report Tab

Results are grouped into four sections:

**FILE** — Container format, duration, file size, and overall bitrate.

**VIDEO STREAM** — Codec, profile, resolution, pixel format, bit depth, color range, color primaries, transfer function (TRC), matrix coefficients, and frame rate.

**HDR SIDE-DATA** — Whether SMPTE ST 2086 Mastering Display (MDCV) and Content Light Level (CLL / MaxCLL / MaxFALL) metadata are present, and their values if found. Any additional side data (e.g. Dolby Vision) is shown here as well.

**HDR Compatibility Diagnosis** — Each check appears as a colored badge:
- **PASS** (green) — criterion met.
- **WARN** (amber) — not a hard failure but may cause issues.
- **FAIL** (red) — will likely prevent HDR recognition.

A verdict card at the bottom summarizes the overall result.

### What Platforms Require for HDR Recognition

All three of the following must be present:
1. **10-bit** pixel depth (8-bit is always treated as SDR).
2. **PQ (SMPTE ST 2084)** or **HLG (ARIB STD-B67)** transfer function.
3. **BT.2020** color primaries.

MDCV and MaxCLL/MaxFALL metadata are not strictly required but are expected for proper HDR10 signaling. A WARN is issued if they are absent in a PQ-tagged file.

The diagnosis also checks the **container format**. MP4 and MOV are compatible with all major platforms. Matroska (MKV), WebM, AVI, MPEG-TS, MXF, and raw HEVC bitstreams are accepted by YouTube but not reliably by TikTok or Instagram, and will generate a WARN with a recommendation to remux to MP4.

---

## Fix Missing Metadata

If MDCV or CLL metadata is absent, a **Fix Metadata…** button appears at the bottom of the diagnosis section. Clicking it opens the Fix Missing Metadata dialog.

> **Note:** This process performs a full x265 re-encode of the video track. The audio stream is copied without modification. Re-encoding is inherently lossy for the video, though CRF 18 (the default) is considered visually lossless for most content.

### Metadata Preset

Choose from five common presets covering P3-D65 and BT.2020 color spaces at 1000, 4000, and 10000 nit peak luminance levels, or select **Custom** to enter values manually.

When **Custom** is selected, the window expands to reveal:

- **Mastering Display** — Peak Luminance and Minimum Luminance in nits.
- **Display Primaries** — Green, Blue, Red, and White Point chromaticity coordinates as decimal values in the 0–1 range.
- **Content Light Level** — MaxCLL and MaxFALL in nits.

Editing any field while a preset is active will automatically switch the dropdown to Custom.

### Re-encode Quality

- **CRF** — Constant Rate Factor, controlling video quality. The range is 0–51; 18 is the default and is visually lossless. Lower values produce higher quality at larger file sizes. Higher values reduce file size at some quality cost.
- **Encoding speed** — Controls the x265 encoder's effort level. Slower presets produce better compression at the same CRF but take longer to run. The default is `medium`.

### Saving the Result

| Button | Behavior |
|---|---|
| **Save as New File** | Opens a Save dialog. Output is named `<original>_hdr10.<ext>` by default. |
| **Overwrite Original** | Shows a confirmation prompt, then encodes to a temporary file and replaces the original on success. The partial temp file is left on disk if the encode is interrupted. |

A progress percentage is shown in the status area during encoding. When encoding completes, an **Open Folder** button appears for quick access to the output location.

### Interrupting an Encode

Closing the Fix Metadata dialog mid-encode will kill the ffmpeg process immediately. If you try to close the main window while an encode is running, you will be prompted to confirm; choosing to proceed kills the encode and any partial output file will remain on disk for manual cleanup.

---

## Exporting Results

Two export options are available at the bottom of the main window after a file has been analysed:

- **Copy to Clipboard** — Copies a plain-text version of the full report.
- **Save Report…** — Saves the same plain-text report to a `.txt` file. The default filename is `<videoname>_hdr_report.txt`.

The **Raw JSON** tab contains the complete unformatted ffprobe output for advanced inspection.

---

## Light / Dark Mode

The **Light Mode / Dark Mode** toggle in the top-right corner switches the application theme. The setting is not persisted between sessions.

---

## Building from Source

**Requirements:** Python 3.11+, and the packages listed in `requirements.txt`.

```bash
pip install -r requirements.txt
```

To run without building:

```bash
python src/hdr_check_gui.py
```

To build the standalone executable (place `assets/icon.ico` first):

```bash
cd build
pyinstaller NHNBHDRAnalyser.spec
```

The compiled exe will be at `build/dist/NHNBHDRAnalyser.exe`. Bundle it with `ffmpeg.exe`, `ffprobe.exe`, and the FFmpeg DLLs for distribution.

---

## Third-Party Software

This project uses [FFmpeg](https://ffmpeg.org) for video analysis and re-encoding. Release packages include FFmpeg binaries licensed under the GNU General Public License v2 or later. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for details.

---

## License

This project is licensed under the GNU General Public License v3.0. See [LICENSE](LICENSE) for the full text.
