"""Train a custom YOLO model using an explicitly supplied local dataset."""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from ultralytics import YOLO
import yaml


def prepare_dataset_config(source: Path) -> dict:
    """Resolve a dataset root relative to its YAML, independently of Ultralytics settings."""
    values = yaml.safe_load(source.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("数据集配置必须是 YAML 对象")
    dataset_root = Path(values.get("path") or ".").expanduser()
    if not dataset_root.is_absolute():
        dataset_root = source.parent / dataset_root
    values["path"] = str(dataset_root.resolve())
    return values


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="训练 Aim Lab 自定义 YOLO 检测模型")
    parser.add_argument("--data", type=Path, default=root / "configs" / "dataset.yaml")
    parser.add_argument("--model", default="yolo11s.pt", help="基础模型名称或本地权重路径")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--device", default="0", help="GPU 编号或 cpu")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--project", type=Path, default=root / "Aim_Bot_Runs")
    parser.add_argument("--name", default="5080_training")
    parser.add_argument("--exist-ok", action="store_true", help="允许复用同名训练输出目录")
    args = parser.parse_args()

    data = args.data.expanduser().resolve()
    if not data.is_file():
        parser.error(f"数据集配置不存在: {data}")
    dataset_config = prepare_dataset_config(data)
    model_path = Path(args.model).expanduser()
    if not model_path.is_file():
        local_base = root / "weights" / args.model
        if local_base.is_file():
            model_path = local_base
    model = YOLO(str(model_path))
    with TemporaryDirectory(prefix="aim-ai-dataset-") as temporary:
        normalized_data = Path(temporary) / "dataset.yaml"
        normalized_data.write_text(yaml.safe_dump(dataset_config, allow_unicode=True), encoding="utf-8")
        model.train(
            data=str(normalized_data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
            device=args.device, workers=args.workers,
            project=str(args.project.expanduser().resolve()), name=args.name,
            exist_ok=args.exist_ok,
        )


if __name__ == "__main__":
    main()
