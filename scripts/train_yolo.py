from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import RTDETR, YOLO


ROOT = Path(__file__).resolve().parents[1]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Treina o detector YOLO de pecas.")
    parser.add_argument("--model", default="yolo11n.pt", help="Checkpoint base do YOLO.")
    parser.add_argument("--data", default=str(ROOT / "data" / "mahjong_soul.yaml"))
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=1600)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default=None, help="Ex.: 0 para GPU, cpu para CPU.")
    parser.add_argument("--patience", type=int, default=100, help="Epocas sem melhora antes de parar. 0 desativa.")
    parser.add_argument("--project", default=str(ROOT / "runs" / "detect"))
    parser.add_argument("--name", default="mahjong_soul_tiles")
    parser.add_argument("--exist-ok", action="store_true")
    parser.add_argument("--skip-test", action="store_true", help="Nao roda avaliacao final no split test.")
    parser.add_argument("--test-split", default="test", help="Split usado para avaliacao final apos o treino.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_class = RTDETR if "rtdetr" in args.model.lower() else YOLO
    model = model_class(args.model)
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        patience=args.patience,
        project=args.project,
        name=args.name,
        exist_ok=args.exist_ok,
    )
    run_dir = Path(getattr(getattr(model, "trainer", None), "save_dir", Path(args.project) / args.name))
    best_path = Path(getattr(getattr(model, "trainer", None), "best", run_dir / "weights" / "best.pt"))
    if not best_path.exists():
        best_path = run_dir / "weights" / "best.pt"

    if args.skip_test:
        print("[TEST] Avaliacao final no split test desativada por --skip-test.", flush=True)
        return
    if not best_path.exists():
        print(f"[TEST] best.pt nao encontrado; teste final ignorado: {best_path}", flush=True)
        return
    if not split_has_images(Path(args.data), args.test_split):
        print(f"[TEST] Split {args.test_split!r} sem imagens locais; teste final ignorado.", flush=True)
        return

    print(f"[TEST] Avaliando best.pt no split {args.test_split!r}: {best_path}", flush=True)
    test_model = model_class(str(best_path))
    test_model.val(
        data=args.data,
        split=args.test_split,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        project=str(run_dir),
        name=args.test_split,
        exist_ok=True,
    )


def split_has_images(data_yaml: Path, split: str) -> bool:
    paths = simple_dataset_yaml_paths(data_yaml)
    dataset_root = paths.get("path")
    split_value = paths.get(split)
    if dataset_root is None or split_value is None:
        return True

    split_path = split_value if split_value.is_absolute() else dataset_root / split_value
    if not split_path.exists():
        return False
    if split_path.is_file():
        return True
    return any(path.suffix.lower() in IMAGE_EXTENSIONS for path in split_path.rglob("*"))


def simple_dataset_yaml_paths(data_yaml: Path) -> dict[str, Path]:
    if not data_yaml.exists():
        return {}
    values: dict[str, Path] = {}
    for raw_line in data_yaml.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key not in {"path", "train", "val", "test"}:
            continue
        value = raw_value.strip().strip("'\"")
        if not value or value.startswith("["):
            continue
        path = Path(value)
        if key == "path":
            values[key] = path if path.is_absolute() else (data_yaml.parent / path).resolve()
        else:
            values[key] = path
    return values


if __name__ == "__main__":
    main()
