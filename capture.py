#!/usr/bin/env python3
"""
Instagram Highlight Downloader - V3

Extension-only version.

Behavior:
    1. Open each Instagram Highlight URL.
    2. Wait for Turbo Downloader's Story controls.
    3. Prefer "Download all stories".
    4. ONLY if "Download all stories" does not exist, click
       "Download current story".
    5. Do not capture Instagram media directly -- Turbo does the work,
       this script just detects when files land and files them away.
    6. Every URL gets its OWN subfolder under ./output/.

Usage:
    python capture.py "URL"
    python capture.py -h "URL"
    python capture.py "URL" -i inp.txt
    python capture.py -i inp.txt

inp.txt: one Instagram URL per line. Blank lines and lines starting
with # are ignored.

(Note: -h here is a custom flag for passing a single URL -- not the
default argparse help. Use --help to see this usage text.)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Locator, Page, sync_playwright


# Force real-time, unbuffered output. Without this, on some terminals
# (especially when stdout isn't a real tty -- some IDE terminals, some
# launchers) Python fully buffers stdout, so log lines can sit queued
# up while input()'s prompt -- which is flushed immediately -- appears
# to jump the queue and show up "too early". This makes every print
# show up the instant it happens.
try:
    sys.stdout.reconfigure(line_buffering=True, write_through=True)
except Exception:
    pass


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

PROFILE_DIR = BASE_DIR / "browser" / "profile"
OUTPUT_DIR = BASE_DIR / "output"

# Chrome/Turbo download everything here first; we then sort each file
# into its own per-URL subfolder under OUTPUT_DIR.
STAGING_DIR = OUTPUT_DIR / "_incoming"

TURBO_EXTENSION_ID = "cpgaheeihidjmolbakklolchdplenjai"

MEDIA_EXTENSIONS = {
    ".mp4", ".webm", ".mov", ".mkv", ".avi",
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
}

# Turbo Downloader frequently saves files as a bare UUID with NO
# extension at all (Chrome could not infer a type from the blob it
# fetched). We must not filter those out. We DO still ignore
# in-progress / temp download artifacts so we don't report a partial
# file as "done".
IGNORED_SUFFIXES = {".crdownload", ".tmp", ".partial", ".part", ".download"}

PAGE_LOAD_WAIT = 3.0
BUTTON_TIMEOUT = 30.0
DOWNLOAD_WAIT = 120.0
BETWEEN_URLS_WAIT = 2.0


# ============================================================
# PRETTY TERMINAL LOGGING
# ============================================================

class _C:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"
    DIM = "\x1b[2m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"
    GRAY = "\x1b[90m"


def _enable_windows_ansi() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        new_mode = mode.value | 0x0004
        return bool(kernel32.SetConsoleMode(handle, new_mode))
    except Exception:
        return False


_USE_COLOR = sys.stdout.isatty() and _enable_windows_ansi()


def _paint(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{code}{text}{_C.RESET}"


def _timestamp() -> str:
    return _paint(_C.GRAY, datetime.now().strftime("%H:%M:%S"))


def log(message: str) -> None:
    print(f"{_timestamp()}  {_paint(_C.CYAN, 'i')}  {message}", flush=True)


def ok(message: str) -> None:
    print(f"{_timestamp()}  {_paint(_C.GREEN, '+')}  {_paint(_C.GREEN, message)}", flush=True)


def warn(message: str) -> None:
    print(f"{_timestamp()}  {_paint(_C.YELLOW, '!')}  {_paint(_C.YELLOW, message)}", flush=True)


def error(message: str) -> None:
    print(f"{_timestamp()}  {_paint(_C.RED, 'x')}  {_paint(_C.RED, message)}", flush=True)


def section(title: str) -> None:
    line = "-" * 70
    print(flush=True)
    print(_paint(_C.BLUE, line), flush=True)
    print(_paint(_C.BOLD + _C.BLUE, f" {title}"), flush=True)
    print(_paint(_C.BLUE, line), flush=True)


def banner(title: str) -> None:
    line = "=" * 70
    print(_paint(_C.MAGENTA, line), flush=True)
    print(_paint(_C.BOLD + _C.MAGENTA, f" {title}"), flush=True)
    print(_paint(_C.MAGENTA, line), flush=True)


# ============================================================
# CHROME DOWNLOAD LOCATION HELPERS
# ============================================================

def read_chrome_default_download_dir() -> Optional[Path]:
    """Read download.default_directory straight from the profile's
    Preferences JSON, so we watch wherever Chrome/Turbo is ACTUALLY
    configured to save files, instead of guessing."""
    pref_path = PROFILE_DIR / "Default" / "Preferences"
    if not pref_path.is_file():
        return None
    try:
        data = json.loads(pref_path.read_text(encoding="utf-8"))
        raw = data.get("download", {}).get("default_directory")
        if raw:
            return Path(raw)
    except Exception:
        pass
    return None


def candidate_download_dirs() -> list[Path]:
    """Directories where Chrome/Turbo may actually place downloads."""
    dirs = [STAGING_DIR]

    user_home = Path.home()
    dirs.extend([
        user_home / "Downloads",
        user_home / "Downloads" / "Instagram",
        user_home / "Downloads" / "Turbo Downloader",
        user_home / "Desktop",
    ])

    pref_dir = read_chrome_default_download_dir()
    if pref_dir:
        dirs.append(pref_dir)

    out: list[Path] = []
    seen = set()
    for d in dirs:
        try:
            key = str(d.resolve()).lower()
        except Exception:
            key = str(d).lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def is_candidate_media_file(p: Path) -> bool:
    suffix = p.suffix.lower()

    if suffix in IGNORED_SUFFIXES:
        return False  # still downloading

    if suffix in MEDIA_EXTENSIONS:
        return True

    # Turbo often saves with NO extension at all -- accept those too.
    if suffix == "":
        return True

    return False


def snapshot_media_files() -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for root in candidate_download_dirs():
        if not root.exists() or not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                if p.is_file() and is_candidate_media_file(p):
                    try:
                        st = p.stat()
                        snapshot[str(p.resolve()).lower()] = (st.st_size, st.st_mtime_ns)
                    except OSError:
                        pass
        except (OSError, PermissionError):
            pass
    return snapshot


def count_all_files_per_dir() -> dict[str, int]:
    """Diagnostic helper: total file count per watched dir, regardless
    of extension."""
    counts: dict[str, int] = {}
    for root in candidate_download_dirs():
        if not root.exists() or not root.is_dir():
            counts[str(root)] = -1
            continue
        try:
            counts[str(root)] = sum(1 for p in root.rglob("*") if p.is_file())
        except (OSError, PermissionError):
            counts[str(root)] = -1
    return counts


def find_new_media(before: dict[str, tuple[int, int]]) -> list[tuple[Path, tuple[int, int]]]:
    """Return media files that appeared or changed after the Turbo click."""
    after = snapshot_media_files()
    changed = []
    for path, meta in after.items():
        if path not in before or before[path] != meta:
            changed.append((Path(path), meta))
    return changed


def wait_for_new_media(
    before: dict[str, tuple[int, int]],
    timeout: float = 120,
    stable_window: float = 6.0,
):
    """Keep watching until new files stop appearing for `stable_window`
    seconds (so a multi-file "Download all stories" batch has time to
    fully land, not just its first file), or until `timeout` is hit."""
    deadline = time.time() + timeout
    last_report = 0.0
    last_change_time: Optional[float] = None
    latest: list[tuple[Path, tuple[int, int]]] = []

    while time.time() < deadline:
        changed = find_new_media(before)

        if len(changed) != len(latest):
            log(f"  ...{len(changed)} file(s) detected so far, still watching for more.")

        if changed:
            latest = changed
            last_change_time = time.time()

        if last_change_time is not None and (time.time() - last_change_time) >= stable_window:
            return latest

        if time.time() - last_report > 15:
            last_report = time.time()
            counts = count_all_files_per_dir()
            summary = ", ".join(f"{d} ({n} total files present)" for d, n in counts.items())
            log(f"Still waiting ({len(latest)} confirmed for this URL so far) -- {summary}")

        time.sleep(1.5)

    return latest


# Signature (magic bytes) -> extension, checked in order. Covers what
# Turbo actually saves from Instagram: mp4 video and jpg/png/webp
# images are the overwhelming majority.
_SIGNATURES: list[tuple[bytes, Optional[int], str]] = [
    # (signature bytes, offset to check at, extension)
    (b"\xff\xd8\xff", 0, ".jpg"),
    (b"\x89PNG\r\n\x1a\n", 0, ".png"),
    (b"GIF87a", 0, ".gif"),
    (b"GIF89a", 0, ".gif"),
    (b"RIFF", 0, ".webp"),  # confirmed further below (WEBP at offset 8)
    (b"\x1a\x45\xdf\xa3", 0, ".webm"),
    (b"ftyp", 4, ".mp4"),  # ISO base media container (mp4/mov/m4v/heic...)
]


def guess_extension(path: Path) -> Optional[str]:
    """Sniff a file's actual content to determine its real type, since
    Turbo Downloader frequently saves files with NO extension at all.
    Returns a leading-dot extension like '.mp4', or None if unknown."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None

    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"

    for signature, offset, ext in _SIGNATURES:
        if signature == b"RIFF":
            continue  # handled above with the WEBP sub-check
        end = offset + len(signature)
        if len(head) >= end and head[offset:end] == signature:
            return ext

    return None


def move_downloaded_files(
    downloaded: list[tuple[Path, tuple[int, int]]],
    dest_dir: Path,
) -> tuple[list[Path], int, int]:
    """Move every detected file into dest_dir, giving it a correct file
    extension (sniffed from its content) if it doesn't already have
    one. Returns (moved_paths, renamed_count, unresolved_count)."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    moved: list[Path] = []
    renamed_count = 0
    unresolved_count = 0

    for path, _meta in downloaded:
        if not path.exists():
            continue

        name = path.name

        if path.suffix == "":
            guessed = guess_extension(path)
            if guessed:
                name = f"{path.stem}{guessed}"
                renamed_count += 1
            else:
                unresolved_count += 1
                warn(
                    f"Could not detect the file type of {path.name}; "
                    "keeping it without an extension -- you may need to "
                    "check it manually."
                )

        stem = Path(name).stem
        suffix = Path(name).suffix
        target = dest_dir / name
        counter = 1
        while target.exists():
            target = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        try:
            shutil.move(str(path), str(target))
            moved.append(target)
        except OSError as exc:
            warn(f"Could not move {path} -> {target}: {exc}")

    return moved, renamed_count, unresolved_count


# ============================================================
# OUTPUT FOLDER NAMING
# ============================================================

def sanitize_folder_name(name: str) -> str:
    """Turn a user-supplied label (from #Label or -fn) into a safe
    Windows/macOS/Linux folder name."""
    name = name.strip().lstrip("#").strip()
    # Strip characters that are illegal in Windows folder names.
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name.strip("_.")


def slug_for_url(url: str, index: int, custom_name: Optional[str] = None) -> str:
    if custom_name:
        cleaned = sanitize_folder_name(custom_name)
        if cleaned:
            return cleaned
        warn(f"Ignoring empty/invalid custom folder name for URL {index}; using the default instead.")

    match = re.search(r"/highlights/(\d+)", url)
    if match:
        return f"highlight_{match.group(1)}"

    tail = url.rstrip("/").split("/")[-1]
    tail = re.sub(r"[^A-Za-z0-9_-]+", "_", tail).strip("_")
    return tail or f"url_{index}"


# ============================================================
# TURBO EXTENSION
# ============================================================

def find_turbo_extension() -> Optional[Path]:
    """Find Turbo Downloader in installed Chrome profiles."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None

    user_data = Path(local_app_data) / "Google" / "Chrome" / "User Data"
    if not user_data.exists():
        return None

    candidates: list[Path] = []

    for profile_dir in user_data.iterdir():
        if not profile_dir.is_dir():
            continue

        ext_root = profile_dir / "Extensions" / TURBO_EXTENSION_ID
        if not ext_root.is_dir():
            continue

        for version_dir in ext_root.iterdir():
            if not version_dir.is_dir():
                continue
            if (version_dir / "manifest.json").is_file():
                candidates.append(version_dir)

    if not candidates:
        return None

    candidates.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return candidates[0]


# ============================================================
# INPUT / CLI
# ============================================================

def is_instagram_url(value: str) -> bool:
    value = value.strip()
    return bool(re.match(r"^https?://(www\.)?instagram\.com/", value, re.I))


def load_urls(
    direct_urls: list[str],
    highlight_url: Optional[str],
    folder_name: Optional[str],
    input_file: Optional[str],
) -> list[tuple[str, Optional[str]]]:
    """Returns a list of (url, custom_folder_name_or_None), in order,
    with duplicate URLs removed (first occurrence wins)."""

    entries: list[tuple[str, Optional[str]]] = []

    all_direct = list(direct_urls)
    if highlight_url:
        all_direct.append(highlight_url)

    for url in all_direct:
        url = url.strip()
        if not url:
            continue
        if not is_instagram_url(url):
            warn(f"Skipping invalid Instagram URL: {url}")
            continue
        entries.append((url, folder_name))

    if input_file:
        path = Path(input_file)
        if not path.is_file():
            error(f"Input file not found: {path}")
            sys.exit(1)

        for line_number, raw in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        ):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            # Support "URL #Label" -- everything after the first '#'
            # (not counting a leading one, handled above) becomes the
            # output folder name for that URL.
            url_part, sep, label_part = line.partition("#")
            url_part = url_part.strip()
            label = label_part.strip() if sep else None

            if not is_instagram_url(url_part):
                warn(f"Skipping invalid URL in {path.name} line {line_number}: {line}")
                continue

            entries.append((url_part, label))

    seen = set()
    unique_entries: list[tuple[str, Optional[str]]] = []
    for url, name in entries:
        if url in seen:
            continue
        seen.add(url)
        unique_entries.append((url, name))

    return unique_entries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Instagram Highlight Downloader (Turbo Downloader extension only)",
        add_help=False,
    )

    parser.add_argument(
        "urls",
        nargs="*",
        help="One or more Instagram highlight URLs",
    )

    parser.add_argument(
        "-H", "-h", "--highlight",
        dest="highlight_url",
        metavar="URL",
        help="Pass a single Instagram highlight URL via flag instead of positionally",
    )

    parser.add_argument(
        "-i", "--input",
        dest="input_file",
        metavar="FILE",
        help="Text file containing one Instagram URL per line",
    )

    parser.add_argument(
        "-fn", "--folder-name",
        dest="folder_name",
        metavar="NAME",
        help=(
            "Custom output folder name for the URL(s) given directly on "
            "the command line (positionally or via -h). Not needed for "
            "-i files -- use 'URL #Label' per line there instead."
        ),
    )

    parser.add_argument(
        "-r", "--retries",
        dest="retries",
        type=int,
        default=1,
        metavar="N",
        help=(
            "How many extra attempts to make for URLs that fail on the "
            "first pass (default: 1). Set to 0 to disable retries."
        ),
    )

    parser.add_argument(
        "--help",
        action="help",
        help="Show this help message and exit",
    )

    return parser.parse_args()


# ============================================================
# LOGIN / STORY / TURBO INTERACTION
# ============================================================

def is_login_page(page: Page) -> bool:
    if "/accounts/login" in page.url.lower():
        return True
    try:
        return page.get_by_role(
            "heading", name=re.compile(r"log in|login", re.I)
        ).count() > 0
    except Exception:
        return False


def wait_for_login(page: Page) -> None:
    if not is_login_page(page):
        return

    section("Instagram login required")
    print("Log in manually in the opened Chromium window.")
    print("The script will continue automatically once you're logged in.")

    while is_login_page(page):
        time.sleep(2)

    ok("Instagram login detected.")


def click_view_story(page: Page) -> bool:
    patterns = (r"view story", r"مشاهده.*استوری", r"دیدن.*استوری")

    for pattern in patterns:
        try:
            locator = page.get_by_role("button", name=re.compile(pattern, re.I))
            for i in range(locator.count()):
                element = locator.nth(i)
                if not element.is_visible():
                    continue
                element.click(timeout=5000)
                log("Clicked 'View story'.")
                time.sleep(2)
                return True
        except Exception:
            pass

    return False


def visible_exact_button(page: Page, title: str) -> Optional[Locator]:
    locator = page.locator(f'[title="{title}"]')
    try:
        count = locator.count()
        for i in range(count):
            element = locator.nth(i)
            if not element.is_visible():
                continue
            box = element.bounding_box()
            if not box:
                continue
            if box["width"] < 8 or box["height"] < 8:
                continue
            return element
    except Exception:
        pass
    return None


def wait_for_turbo_buttons(
    page: Page, timeout: float = BUTTON_TIMEOUT
) -> tuple[Optional[Locator], Optional[Locator]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        all_button = visible_exact_button(page, "Download all stories")
        current_button = visible_exact_button(page, "Download current story")
        if all_button or current_button:
            return all_button, current_button
        time.sleep(0.5)
    return None, None


def click_turbo_for_url(page: Page) -> str:
    log("Looking for Turbo Downloader buttons...")

    all_button, current_button = wait_for_turbo_buttons(page)

    if all_button:
        ok('Turbo: "Download all stories" found -- clicking it.')
        all_button.click(timeout=5000)
        return "all"

    if current_button:
        warn('Turbo: "Download all stories" not found.')
        log('Turbo: falling back to "Download current story".')
        current_button.click(timeout=5000)
        return "current"

    raise RuntimeError("Turbo Downloader buttons were not found.")


# ============================================================
# ONE URL
# ============================================================

def process_url(page: Page, url: str, custom_name: Optional[str], index: int, total: int) -> dict:
    section(f"URL {index}/{total}")
    print(url)

    dest_dir = OUTPUT_DIR / slug_for_url(url, index, custom_name)
    log(f"Output folder: {dest_dir}")

    result = {
        "url": url,
        "custom_name": custom_name,
        "success": False,
        "saved": 0,
        "renamed": 0,
        "unresolved": 0,
    }

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(PAGE_LOAD_WAIT)

        wait_for_login(page)
        click_view_story(page)
        time.sleep(2)

        before = snapshot_media_files()
        mode = click_turbo_for_url(page)

        if mode == "all":
            log("Turbo bulk download started.")
        else:
            log("Turbo single-story fallback started.")

        downloaded = wait_for_new_media(before, timeout=DOWNLOAD_WAIT)

        if downloaded:
            moved, renamed_count, unresolved_count = move_downloaded_files(downloaded, dest_dir)

            result["success"] = True
            result["saved"] = len(moved)
            result["renamed"] = renamed_count
            result["unresolved"] = unresolved_count

            section(f"URL {index}/{total} -- summary")
            ok(f"Downloaded : {len(moved)}/{len(moved)} file(s) confirmed saved")
            if renamed_count:
                ok(f"Format fix : {renamed_count} file(s) had their extension corrected automatically")
            if unresolved_count:
                warn(f"Format fix : {unresolved_count} file(s) could NOT be identified -- check manually")
            ok(f"Saved to   : {dest_dir}")
            for path in moved:
                size = path.stat().st_size
                print(f"             - {path.name}  ({size:,} bytes)")
        else:
            warn(
                f"No new media file was detected within {DOWNLOAD_WAIT:.0f}s. "
                "The extension may still be downloading, or using a "
                "location this script isn't watching."
            )

        if mode == "all":
            log("Waiting a little before processing the next URL...")
            time.sleep(5)

        return result

    except Exception as exc:
        error(f"URL failed: {exc}")
        return result


# ============================================================
# MAIN
# ============================================================

RETRY_COOLDOWN = 10.0  # extra wait before a retry round, in case of rate-limiting
WARMUP_WAIT = 3.0      # let the extension settle right after Chromium starts


def main() -> None:
    args = parse_args()

    urls = load_urls(args.urls, args.highlight_url, args.folder_name, args.input_file)

    if not urls:
        error("No Instagram URLs were supplied.")
        print()
        print("Examples:")
        print('  python capture.py "https://www.instagram.com/stories/highlights/123/"')
        print('  python capture.py -h "https://www.instagram.com/stories/highlights/123/"')
        print('  python capture.py "https://www.instagram.com/stories/highlights/123/" -fn MyLabel')
        print('  python capture.py "https://www.instagram.com/stories/highlights/123/" -i inp.txt')
        print("  python capture.py -i inp.txt")
        print('  python capture.py -i inp.txt -r 3   # retry failures up to 3 extra times')
        sys.exit(1)

    extension_path = find_turbo_extension()
    if not extension_path:
        error("Turbo Downloader extension was not found.")
        error(f"Expected extension ID: {TURBO_EXTENSION_ID}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    banner("Instagram Highlight Downloader")
    log(f"URLs to process : {len(urls)}")
    log(f"Retries on fail : {args.retries}")
    log(f"Profile         : {PROFILE_DIR}")
    log(f"Output folder   : {OUTPUT_DIR}")
    log(f"Turbo extension : {extension_path}")

    with sync_playwright() as p:
        log("Starting Chromium...")

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            accept_downloads=True,
            downloads_path=str(STAGING_DIR),
            viewport={"width": 1280, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--disable-extensions-except={extension_path}",
                f"--load-extension={extension_path}",
            ],
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(10000)

            log("Letting the extension settle before the first URL...")
            time.sleep(WARMUP_WAIT)

            # ---- Pass 1: process every URL once ----
            results: dict[str, dict] = {}
            for index, (url, custom_name) in enumerate(urls, start=1):
                r = process_url(page, url, custom_name, index, len(urls))
                r["attempts"] = 1
                results[url] = r

                if index < len(urls):
                    time.sleep(BETWEEN_URLS_WAIT)

            # ---- Retry rounds for whatever failed ----
            retry_round = 0
            while retry_round < args.retries:
                failed_urls = [u for u, r in results.items() if not r["success"]]
                if not failed_urls:
                    break

                retry_round += 1
                banner(f"RETRY ROUND {retry_round}/{args.retries} -- {len(failed_urls)} URL(s) to redo")
                log(f"Cooling down for {RETRY_COOLDOWN:.0f}s first, in case this was rate-limiting...")
                time.sleep(RETRY_COOLDOWN)

                for index, u in enumerate(failed_urls, start=1):
                    custom_name = results[u]["custom_name"]
                    r = process_url(page, u, custom_name, index, len(failed_urls))
                    r["attempts"] = results[u]["attempts"] + 1
                    results[u] = r

                    if index < len(failed_urls):
                        time.sleep(BETWEEN_URLS_WAIT)

            ordered_results = [results[url] for url, _ in urls]

            successful_urls = sum(1 for r in ordered_results if r["success"])
            total_saved = sum(r["saved"] for r in ordered_results)
            total_renamed = sum(r["renamed"] for r in ordered_results)
            total_unresolved = sum(r["unresolved"] for r in ordered_results)
            recovered = [r for r in ordered_results if r["success"] and r["attempts"] > 1]
            still_failed = [r for r in ordered_results if not r["success"]]

            banner("ALL DONE")
            if successful_urls == len(urls):
                ok(f"URLs completed      : {successful_urls}/{len(urls)}  (all of them)")
            else:
                warn(f"URLs completed      : {successful_urls}/{len(urls)}")

            if recovered:
                ok(f"Recovered on retry  : {len(recovered)}")
                for r in recovered:
                    ok(f"  - {r['url']}  (succeeded on attempt {r['attempts']})")

            if still_failed:
                warn(f"Still failing       : {len(still_failed)} (after {args.retries + 1} attempt(s) each)")
                for r in still_failed:
                    warn(f"  - FAILED: {r['url']}")
                warn(
                    "These may need a manual look (open the URL yourself and "
                    "check Turbo works on it) -- could be a highlight-specific "
                    "issue rather than something retries can fix."
                )

            ok(f"Total files saved   : {total_saved}")
            if total_renamed:
                ok(f"Formats auto-fixed  : {total_renamed}")
            if total_unresolved:
                warn(f"Unrecognized format : {total_unresolved} (check these manually)")
            log(f"Output folder       : {OUTPUT_DIR}")

            print()
            ok("Everything is finished.")
            input("Press ENTER to close the browser...")

        except KeyboardInterrupt:
            print()
            warn("Stopped by user.")

        finally:
            context.close()


if __name__ == "__main__":
    main()