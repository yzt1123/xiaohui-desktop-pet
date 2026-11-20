# -*- coding: utf-8 -*-
"""小灰桌宠 —— 网格形变的伪 Live2D 版本。
思路：不走逐帧 GIF，那套图只取「一张一个状态」。人物的动感全部由代码算：
呼吸、摇摆、朝鼠标倾、按键时的挤压回弹，外加头顶一个显示刚按了哪个键的小气泡。
形变用的是横向切片（把整张图切成 50 条横带，每条带单独平移），合起来就是一条
平滑的弯折曲线，效果接近 Live2D 的变形器，但不需要模型文件。
"""
import ctypes
import ctypes.wintypes as wintypes
import json
import math
import os
import random
import sys
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QImage, QPainter,
                           QPainterPath, QPen, QTransform)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

FROZEN = bool(getattr(sys, "frozen", False))
BASE = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(getattr(sys, "_MEIPASS", BASE), "assets")
CONFIG = os.path.join(BASE, "config.json")
MAPPING = os.path.join(BASE, "mapping.json")
# 打包成 exe 后不往用户那儿丢日志，写进空设备
LOGDIR = BASE
LOG_ON = bool(os.environ.get("XIAOHUI_LOG")) or (not FROZEN)


def log_path(name):
    if not LOG_ON:
        return os.devnull
    return os.path.join(LOGDIR, name)

BUBBLE_STYLES = ("白框", "云朵", "深色", "无框")

user32 = ctypes.windll.user32
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD), ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    ]


WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1

# ---------------------------------------------------------------- 键位

VK_LABEL = {0x08: "退格", 0x09: "Tab", 0x0D: "回车", 0x13: "Pause", 0x14: "Caps",
            0x1B: "Esc", 0x20: "空格", 0x21: "PgUp", 0x22: "PgDn", 0x23: "End",
            0x24: "Home", 0x25: "↑", 0x26: "↓", 0x27: "→", 0x28: "←",
            0x2C: "PrtSc", 0x2D: "Ins", 0x2E: "Del", 0x5B: "Win", 0x5C: "Win",
            0x5D: "菜单", 0x90: "NumLock", 0x91: "ScrLk", 0xA0: "LShift",
            0xA1: "RShift", 0xA2: "LCtrl", 0xA3: "RCtrl", 0xA4: "LAlt", 0xA5: "RAlt",
            0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
            0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'"}
for _i in range(26):
    VK_LABEL[0x41 + _i] = chr(0x41 + _i)
for _i in range(10):
    VK_LABEL[0x30 + _i] = str(_i)
for _i in range(12):
    VK_LABEL[0x70 + _i] = "F%d" % (_i + 1)
for _i in range(10):
    VK_LABEL[0x60 + _i] = "Num%d" % _i

WATCH = [vk for vk in VK_LABEL if vk not in (0xA0, 0xA1)]  # 左右 Shift 只算一个
MOUSE_VK = {0x01: "左键", 0x02: "右键", 0x04: "中键"}

# ---------------------------------------------------------------- 全局钩子
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
MS_BTN = {
    0x0201: (0x01, True), 0x0202: (0x01, False),
    0x0204: (0x02, True), 0x0205: (0x02, False),
    0x0207: (0x04, True), 0x0208: (0x04, False),
}
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


# ---------------------------------------------------------------- 原始输入
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEKEYBOARD = 1


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", ctypes.c_void_p), ("wParam", ctypes.c_size_t)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", ctypes.c_ushort), ("usUsage", ctypes.c_ushort),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", ctypes.c_void_p)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", ctypes.c_ushort), ("Flags", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort), ("VKey", ctypes.c_ushort),
                ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]



# ---------------------------------------------------------------- 默认配置

DEFAULT_MAPPING = {
    "neutral": "typing",        # 什么都不干时的表情：趴键盘那张
    "typing": "typing",         # 正常打字
    "typing_fast": "typing_angry",  # 一秒八下以上
    "sleep": "sleep",           # 闲置
    "click": "pat",             # 点她
    "drag": "cry",              # 拎起来
    "boot": "loading",          # 刚启动
    "keys": {                   # 专用表情键：Alt + 键，按一下换脸，一直保持
        "alt+j": "talk",
        "alt+7": "work",
        "alt+8": "pat",
        "alt+9": "cry",
        "alt+0": "typing_angry",
        "alt+-": "loading",
        "alt+1": "glasses",
        "alt+2": "sleep",
        "alt+3": "hungry",
        "alt+4": "bug",
    },
    "sticky": ["alt+2"],        # 旧字段，现在所有表情键都钉住
    "expr_hold": 3.0,           # 表情停多久（秒）
    "fast_threshold": 8.0,      # 每秒按键数才算快
    "fast_sustain": 1.0,        # 快打要持续这么久才翻脸
    "angry_hold": 2.0,          # 翻脸后停多久
    "idle_sleep": 90.0,         # 秒
    "rev": 4,
}


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(default, dict):
            merged = dict(default)
            merged.update(data)
            return merged
        return data
    except Exception:
        return default


def pick_font(size):
    f = QFont()
    for name in ("LXGW WenKai GB", "微软雅黑", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        f.setFamily(name)
        if f.exactMatch():
            break
    f.setPixelSize(size)
    return f


# ---------------------------------------------------------------- 主窗口
class Pet(QWidget):
    PAD_TOP = 74          # 气泡占的高度

    def __init__(self):
        super().__init__()
        self.cfg = load_json(CONFIG, {"size": 260, "pos": None})
        self.map = load_json(MAPPING, DEFAULT_MAPPING)
        if int(self.map.get("rev", 0)) != int(DEFAULT_MAPPING["rev"]):
            self.map = dict(DEFAULT_MAPPING)      # 老配置直接换掉
        with open(MAPPING, "w", encoding="utf-8") as f:
            json.dump(self.map, f, ensure_ascii=False, indent=2)

        self.size_px = int(self.cfg.get("size", 260))
        self.bubble_style = self.cfg.get("bubble", "云朵")
        if self.bubble_style not in BUBBLE_STYLES:
            self.bubble_style = BUBBLE_STYLES[0]
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.NoDropShadowWindowHint |
                            Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.resize(QSize(self.size_px, self.size_px + self.PAD_TOP))

        self.images = {}
        self.state = "boot"
        self.prev_state = None
        self.state_t = 0.0
        self.fade = 1.0

        self.t0 = time.perf_counter()
        self.last_input = self.t0
        self.last_key_time = 0.0
        self.press_times = []
        self.held = set()
        self.impulse = 0.0
        self.pop = 1.0
        self.phase = random.random() * 10
        self.look_x = 0.0
        self.look_y = 0.0

        self.bubble_text = ""
        self.bubble_t = 0.0
        self.bubble_side = 0

        self.expr_until = 0.0        # 表情停到什么时候
        self.sticky_state = ""       # 一直保持的表情
        self.fast_since = None       # 开始快打的时间

        self.dragging = False
        self.drag_off = QPoint()
        self.press_pos = None

        self.set_state("boot")
        QTimer.singleShot(1400, lambda: self.set_state(self.idle_state(force=True)))

        pos = self.cfg.get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            self.move_to_corner()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

    # ---------------- 资源

    def img(self, name):
        im = self.images.get(name)
        if im is None:
            path = os.path.join(ASSETS, name + ".png")
            if not os.path.exists(path):
                path = os.path.join(ASSETS, "talk.png")
            im = QImage(path)
            if im.isNull():
                im = QImage(200, 200, QImage.Format_ARGB32)
                im.fill(QColor(255, 200, 220, 200))
            self.images[name] = im
        return im

    def set_state(self, name, force=False):
        if not name:
            return
        if name == self.state and not force:
            return
        self.prev_state = self.state
        self.state = name
        self.state_t = time.perf_counter()
        self.fade = 0.0
        self.pop = 1.10
        dbg = os.environ.get("PET_LOG")
        if dbg:
            with open(dbg, "a", encoding="utf-8") as f:
                f.write("%.2f -> %s\n" % (time.perf_counter() - self.t0, name))

    def idle_state(self, force=False):
        if force:
            return self.map.get("neutral", "typing")
        return None

    # ---------------- 输入

    def install_hooks(self):
        """低级键鼠钩子。拿系统真实事件，比轮询可靠。"""
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.restype = LRESULT
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          wintypes.WPARAM, wintypes.LPARAM]
        self._pending = []

        def kb_proc(nCode, wParam, lParam):
            if nCode == 0:
                try:
                    vk = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                    if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                        self._pending.append((vk, True))
                    elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                        self._pending.append((vk, False))
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        def ms_proc(nCode, wParam, lParam):
            if nCode == 0:
                hit = MS_BTN.get(wParam)
                if hit:
                    self._pending.append(hit)
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._kb_cb = HOOKPROC(kb_proc)
        self._ms_cb = HOOKPROC(ms_proc)
        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_cb, None, 0)
        self._ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_cb, None, 0)
        self._hooks_ok = bool(self._kb_hook)
        with open(log_path("_hook.log"), "a", encoding="utf-8") as f:
            f.write("kb=%s ms=%s err=%s\n" % (self._kb_hook, self._ms_hook,
                                              ctypes.get_last_error()))

    def scan(self, now):
        if getattr(self, "_hooks_ok", False):
            events, self._pending = self._pending, []
            # 原始输入和钩子会同时报同一个键，去重
            clean = []
            for item in events:
                if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], bool):
                    key = item
                    prev = getattr(self, "_last_ev", None)
                    if prev and prev[0] == key and now - prev[1] < 0.06:
                        continue
                    self._last_ev = (key, now)
                clean.append(item)
            events = clean
            downs = []
            for vk, down in events:
                if down:
                    if vk not in self.held:
                        self.held.add(vk)
                        downs.append(vk)
                else:
                    self.held.discard(vk)
        else:
            pressed = [vk for vk in WATCH if user32.GetAsyncKeyState(vk) & 0x8000]
            pressed += [vk for vk in MOUSE_VK if user32.GetAsyncKeyState(vk) & 0x8000]
            downs = [vk for vk in pressed if vk not in self.held]
            self.held = set(pressed)
            self._maxpressed = max(getattr(self, "_maxpressed", 0), len(pressed))
        new_edges = [VK_LABEL.get(vk, MOUSE_VK.get(vk, "?")) for vk in downs]
        self._scans = getattr(self, "_scans", 0) + 1
        if getattr(self, "_ilog", None) is None:
            try:
                self._ilog = open(log_path("_input.log"), "a", encoding="utf-8")
            except Exception:
                self._ilog = False
        if new_edges and self._ilog:
            try:
                self._ilog.write("%.2f %s\n" % (time.time(), ",".join(new_edges)))
                self._ilog.flush()
            except Exception:
                pass

        if new_edges:
            self.last_input = now
            self.last_key_time = now
            self.impulse = 1.0
            if os.environ.get("PET_DEBUG"):
                print("edge", new_edges, flush=True)
            self.press_times = [t for t in self.press_times if now - t < 1.0]
            self.press_times.append(now)
            if len(self.press_times) > 40:
                self.press_times = self.press_times[-40:]
            if downs[-1] not in self.MOD_VK:
                self.show_bubble(new_edges[-1])
            self.on_keys(new_edges, downs, now)
        return new_edges

    MODS = (("Ctrl", (0x11, 0xA2, 0xA3)), ("Alt", (0x12, 0xA4, 0xA5)),
            ("Shift", (0x10, 0xA0, 0xA1)), ("Win", (0x5B, 0x5C)))
    MOD_VK = set(v for _, vks in MODS for v in vks)

    def mods_now(self):
        return [name for name, vks in self.MODS
                if any(user32.GetAsyncKeyState(v) & 0x8000 for v in vks)]

    def show_bubble(self, label):
        self.bubble_text = label
        self.bubble_t = time.perf_counter()
        self.bubble_side = -1 if label and ord(label[0]) < 128 and len(label) == 1 and label.isalpha() and label in "QWERTASDFGZXCVB12345" else 1

    def on_keys(self, labels, vks, now):
        keys_map = dict((k.lower(), v) for k, v in self.map.get("keys", {}).items())
        mods = self.mods_now()
        for vk in reversed(vks):
            lb = VK_LABEL.get(vk, MOUSE_VK.get(vk, "?"))
            spec = "+".join(mods + [lb]).lower() if vk not in self.MOD_VK else ""
            if spec not in keys_map:
                continue
            target = keys_map[spec]
            self.show_bubble("+".join(mods + [lb]))
            # 表情一律钉住，不自己回默认。再按一次同一个组合才收回去
            if self.sticky_state == target:
                self.sticky_state = ""
                self.expr_until = 0.0
                self.set_state(self.map.get("neutral", "typing"))
            else:
                self.sticky_state = target
                self.expr_until = 0.0
                self.set_state(target)
            return

        # 普通打字不换脸。只有一直快打，才翻一下脸
        rate = len([t for t in self.press_times if now - t < 1.0])
        if rate >= float(self.map.get("fast_threshold", 8.0)):
            if self.fast_since is None:
                self.fast_since = now
            elif now - self.fast_since >= float(self.map.get("fast_sustain", 1.0)):
                if not self.sticky_state:
                    self.expr_until = now + float(self.map.get("angry_hold", 2.0))
                    self.set_state(self.map.get("angry", "typing_angry"))
        else:
            self.fast_since = None

    # ---------------- 帧循环
    def tick(self):
        now = time.perf_counter()
        try:
            self._tick(now)
        except Exception:
            import traceback
            with open(log_path("_crash.log"), "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
            self.timer.stop()

    def _tick(self, now):
        self._n = getattr(self, "_n", 0) + 1
        if self._n % 120 == 0:
            path = log_path("_beat.log")
            held = [VK_LABEL.get(v, str(v)) for v in self.held if not isinstance(v, str)]
            with open(path, "a", encoding="utf-8") as f:
                f.write("pid=%s t=%.1f n=%d held=%s state=%s scans=%d max=%d raw41=%d raw0d=%d\n"
                        % (os.getpid(), now - self.t0, self._n, held[:6], self.state,
                           getattr(self, "_scans", 0), getattr(self, "_maxpressed", 0),
                           user32.GetAsyncKeyState(0x41), user32.GetAsyncKeyState(0x0D)))
        self.scan(now)
        idle = now - self.last_input

        if self.state == self.map.get("boot", "loading") and now - self.state_t > 1.4:
            self.set_state(self.map.get("neutral", "typing"))
        elif idle > float(self.map.get("idle_sleep", 90.0)):
            self.set_state(self.map.get("sleep", "sleep"))
        elif self.sticky_state:
            if self.state != self.sticky_state:
                self.set_state(self.sticky_state)
        elif self.expr_until and now < self.expr_until:
            pass
        else:
            self.set_state(self.map.get("neutral", "typing"))

        # 鼠标朝向
        g = self.geometry()
        gp = QPoint(g.x() + g.width() // 2, g.y() + g.height() // 2)
        from PySide6.QtGui import QCursor
        c = QCursor.pos()
        dx = (c.x() - gp.x()) / 320.0
        dy = (c.y() - gp.y()) / 320.0
        k = 0.18
        self.look_x += (max(-1.0, min(1.0, dx)) - self.look_x) * k
        self.look_y += (max(-1.0, min(1.0, dy)) - self.look_y) * k

        self.impulse *= 0.86
        self.pop += (1.0 - self.pop) * 0.18
        self.fade = min(1.0, self.fade + 0.12)
        self.update()

        snap = os.environ.get("PET_SNAP")
        if snap and now - self.t0 > 1.2 and not getattr(self, "_snapped", False):
            self._snapped = True
            self.grab().save(snap)

        if not self.dragging and now - self.last_input > 420:
            rect = self.geometry()
            self.cfg["pos"] = [rect.x(), rect.y()]

    # ---------------- 绘制

    def paintEvent(self, _):
        now = time.perf_counter()
        t = now - self.t0
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform, True)

        s = float(self.size_px)
        w = s
        h = s
        y0 = self.PAD_TOP

        # 全局：呼吸 + 摇摆 + 朝鼠标倾 + 按键挤压
        breath = math.sin(t * 1.9 + self.phase)
        sx = 1.0 + 0.010 * breath + 0.020 * self.impulse
        sy = 1.0 - 0.010 * breath - 0.030 * self.impulse
        tilt = self.look_x * 2.0 + math.sin(t * 0.7 + self.phase) * 0.6
        bob = math.sin(t * 1.9 + self.phase) * 1.6 + self.impulse * 4.0

        cx, cy = w / 2.0, y0 + h * 0.85

        def warped(name, t):
            """把整张图按横向切片弯一下，回到 1:1 的离屏图上。
            切片宽度取整，切片之间严丝合缝，缩放交给最后一手，才不会出现横纹。"""
            im = self.img(name)
            w, h = im.width(), im.height()
            m = 28
            out = QImage(w + 2 * m, h + 2 * m, QImage.Format_ARGB32_Premultiplied)
            out.fill(Qt.transparent)
            wp = QPainter(out)
            wp.setRenderHint(QPainter.SmoothPixmapTransform, False)
            bands = 50
            step = h / bands
            for i in range(bands):
                y0 = round(i * step)
                y1 = round((i + 1) * step)
                if y1 <= y0:
                    continue
                mid = (i + 0.5) / bands
                bend = self.look_x * 9.0 * (1.0 - mid) ** 1.7
                bend += math.sin(t * 1.1 + self.phase + mid * 2.2) * 2.2 * (1.0 - mid)
                dx = int(round(bend))
                wp.drawImage(QRectF(m + dx, m + y0, w, y1 - y0), im, QRectF(0, y0, w, y1 - y0))
            wp.end()
            return out, m

        def draw_layer(name, alpha):
            if alpha <= 0.01:
                return
            pic, m = warped(name, t)
            src = self.img(name)
            cw = s * 0.92                      # 画面占窗口的九成，四周留点空
            k = cw / float(src.width())
            p.save()
            p.setOpacity(alpha)
            p.translate(cx, cy + bob)
            p.rotate(tilt)
            p.scale(sx * self.pop, sy * self.pop)
            p.translate(-cw / 2.0, -cw * 0.85)
            p.drawImage(QRectF(-m * k, -m * k, pic.width() * k, pic.height() * k), pic)
            p.restore()

        if self.fade < 1.0 and self.prev_state:
            draw_layer(self.prev_state, 1.0 - self.fade)
        draw_layer(self.state, 1.0)

        self.draw_bubble(p, now, w, t)
        p.end()

    def set_bubble_style(self, style):
        if style not in BUBBLE_STYLES:
            return
        self.bubble_style = style
        self.cfg["bubble"] = style
        self.save_cfg()
        self.bubble_text = "Aa"
        self.bubble_t = time.perf_counter()
        self.update()

    def draw_bubble(self, p, now, w, t):
        if not self.bubble_text:
            return
        age = now - self.bubble_t
        if age > 0.95:
            self.bubble_text = ""
            return
        a = 1.0 if age < 0.6 else max(0.0, 1.0 - (age - 0.6) / 0.35)
        rise = min(1.0, age / 0.12) * 4.0

        style = self.bubble_style
        if style == "云朵":
            f = pick_font(20)
            fm = QFontMetrics(f)
            tw = fm.horizontalAdvance(self.bubble_text)
            bw = max(104.0, tw + 58.0)
            bh = 46.0
        elif style == "无框":
            f = pick_font(15)
            fm = QFontMetrics(f)
            tw = fm.horizontalAdvance(self.bubble_text)
            bw = max(20.0, tw + 12.0)
            bh = 24.0
        else:
            f = pick_font(15)
            fm = QFontMetrics(f)
            tw = fm.horizontalAdvance(self.bubble_text)
            bw = max(34.0, tw + 22.0)
            bh = 28.0
        bx = w / 2.0 + self.bubble_side * 26.0 - bw / 2.0
        by = 12.0 - rise
        tail_x = min(max(w / 2.0, bx + 12.0), bx + bw - 12.0)
        box = QRectF(bx, by, bw, bh)

        p.save()
        p.setOpacity(a)
        p.setFont(f)

        if style == "云朵":
            path = QPainterPath()
            path.addRoundedRect(QRectF(bx + 8, by + bh * 0.44, bw - 16, bh * 0.54),
                                bh * 0.27, bh * 0.27)
            for fx, fy, fd in ((0.21, 0.36, 0.56), (0.39, 0.24, 0.74),
                               (0.58, 0.27, 0.66), (0.76, 0.38, 0.54)):
                d = bh * fd
                c = QPainterPath()
                c.addEllipse(QRectF(bx + bw * fx - d / 2.0, by + bh * fy - d / 2.0, d, d))
                path = path.united(c)
            p.setPen(QPen(QColor(196, 200, 214, 210), 1.6))
            p.setBrush(QColor(255, 255, 255, 246))
            p.drawPath(path)
            p.setPen(QColor(52, 52, 66))
            p.drawText(box.adjusted(0, 6, 0, 0), Qt.AlignCenter, self.bubble_text)

        elif style == "深色":
            path = QPainterPath()
            path.addRoundedRect(box, 9, 9)
            tail = QPainterPath()
            tail.moveTo(tail_x - 6, by + bh - 1.5)
            tail.lineTo(tail_x, by + bh + 8)
            tail.lineTo(tail_x + 6, by + bh - 1.5)
            tail.closeSubpath()
            path = path.united(tail)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(34, 36, 48, 216))
            p.drawPath(path)
            p.setPen(QColor(240, 243, 252))
            p.drawText(box, Qt.AlignCenter, self.bubble_text)

        elif style == "无框":
            path = QPainterPath()
            path.addText(0, 0, f, self.bubble_text)
            br = path.boundingRect()
            sp = QTransform().translate(bx + (bw - br.width()) / 2.0 - br.left(),
                                        by + (bh - br.height()) / 2.0 - br.top()).map(path)
            p.setPen(QPen(QColor(255, 255, 255, 242), 3.4))
            p.setBrush(QColor(255, 255, 255, 242))
            p.drawPath(sp)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(40, 40, 54))
            p.drawPath(sp)

        else:
            p.setOpacity(a * 0.96)
            path = QPainterPath()
            path.addRoundedRect(box, 13, 13)
            tail = QPainterPath()
            tail.moveTo(tail_x - 7, by + bh - 1)
            tail.lineTo(tail_x, by + bh + 9)
            tail.lineTo(tail_x + 7, by + bh - 1)
            tail.closeSubpath()
            path = path.united(tail)
            p.setPen(QPen(QColor(70, 70, 84, 90), 1.2))
            p.setBrush(QColor(255, 255, 255, 236))
            p.drawPath(path)
            p.setPen(QColor(46, 46, 58))
            p.drawText(box, Qt.AlignCenter, self.bubble_text)
        p.restore()

    # ---------------- 鼠标

    def frame_box(self):
        s = float(self.size_px)
        cw = s * 0.92
        left = (s - cw) / 2.0
        top = self.PAD_TOP + s * 0.85 - cw * 0.85
        return cw, left, top

    def hit(self, pos):
        cw, left, top = self.frame_box()
        x = (pos.x() - left) / cw
        y = (pos.y() - top) / cw
        if x < 0 or x > 1 or y < 0 or y > 1:
            return False
        im = self.img(self.state)
        sx = int(x * im.width())
        sy = int(y * im.height())
        return (im.pixel(sx, sy) >> 24) > 24

    def nativeEvent(self, etype, message):
        try:
            et = bytes(etype) if not isinstance(etype, (bytes, bytearray)) else etype
            if et == b"windows_generic_MSG":
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                if msg.message == WM_NCHITTEST:
                    sx = ctypes.c_short(msg.lParam & 0xFFFF).value
                    sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    if not self.hit(self.mapFromGlobal(QPoint(sx, sy))):
                        return True, HTTRANSPARENT
                elif msg.message == WM_INPUT and getattr(self, "_raw_ok", False):
                    self.read_raw(msg.lParam)
        except Exception:
            pass
        return super().nativeEvent(etype, message)

    def setup_rawinput(self):
        """原始输入：直接从设备拿按键，绕过钩子链和输入法。"""
        try:
            user32.RegisterRawInputDevices.restype = wintypes.BOOL
            user32.RegisterRawInputDevices.argtypes = [ctypes.c_void_p,
                                                       wintypes.UINT, wintypes.UINT]
            rid = RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, int(self.winId()))
            ok = user32.RegisterRawInputDevices(ctypes.byref(rid), 1,
                                                ctypes.sizeof(RAWINPUTDEVICE))
            self._raw_ok = bool(ok)
            with open(log_path("_hook.log"), "a", encoding="utf-8") as f:
                f.write("raw=%s err=%s\n" % (self._raw_ok, ctypes.get_last_error()))
        except Exception as ex:
            self._raw_ok = False
            with open(log_path("_hook.log"), "a", encoding="utf-8") as f:
                f.write("raw fail %r\n" % (ex,))

    def read_raw(self, hRawInput):
        size = wintypes.UINT(0)
        user32.GetRawInputData.restype = wintypes.UINT
        user32.GetRawInputData.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                           ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                                           wintypes.UINT]
        head = ctypes.sizeof(RAWINPUTHEADER)
        if not user32.GetRawInputData(ctypes.c_void_p(hRawInput), RID_INPUT, None,
                                      ctypes.byref(size), head):
            buf = ctypes.create_string_buffer(size.value)
            got = user32.GetRawInputData(ctypes.c_void_p(hRawInput), RID_INPUT, buf,
                                         ctypes.byref(size), head)
            if got:
                hdr = RAWINPUTHEADER.from_buffer_copy(buf.raw[:head])
                if hdr.dwType == RIM_TYPEKEYBOARD:
                    kb = RAWKEYBOARD.from_buffer_copy(buf.raw[head:head + ctypes.sizeof(RAWKEYBOARD)])
                    if kb.VKey:
                        down = not (kb.Flags & 0x01)
                        self._pending.append((kb.VKey, down))
                        if getattr(self, "_rlog", None) is None:
                            self._rlog = open(log_path("_raw.log"), "a", encoding="utf-8")
                        self._rlog.write("%.3f vk=%02x %s\n" % (time.time(), kb.VKey,
                                                                "down" if down else "up"))
                        self._rlog.flush()


    def apply_noactivate(self):
        """点她不会把焦点抢走，聊天框还能继续打字。"""
        try:
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            u = ctypes.windll.user32
            u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            h = ctypes.c_void_p(int(self.winId()))
            ex = u.GetWindowLongPtrW(h, GWL_EXSTYLE)
            u.SetWindowLongPtrW(h, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
            with open(log_path("_hook.log"), "a", encoding="utf-8") as f:
                f.write("exstyle %x -> %x\n" % (ex, u.GetWindowLongPtrW(h, GWL_EXSTYLE)))
        except Exception:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.hit(e.position().toPoint()):
            self.press_pos = e.globalPosition().toPoint()
            self.dragging = False
            self.drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.last_input = time.perf_counter()
            self.sticky_state = ""
            self.expr_until = self.last_input + 1.2
            self.set_state(self.map.get("click", "pat"))

    def mouseMoveEvent(self, e):
        if not self.press_pos:
            return
        if not self.dragging:
            if (e.globalPosition().toPoint() - self.press_pos).manhattanLength() < 5:
                return
            self.dragging = True
            self.set_state(self.map.get("drag", "cry"))
        self.move(e.globalPosition().toPoint() - self.drag_off)
        self.last_input = time.perf_counter()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.dragging:
                r = self.geometry()
                self.cfg["pos"] = [r.x(), r.y()]
                self.save_cfg()
                self.set_state(self.map.get("neutral", "typing"))
            elif self.press_pos:
                self.set_state(self.map.get("click", "pat"))
                self.pop = 1.08
            self.dragging = False
            self.press_pos = None

    def mouseDoubleClickEvent(self, e):
        self.set_state(self.map.get("click", "pat"))

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0
        self.resize_pet(self.size_px + int(round(steps * 14)), save=True)

    # ---------------- 菜单

    def resize_pet(self, size, save=False):
        self.size_px = max(140, min(680, int(size)))
        self.resize(QSize(self.size_px, self.size_px + self.PAD_TOP))
        if save:
            self.cfg["size"] = self.size_px
            self.save_cfg()

    def move_to_corner(self):
        g = QApplication.primaryScreen().availableGeometry()
        self.move(g.right() - self.width() - 12, g.bottom() - self.height() - 4)

    def save_cfg(self):
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def pick_expr(self, sid):
        """菜单里选表情：钉住，直到再选别的。选「默认」就放开。"""
        if sid == self.map.get("neutral", "typing") or sid == "talk":
            self.sticky_state = ""
            self.expr_until = 0.0
            self.set_state(self.map.get("neutral", "typing"), force=True)
            return
        if self.sticky_state == sid:
            self.sticky_state = ""
            self.set_state(self.map.get("neutral", "typing"), force=True)
            return
        self.sticky_state = sid
        self.expr_until = 0.0
        self.set_state(sid, force=True)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setFont(pick_font(13))
        states = [("默认", "talk"), ("打字", "typing"), ("生气", "typing_angry"),
                  ("哭", "cry"), ("墨镜", "glasses"), ("摸头", "pat"),
                  ("睡觉", "sleep"), ("馋", "hungry"), ("Bug", "bug"), ("工作", "work")]
        sub = menu.addMenu("换个表情")
        sub.setFont(pick_font(13))
        for label, sid in states:
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, s=sid: self.pick_expr(s))
            sub.addAction(act)
        size_menu = menu.addMenu("大小")
        size_menu.setFont(pick_font(13))
        for label, px in (("小", 180), ("中", 260), ("大", 340), ("超大", 440)):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, v=px: self.resize_pet(v, save=True))
            size_menu.addAction(act)
        bub_menu = menu.addMenu("气泡样式")
        bub_menu.setFont(pick_font(13))
        for style in BUBBLE_STYLES:
            act = QAction(style, self)
            act.setCheckable(True)
            act.setChecked(self.bubble_style == style)
            act.triggered.connect(lambda _=False, s=style: self.set_bubble_style(s))
            bub_menu.addAction(act)
        menu.addSeparator()
        a = QAction("回到右下角", self)
        a.triggered.connect(self.move_to_corner)
        menu.addAction(a)
        a = QAction("退出", self)
        a.triggered.connect(QApplication.quit)
        menu.addAction(a)
        menu.exec(e.globalPos())


def self_integrity():
    """看自己是不是被降权了。低完整性进程收不到别人窗口的按键。"""
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, os.getpid())
        tok = ctypes.c_void_p()
        if not ctypes.windll.advapi32.OpenProcessToken(ctypes.c_void_p(h), 0x0008,
                                                       ctypes.byref(tok)):
            return "?"
        buf = ctypes.create_string_buffer(128)
        need = wintypes.DWORD(0)
        ctypes.windll.advapi32.GetTokenInformation(tok, 25, buf, 128, ctypes.byref(need))
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        cnt = ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte))[1]
        arr = ctypes.cast(sid, ctypes.POINTER(ctypes.c_uint))
        return "%04X" % arr[3] if cnt >= 2 else "?"
    except Exception:
        return "?"


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True) if hasattr(Qt, "AA_EnableHighDpiScaling") else None
    # 只允许开一个，双击两次不会出现两只
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW(None, False, "Global\\dsh_desk_pet_mutex")
    if k32.GetLastError() == 183:
        return
    app = QApplication([])
    app.setQuitOnLastWindowClosed(True)
    pet = Pet()
    with open(log_path("_hook.log"), "a", encoding="utf-8") as f:
        f.write("start pid=%d il=%s\n" % (os.getpid(), self_integrity()))
    pet.show()
    pet.apply_noactivate()
    pet.setup_rawinput()
    pet.install_hooks()
    app.exec()


if __name__ == "__main__":
    main()
