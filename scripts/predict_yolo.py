from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import RTDETR, YOLO


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Testa um modelo YOLO treinado.")
    parser.add_argument(
        "--weights",
        default=str(ROOT / "runs" / "detect" / "mahjong_soul_tiles" / "weights" / "best.pt"),
        help="Caminho para best.pt ou last.pt.",
    )
    parser.add_argument(
        "--source",
        default=str(ROOT / "dataset" / "raw"),
        help="Imagem, pasta de imagens ou video para testar.",
    )
    parser.add_argument("--imgsz", type=int, default=1600)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default="0")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model = RTDETR(args.weights) if "rtdetr" in args.weights.lower() else YOLO(args.weights)
    model.predict(
        source=args.source,
        imgsz=args.imgsz,
        conf=args.conf,
        device=args.device,
        save=True,
        project=str(ROOT / "runs" / "predict"),
        name="mahjong_soul_tiles",
        exist_ok=True,
    )


if __name__ == "__main__":
    main()
