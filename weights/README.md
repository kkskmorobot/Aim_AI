# 模型权重

此目录保留给本地权重，`*.pt` 等模型文件不会进入 Git。

运行入口优先查找 `weights/aim.pt`，也兼容原有的
`Aim_Bot_Runs/5080_training/weights/aim.pt` 和 `best.pt`。

也可以显式指定：

```powershell
python main.py --model "D:/models/aim.pt"
```

请使用检测类别顺序为 `0: Body`、`1: Head` 的自训练模型。通用的
`yolo11s.pt` 是训练基础模型，不能替代本项目专用检测权重。

训练基础模型 `yolo11n.pt` / `yolo11s.pt` 可以放在本目录；训练脚本会先查找本地文件，
未找到时由 Ultralytics 按模型名称加载或下载。
