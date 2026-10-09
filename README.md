# Aim AI

基于 YOLO 的 Aim Lab 视觉目标检测、单目标跟踪与鼠标控制实验项目。
运行平台为 Windows，使用 MSS 捕获屏幕、Ultralytics 推理和 Win32 相对鼠标输入。

当前实现包含整屏粗搜索、重叠分块精细搜索、动态目标区域跟踪、鼠标响应在线标定和移动目标提前量。
检测与控制算法沿用整理前的独立新版；本次整理主要调整入口、配置、路径和仓库文件布局。

## 目录

```text
Aim_AI/
├── aim_ai/                    # 运行代码包
│   ├── runtime.py             # 检测、跟踪、标定和控制
│   └── __main__.py            # python -m aim_ai
├── main.py                    # 推荐运行入口
├── training.py                # 参数化训练入口
├── new aimbot.py               # 原新版入口的兼容启动文件
├── Train.py                   # 原训练入口的兼容启动文件
├── configs/
│   ├── profile.example.json   # 显示与游戏参数示例
│   └── dataset.yaml           # 可移植的数据集配置
├── weights/                   # 本地模型，Git 只收录说明文件
├── tests/                     # 配置、路径和分块检查
├── docs/                      # 运行配置和上传说明
├── requirements.txt
└── pyproject.toml
```

本地还保留 `Aimbot.py`、`local_archive/`、`data/`、`Aim_Bot_Runs/`、`runs/` 和 `.venv/`。
它们已被 Git 忽略；历史脚本与备份的位置见 [整理说明](docs/REPOSITORY.md)。

## 安装

Python 3.10+；当前项目实际环境为 Python 3.13.7、PyTorch 2.8.0+cu129、RTX 5080。
首次运行需要本项目的自训练权重，仓库不包含模型或数据集。

在 PowerShell 7 的仓库根目录运行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果电脑没有安装 Python 3.13，使用已安装的 Python 3.10+ 创建环境。
已有可用虚拟环境时直接复用，不必重建。

`requirements.txt` 记录的是当前可用环境的直接依赖版本。
GPU 用户需要匹配硬件与驱动的 PyTorch CUDA 构建；普通 PyPI 安装不保证得到与当前机器相同的 CUDA 版本。
当前机器使用的安装源示例：

```powershell
.\.venv\Scripts\python.exe -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu129
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

可选的可编辑安装（注册 `aim-ai` 命令）：

```powershell
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

## 运行

推荐将自训练检测权重放到 `weights/aim.pt`。现有项目也会自动查找原有
`Aim_Bot_Runs/5080_training/weights/aim.pt`，以及同目录下的 `best.pt`。
其他位置使用 `--model` 指定。

```powershell
.\.venv\Scripts\python.exe main.py
.\.venv\Scripts\python.exe main.py --model "D:/models/aim.pt"
.\.venv\Scripts\python.exe -m aim_ai --help
```

按住 **Shift** 启用，松开暂停；按 **Q** 退出。程序预热模型后才开始循环。
当前实现使用主显示器物理坐标，运行时建议使用主屏全屏 Aim Lab。

个人配置复制到被 Git 忽略的文件中：

```powershell
Copy-Item configs/profile.example.json configs/local.json
.\.venv\Scripts\python.exe main.py --config configs/local.json
```

示例沿用原项目的 4K/160Hz 参数，其他机器应按实际设置修改。
参数含义和当前限制见 [配置说明](docs/CONFIGURATION.md)。

## 训练

将 YOLO 格式数据放入 `data/train/images`、`data/valid/images` 和 `data/test/images`，
相应标签放入各划分的 `labels` 目录。默认类别顺序为 `0: Body`、`1: Head`。
`configs/dataset.yaml` 中 `path` 相对于配置文件自身解析，支持更换电脑或从其他工作目录启动。

```powershell
.\.venv\Scripts\python.exe training.py --data configs/dataset.yaml --device 0
.\.venv\Scripts\python.exe training.py --epochs 100 --imgsz 640 --batch 32
```

默认输出至 `Aim_Bot_Runs/5080_training`；同名目录存在时由 Ultralytics 创建新运行目录，
只有显式传入 `--exist-ok` 才复用同名目录。
训练生成 `best.pt` 后，复制到 `weights/aim.pt` 或用 `--model` 指定该文件。
基础模型 `yolo11s.pt` 优先从 `weights/` 查找，未找到时可能触发 Ultralytics 下载。

## 检查与上传

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git status --short
git status --short --ignored
```

GitHub Actions 会检查 Python 语法、命令行帮助和单元测试。它不启动瞄准循环，不训练模型。
上传流程见 [GitHub 上传说明](docs/REPOSITORY.md)。

## 数据与许可

本地数据集导出说明标注来源为 Roboflow AimLabs 数据集、许可为 CC BY 4.0；
源码仓库只记录来源，不收录数据、模型或训练图片。详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
本次整理未替项目所有者选择源码开源许可证；公开发布前请确定源码授权方式。
