#!/usr/bin/env bash
set -euo pipefail

MODEL_ID="auto"
EXPORT_DIR=""
OUTPUT_DIR=""
LANG="${OCR_LANG:-heb+eng}"
TESSDATA_DIR="${OCR_TESSDATA:-/opt/homebrew/share/tessdata}"

usage() {
  echo "Usage: $0 --export-dir <dir> --output-dir <dir> --model-id <id> [--lang heb+eng] [--tessdata-dir /path/to/tessdata]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --export-dir)
      EXPORT_DIR="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --model-id)
      MODEL_ID="$2"
      shift 2
      ;;
    --lang)
      LANG="$2"
      shift 2
      ;;
    --tessdata-dir)
      TESSDATA_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$EXPORT_DIR" ]]; then
  echo "Missing --export-dir" >&2
  exit 1
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="${EXPORT_DIR}/model"
fi

mkdir -p "$OUTPUT_DIR"
TRAIN_DIR="${EXPORT_DIR}/train"
if [[ ! -d "$TRAIN_DIR" ]]; then
  echo "No approved training folder was found at $TRAIN_DIR" >&2
  echo "Run the reviewer approval flow first, then export the approved examples." >&2
  exit 1
fi

shopt -s nullglob
pngs=("$TRAIN_DIR"/*.png)
if [[ ${#pngs[@]} -eq 0 ]]; then
  echo "No PNG images were found in $TRAIN_DIR" >&2
  exit 1
fi

for img in "${pngs[@]}"; do
  base="${img%.png}"
  if [[ ! -f "${base}.box" ]]; then
    echo "Creating training box for: $img"
    tesseract "$img" "$base" --psm 7 makebox >/dev/null 2>&1 || {
      echo "Failed to generate .box data for $img" >&2
      exit 1
    }
  fi
done

box_files=("$TRAIN_DIR"/*.box)
if [[ ${#box_files[@]} -gt 0 ]]; then
  echo "Extracting character set from ${#box_files[@]} box files"
  unicharset_extractor "${box_files[@]}" > "$OUTPUT_DIR/unicharset" 2>/dev/null || {
    echo "Character-set extraction finished with warnings; the training set is still usable for review." >&2
  }
fi

cat > "$OUTPUT_DIR/training_manifest.json" <<EOF
{
  "model_id": "$MODEL_ID",
  "lang": "$LANG",
  "tessdata_dir": "$TESSDATA_DIR",
  "export_dir": "$EXPORT_DIR",
  "approved_png_count": ${#pngs[@]},
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo "Prepared Tesseract training assets in $OUTPUT_DIR"
echo "Next step: add more approved examples, then run a full lstmtraining workflow in a Tesseract training environment."

echo "Ready for fine-tuning when enough examples are approved."
exit 0
