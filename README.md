# Instagram Highlight Capture

A Playwright-based tool that automates downloading Instagram Story
Highlights, using the **Turbo Downloader** Chrome extension to do the
actual media fetching. The script drives a real (persistent) Chromium
profile, opens each highlight, clicks Turbo's download buttons for
you, watches for the files to land, automatically fixes their file
extensions, and sorts everything into a clean `output/` folder — one
subfolder per highlight.

## Features

- Batch mode: pass one URL, several URLs, or a text file with one URL
  per line (or mix all three).
- Prefers Turbo's **"Download all stories"** button; falls back to
  **"Download current story"** only if the bulk option isn't available.
- Detects when Turbo has actually finished writing files (waits for a
  quiet period with no new files, not just the first file that
  appears) before moving on.
- Automatically fixes files that Turbo saves with **no extension** by
  sniffing their real file type (mp4, webm, jpg, png, gif, webp) and
  renaming them correctly.
- Each processed URL gets its own subfolder under `output/`, named
  after its highlight ID.
- Clean, color-coded terminal logging with a final summary (files
  saved, formats auto-fixed, anything that needs manual attention).
- Reuses a persistent browser profile, so you only log in to Instagram
  once.

## Requirements

- Python 3.10+
- Google Chrome installed, with the **Turbo Downloader** extension
  added to it (any Chrome profile — the script finds it automatically
  by its extension ID)
- [Playwright for Python](https://playwright.dev/python/)

## Setup

```bash
git clone https://github.com/noonehp/instagram-highlight-capture.git
cd instagram-highlight-capture

python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install playwright
playwright install chromium
```

Make sure the Turbo Downloader Chrome extension is already installed
in your regular Chrome browser at least once (the script locates it on
disk automatically).

## Usage

```bash
# Single URL
python capture.py "https://www.instagram.com/stories/highlights/18106247134439092/"

# Same thing, via the -h/--highlight flag
python capture.py -h "https://www.instagram.com/stories/highlights/18106247134439092/"

# A URL plus a text file of more URLs (one per line)
python capture.py "https://www.instagram.com/stories/highlights/18106247134439092/" -i inp.txt

# Just a text file of URLs
python capture.py -i inp.txt
```

`inp.txt` format — one Instagram URL per line; blank lines and lines
starting with `#` are ignored:

```
# my highlights
https://www.instagram.com/stories/highlights/18106247134439092/
https://www.instagram.com/stories/highlights/17999999999999999/
```

On first run, if Instagram isn't logged in inside the automated
browser window, the script pauses and asks you to log in manually —
after that it remembers the session (stored in `browser/profile/`).

## Output

```
output/
├── highlight_18106247134439092/
│   ├── clip_01.mp4
│   ├── photo_01.jpg
│   └── ...
└── highlight_17999999999999999/
    └── ...
```

## Notes

- `browser/`, `output/`, and `.venv/` are git-ignored — they're
  machine-specific and can get large, so they aren't tracked in this
  repo.
- This project only automates clicking Turbo Downloader's own buttons;
  it does not scrape or reverse-engineer Instagram directly.

## License

MIT (or your preferred license — add a `LICENSE` file if you want this
to be explicit).
