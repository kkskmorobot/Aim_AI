from __future__ import annotations

import ctypes
import argparse
import json
import math
import time
from collections import deque
from dataclasses import dataclass, fields
from pathlib import Path

import mss
import numpy as np
import torch
import win32api
import win32con
from ultralytics import YOLO


@dataclass(frozen=True)
class Config:
    # 已读取的当前 Aim Lab / 显示器参数。
    expected_screen_width: int = 3840
    expected_screen_height: int = 2160
    expected_refresh_rate: int = 160
    aimlab_vertical_fov: float = 73.73979187011719
    aimlab_look_increment_degrees: float = 0.044
    aimlab_mouse_dpi: int = 800

    # 4K 全屏使用 3x2 重叠分块搜索。锁定后每帧只处理目标附近的一块。
    search_tile_width: int = 1344
    search_tile_height: int = 1152
    search_stride_x: int = 1248
    search_stride_y: int = 1008
    inference_size: int = 1344
    coarse_inference_size: int = 1536
    confidence: float = 0.25
    nms_iou: float = 0.65
    max_detections: int = 100

    head_class_id: int = 1
    body_class_id: int = 0
    body_aim_height: float = 0.40

    # 目标关联和 alpha-beta 跟踪器。
    association_gate: float = 110.0
    ambiguity_margin: float = 16.0
    lost_frame_tolerance: int = 4
    tracker_alpha: float = 0.90
    tracker_beta: float = 0.20
    max_target_speed: float = 6000.0
    max_frame_delta: float = 0.12

    # 像素/鼠标单位在线标定。标定值在按键释放后依然保留。
    fallback_mouse_response: float = 1.10
    min_mouse_response: float = 0.5
    max_mouse_response: float = 35.0
    calibration_rate: float = 0.55
    calibration_window: int = 9
    min_calibration_move: int = 2
    calibration_samples_for_full_speed: int = 3

    # 响应归一化控制：快速拉枪，近目标与过零时自动刹车。
    near_correction: float = 0.58
    far_correction: float = 0.88
    crossing_correction: float = 0.28
    full_speed_distance: float = 260.0
    base_deadzone: float = 3.0
    quantization_deadzone_ratio: float = 0.55
    uncalibrated_max_mouse_step: float = 6.0
    max_mouse_step: float = 260.0

    # 目标超前量 = 已发生的推理延迟 + 额外输入延迟。
    extra_lead_time: float = 0.025
    max_lead_time: float = 0.10
    max_lead_pixels: float = 90.0

    activation_key: int = 0x10  # Shift
    quit_key: int = 0x51  # Q


@dataclass
class Detection:
    center: np.ndarray
    size: np.ndarray
    class_id: int
    confidence: float
    aim_offset_y: float

    def aim_point(self) -> np.ndarray:
        return self.center + np.array((0.0, self.aim_offset_y), dtype=np.float64)


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    def as_monitor(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def clamp_vector(vector: np.ndarray, maximum_length: float) -> np.ndarray:
    length = float(np.linalg.norm(vector))
    if length <= maximum_length or length <= 1e-9:
        return vector
    return vector * (maximum_length / length)


def calculate_initial_mouse_response(screen_height: int, config: Config) -> float:
    """用 Aim Lab 实际垂直 FOV 和每鼠标计数角增量，计算屏幕中心的像素/计数。"""
    half_fov_radians = math.radians(config.aimlab_vertical_fov / 2.0)
    focal_length_pixels = (screen_height / 2.0) / math.tan(half_fov_radians)
    count_angle_radians = math.radians(config.aimlab_look_increment_degrees)
    response = focal_length_pixels * math.tan(count_angle_radians)
    if not config.min_mouse_response <= response <= config.max_mouse_response:
        return config.fallback_mouse_response
    return response


class MouseResponseCalibrator:
    """使用滑动中位数抑制移动目标对鼠标响应估计的污染。"""

    def __init__(self, config: Config, initial_response: float) -> None:
        self.config = config
        self.response = np.array((initial_response, initial_response), dtype=np.float64)
        self.samples = (
            deque(maxlen=config.calibration_window),
            deque(maxlen=config.calibration_window),
        )

    def observe(
        self,
        previous_position: np.ndarray,
        previous_velocity: np.ndarray,
        measured_position: np.ndarray,
        mouse_move: tuple[int, int],
        frame_delta: float,
    ) -> float:
        """返回本帧标定值的最大相对变化。"""
        old_response = self.response.copy()
        expected_without_camera = previous_position + previous_velocity * frame_delta

        for axis in (0, 1):
            move = mouse_move[axis]
            if abs(move) < self.config.min_calibration_move:
                continue

            estimate = (expected_without_camera[axis] - measured_position[axis]) / move
            if not self.config.min_mouse_response <= estimate <= self.config.max_mouse_response:
                continue

            self.samples[axis].append(float(estimate))
            robust_estimate = float(np.median(self.samples[axis]))
            if len(self.samples[axis]) == 1:
                # 首个可信样本直接完成初次标定，不让错误初值拖慢整个控制器。
                self.response[axis] = robust_estimate
            else:
                self.response[axis] += self.config.calibration_rate * (
                    robust_estimate - self.response[axis]
                )

        relative_change = np.abs(self.response - old_response) / np.maximum(old_response, 1e-6)
        return float(np.max(relative_change))

    def confidence(self) -> np.ndarray:
        required = max(self.config.calibration_samples_for_full_speed, 1)
        return np.array(
            (
                min(len(self.samples[0]) / required, 1.0),
                min(len(self.samples[1]) / required, 1.0),
            ),
            dtype=np.float64,
        )


class TargetTracker:
    """带鼠标画面位移补偿的单目标 alpha-beta 跟踪器。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.reset()

    def reset(self) -> None:
        self.locked = False
        self.position = np.zeros(2, dtype=np.float64)
        self.velocity = np.zeros(2, dtype=np.float64)
        self.size = np.ones(2, dtype=np.float64)
        self.class_id = -1
        self.aim_offset_y = 0.0
        self.last_time: float | None = None
        self.missing_frames = 0
        self.was_on_target = False

    def _acquire(self, detections: list[Detection], screen_center: np.ndarray, now: float) -> bool:
        if not detections:
            return False

        preferred = [d for d in detections if d.class_id == self.config.head_class_id]
        pool = preferred if preferred else detections
        chosen = min(pool, key=lambda d: float(np.linalg.norm(d.aim_point() - screen_center)))

        self.locked = True
        self.position = chosen.center.copy()
        self.velocity.fill(0.0)
        self.size = chosen.size.copy()
        self.class_id = chosen.class_id
        self.aim_offset_y = chosen.aim_offset_y
        self.last_time = now
        self.missing_frames = 0
        self.was_on_target = False
        return True

    def _prediction(
        self,
        now: float,
        mouse_move: tuple[int, int],
        mouse_response: np.ndarray,
    ) -> tuple[np.ndarray, float]:
        if self.last_time is None:
            return self.position.copy(), 0.0

        frame_delta = min(max(now - self.last_time, 0.0), self.config.max_frame_delta)
        camera_shift = np.asarray(mouse_move, dtype=np.float64) * mouse_response
        predicted = self.position + self.velocity * frame_delta - camera_shift
        return predicted, frame_delta

    def capture_focus(
        self,
        now: float,
        mouse_move: tuple[int, int],
        mouse_response: np.ndarray,
    ) -> np.ndarray:
        if not self.locked:
            raise RuntimeError("capture_focus requires a locked target")
        predicted, _ = self._prediction(now, mouse_move, mouse_response)
        return predicted

    def _associate(
        self,
        detections: list[Detection],
        predicted_position: np.ndarray,
        frame_delta: float,
        mouse_move: tuple[int, int],
        mouse_response: np.ndarray,
    ) -> Detection | None:
        scored: list[tuple[float, float, Detection]] = []
        for detection in detections:
            position_distance = float(np.linalg.norm(detection.center - predicted_position))
            size_ratio = np.maximum(detection.size, 1.0) / np.maximum(self.size, 1.0)
            size_penalty = float(np.sum(np.abs(np.log(size_ratio))) * 28.0)
            class_penalty = 18.0 if detection.class_id != self.class_id else 0.0
            confidence_penalty = (1.0 - detection.confidence) * 4.0
            score = position_distance + size_penalty + class_penalty + confidence_penalty
            scored.append((score, position_distance, detection))

        if not scored:
            return None

        scored.sort(key=lambda item: item[0])
        best_score, best_distance, best_detection = scored[0]
        visual_mouse_step = float(
            np.linalg.norm(np.asarray(mouse_move, dtype=np.float64) * mouse_response)
        )
        speed_allowance = min(80.0, float(np.linalg.norm(self.velocity)) * frame_delta * 0.50)
        adaptive_gate = self.config.association_gate + speed_allowance + min(50.0, visual_mouse_step * 0.20)

        if best_distance > adaptive_gate:
            return None

        if len(scored) > 1:
            second_score = scored[1][0]
            if (
                second_score - best_score < self.config.ambiguity_margin
                and best_distance > 24.0
            ):
                return None

        return best_detection

    def update(
        self,
        detections: list[Detection],
        screen_center: np.ndarray,
        now: float,
        mouse_move: tuple[int, int],
        calibrator: MouseResponseCalibrator,
    ) -> bool:
        """返回 True 表示本帧有可信测量，可以执行鼠标控制。"""
        if not self.locked:
            return self._acquire(detections, screen_center, now)

        previous_position = self.position.copy()
        previous_velocity = self.velocity.copy()
        predicted, frame_delta = self._prediction(now, mouse_move, calibrator.response)
        matched = self._associate(
            detections,
            predicted,
            frame_delta,
            mouse_move,
            calibrator.response,
        )

        if matched is None:
            self.position = predicted
            self.last_time = now
            self.missing_frames += 1

            # 上一帧已在目标内且目标消失，大概率是已经命中，立即攻击下一个。
            if self.was_on_target:
                self.reset()
                return self._acquire(detections, screen_center, now)

            if self.missing_frames > self.config.lost_frame_tolerance:
                self.reset()
                return self._acquire(detections, screen_center, now)
            return False

        calibration_change = calibrator.observe(
            previous_position,
            previous_velocity,
            matched.center,
            mouse_move,
            frame_delta,
        )

        # 标定值明显变化时，用新标定重算画面位移，并丢弃被旧标定污染的速度。
        if calibration_change > 0.04:
            previous_velocity *= 0.20
            self.velocity *= 0.20
        camera_shift = np.asarray(mouse_move, dtype=np.float64) * calibrator.response
        predicted = previous_position + previous_velocity * frame_delta - camera_shift

        innovation = matched.center - predicted
        self.position = predicted + self.config.tracker_alpha * innovation
        if frame_delta > 1e-4:
            self.velocity = previous_velocity + (
                self.config.tracker_beta / frame_delta
            ) * innovation
            self.velocity = clamp_vector(self.velocity, self.config.max_target_speed)

        self.size = self.size * 0.25 + matched.size * 0.75
        self.class_id = matched.class_id
        self.aim_offset_y = matched.aim_offset_y
        self.last_time = now
        self.missing_frames = 0
        return True

    def predicted_aim_point(self, lead_time: float) -> np.ndarray:
        lead = clamp_vector(self.velocity * lead_time, self.config.max_lead_pixels)
        return self.position + lead + np.array((0.0, self.aim_offset_y), dtype=np.float64)


class AimController:
    """不累积小数残差；直接四舍五入到最优整数鼠标步长，避免量化振荡。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.previous_error: np.ndarray | None = None

    def reset(self) -> None:
        self.previous_error = None

    @staticmethod
    def _round_away_from_zero(value: float) -> int:
        if value == 0.0:
            return 0
        return int(math.copysign(math.floor(abs(value) + 0.5), value))

    def _quantize_axis(self, requested: float, error: float, response: float) -> int:
        rounded = self._round_away_from_zero(requested)
        if rounded != 0 or error == 0.0:
            return rounded

        # 比如剩余误差 10px、1 鼠标单位为 15px 时，移动 1 单位会将误差降到 5px。
        # 只有在整数一步确实能缩小误差时才发送，因此不会引入量化振荡。
        one_step = 1 if error > 0.0 else -1
        if abs(error - one_step * response) < abs(error):
            return one_step
        return 0

    def is_on_target(self, error: np.ndarray, response: np.ndarray) -> bool:
        deadzone = np.maximum(
            self.config.base_deadzone,
            response * self.config.quantization_deadzone_ratio,
        )
        return bool(np.all(np.abs(error) <= deadzone))

    def compute(
        self,
        error: np.ndarray,
        response: np.ndarray,
        calibration_confidence: np.ndarray,
    ) -> tuple[int, int]:
        distance = float(np.linalg.norm(error))
        ratio = float(np.clip(distance / self.config.full_speed_distance, 0.0, 1.0))
        correction = self.config.near_correction + (
            self.config.far_correction - self.config.near_correction
        ) * ratio

        axis_correction = np.array((correction, correction), dtype=np.float64)
        if self.previous_error is not None:
            crossed = error * self.previous_error < 0.0
            axis_correction[crossed] = np.minimum(
                axis_correction[crossed],
                self.config.crossing_correction,
            )

        deadzone = np.maximum(
            self.config.base_deadzone,
            response * self.config.quantization_deadzone_ratio,
        )
        controlled_error = error.copy()
        controlled_error[np.abs(controlled_error) <= deadzone] = 0.0

        safe_response = np.clip(
            response,
            self.config.min_mouse_response,
            self.config.max_mouse_response,
        )
        requested_move = controlled_error / safe_response * axis_correction

        active_axes = np.abs(controlled_error) > 0.0
        if np.any(active_axes):
            confidence = float(np.min(calibration_confidence[active_axes]))
        else:
            confidence = 1.0
        step_limit = self.config.uncalibrated_max_mouse_step + confidence * (
            self.config.max_mouse_step - self.config.uncalibrated_max_mouse_step
        )
        requested_move = clamp_vector(requested_move, step_limit)

        move = (
            self._quantize_axis(
                float(requested_move[0]),
                float(controlled_error[0]),
                float(safe_response[0]),
            ),
            self._quantize_axis(
                float(requested_move[1]),
                float(controlled_error[1]),
                float(safe_response[1]),
            ),
        )
        self.previous_error = error.copy()
        return move


def set_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def key_is_down(key_code: int) -> bool:
    return bool(win32api.GetAsyncKeyState(key_code) & 0x8000)


def send_mouse_move(move: tuple[int, int]) -> None:
    if move != (0, 0):
        win32api.mouse_event(
            win32con.MOUSEEVENTF_MOVE,
            move[0],
            move[1],
            0,
            0,
        )


def axis_tile_positions(total_size: int, tile_size: int, stride: int) -> list[int]:
    if tile_size >= total_size:
        return [0]
    last_position = total_size - tile_size
    positions = list(range(0, last_position + 1, stride))
    if positions[-1] != last_position:
        positions.append(last_position)
    return positions


def build_search_regions(screen_width: int, screen_height: int, config: Config) -> list[CaptureRegion]:
    tile_width = min(config.search_tile_width, screen_width)
    tile_height = min(config.search_tile_height, screen_height)
    left_positions = axis_tile_positions(screen_width, tile_width, config.search_stride_x)
    top_positions = axis_tile_positions(screen_height, tile_height, config.search_stride_y)
    return [
        CaptureRegion(left, top, tile_width, tile_height)
        for top in top_positions
        for left in left_positions
    ]


def build_tracking_region(
    focus: np.ndarray,
    screen_width: int,
    screen_height: int,
    config: Config,
) -> CaptureRegion:
    width = min(config.search_tile_width, screen_width)
    height = min(config.search_tile_height, screen_height)
    left = int(round(float(focus[0]) - width / 2.0))
    top = int(round(float(focus[1]) - height / 2.0))
    left = int(np.clip(left, 0, screen_width - width))
    top = int(np.clip(top, 0, screen_height - height))
    return CaptureRegion(left, top, width, height)


def capture_frame(screenshotter: mss.mss, region: CaptureRegion) -> np.ndarray:
    screenshot = np.asarray(screenshotter.grab(region.as_monitor()))
    return np.ascontiguousarray(screenshot[:, :, :3])


def detection_iou(first: Detection, second: Detection) -> float:
    first_min = first.center - first.size / 2.0
    first_max = first.center + first.size / 2.0
    second_min = second.center - second.size / 2.0
    second_max = second.center + second.size / 2.0
    intersection_size = np.maximum(0.0, np.minimum(first_max, second_max) - np.maximum(first_min, second_min))
    intersection = float(intersection_size[0] * intersection_size[1])
    first_area = float(first.size[0] * first.size[1])
    second_area = float(second.size[0] * second.size[1])
    union = first_area + second_area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def deduplicate_detections(detections: list[Detection]) -> list[Detection]:
    """去除重叠搜索块中同一目标产生的重复框。"""
    kept: list[Detection] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        duplicate = any(
            detection.class_id == existing.class_id
            and detection_iou(detection, existing) >= 0.45
            for existing in kept
        )
        if not duplicate:
            kept.append(detection)
    return kept


def parse_detections(
    result,
    config: Config,
    origin: tuple[int, int] = (0, 0),
) -> list[Detection]:
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []

    xywh = boxes.xywh.detach().cpu().numpy()
    classes = boxes.cls.detach().cpu().numpy().astype(np.int32)
    confidences = boxes.conf.detach().cpu().numpy()

    origin_vector = np.asarray(origin, dtype=np.float64)
    detections: list[Detection] = []
    for (x_center, y_center, width, height), class_id, confidence in zip(
        xywh,
        classes,
        confidences,
    ):
        aim_offset_y = -height * config.body_aim_height if class_id == config.body_class_id else 0.0
        detections.append(
            Detection(
                center=np.array((x_center, y_center), dtype=np.float64) + origin_vector,
                size=np.array((width, height), dtype=np.float64),
                class_id=int(class_id),
                confidence=float(confidence),
                aim_offset_y=float(aim_offset_y),
            )
        )
    return detections


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("配置必须是 JSON 对象")
    allowed = {field.name: field.type for field in fields(Config)}
    unknown = set(values) - set(allowed)
    if unknown:
        raise ValueError(f"未知配置字段: {', '.join(sorted(unknown))}")
    for key, value in values.items():
        expected = allowed[key]
        if expected == "int" and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError(f"{key} 必须是整数")
        if expected == "float" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"{key} 必须是数值")
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise ValueError(f"{key} 必须是有限数值")
    config = Config(**values)
    for key in (
        "expected_screen_width", "expected_screen_height", "expected_refresh_rate",
        "search_tile_width", "search_tile_height", "search_stride_x", "search_stride_y",
        "inference_size", "coarse_inference_size", "calibration_window",
        "calibration_samples_for_full_speed", "min_calibration_move", "max_detections",
        "max_frame_delta", "full_speed_distance", "max_mouse_step", "max_lead_time",
        "min_mouse_response", "max_mouse_response", "aimlab_look_increment_degrees",
    ):
        if getattr(config, key) <= 0:
            raise ValueError(f"{key} 必须大于 0")
    if config.search_stride_x > config.search_tile_width or config.search_stride_y > config.search_tile_height:
        raise ValueError("搜索步长不能大于分块尺寸，否则会出现未覆盖区域")
    if not 0 < config.aimlab_vertical_fov < 180:
        raise ValueError("aimlab_vertical_fov 必须在 0 到 180 度之间")
    for key in ("confidence", "nms_iou", "calibration_rate", "tracker_alpha", "tracker_beta",
                "near_correction", "far_correction", "crossing_correction"):
        if not 0 < getattr(config, key) <= 1:
            raise ValueError(f"{key} 必须在 (0, 1] 范围内")
    if config.min_mouse_response >= config.max_mouse_response:
        raise ValueError("min_mouse_response 必须小于 max_mouse_response")
    return config


def resolve_model_path(requested: Path | None, project_dir: Path) -> Path:
    if requested is not None:
        candidates = [requested.expanduser().resolve()]
    else:
        candidates = [
            project_dir / "weights" / "aim.pt",
            project_dir / "Aim_Bot_Runs" / "5080_training" / "weights" / "aim.pt",
            project_dir / "Aim_Bot_Runs" / "5080_training" / "weights" / "best.pt",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "找不到自训练模型。请将权重放入 weights/aim.pt，或使用 --model 指定文件。\n"
        + "\n".join(str(path) for path in candidates)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Aim AI：Aim Lab 目标检测、跟踪与鼠标控制")
    parser.add_argument("--model", type=Path, help="自训练 YOLO 权重路径")
    parser.add_argument("--config", type=Path, help="JSON 配置文件；未提供的字段使用默认值")
    args = parser.parse_args()
    config = load_config(args.config)
    set_dpi_awareness()

    project_dir = Path(__file__).resolve().parents[1]
    model_path = resolve_model_path(args.model, project_dir)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    use_half = device == "cuda"
    if use_half:
        torch.backends.cudnn.benchmark = True

    screen_width = win32api.GetSystemMetrics(0)
    screen_height = win32api.GetSystemMetrics(1)
    screen_center = np.array((screen_width / 2.0, screen_height / 2.0), dtype=np.float64)
    search_regions = build_search_regions(screen_width, screen_height, config)
    full_screen_region = CaptureRegion(0, 0, screen_width, screen_height)
    initial_mouse_response = calculate_initial_mouse_response(screen_height, config)

    model = YOLO(str(model_path))
    calibrator = MouseResponseCalibrator(config, initial_mouse_response)
    tracker = TargetTracker(config)
    controller = AimController(config)
    screenshotter = mss.mss()

    predict_options = {
        "imgsz": config.inference_size,
        "conf": config.confidence,
        "iou": config.nms_iou,
        "max_det": config.max_detections,
        "device": device,
        "half": use_half,
        "rect": True,
        "verbose": False,
        "save": False,
    }
    coarse_predict_options = dict(predict_options)
    coarse_predict_options["imgsz"] = config.coarse_inference_size

    # 预热模型，避免第一次按 Shift 时出现大延迟。
    warmup_frame = np.zeros(
        (search_regions[0].height, search_regions[0].width, 3),
        dtype=np.uint8,
    )
    model.predict([warmup_frame], **predict_options)
    del warmup_frame
    coarse_warmup = np.zeros((screen_height, screen_width, 3), dtype=np.uint8)
    model.predict([coarse_warmup], **coarse_predict_options)
    del coarse_warmup

    print("=== New Aimbot: 4K 分块搜索 + 动态跟踪 + 响应归一化控制 ===")
    print(
        f"物理屏幕: {screen_width}x{screen_height} | "
        f"搜索块: {len(search_regions)} x "
        f"{search_regions[0].width}x{search_regions[0].height} | "
        f"精细推理: {config.inference_size} | "
        f"粗搜推理: {config.coarse_inference_size} | 设备: {device}"
    )
    print(
        f"Aim Lab: {config.expected_refresh_rate}Hz, "
        f"VFOV {config.aimlab_vertical_fov:.4f}, "
        f"角增量 {config.aimlab_look_increment_degrees:.3f}°, "
        f"初始响应 {initial_mouse_response:.3f} px/count"
    )
    if (
        screen_width != config.expected_screen_width
        or screen_height != config.expected_screen_height
    ):
        print(
            f"警告: 当前物理分辨率与已读取的 "
            f"{config.expected_screen_width}x{config.expected_screen_height} 不一致"
        )
    print("Shift 启用，Q 退出")

    last_mouse_move = (0, 0)
    fps_frames = 0
    fps_started = time.perf_counter()
    tiled_search_next = False

    try:
        while True:
            if key_is_down(config.quit_key):
                break

            if not key_is_down(config.activation_key):
                tracker.reset()
                controller.reset()
                last_mouse_move = (0, 0)
                time.sleep(0.001)
                continue

            frame_time = time.perf_counter()
            if tracker.locked:
                focus = tracker.capture_focus(
                    frame_time,
                    last_mouse_move,
                    calibrator.response,
                )
                active_regions = [
                    build_tracking_region(
                        focus,
                        screen_width,
                        screen_height,
                        config,
                    )
                ]
                detection_mode = "TRACK"
                active_predict_options = predict_options
                tiled_search_next = False
            elif tiled_search_next:
                active_regions = search_regions
                detection_mode = "TILED"
                active_predict_options = predict_options
            else:
                active_regions = [full_screen_region]
                detection_mode = "COARSE"
                active_predict_options = coarse_predict_options

            frames = [capture_frame(screenshotter, region) for region in active_regions]
            results = model.predict(frames, **active_predict_options)
            detections = []
            for result, region in zip(results, active_regions):
                detections.extend(
                    parse_detections(
                        result,
                        config,
                        origin=(region.left, region.top),
                    )
                )
            if len(active_regions) > 1:
                detections = deduplicate_detections(detections)

            if detection_mode == "COARSE":
                tiled_search_next = not bool(detections)
            elif detection_mode == "TILED":
                tiled_search_next = False

            has_measurement = tracker.update(
                detections,
                screen_center,
                frame_time,
                last_mouse_move,
                calibrator,
            )

            if has_measurement:
                processing_delay = time.perf_counter() - frame_time
                lead_time = min(
                    processing_delay + config.extra_lead_time,
                    config.max_lead_time,
                )
                aim_point = tracker.predicted_aim_point(lead_time)
                error = aim_point - screen_center
                tracker.was_on_target = controller.is_on_target(error, calibrator.response)
                last_mouse_move = controller.compute(
                    error,
                    calibrator.response,
                    calibrator.confidence(),
                )
                send_mouse_move(last_mouse_move)
            else:
                controller.reset()
                last_mouse_move = (0, 0)

            fps_frames += 1
            now = time.perf_counter()
            if now - fps_started >= 1.0:
                fps = fps_frames / (now - fps_started)
                speed = float(np.linalg.norm(tracker.velocity)) if tracker.locked else 0.0
                print(
                    f"{detection_mode} FPS: {fps:.1f} | 响应 X/Y: "
                    f"{calibrator.response[0]:.2f}/{calibrator.response[1]:.2f} px | "
                    f"目标速度: {speed:.0f} px/s"
                )
                fps_frames = 0
                fps_started = now
    finally:
        screenshotter.close()


if __name__ == "__main__":
    main()
