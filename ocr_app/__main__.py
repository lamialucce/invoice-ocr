"""Command line: python -m ocr_app <file> [<file> ...] [--data data] [--reprocess]"""
import argparse
import json
from pathlib import Path

from . import pipeline, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--data", default="data")
    ap.add_argument("--reprocess", action="store_true")
    a = ap.parse_args()
    for f in a.files:
        doc_dir = pipeline.process(Path(f), Path(a.data), reprocess=a.reprocess)
        report.write(doc_dir)
        report.write_index(Path(a.data))
        fields = json.loads((doc_dir / "fields.json").read_text())
        print(f"{f} -> {doc_dir}  pages={[p['method'] for p in fields['pages']]} "
              f"lines={len(fields['lines'])} warnings={fields['warnings']}")


if __name__ == "__main__":
    main()
