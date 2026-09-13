# Instagram Highlight Capture

A Playwright-based tool that automates downloading Instagram Story
Highlights, using the **Turbo Downloader** Chrome extension to do the
actual media fetching. The script drives a real (persistent) Chromium
profile, opens each highlight, clicks Turbo's download buttons for
you, watches for the files to land, automatically fixes their file
extensions, retries anything that fails, and sorts everything into a
clean `output/` folder — one subfolder per highlight.

## Features

- **Batch mode**: pass one URL, several URLs, or a text file with one
  URL per line (or mix all three).
- **Custom output folder names**: label each highlight (`#Vintage`,
  `#Classic`, ...) so its files land in `output/Vintage/` instead of
  an ID-named folder — either inline in the input file or via `-fn` on
  the command line.
- Prefers Turbo's **"Download all stories"** button; falls back to
  **"Download current story"** only if the bulk option isn't
  available.
- Detects when Turbo has actually finished writing files (waits for a
  quiet period with no new files, not just the first file that
  appears) before moving on.
- Automatically fixes files that Turbo saves with **no extension** by
  sniffing their real file type (mp4, webm, jpg, png, gif, webp) and
  renaming them correctly.
- **Automatic retries**: if a URL fails (extension hiccup, rate
  limiting, cold start, etc.), the script automatically re-attempts it
  after processing the rest of the batch — configurable via `-r`.
- Clean, color-coded terminal logging with a full final summary:
  files saved, formats auto-fixed, which URLs succeeded on retry,
  and which still need a manual look.
- Reuses a persistent browser profile, so you only log in to
  Instagram once.

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

# Give it a custom output folder name instead of the highlight ID
python capture.py "https://www.instagram.com/stories/highlights/18106247134439092/" -fn Vintage

# A URL plus a text file of more URLs (one per line)
python capture.py "https://www.instagram.com/stories/highlights/18106247134439092/" -i inp.txt

# Just a text file of URLs
python capture.py -i inp.txt

# Retry failed URLs up to 3 extra times instead of the default 1
python capture.py -i inp.txt -r 3
```

`inp.txt` format — one Instagram URL per line, optionally followed by
`#Label` to control the output folder name; blank lines and lines
starting with `#` are ignored:

```
https://www.instagram.com/stories/highlights/18296231302149109/	#Classic
https://www.instagram.com/stories/highlights/18001540943128647/	#Vintage
https://www.instagram.com/stories/highlights/18014054233841781/ #Fantasy
```

On first run, if Instagram isn't logged in inside the automated
browser window, the script pauses and asks you to log in manually —
after that it remembers the session (stored in `browser/profile/`).

## Output

```
output/
├── Classic/
│   ├── clip_01.mp4
│   └── photo_01.jpg
├── Vintage/
│   └── ...
└── highlight_18014054233841781/   # no #Label given -> falls back to the ID
    └── ...
```

## Retries

If a URL fails on the first pass, the script automatically retries
just the failed ones (with a short cooldown, in case it was rate
limiting) up to `-r N` extra times (default: `1`). The final summary
clearly reports what succeeded on the first try, what recovered on a
retry, and what's still failing after every attempt.

## Notes

- `browser/`, `output/`, and `.venv/` are git-ignored — they're
  machine-specific and can get large, so they aren't tracked in this
  repo.
- This project only automates clicking Turbo Downloader's own buttons;
  it does not scrape or reverse-engineer Instagram directly.

## License

MIT (or your preferred license — add a `LICENSE` file if you want this
to be explicit).