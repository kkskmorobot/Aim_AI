# 整理与 GitHub 上传

## 本地保留内容

- `Aimbot.py`：旧稳定版本原样留在根目录，被 Git 忽略。
- `local_archive/legacy/`：原 `ab.py`、`D.py` 和 `aimlab_realtime.py`；含历史本机路径，仅供回退。
- `local_archive/backups/`：历史 `.bak-*`，本次整理前的完整 `new aimbot.py` 和 `Train.py`。
- `weights/`：基础模型 `yolo11n.pt` / `yolo11s.pt`，模型文件被 Git 忽略。
- `data/`、`Aim_Bot_Runs/`、`runs/`、`.venv/`、`.idea/`：保留原位置，被 Git 忽略。

`new aimbot.py` 和 `Train.py` 现在是兼容启动文件，可继续使用既有运行配置。
推荐新配置分别使用 `main.py` 和 `training.py`。

## 提交前检查

使用 Git 上传整个项目目录，Git 会按 `.gitignore` 排除本地文件。
不要通过网页直接拖拽整个本地目录，否则可能绕过这些忽略规则。

```powershell
git status --short
git status --short --ignored
git add .
git diff --cached --stat
git diff --cached --name-only
```

暂存列表应该只有源码、文档、配置示例和检查脚本，不能包含 `.venv`、`data`、模型权重、
截图、日志、`.env` 或 `local_archive`。

确认后创建提交：

```powershell
git commit -m "Organize Aim AI project for GitHub"
```

在 GitHub 新建空仓库，将下面的示例地址换成自己的仓库地址：

```powershell
git remote add origin https://github.com/YOUR_USERNAME/Aim_AI.git
git push -u origin main
```

本次整理仅初始化本地仓库，不创建 GitHub 远程仓库，不提交或推送。
如果以后要分享自训练模型，请单独准备 Release 附件或模型存储，并标明模型/数据来源及使用许可。
当前仓库不替用户选择源码开源许可证，也不将数据集的 CC BY 4.0 自动套用到源码上。
