from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
ROOT = Path(__file__).resolve().parents[1]
GENERATED_MARKER = "_red5dup"
RED_FIVE_CLASSES = ("man_5_red", "pin_5_red", "sou_5_red")
REGULAR_FIVE_CLASSES = ("man_5", "pin_5", "sou_5")


@dataclass
class CandidateImage:
    image_path: Path
    label_path: Path
    red_counts: Counter[int]

    @property
    def distinct_reds(self) -> int:
        return len(self.red_counts)

    @property
    def total_reds(self) -> int:
        return sum(self.red_counts.values())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Duplica imagens raras com 5 vermelho no split train.")
    parser.add_argument("--data", default="data/mahjong_soul.yaml", help="YAML do dataset.")
    parser.add_argument("--split", default="train", help="Split usado para oversampling. Use train.")
    parser.add_argument("--target-ratio", type=float, default=0.75, help="Alvo relativo a mediana dos 5 normais.")
    parser.add_argument("--max-multiplier", type=float, default=3.0, help="Limite do alvo vs classe red mais comum.")
    parser.add_argument("--max-copies-per-image", type=int, default=3)
    parser.add_argument("--max-new-image-ratio", type=float, default=0.25, help="Maximo de novas imagens vs imagens originais.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-existing", action="store_true", help="Nao remove copias red5dup antigas antes de balancear.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = balance_red_fives(
        Path(args.data),
        split=args.split,
        target_ratio=args.target_ratio,
        max_multiplier=args.max_multiplier,
        max_copies_per_image=args.max_copies_per_image,
        max_new_image_ratio=args.max_new_image_ratio,
        dry_run=args.dry_run,
        clean_existing=not args.keep_existing,
    )
    print(format_report(report), flush=True)


def balance_red_fives(
    data_yaml: Path,
    split: str = "train",
    target_ratio: float = 0.75,
    max_multiplier: float = 3.0,
    max_copies_per_image: int = 3,
    max_new_image_ratio: float = 0.25,
    dry_run: bool = False,
    clean_existing: bool = True,
) -> dict[str, Any]:
    data_yaml = data_yaml.resolve()
    dataset_root, names = read_dataset_yaml(data_yaml)
    class_to_id = {name: class_id for class_id, name in names.items()}
    missing = [name for name in (*RED_FIVE_CLASSES, *REGULAR_FIVE_CLASSES) if name not in class_to_id]
    if missing:
        return {"enabled": False, "reason": f"classes ausentes no YAML: {missing}"}

    image_dir = dataset_root / "images" / split
    label_dir = dataset_root / "labels" / split
    if not image_dir.exists() or not label_dir.exists():
        return {"enabled": False, "reason": f"split {split!r} nao encontrado em {dataset_root}"}

    removed = cleanup_generated_files(image_dir, label_dir) if clean_existing and not dry_run else 0
    candidates, class_counts, original_image_count = scan_split(image_dir, label_dir, class_to_id)
    red_ids = [class_to_id[name] for name in RED_FIVE_CLASSES]
    regular_ids = [class_to_id[name] for name in REGULAR_FIVE_CLASSES]
    red_counts_before = {names[class_id]: class_counts[class_id] for class_id in red_ids}
    regular_counts = [class_counts[class_id] for class_id in regular_ids if class_counts[class_id] > 0]
    if not candidates:
        return {
            "enabled": True,
            "created": 0,
            "removed_old": removed,
            "reason": "nenhuma imagem com 5 vermelho no split",
            "red_counts_before": red_counts_before,
        }

    base_target = round(median(regular_counts) * max(0.0, target_ratio)) if regular_counts else max(red_counts_before.values())
    max_current_red = max(red_counts_before.values()) if red_counts_before else 0
    target = max(max_current_red, min(base_target, round(max_current_red * max(1.0, max_multiplier))))
    target = max(1, int(target))
    max_new_images = max(0, round(original_image_count * max(0.0, max_new_image_ratio)))

    copies_by_source: Counter[Path] = Counter()
    created_files: list[dict[str, str]] = []
    simulated_counts = Counter(class_counts)
    created = 0

    ordered_candidates = sorted(
        candidates,
        key=lambda item: (-item.distinct_reds, -item.total_reds, item.image_path.name),
    )
    while created < max_new_images and any(simulated_counts[class_id] < target for class_id in red_ids):
        candidate = choose_candidate(ordered_candidates, red_ids, simulated_counts, target, copies_by_source, max_copies_per_image)
        if candidate is None:
            break
        copy_index = copies_by_source[candidate.image_path] + 1
        copies_by_source[candidate.image_path] = copy_index
        for class_id, amount in candidate.red_counts.items():
            simulated_counts[class_id] += amount
        created += 1
        if dry_run:
            continue
        new_stem = f"{candidate.image_path.stem}{GENERATED_MARKER}{copy_index:02d}"
        new_image = candidate.image_path.with_name(f"{new_stem}{candidate.image_path.suffix}")
        new_label = candidate.label_path.with_name(f"{new_stem}.txt")
        shutil.copy2(candidate.image_path, new_image)
        shutil.copy2(candidate.label_path, new_label)
        created_files.append({"image": str(new_image), "label": str(new_label)})

    red_counts_after = {names[class_id]: simulated_counts[class_id] for class_id in red_ids}
    report = {
        "enabled": True,
        "dry_run": dry_run,
        "split": split,
        "target": target,
        "target_ratio": target_ratio,
        "max_multiplier": max_multiplier,
        "max_copies_per_image": max_copies_per_image,
        "max_new_images": max_new_images,
        "original_images": original_image_count,
        "candidate_images": len(candidates),
        "removed_old": removed,
        "created": created,
        "red_counts_before": red_counts_before,
        "red_counts_after": red_counts_after,
        "regular_five_counts": {names[class_id]: class_counts[class_id] for class_id in regular_ids},
        "copies_by_source": {path.name: count for path, count in copies_by_source.items()},
        "created_files": created_files[:20],
    }
    if not dry_run:
        report_path = dataset_root / f"red_five_balance_{split}.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def choose_candidate(
    candidates: list[CandidateImage],
    red_ids: list[int],
    counts: Counter[int],
    target: int,
    copies_by_source: Counter[Path],
    max_copies_per_image: int,
) -> CandidateImage | None:
    deficits = {class_id: max(0, target - counts[class_id]) for class_id in red_ids}
    best: tuple[float, int, int, str, CandidateImage] | None = None
    for candidate in candidates:
        if copies_by_source[candidate.image_path] >= max_copies_per_image:
            continue
        gain = sum(min(deficits.get(class_id, 0), amount) for class_id, amount in candidate.red_counts.items())
        if gain <= 0:
            continue
        key = (
            float(gain),
            candidate.distinct_reds,
            candidate.total_reds,
            "".join(chr(255 - ord(char) % 255) for char in candidate.image_path.name),
            candidate,
        )
        if best is None or key[:4] > best[:4]:
            best = key
    return best[4] if best else None


def scan_split(image_dir: Path, label_dir: Path, class_to_id: dict[str, int]) -> tuple[list[CandidateImage], Counter[int], int]:
    red_ids = {class_to_id[name] for name in RED_FIVE_CLASSES}
    class_counts: Counter[int] = Counter()
    candidates: list[CandidateImage] = []
    image_paths = [
        path for extension in IMAGE_EXTENSIONS for path in image_dir.glob(f"*{extension}")
        if GENERATED_MARKER not in path.stem
    ]
    for image_path in sorted(image_paths):
        label_path = label_dir / f"{image_path.stem}.txt"
        if not label_path.exists():
            continue
        ids = label_class_ids(label_path)
        class_counts.update(ids)
        red_counts = Counter(class_id for class_id in ids if class_id in red_ids)
        if red_counts:
            candidates.append(CandidateImage(image_path, label_path, red_counts))
    return candidates, class_counts, len(image_paths)


def cleanup_generated_files(image_dir: Path, label_dir: Path) -> int:
    removed = 0
    for directory in (image_dir, label_dir):
        for path in directory.glob(f"*{GENERATED_MARKER}*"):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed


def label_class_ids(label_path: Path) -> list[int]:
    ids: list[int] = []
    for raw_line in label_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        try:
            ids.append(int(float(parts[0])))
        except ValueError:
            continue
    return ids


def read_dataset_yaml(data_yaml: Path) -> tuple[Path, dict[int, str]]:
    dataset_root = data_yaml.parent
    names: dict[int, str] = {}
    in_names = False
    for raw_line in data_yaml.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "names:":
            in_names = True
            continue
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        value = raw_value.strip().strip("'\"")
        if key == "path":
            path = Path(value)
            if path.is_absolute():
                dataset_root = path
            elif (ROOT / path).exists():
                dataset_root = (ROOT / path).resolve()
            else:
                dataset_root = (data_yaml.parent / path).resolve()
            in_names = False
            continue
        if in_names and key.isdigit():
            names[int(key)] = value
    return dataset_root, names


def format_report(report: dict[str, Any]) -> str:
    if not report.get("enabled"):
        return f"[BALANCE] desativado: {report.get('reason')}"
    return (
        "[BALANCE] red fives | "
        f"split={report.get('split')} target={report.get('target')} "
        f"created={report.get('created')}/{report.get('max_new_images')} "
        f"old_removed={report.get('removed_old')} "
        f"before={report.get('red_counts_before')} "
        f"after={report.get('red_counts_after')}"
    )


if __name__ == "__main__":
    main()
