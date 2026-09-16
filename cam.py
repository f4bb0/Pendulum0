import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

from control import *


CAMERA_INDEX = 0
MOTION_THRESHOLD = 25
MOTION_PIXELS_MAX_RATIO = 0.01
MOTION_SMOOTH_ALPHA = 0.2
STEP_SMOOTH_ALPHA = 0.6
STEP_HOLD_SEC = 3
STEP_RELEASE_ALPHA = 0.01
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080


class _WebHandler(BaseHTTPRequestHandler):
    state = None

    def do_GET(self):
        if self.path == "/stream":
            with self.state.frame_condition:
                frame = self.state.jpeg_frame
            if frame is None:
                self.send_error(503, "画面尚未准备好")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)
            return

        if self.path == "/":
            page = b'<html><body><img src="/stream" onload="setTimeout(function(){location.reload()},1000)"></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return

        self.send_error(404)

    def log_message(self, format, *args):
        return


def _build_valid_step_choices(min_angle, max_angle, max_step_limit=None):
    span = max_angle - min_angle
    if span <= 0:
        raise ValueError("最大角度必须大于最小角度")

    if max_step_limit is None:
        max_step_limit = MAX_STEP_LIMIT
    if max_step_limit < 0:
        raise ValueError("最大步长限制必须大于等于 0")

    choices = [0] + [
        step for step in range(1, span + 1)
        if span % step == 0 and step <= max_step_limit
    ]
    if not choices:
        raise ValueError("无法为当前角度范围生成有效步长")
    return choices


def _map_motion_pixels_to_step(motion_pixels, frame_area, min_angle, max_angle):
    valid_steps = _build_valid_step_choices(min_angle, max_angle)
    motion_pixels_max = max(1, int(frame_area * MOTION_PIXELS_MAX_RATIO))
    clamped_pixels = max(0, min(motion_pixels, motion_pixels_max))
    step_index = round(clamped_pixels / motion_pixels_max * (len(valid_steps) - 1))
    return valid_steps[step_index]


def _calculate_motion_pixels(previous_frame_2, previous_frame_1, current_frame):
    gray_1 = cv2.cvtColor(previous_frame_2, cv2.COLOR_BGR2GRAY)
    gray_2 = cv2.cvtColor(previous_frame_1, cv2.COLOR_BGR2GRAY)
    gray_3 = cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)

    blur_1 = cv2.GaussianBlur(gray_1, (5, 5), 0)
    blur_2 = cv2.GaussianBlur(gray_2, (5, 5), 0)
    blur_3 = cv2.GaussianBlur(gray_3, (5, 5), 0)

    diff_1 = cv2.absdiff(blur_1, blur_2)
    diff_2 = cv2.absdiff(blur_2, blur_3)
    motion_mask = cv2.bitwise_and(diff_1, diff_2)

    _, binary_mask = cv2.threshold(motion_mask, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
    motion_pixels = cv2.countNonZero(binary_mask)
    return motion_pixels, binary_mask


def _open_camera():
    capture = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_DSHOW)
    if not capture.isOpened():
        capture.release()
        capture = cv2.VideoCapture(CAMERA_INDEX)
    if not capture.isOpened():
        raise RuntimeError("无法打开摄像头，请检查摄像头是否被占用或索引是否正确")
    return capture

def DIFF_SWING_DEMO():
    """
    在独立后台线程中启动摄像头三帧差分驱动的差动摆动示例。

    返回 thread 对象，调用方可以选择 join() 等待结束，或者继续执行其它逻辑。
    """
    class SharedState:
        def __init__(self):
            self.lock = threading.Lock()
            self.frame_condition = threading.Condition()
            self.jpeg_frame = None
            self.step = max(0, DIFF_SWING_STEP)
            self.motion_pixels = 0
            self.detection_fps = 0.0
            self.send_fps = 0.0
            self.stop_event = threading.Event()

    state = SharedState()

    def cv_worker():
        camera = None
        try:
            camera = _open_camera()
            frame_buffer = deque(maxlen=3)
            detection_count = 0
            fps_start = time.monotonic()
            last_motion_time = time.monotonic()
            while not state.stop_event.is_set():
                ret, frame = camera.read()
                if not ret:
                    time.sleep(0.05)
                    continue

                frame_buffer.append(frame)
                if len(frame_buffer) < 3:
                    continue
                motion_pixels_raw, binary_mask = _calculate_motion_pixels(frame_buffer[0], frame_buffer[1], frame_buffer[2])
                frame_area = frame.shape[0] * frame.shape[1]

                # 平滑 motion_pixels（EMA）并平滑步长索引，避免剧烈跳变
                if not hasattr(cv_worker, "_smoothed_motion"):
                    cv_worker._smoothed_motion = float(motion_pixels_raw)
                if not hasattr(cv_worker, "_smoothed_step_idx"):
                    cv_worker._smoothed_step_idx = 0.0

                cv_worker._smoothed_motion = (
                    MOTION_SMOOTH_ALPHA * motion_pixels_raw
                    + (1 - MOTION_SMOOTH_ALPHA) * cv_worker._smoothed_motion
                )

                # 计算最大参考像素数并映射到步长索引（float）
                valid_steps = _build_valid_step_choices(
                    DIFF_SWING_MIN_ANGLE,
                    DIFF_SWING_MAX_ANGLE,
                    MAX_STEP_LIMIT,
                )
                motion_pixels_max = max(1, int(frame_area * MOTION_PIXELS_MAX_RATIO))
                ratio = max(0.0, min(1.0, cv_worker._smoothed_motion / motion_pixels_max))
                float_idx = ratio * (len(valid_steps) - 1)

                now = time.monotonic()
                if motion_pixels_raw > 0:
                    last_motion_time = now
                    cv_worker._smoothed_step_idx = (
                        STEP_SMOOTH_ALPHA * float_idx
                        + (1 - STEP_SMOOTH_ALPHA) * cv_worker._smoothed_step_idx
                    )
                elif now - last_motion_time >= STEP_HOLD_SEC:
                    cv_worker._smoothed_step_idx *= 1 - STEP_RELEASE_ALPHA

                step_idx = int(round(cv_worker._smoothed_step_idx))
                step_idx = max(0, min(len(valid_steps) - 1, step_idx))
                step = valid_steps[step_idx]

                with state.lock:
                    state.motion_pixels = int(round(cv_worker._smoothed_motion))
                    state.step = step

                detection_count += 1
                fps_elapsed = time.monotonic() - fps_start
                if fps_elapsed >= 1.0:
                    with state.lock:
                        state.detection_fps = detection_count / fps_elapsed
                    detection_count = 0
                    fps_start = time.monotonic()

                if not hasattr(cv_worker, "_last_web_frame_time") or time.time() - cv_worker._last_web_frame_time >= 1:
                    mask_bgr = cv2.cvtColor(binary_mask, cv2.COLOR_GRAY2BGR)
                    overlay = cv2.addWeighted(frame_buffer[2], 0.7, mask_bgr, 0.3, 0)
                    encoded, jpeg_frame = cv2.imencode(".jpg", overlay)
                    if encoded:
                        with state.frame_condition:
                            state.jpeg_frame = jpeg_frame.tobytes()
                            state.frame_condition.notify_all()
                    cv_worker._last_web_frame_time = time.time()

                time.sleep(0.01)
        finally:
            if camera is not None:
                camera.release()

    def control_worker():
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            print(f"开始向 {TARGET_IP}:{TARGET_PORT} 发送控制指令...\n")
            print("正在执行 1-24 号设备差动摆动示例：CV 和控制运行于独立线程。")

            cycle_index = 0
            send_count = 0
            fps_start = time.monotonic()
            while not state.stop_event.is_set():
                with state.lock:
                    current_step = state.step
                    motion_pixels = state.motion_pixels
                    detection_fps = state.detection_fps

                devices_data = build_differential_angle_data(
                    cycle_index,
                    DIFF_SWING_DEVICE_COUNT,
                    DIFF_SWING_MIN_ANGLE,
                    DIFF_SWING_MAX_ANGLE,
                    current_step,
                    DIFF_SWING_STAGGER_CYCLES,
                    1,
                    DIFF_SWING_COLUMN_GROUPS,
                )
                packet = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, DIFF_SWING_START_ID, devices_data)

                send_count += 1
                fps_elapsed = time.monotonic() - fps_start
                if fps_elapsed >= 1.0:
                    with state.lock:
                        state.send_fps = send_count / fps_elapsed
                    send_count = 0
                    fps_start = time.monotonic()
                with state.lock:
                    send_fps = state.send_fps

                angle_text = ", ".join(f"{DIFF_SWING_START_ID + index}:{angle}" for index, (angle, _) in enumerate(devices_data))
                print(f"[循环 {cycle_index}] 检测FPS={detection_fps:.1f}，发送FPS={send_fps:.1f}，运动像素={motion_pixels}，动态步长={current_step}，{angle_text}")
               # print(f"发送 HEX: {packet.hex(' ').upper()}\n")

                cycle_index += 1
                time.sleep(DIFF_SWING_DELAY_SEC)
        finally:
            udp_socket.close()

    t_cv = threading.Thread(target=cv_worker, name="CV_THREAD", daemon=True)
    t_ctrl = threading.Thread(target=control_worker, name="CTRL_THREAD", daemon=True)
    _WebHandler.state = state
    web_server = ThreadingHTTPServer((WEB_HOST, WEB_PORT), _WebHandler)
    t_web = threading.Thread(target=web_server.serve_forever, name="WEB_THREAD", daemon=True)
    t_cv.start()
    t_ctrl.start()
    t_web.start()
    print(f"检测画面地址: http://127.0.0.1:{WEB_PORT}/")

    return t_cv, t_ctrl, t_web, state


# 现在可以直接使用 control.py 中的所有内容
# 包括：
# - 常量: TARGET_IP, TARGET_PORT, CMD_SET_BASIC, CMD_SWING, CMD_SWING_LIMIT, DEFAULT_MAGIC
# - 函数: build_udp_packet(), send_control_packet(), send_raw_hex_packet()
if __name__ == "__main__":
    try:
        result = DIFF_SWING_DEMO()
        # DIFF_SWING_DEMO returns (cv_thread, ctrl_thread, web_thread, state)
        if isinstance(result, tuple) and len(result) == 4:
            cv_t, ctrl_t, web_t, state = result
            try:
                while cv_t.is_alive() and ctrl_t.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                state.stop_event.set()
                cv_t.join()
                ctrl_t.join()
        else:
            # 兼容旧返回单线程对象
            result.join()

    except PermissionError:
        print("❌ 权限被拒绝：请检查防火墙设置，或尝试以管理员/Root权限运行此脚本。")
    except Exception as e:
        print(f"❌ 发送失败: {e}")