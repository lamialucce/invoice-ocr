#!/usr/bin/env python3
"""Generate a QA summary for a reference-vs-candidate document set."""

import argparse
import json
from pathlib import Path

from ocr_app.qa import render_markdown, summarize_directory


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference_dir")
    ap.add_argument("candidate_dir")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    summary = summarize_directory(a.reference_dir, a.candidate_dir)
    markdown = render_markdown(summary)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        (out.with_suffix(".json")).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
