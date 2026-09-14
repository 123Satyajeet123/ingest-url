#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf4llm"]
# ///
"""PDF to page-marked markdown for ingest.py, in its own environment so non-PDF work never installs it.

pdf.py <file.pdf> <images_dir>   prints JSON {"pages": N, "images": M, "text": "..."}
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    import pymupdf4llm

    source, images = sys.argv[1], Path(sys.argv[2])
    pages = pymupdf4llm.to_markdown(source, page_chunks=True, write_images=True, image_path=str(images))
    text = "".join(f"\n\n--- page {n} ---\n\n{page['text']}" for n, page in enumerate(pages, 1))
    print(json.dumps({"pages": len(pages), "images": len(list(images.glob("*"))) if images.exists() else 0, "text": text}))


if __name__ == "__main__":
    main()
