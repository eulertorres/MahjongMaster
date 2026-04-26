from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "colab_packages" / "mahjongmaster_colab_dataset.zip"
INCLUDED_EXTENSIONS = {".yaml", ".yml", ".png", ".jpg", ".jpeg", ".txt"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera um pacote de dataset para treinar no Google Colab.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Caminho do .zip gerado.")
    parser.add_argument("--include-raw", action="store_true", help="Inclui dataset/raw no pacote.")
    return parser.parse_args()


def should_include(path: Path, include_raw: bool) -> bool:
    if path.suffix.lower() not in INCLUDED_EXTENSIONS:
        return False
    if not include_raw and path.parts[-2:] and "raw" in path.parts:
        return False
    return True


def add_tree(zip_file: ZipFile, directory: Path, include_raw: bool) -> int:
    count = 0
    if not directory.exists():
        return count
    for path in directory.rglob("*"):
        if not path.is_file() or not should_include(path, include_raw):
            continue
        zip_file.write(path, path.relative_to(ROOT))
        count += 1
    return count


def main() -> None:
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with ZipFile(output, "w", ZIP_DEFLATED) as zip_file:
        file_count = 0
        file_count += add_tree(zip_file, ROOT / "data", args.include_raw)
        file_count += add_tree(zip_file, ROOT / "dataset", args.include_raw)

    print(f"Pacote criado: {output}")
    print(f"Arquivos incluidos: {file_count}")


if __name__ == "__main__":
    main()
