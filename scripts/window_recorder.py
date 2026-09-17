"""Records a single window on Windows, even when it is behind other windows.

Uses PrintWindow(PW_RENDERFULLCONTENT), so nothing else on the desktop is ever captured:
no other windows, no taskbar, no notifications. Frames are piped into FFmpeg.

    python scripts/window_recorder.py --title "YouTube Data API demo" --seconds 30 \
        --out docs/screencast/raw/01_doctor.mp4
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def find_window(title_part: str) -> int:
    """Largest visible window whose title contains `title_part` (helper windows are tiny)."""
    matches: list[int] = []
    title_part = title_part.lower()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title_part in buffer.value.lower():
                matches.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if not matches:
        raise SystemExit(f"no visible window matching {title_part!r}")

    def area(hwnd: int) -> int:
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        return (rect.right - rect.left) * (rect.bottom - rect.top)

    return max(matches, key=area)


def window_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width = (rect.right - rect.left) // 2 * 2
    height = (rect.bottom - rect.top) // 2 * 2
    if width < 16 or height < 16:
        raise SystemExit(f"window is too small to record ({width}x{height})")
    return width, height


def capture(hwnd: int, width: int, height: int) -> bytes:
    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT):
            gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, SRCCOPY)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 24
        info.bmiHeader.biCompression = 0
        stride = ((width * 3 + 3) // 4) * 4
        buffer = ctypes.create_string_buffer(stride * height)
        gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
        raw = buffer.raw
        if stride == width * 3:
            return raw
        return b"".join(raw[row * stride : row * stride + width * 3] for row in range(height))
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title", required=True, help="part of the window title")
    parser.add_argument("--seconds", type=float, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--ffmpeg", default=None)
    args = parser.parse_args()

    ffmpeg = args.ffmpeg or next(Path(r"C:\Users").glob("*/AppData/Local/Microsoft/WinGet/Packages/*FFmpeg*/*/bin/ffmpeg.exe"))
    hwnd = find_window(args.title)
    width, height = window_size(hwnd)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "bgr24", "-video_size", f"{width}x{height}", "-framerate", str(args.fps),
        "-i", "-", "-vf", "scale=1920:-2:flags=lanczos,pad=1920:1080:0:(oh-ih)/2:color=0x10151a",
        "-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "8M", "-pix_fmt", "yuv420p", str(out),
    ]
    print(f"recording {width}x{height} window '{args.title}' for {args.seconds:.0f}s -> {out}")
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    frame_time = 1 / args.fps
    end = time.perf_counter() + args.seconds
    frames = 0
    try:
        while time.perf_counter() < end:
            started = time.perf_counter()
            process.stdin.write(capture(hwnd, width, height))
            frames += 1
            sleep = frame_time - (time.perf_counter() - started)
            if sleep > 0:
                time.sleep(sleep)
    finally:
        process.stdin.close()
        process.wait()
    print(f"saved {out} ({frames} frames)")


if __name__ == "__main__":
    main()
