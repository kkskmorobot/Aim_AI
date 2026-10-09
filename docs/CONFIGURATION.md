# 配置与运行限制

`--config` 支持 `aim_ai/runtime.py` 中 `Config` 的字段。文件可只指定要覆盖的字段；
未知字段、类型错误、无效 FOV 和会漏掉屏幕区域的分块步长会在启动前报错。

| 字段 | 含义 |
| --- | --- |
| `expected_screen_width` / `expected_screen_height` | 预期物理分辨率，用于提示；实际尺寸仍从 Win32 读取 |
| `expected_refresh_rate` | 控制台显示的预期刷新率，不会修改显示设置 |
| `aimlab_vertical_fov` | 游戏实际垂直 FOV，单位度 |
| `aimlab_look_increment_degrees` | 每鼠标计数角增量，用于估算中心响应 |
| `aimlab_mouse_dpi` | 配置记录值，不会修改鼠标硬件 DPI |
| `search_tile_width` / `search_tile_height` | 精细搜索和跟踪块的物理像素尺寸 |
| `search_stride_x` / `search_stride_y` | 全屏分块间距，不能大于对应块尺寸 |
| `inference_size` / `coarse_inference_size` | 单块精细推理与整屏粗推理尺寸 |
| `confidence` | 检测置信度门槛 |
| `activation_key` / `quit_key` | Win32 虚拟键码，默认 Shift=16、Q=81 |

默认参数来自 2026-07-20 的本机读取结果：3840×2160、160Hz、150% Windows 缩放、
Aim Lab CS2 配置、X/Y 灵敏度 0.88、垂直 FOV 73.7398°、角增量 0.044°/count、
游戏内记录 DPI=800。它们是默认配置快照，不能作为其他电脑的自动检测结果。

程序在读取屏幕尺寸前启用 DPI 感知，因此屏幕坐标使用物理像素。
其当前捕获逻辑针对主显示器全屏场景；窗口模式、非主屏、ADS 视角、游戏参数变化需要重新匹配配置。
JSON 示例没有加入账号、个人路径或认证信息。

历史的 RTX 5080 空白图基准：单块推理约 8.6ms、整屏粗搜约 9.8ms、六块精搜约 37.6ms。
这是 2026-07-20 的纯推理测量，未包含实际截屏、游戏渲染和输入生效延迟，也不是本次整理的性能承诺。
