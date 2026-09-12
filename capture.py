#!/usr/bin/env python3

"""
Instagram Highlight Screenshot Automator

Usage:
    python capture.py "https://www.instagram.com/stories/highlights/18024150727811997/"

What it does:
    1. Opens a persistent Chromium profile.
    2. Lets the user log into Instagram manually on first run.
    3. Automatically clicks "View story" when Instagram shows it.
    4. Opens the supplied Highlight URL.
    5. Takes a screenshot of each Story.
    6. Finds/clicks the Next button using several strategies.
    7. Waits for the Story to actually change.
    8. Prevents duplicate screenshots using SHA-256 hashes.
    9. Detects loops/end-of-highlight.
    10. Saves metadata and debug screenshots.

This tool is intended for content you are authorized to access/download.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

PROFILE_DIR = BASE_DIR / "browser" / "profile"
OUTPUT_DIR = BASE_DIR / "output"

DEFAULT_WAIT_AFTER_LOAD = 3.0
DEFAULT_WAIT_AFTER_NEXT = 1.5

MAX_STORIES = 500

# How many consecutive unchanged states before we consider
# the highlight stuck/finished.
MAX_UNCHANGED_ATTEMPTS = 3

# Instagram can animate transitions.
ANIMATION_SETTLE_TIME = 0.7


# ============================================================
# LOGGING
# ============================================================

def log(message: str) -> None:
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")


def warn(message: str) -> None:
    print(f"[WARNING] {message}")


def error(message: str) -> None:
    print(f"[ERROR] {message}")


# ============================================================
# HASHING
# ============================================================

def sha256_file(path: Path) -> str:
    """Return SHA-256 hash of a file."""

    digest = hashlib.sha256()

    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


# ============================================================
# OUTPUT
# ============================================================

def make_output_dir(url: str) -> Path:
    """
    Create a deterministic-ish output directory.

    Example:

        output/
            highlight_18024150727811997/
    """

    match = re.search(r"/highlights/([^/?#]+)", url)

    if match:
        identifier = match.group(1)
    else:
        identifier = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_identifier = re.sub(
        r"[^a-zA-Z0-9_.-]+",
        "_",
        identifier,
    )

    directory = OUTPUT_DIR / f"highlight_{safe_identifier}"
    directory.mkdir(parents=True, exist_ok=True)

    return directory


# ============================================================
# INSTAGRAM STATE
# ============================================================

def is_login_page(page: Page) -> bool:
    """
    Basic detection for Instagram login screen.
    """

    url = page.url.lower()

    if "/accounts/login" in url:
        return True

    try:
        if page.get_by_role(
            "heading",
            name=re.compile(r"log in|login", re.I),
        ).count():
            return True
    except Exception:
        pass

    return False


def wait_for_manual_login(page: Page) -> None:
    """
    On first run, give the user time to log into Instagram.
    """

    if not is_login_page(page):
        return

    print()
    print("=" * 70)
    print("Instagram login required")
    print("=" * 70)
    print()
    print("A Chromium window has opened.")
    print("Log into Instagram manually in that window.")
    print()
    print("After you are logged in, the script will detect it automatically.")
    print()
    print("Do NOT close the browser.")
    print("=" * 70)
    print()

    while True:

        if not is_login_page(page):
            log("Instagram login detected.")
            break

        time.sleep(2)


# ============================================================
# VIEW STORY BUTTON
# ============================================================

def find_view_story_button(page: Page) -> Optional[Locator]:
    """
    Find Instagram's "View story" confirmation button.

    Instagram may change its DOM, so several strategies are used.
    """

    candidates = []

    # --------------------------------------------------------
    # Strategy 1: ARIA button
    # --------------------------------------------------------

    aria_patterns = [
        r"view story",
        r"مشاهده.*استوری",
        r"دیدن.*استوری",
    ]

    for pattern in aria_patterns:
        try:
            locator = page.get_by_role(
                "button",
                name=re.compile(pattern, re.I),
            )

            candidates.append(("ARIA", locator))

        except Exception:
            pass

    # --------------------------------------------------------
    # Strategy 2: text
    # --------------------------------------------------------

    text_patterns = [
        r"view story",
        r"مشاهده.*استوری",
        r"دیدن.*استوری",
    ]

    for pattern in text_patterns:
        try:
            locator = page.get_by_text(
                re.compile(pattern, re.I)
            )

            candidates.append(("TEXT", locator))

        except Exception:
            pass

    # --------------------------------------------------------
    # Strategy 3: button containing matching text
    # --------------------------------------------------------

    try:
        locator = page.locator(
            "button"
        ).filter(
            has_text=re.compile(
                r"view story|مشاهده.*استوری|دیدن.*استوری",
                re.I,
            )
        )

        candidates.append(("BUTTON-TEXT", locator))

    except Exception:
        pass

    # --------------------------------------------------------
    # Return first visible candidate
    # --------------------------------------------------------

    for source, locator in candidates:

        try:
            count = locator.count()

            for i in range(count):

                candidate = locator.nth(i)

                if not candidate.is_visible():
                    continue

                box = candidate.bounding_box()

                if not box:
                    continue

                # Ignore tiny unrelated text elements.
                if box["width"] < 40 or box["height"] < 20:
                    continue

                log(
                    f"'View story' button found using {source}."
                )

                return candidate

        except Exception:
            continue

    return None


def click_view_story(page: Page) -> bool:
    """
    Automatically click Instagram's "View story" confirmation
    if it is present.

    Returns:
        True  -> button was found and clicked
        False -> button was not found
    """

    log("Checking for 'View story'...")

    button = find_view_story_button(page)

    if not button:
        log("'View story' button not found. Continuing...")
        return False

    try:
        button.scroll_into_view_if_needed()
    except Exception:
        pass

    time.sleep(0.3)

    # --------------------------------------------------------
    # Normal click
    # --------------------------------------------------------

    try:

        button.click(timeout=3000)

        log("Clicked 'View story'.")

        time.sleep(2)

        return True

    except Exception as exc:

        warn(
            f"Normal 'View story' click failed: {exc}"
        )

    # --------------------------------------------------------
    # Force click fallback
    # --------------------------------------------------------

    try:

        button.click(
            timeout=3000,
            force=True,
        )

        log("Clicked 'View story' using force click.")

        time.sleep(2)

        return True

    except Exception as exc:

        warn(
            f"Force 'View story' click failed: {exc}"
        )

    return False


def wait_for_story_to_open(page: Page, timeout: float = 8.0) -> bool:
    """
    Wait until the actual Story media appears after clicking
    "View story".

    This prevents taking a screenshot of the confirmation screen.
    """

    deadline = time.time() + timeout

    while time.time() < deadline:

        try:

            # Look for a large visible image/video.
            elements = page.locator("img, video")

            count = elements.count()

            for i in range(count):

                try:

                    element = elements.nth(i)

                    if not element.is_visible():
                        continue

                    box = element.bounding_box()

                    if not box:
                        continue

                    if box["width"] >= 250 and box["height"] >= 250:
                        log("Story media detected.")
                        return True

                except Exception:
                    continue

        except Exception:
            pass

        time.sleep(0.4)

    warn("Could not confidently detect Story media.")

    return False


# ============================================================
# STORY AREA DETECTION
# ============================================================

def get_story_media_signature(page: Page) -> Optional[str]:
    """
    Try to obtain a signature representing the currently visible
    Story media.

    We inspect visible IMG and VIDEO elements.

    This is not used as the only duplicate detector;
    the actual screenshot hash remains the final authority.
    """

    try:

        candidates = page.locator("img, video").all()

        signatures = []

        for element in candidates:

            try:

                if not element.is_visible():
                    continue

                box = element.bounding_box()

                if not box:
                    continue

                width = box["width"]
                height = box["height"]

                # Story media is normally large.
                if width < 200 or height < 200:
                    continue

                tag = element.evaluate(
                    "(el) => el.tagName.toLowerCase()"
                )

                if tag == "img":

                    src = (
                        element.get_attribute("src")
                        or element.get_attribute("currentSrc")
                    )

                    if src:

                        signatures.append(
                            f"img:{src}:{round(width)}x{round(height)}"
                        )

                elif tag == "video":

                    src = (
                        element.get_attribute("src")
                        or element.get_attribute("currentSrc")
                    )

                    if src:

                        signatures.append(
                            f"video:{src}:{round(width)}x{round(height)}"
                        )

            except Exception:
                continue

        if not signatures:
            return None

        signatures.sort()

        combined = "|".join(signatures)

        return hashlib.sha256(
            combined.encode("utf-8")
        ).hexdigest()

    except Exception:
        return None


# ============================================================
# NEXT BUTTON DETECTION
# ============================================================

def visible(locator: Locator) -> bool:
    """
    Safely check whether a locator is visible.
    """

    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def find_next_button(page: Page) -> Optional[Locator]:
    """
    Find Instagram's Next Story button using multiple strategies.

    Instagram changes DOM structure frequently, so we intentionally
    don't rely on one CSS selector.
    """

    candidates = []

    # --------------------------------------------------------
    # Strategy 1: ARIA button name
    # --------------------------------------------------------

    aria_names = [
        r"next",
        r"next story",
        r"next photo",
        r"next video",
        r"بعدی",
    ]

    for pattern in aria_names:

        try:

            locator = page.get_by_role(
                "button",
                name=re.compile(pattern, re.I),
            )

            candidates.append(
                ("ARIA", locator)
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # Strategy 2: title attribute
    # --------------------------------------------------------

    title_patterns = [
        r"next",
        r"next story",
        r"بعدی",
    ]

    for pattern in title_patterns:

        try:

            locator = page.locator(
                f'[title*="{pattern}" i]'
            )

            candidates.append(
                ("TITLE", locator)
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # Strategy 3: aria-label
    # --------------------------------------------------------

    aria_patterns = [
        r"next",
        r"next story",
        r"بعدی",
    ]

    for pattern in aria_patterns:

        try:

            locator = page.locator(
                f'[aria-label*="{pattern}" i]'
            )

            candidates.append(
                ("ARIA-LABEL", locator)
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # Return first visible candidate
    # --------------------------------------------------------

    for source, locator in candidates:

        try:

            count = locator.count()

            for i in range(count):

                candidate = locator.nth(i)

                if not candidate.is_visible():
                    continue

                box = candidate.bounding_box()

                if not box:
                    continue

                viewport = page.viewport_size

                if viewport:

                    center_x = (
                        box["x"]
                        + box["width"] / 2
                    )

                    if center_x < viewport["width"] * 0.55:
                        continue

                log(
                    f"Next button found using {source}."
                )

                return candidate

        except Exception:
            continue

    # --------------------------------------------------------
    # Strategy 4: geometric detection
    #
    # Look for clickable elements in the right side of the
    # viewport with an SVG/icon.
    # --------------------------------------------------------

    try:

        viewport = page.viewport_size

        if viewport:

            elements = page.locator(
                "button, [role='button']"
            )

            count = elements.count()

            possible = []

            for i in range(count):

                try:

                    element = elements.nth(i)

                    if not element.is_visible():
                        continue

                    box = element.bounding_box()

                    if not box:
                        continue

                    center_x = (
                        box["x"]
                        + box["width"] / 2
                    )

                    center_y = (
                        box["y"]
                        + box["height"] / 2
                    )

                    # Right side.
                    if center_x < viewport["width"] * 0.70:
                        continue

                    # Ignore extreme top/bottom.
                    if center_y < viewport["height"] * 0.15:
                        continue

                    if center_y > viewport["height"] * 0.85:
                        continue

                    area = (
                        box["width"]
                        * box["height"]
                    )

                    possible.append(
                        (area, element)
                    )

                except Exception:
                    continue

            if possible:

                # Usually navigation button is reasonably small.
                possible.sort(
                    key=lambda x: x[0]
                )

                for _, element in possible:

                    try:

                        if element.is_visible():

                            log(
                                "Next button found using "
                                "geometric fallback."
                            )

                            return element

                    except Exception:
                        continue

    except Exception:
        pass

    return None


# ============================================================
# STORY SCREENSHOT
# ============================================================

def screenshot_story(
    page: Page,
    output_path: Path,
) -> str:
    """
    Take viewport screenshot.

    Returns SHA-256 hash.
    """

    page.screenshot(
        path=str(output_path),
        animations="disabled",
    )

    return sha256_file(output_path)


# ============================================================
# WAIT FOR STORY CHANGE
# ============================================================

def wait_for_story_change(
    page: Page,
    old_hash: str,
    timeout: float = 6.0,
) -> bool:
    """
    After clicking Next, wait until the viewport screenshot
    actually changes.

    This prevents capturing the same Story twice because the
    click happened before Instagram finished transitioning.
    """

    deadline = time.time() + timeout

    while time.time() < deadline:

        time.sleep(0.35)

        temp_path = OUTPUT_DIR / "__story_probe.png"

        try:

            page.screenshot(
                path=str(temp_path),
                animations="disabled",
            )

            new_hash = sha256_file(
                temp_path
            )

            try:
                temp_path.unlink()
            except Exception:
                pass

            if new_hash != old_hash:

                time.sleep(
                    ANIMATION_SETTLE_TIME
                )

                return True

        except Exception:

            try:
                temp_path.unlink()
            except Exception:
                pass

    return False


# ============================================================
# CLICK NEXT
# ============================================================

def click_next(page: Page) -> bool:
    """
    Locate and click Next.
    """

    button = find_next_button(page)

    if not button:
        return False

    try:

        button.scroll_into_view_if_needed()

        time.sleep(0.2)

        button.click(
            timeout=3000,
        )

        return True

    except Exception as exc:

        warn(
            f"Normal click failed: {exc}"
        )

        # Force click fallback.
        try:

            button.click(
                timeout=2000,
                force=True,
            )

            return True

        except Exception as exc2:

            warn(
                f"Force click failed: {exc2}"
            )

            return False


# ============================================================
# DEBUG
# ============================================================

def save_debug(
    page: Page,
    output_dir: Path,
) -> None:
    """
    Save diagnostic files if automation gets stuck.
    """

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    screenshot_path = (
        output_dir
        / f"DEBUG_{timestamp}.png"
    )

    html_path = (
        output_dir
        / f"DEBUG_{timestamp}.html"
    )

    try:

        page.screenshot(
            path=str(screenshot_path),
            full_page=False,
        )

        html = page.content()

        html_path.write_text(
            html,
            encoding="utf-8",
        )

        log(
            f"Debug screenshot: {screenshot_path}"
        )

        log(
            f"Debug HTML: {html_path}"
        )

    except Exception as exc:

        warn(
            f"Could not save debug data: {exc}"
        )


# ============================================================
# METADATA
# ============================================================

def save_metadata(
    output_dir: Path,
    url: str,
    screenshots: list[dict],
    stopped_reason: str,
) -> None:

    metadata = {
        "source_url": url,
        "captured_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "stories_captured": len(
            screenshots
        ),
        "stopped_reason": stopped_reason,
        "screenshots": screenshots,
    }

    path = output_dir / "metadata.json"

    path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ============================================================
# MAIN CAPTURE LOOP
# ============================================================

def capture_highlight(
    page: Page,
    url: str,
    output_dir: Path,
) -> None:

    log("Opening highlight:")
    log(f"  {url}")

    page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    time.sleep(
        DEFAULT_WAIT_AFTER_LOAD
    )

    # --------------------------------------------------------
    # Login
    # --------------------------------------------------------

    wait_for_manual_login(page)

    # --------------------------------------------------------
    # Wait after login
    # --------------------------------------------------------

    time.sleep(3)

    log("Current URL:")
    log(f"  {page.url}")

    # --------------------------------------------------------
    # Check URL
    # --------------------------------------------------------

    if "/stories/" not in page.url:

        warn(
            "The current URL does not look like an Instagram "
            "Story/Highlight URL."
        )

    # --------------------------------------------------------
    # NEW:
    # Automatically handle Instagram's "View story" screen.
    # --------------------------------------------------------

    clicked_view_story = click_view_story(page)

    if clicked_view_story:

        # Give Instagram time to transition from the
        # confirmation screen into the actual Story.
        wait_for_story_to_open(page)

        time.sleep(
            ANIMATION_SETTLE_TIME
        )

    # --------------------------------------------------------
    # Capture loop
    # --------------------------------------------------------

    screenshots = []

    known_hashes = set()

    unchanged_attempts = 0

    last_hash = None

    stopped_reason = "unknown"

    for story_index in range(
        1,
        MAX_STORIES + 1,
    ):

        log("")
        log(
            f"Processing Story #{story_index}"
        )

        time.sleep(
            ANIMATION_SETTLE_TIME
        )

        # ----------------------------------------------------
        # Temporary screenshot
        # ----------------------------------------------------

        temp_path = (
            output_dir
            / "__current.png"
        )

        page.screenshot(
            path=str(temp_path),
            animations="disabled",
        )

        current_hash = sha256_file(
            temp_path
        )

        # ----------------------------------------------------
        # Duplicate prevention
        # ----------------------------------------------------

        if current_hash in known_hashes:

            log(
                "This Story screenshot is already known."
            )

            try:
                temp_path.unlink()
            except Exception:
                pass

            stopped_reason = (
                "duplicate_story_detected"
            )

            break

        # ----------------------------------------------------
        # Detect consecutive unchanged state
        # ----------------------------------------------------

        if (
            last_hash is not None
            and current_hash == last_hash
        ):

            unchanged_attempts += 1

            log(
                f"Story has not changed "
                f"({unchanged_attempts}/"
                f"{MAX_UNCHANGED_ATTEMPTS})."
            )

            if (
                unchanged_attempts
                >= MAX_UNCHANGED_ATTEMPTS
            ):

                try:
                    temp_path.unlink()
                except Exception:
                    pass

                stopped_reason = (
                    "story_did_not_change"
                )

                break

        else:

            unchanged_attempts = 0

        last_hash = current_hash

        # ----------------------------------------------------
        # Save final screenshot
        # ----------------------------------------------------

        final_path = (
            output_dir
            / f"{story_index:03d}.png"
        )

        temp_path.replace(
            final_path
        )

        known_hashes.add(
            current_hash
        )

        log(
            f"Saved: {final_path.name}"
        )

        screenshots.append(
            {
                "index": story_index,
                "file": final_path.name,
                "sha256": current_hash,
                "captured_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        )

        # ----------------------------------------------------
        # Maximum story limit
        # ----------------------------------------------------

        if story_index >= MAX_STORIES:

            stopped_reason = (
                "maximum_story_limit_reached"
            )

            warn(
                f"Reached MAX_STORIES={MAX_STORIES}."
            )

            break

        # ----------------------------------------------------
        # Find Next
        # ----------------------------------------------------

        log(
            "Looking for Next button..."
        )

        next_button = find_next_button(
            page
        )

        if not next_button:

            log(
                "No Next button found."
            )

            stopped_reason = (
                "next_button_not_found"
            )

            save_debug(
                page,
                output_dir,
            )

            break

        # ----------------------------------------------------
        # Click Next
        # ----------------------------------------------------

        log(
            "Clicking Next..."
        )

        try:

            next_button.scroll_into_view_if_needed()

        except Exception:
            pass

        try:

            next_button.click(
                timeout=3000,
            )

        except Exception as exc:

            warn(
                f"Click failed: {exc}"
            )

            try:

                next_button.click(
                    timeout=3000,
                    force=True,
                )

            except Exception as exc2:

                error(
                    f"Force click also failed: {exc2}"
                )

                stopped_reason = (
                    "next_button_click_failed"
                )

                save_debug(
                    page,
                    output_dir,
                )

                break

        # ----------------------------------------------------
        # Wait for transition
        # ----------------------------------------------------

        log(
            "Waiting for next Story..."
        )

        changed = wait_for_story_change(
            page,
            current_hash,
        )

        if not changed:

            warn(
                "The Story did not visibly change."
            )

            # Take one more attempt before giving up.
            time.sleep(1)

            retry_path = (
                output_dir
                / "__retry.png"
            )

            page.screenshot(
                path=str(retry_path),
                animations="disabled",
            )

            retry_hash = sha256_file(
                retry_path
            )

            try:
                retry_path.unlink()
            except Exception:
                pass

            if retry_hash == current_hash:

                stopped_reason = (
                    "next_click_did_not_change_story"
                )

                save_debug(
                    page,
                    output_dir,
                )

                break

        time.sleep(
            DEFAULT_WAIT_AFTER_NEXT
        )

    else:

        stopped_reason = (
            "loop_finished"
        )

    # --------------------------------------------------------
    # Save metadata
    # --------------------------------------------------------

    save_metadata(
        output_dir,
        url,
        screenshots,
        stopped_reason,
    )

    log("")
    log("=" * 70)
    log("DONE")
    log("=" * 70)

    log(
        f"Stories captured: {len(screenshots)}"
    )

    log(
        f"Stopped because: {stopped_reason}"
    )

    log(
        f"Output: {output_dir}"
    )

    log("=" * 70)


# ============================================================
# ARGUMENTS
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "Automatically screenshot every Story "
            "inside an Instagram Highlight."
        )
    )

    parser.add_argument(
        "url",
        help=(
            "Instagram Highlight URL, e.g. "
            "https://www.instagram.com/stories/highlights/..."
        ),
    )

    parser.add_argument(
        "--max-stories",
        type=int,
        default=MAX_STORIES,
        help=(
            f"Maximum number of Stories to capture "
            f"(default: {MAX_STORIES})"
        ),
    )

    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_WAIT_AFTER_NEXT,
        help=(
            f"Seconds to wait after Next "
            f"(default: {DEFAULT_WAIT_AFTER_NEXT})"
        ),
    )

    return parser.parse_args()


# ============================================================
# ENTRY POINT
# ============================================================

def main():

    args = parse_args()

    global MAX_STORIES
    global DEFAULT_WAIT_AFTER_NEXT

    MAX_STORIES = args.max_stories
    DEFAULT_WAIT_AFTER_NEXT = args.wait

    url = args.url.strip()

    if not url.startswith(
        "https://www.instagram.com/"
    ):

        error(
            "Please provide an Instagram URL."
        )

        sys.exit(1)

    output_dir = make_output_dir(
        url
    )

    PROFILE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    log(
        "Instagram Highlight Capture"
    )

    log(
        f"Profile: {PROFILE_DIR}"
    )

    log(
        f"Output: {output_dir}"
    )

    with sync_playwright() as p:

        log(
            "Starting Chromium..."
        )

        context: BrowserContext = (
            p.chromium.launch_persistent_context(
                user_data_dir=str(
                    PROFILE_DIR
                ),
                headless=False,
                viewport={
                    "width": 1280,
                    "height": 900,
                },
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
            )
        )

        try:

            # ------------------------------------------------
            # Reuse existing page if possible
            # ------------------------------------------------

            if context.pages:

                page = context.pages[0]

            else:

                page = context.new_page()

            page.set_default_timeout(
                5000
            )

            # ------------------------------------------------
            # Capture
            # ------------------------------------------------

            capture_highlight(
                page,
                url,
                output_dir,
            )

            print()

            input(
                "Press ENTER to close the browser..."
            )

        except KeyboardInterrupt:

            print()

            warn(
                "Stopped by user."
            )

        except Exception as exc:

            print()

            error(
                f"Fatal error: {exc}"
            )

            try:

                save_debug(
                    page,
                    output_dir,
                )

            except Exception:
                pass

            raise

        finally:

            context.close()


if __name__ == "__main__":
    main()