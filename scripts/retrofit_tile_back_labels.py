from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtGui import QColor, QImage


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "dataset"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
TILE_BACK_CLASS_ID = 37


@dataclass
class RetrofitStats:
    files_seen: int = 0
    labels_seen: int = 0
    labels_changed: int = 0
    files_changed: int = 0
    images_missing: int = 0


def is_tile_back_orange(color: QColor) -> bool:
    hue = color.hsvHue()
    return (
        15 <= hue <= 45
        and color.saturation() >= 120
        and color.value() >= 145
        and color.red() >= 180
        and color.green() >= 80
        and color.blue() <= 100
    )


def orange_ratio(image: QImage, x1: int, y1: int, x2: int, y2: int) -> float:
    x1 = max(0, min(image.width() - 1, x1))
    y1 = max(0, min(image.height() - 1, y1))
    x2 = max(x1 + 1, min(image.width(), x2))
    y2 = max(y1 + 1, min(image.height(), y2))
    width = x2 - x1
    height = y2 - y1
    step = max(1, min(width, height) // 28)
    hits = 0
    sampled = 0
    for y in range(y1, y2, step):
        for x in range(x1, x2, step):
            sampled += 1
            if is_tile_back_orange(image.pixelColor(x, y)):
                hits += 1
    return hits / sampled if sampled else 0.0


def image_for_label(label_path: Path) -> Path | None:
    split = label_path.parent.name
    image_dir = DATASET_DIR / "images" / split
    for extension in IMAGE_EXTENSIONS:
        candidate = image_dir / f"{label_path.stem}{extension}"
        if candidate.exists():
            return candidate
    return None


def retrofit_label_file(label_path: Path, apply: bool, backup: bool, threshold: float) -> tuple[int, int]:
    image_path = image_for_label(label_path)
    if image_path is None:
        return 0, -1

    image = QImage(str(image_path))
    if image.isNull():
        return 0, -1

    lines = label_path.read_text(encoding="utf-8").splitlines()
    rewritten: list[str] = []
    changed = 0
    seen = 0

    for line in lines:
        parts = line.split()
        if len(parts) < 5:
            rewritten.append(line)
            continue

        seen += 1
        try:
            cx, cy, bw, bh = (float(parts[index]) for index in range(1, 5))
        except ValueError:
            rewritten.append(line)
            continue

        x1 = round((cx - bw / 2) * image.width())
        y1 = round((cy - bh / 2) * image.height())
        x2 = round((cx + bw / 2) * image.width())
        y2 = round((cy + bh / 2) * image.height())

        if orange_ratio(image, x1, y1, x2, y2) >= threshold and parts[0] != str(TILE_BACK_CLASS_ID):
            parts[0] = str(TILE_BACK_CLASS_ID)
            changed += 1
            rewritten.append(" ".join(parts))
        else:
            rewritten.append(line)

    if changed and apply:
        if backup:
            backup_path = label_path.with_suffix(label_path.suffix + ".bak_tile_back")
            if not backup_path.exists():
                shutil.copy2(label_path, backup_path)
        label_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    return changed, seen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reclassifica boxes ja anotadas de pecas viradas laranjas como tile_back."
    )
    parser.add_argument("--dataset", type=Path, default=DATASET_DIR, help="Pasta dataset.")
    parser.add_argument("--apply", action="store_true", help="Grava as labels alteradas.")
    parser.add_argument("--backup", action="store_true", help="Cria .bak_tile_back antes de alterar.")
    parser.add_argument("--threshold", type=float, default=0.48, help="Razao minima de pixels laranja na box.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global DATASET_DIR
    DATASET_DIR = args.dataset.resolve()

    stats = RetrofitStats()
    for label_path in sorted((DATASET_DIR / "labels").glob("*/*.txt")):
        stats.files_seen += 1
        changed, seen = retrofit_label_file(label_path, args.apply, args.backup, args.threshold)
        if seen < 0:
            stats.images_missing += 1
            continue
        stats.labels_seen += seen
        stats.labels_changed += changed
        if changed:
            stats.files_changed += 1
            action = "alterado" if args.apply else "alteraria"
            print(f"{action}: {label_path.relative_to(DATASET_DIR)} ({changed} box)")

    mode = "APLICADO" if args.apply else "DRY-RUN"
    print(
        f"{mode}: {stats.labels_changed}/{stats.labels_seen} labels em "
        f"{stats.files_changed}/{stats.files_seen} arquivos; imagens ausentes/invalidas: {stats.images_missing}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
