import ctypes
import json
import math
import os
import statistics
import sys
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageFont
from pystray import Icon, Menu, MenuItem
from ping3 import ping

from taskbar_widget import TaskbarWidget, frame_rect

try:
    import winreg
except ImportError:  # 非 Windows 平台
    winreg = None


def _get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def enable_dpi_awareness():
    """按物理像素布局窗口，避免任务栏挂件被系统缩放后发虚。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


CONFIG_FILE = os.path.join(_get_app_dir(), "config.json")

DEFAULT_CONFIG = {
    "target": "8.8.8.8",
    "interval": 1,
    "timeout": 2,
    "show_in_taskbar": True,
    # 颜色阈值（毫秒）：≤good 绿、≤fair 黄、>fair 红。
    # 100ms 当红线对公网目标太严，默认放宽到 80/150；内网目标可改成 20/50 之类。
    "thresholds": {"good": 80, "fair": 150},
}

SAMPLE_WINDOW = 3  # 用最近 3 次采样的中位数判色，避免单次抖动导致红黄横跳


def load_config():
    config = {**DEFAULT_CONFIG, "thresholds": dict(DEFAULT_CONFIG["thresholds"])}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, IOError):
            saved = None
        if isinstance(saved, dict):
            thresholds = saved.pop("thresholds", None)
            config.update(saved)
            if isinstance(thresholds, dict):
                config["thresholds"].update(thresholds)
    good, fair = normalize_thresholds(config["thresholds"])
    config["thresholds"] = {"good": good, "fair": fair}
    return config


def normalize_thresholds(raw):
    """校验阈值：必须是正数且 good < fair，否则整体退回默认值。"""
    good_default = DEFAULT_CONFIG["thresholds"]["good"]
    fair_default = DEFAULT_CONFIG["thresholds"]["fair"]

    def _num(name, value, fallback):
        try:
            number = float(value)
        except (TypeError, ValueError):
            print(f"配置里的颜色阈值 {name}={value!r} 不是数字，已用默认值 {fallback}")
            return float(fallback)
        if number <= 0:
            print(f"配置里的颜色阈值 {name}={value!r} 必须是正数，已用默认值 {fallback}")
            return float(fallback)
        return number

    good = _num("good", raw.get("good"), good_default)
    fair = _num("fair", raw.get("fair"), fair_default)
    if good >= fair:
        print(f"配置里的颜色阈值不合理 (good={good:g}, fair={fair:g})，"
              f"已退回默认 {good_default}/{fair_default}")
        return float(good_default), float(fair_default)
    return good, fair


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except IOError as e:
        print(f"Failed to save config: {e}")


# 状态：还没测到 / 绿 / 黄 / 红 / 全部超时
UNKNOWN, GOOD, FAIR, BAD, DOWN = "unknown", "good", "fair", "bad", "down"

# 托盘图标是"色块 + 数字"，底色由色块自带，文字另按亮度自动选黑/白
_ICON_COLORS = {
    GOOD: (34, 197, 94),       # #22C55E
    FAIR: (234, 179, 8),       # #EAB308
    BAD: (220, 38, 38),        # #DC2626（白字 4.8:1，原来的 #EF4444 只有 3.8:1）
    UNKNOWN: (107, 114, 128),  # #6B7280（白字 4.8:1）
}

# 任务栏挂件是"透明背景 + 彩色文字"，文字对比度完全取决于任务栏底色，
# 所以浅色任务栏要换一套深一档的颜色（注释里是对应底色下的对比度）。
_WIDGET_COLORS = {
    "dark": {   # 深色任务栏（约 #1F1F1F）
        GOOD: (34, 197, 94),       # #22C55E  7.2:1
        FAIR: (234, 179, 8),       # #EAB308  8.6:1
        BAD: (240, 82, 82),        # #F05252  4.7:1（#EF4444 只有 4.4:1）
        UNKNOWN: (156, 163, 175),  # #9CA3AF  6.5:1
    },
    "light": {  # 浅色任务栏（约 #F3F3F3）
        GOOD: (22, 101, 52),       # #166534  6.4:1
        FAIR: (133, 77, 14),       # #854D0E  6.2:1
        BAD: (185, 28, 28),        # #B91C1C  5.8:1
        UNKNOWN: (75, 85, 99),     # #4B5563  6.8:1
    },
}


def level_of(latency_ms, thresholds):
    """按阈值把延迟值映射到颜色档位。"""
    good, fair = thresholds
    if latency_ms <= good:
        return GOOD
    if latency_ms <= fair:
        return FAIR
    return BAD


def icon_color(state):
    # "全部超时"用红色而不是灰色：最严重的状态不该是最不显眼的颜色
    return _ICON_COLORS[BAD if state == DOWN else state]


def widget_color(state):
    palette = _WIDGET_COLORS["light" if taskbar_is_light() else "dark"]
    return palette[BAD if state == DOWN else state]


def thresholds_text(thresholds):
    good, fair = thresholds
    return f"颜色规则: ≤{good:g} 绿 / ≤{fair:g} 黄 / >{fair:g} 红"


_theme_cache = {"checked_at": -999.0, "light": False}
_THEME_TTL = 5.0


def taskbar_is_light():
    """任务栏当前是不是浅色主题（结果缓存 5 秒，不必每轮都读注册表）。"""
    now = time.monotonic()
    if now - _theme_cache["checked_at"] < _THEME_TTL:
        return _theme_cache["light"]
    light = False
    if winreg is not None:
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                light = bool(winreg.QueryValueEx(key, "SystemUsesLightTheme")[0])
        except OSError:
            light = False  # 读不到就按深色任务栏处理
    _theme_cache.update(checked_at=now, light=light)
    return light


def pick_text_color(rgb):
    """按背景亮度选择黑字或白字。"""
    luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return (0, 0, 0) if luminance > 128 else (255, 255, 255)


def format_latency(latency_ms, placeholder="--"):
    """把延迟值格式化成用于显示的短文本。"""
    if latency_ms is None:
        return placeholder
    if latency_ms >= 1000:
        return "999+"
    return str(int(latency_ms))


def widget_text(state, latency_ms):
    """挂件文本。挂件字体是 Arial，不能用中文（会缺字）。"""
    if state == UNKNOWN:
        return "--"
    if state == DOWN:
        return "×"
    return format_latency(latency_ms)


def icon_text(state, latency_ms):
    if state == UNKNOWN:
        return "?"
    if state == DOWN:
        return "×"
    return format_latency(latency_ms, "?")


def status_text(state, latency_ms, lost, total):
    """托盘菜单/提示里的一句话状态。"""
    if state == UNKNOWN:
        return "等待测量"
    if state == DOWN:
        return f"超时/无响应（丢包 {total}/{total}）"
    text = f"{int(latency_ms)} ms"
    if lost:
        text += f" · 丢包 {lost}/{total}"
    return text


def probe(target, timeout):
    """测一次 RTT。ping3 用 False 表示解析失败等错误、None 表示超时，统一转成 None。"""
    try:
        value = ping(target, timeout=timeout, unit="ms")
    except Exception as e:
        print(f"Ping error: {e}")
        return None
    if value is None or isinstance(value, bool) or value <= 0:
        return None
    return float(value)


MIN_INTERVAL = 0.2  # 秒。间隔再小就等于连续发包了


def number_text(value):
    """配置里的数值 -> 输入框文本（1.0 显示成 "1"）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "0"
    return str(int(number)) if number.is_integer() else f"{number:g}"


def tidy_number(value):
    """整数就存成整数，免得 config.json 里出现 1.0 这种写法。"""
    return int(value) if float(value).is_integer() else value


def parse_settings(target, interval_text, timeout_text, good_text, fair_text, show_in_taskbar):
    """校验设置对话框的输入，返回 (配置字典, 错误信息)；两者只有一个不是 None。"""
    target = str(target).strip()
    if not target:
        return None, "目标地址不能为空"

    numbers = {}
    for name, text in (("Ping 间隔", interval_text), ("超时时间", timeout_text),
                      ("绿色阈值", good_text), ("黄色阈值", fair_text)):
        try:
            numbers[name] = float(str(text).strip())
        except ValueError:
            return None, f"{name}必须是数字"
        if not math.isfinite(numbers[name]):
            return None, f"{name}必须是有限的数字"

    interval = numbers["Ping 间隔"]
    timeout = numbers["超时时间"]
    good = numbers["绿色阈值"]
    fair = numbers["黄色阈值"]
    if interval < MIN_INTERVAL:
        return None, f"Ping 间隔不能小于 {MIN_INTERVAL:g} 秒"
    if timeout <= 0:
        return None, "超时时间必须大于 0"
    if good <= 0 or fair <= 0:
        return None, "颜色阈值必须是正数"
    if good >= fair:
        return None, "绿色阈值必须小于黄色阈值"

    return {
        "target": target,
        "interval": tidy_number(interval),
        "timeout": tidy_number(timeout),
        "show_in_taskbar": bool(show_in_taskbar),
        "thresholds": {"good": good, "fair": fair},
    }, None


def center_position(screen_size, window_size):
    """让 window_size 大小的窗口居中的左上角坐标。"""
    screen_w, screen_h = screen_size
    win_w, win_h = window_size
    x = max(0, (screen_w - win_w) // 2)
    y = max(0, (screen_h - win_h) // 2)
    return x, y


def center_geometry(screen_size, window_size):
    """返回居中用的 geometry 字符串。"""
    x, y = center_position(screen_size, window_size)
    return f"{window_size[0]}x{window_size[1]}+{x}+{y}"


def window_frame_size(win, fallback):
    """窗口外框尺寸（含标题栏与边框）；量不到就用客户区尺寸兜底。"""
    try:
        rect = frame_rect(win.winfo_id())
    except Exception:
        return fallback
    if rect is None:
        return fallback
    return rect.right - rect.left, rect.bottom - rect.top


def center_window(win):
    """把窗口连同标题栏和边框一起摆到屏幕正中。

    Tk 的 geometry("WxH+X+Y") 中 X/Y 定位的是窗口外框左上角，而 WxH 是客户区尺寸，
    所以先按客户区尺寸定身，量出外框，再用外框尺寸算居中位置。
    """
    win.update_idletasks()
    client_w, client_h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"{client_w}x{client_h}")
    win.update_idletasks()
    frame_w, frame_h = window_frame_size(win, (client_w, client_h))
    x, y = center_position((win.winfo_screenwidth(), win.winfo_screenheight()),
                           (frame_w, frame_h))
    win.geometry(f"{client_w}x{client_h}+{x}+{y}")


def summarize(history, thresholds):
    """把最近几次采样归纳成 (状态, 显示用延迟, 丢包数, 采样数)。"""
    samples = list(history)
    if not samples:
        return UNKNOWN, None, 0, 0
    measured = [v for v in samples if v is not None]
    lost = len(samples) - len(measured)
    if not measured:
        return DOWN, None, lost, len(samples)
    median = statistics.median(measured)
    state = level_of(median, thresholds)
    if lost:
        # 有丢包就至少黄：丢包的链路和"只是慢"的链路不是一回事
        state = BAD if lost >= 2 else (FAIR if state == GOOD else state)
    return state, median, lost, len(samples)


def generate_icon(state, latency_ms):
    size = 128
    color = icon_color(state)

    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)

    text = icon_text(state, latency_ms)
    if state == UNKNOWN:
        font_size = 100
    elif len(text) >= 4:
        font_size = 48
    elif len(text) == 3:
        font_size = 80
    else:
        font_size = 96

    font = None
    for fname in ["arialbd.ttf", "arial.ttf", "msyhbd.ttc", "msyh.ttc"]:
        try:
            font = ImageFont.truetype(fname, font_size)
            break
        except IOError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text_bbox = draw.textbbox((0, 0), text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    x = (size - text_width) // 2 - text_bbox[0]
    y = (size - text_height) // 2 - text_bbox[1]

    draw.text((x, y), text, fill=pick_text_color(color), font=font)

    return img


class PingTrayMonitor:
    def __init__(self):
        self.config = load_config()
        self.config_lock = threading.Lock()
        self.icon = Icon("Ping Monitor")
        self.stop_event = threading.Event()
        self.current_latency = None
        self.state = UNKNOWN
        self.loss = (0, 0)

        self._tk_root = None
        self._dialog = None
        self.taskbar_widget = None

        self.icon.icon = generate_icon(UNKNOWN, None)
        self.icon.title = f"Ping Monitor - {self.config['target']}"
        self.icon.menu = Menu(
            MenuItem("设置...", self.open_settings, default=True),
            MenuItem("在任务栏显示数值", self.toggle_taskbar, checked=self._taskbar_checked),
            MenuItem(Menu.SEPARATOR, None, enabled=False),
            MenuItem(f"当前目标: {self.config['target']}", None, enabled=False),
            MenuItem(f"Ping 间隔: {self.config.get('interval', 1)}s", None, enabled=False),
            MenuItem(thresholds_text(self._thresholds()), None, enabled=False),
            MenuItem(Menu.SEPARATOR, None, enabled=False),
            MenuItem("退出", self.quit),
        )

        self._tk_thread = threading.Thread(target=self._tk_main, daemon=True)
        self._tk_thread.start()

    def _tk_main(self):
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self._tk_root.title("Ping Monitor")
        self._tk_root.protocol("WM_DELETE_WINDOW", self._tk_root.quit)
        try:
            self.taskbar_widget = TaskbarWidget(enabled=self._taskbar_checked())
        except Exception as e:
            print(f"任务栏挂件不可用: {e}")
            self.taskbar_widget = None
        self._tk_root.mainloop()

    def _taskbar_checked(self, item=None):
        with self.config_lock:
            return bool(self.config.get("show_in_taskbar", True))

    def toggle_taskbar(self, icon=None, item=None):
        with self.config_lock:
            enabled = not self.config.get("show_in_taskbar", True)
            self.config["show_in_taskbar"] = enabled
            save_config(self.config)

        widget = self.taskbar_widget
        if widget is None:
            return
        widget.set_enabled(enabled)

    def _thresholds(self):
        with self.config_lock:
            thresholds = self.config.get("thresholds") or {}
        return (thresholds.get("good", DEFAULT_CONFIG["thresholds"]["good"]),
                thresholds.get("fair", DEFAULT_CONFIG["thresholds"]["fair"]))

    def _update_taskbar(self):
        widget = self.taskbar_widget
        if widget is None:
            return
        state = self.state
        widget.update(widget_text(state, self.current_latency), widget_color(state))

    def _get_target(self):
        with self.config_lock:
            return self.config["target"]

    def _apply_settings(self, values):
        """把设置对话框的结果写回 config.json 并立即生效。"""
        with self.config_lock:
            changed = {key: value for key, value in values.items()
                       if self.config.get(key) != value}
            if not changed:
                return  # 什么都没改，不必重写配置文件
            self.config.update(changed)
            save_config(self.config)
        # 目标/间隔/超时/阈值都是 Ping 循环逐轮读取的，下一轮自动生效（换目标时循环自己会
        # 清空采样窗口），这里只需要处理挂件开关这种"立刻要变"的副作用。
        if "show_in_taskbar" in changed:
            widget = self.taskbar_widget
            if widget is not None:
                widget.set_enabled(changed["show_in_taskbar"])

    def _close_dialog(self):
        try:
            if self._dialog and self._dialog.winfo_exists():
                self._dialog.destroy()
        except Exception:
            pass
        self._dialog = None

    def _open_dialog(self, values):
        if self._dialog and self._dialog.winfo_exists():
            self._dialog.lift()
            self._dialog.focus_force()
            return

        dlg = tk.Toplevel(self._tk_root)
        dlg.title("Ping Monitor - 设置")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        def on_close():
            self._close_dialog()

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        form = tk.Frame(dlg, padx=18, pady=14)
        form.pack(fill=tk.BOTH, expand=True)
        form.columnconfigure(1, weight=1)

        def add_label(text, row):
            tk.Label(form, text=text, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=(0, 8))

        thresholds = values.get("thresholds") or {}

        add_label("目标地址 (IP 或域名):", 0)
        target_entry = tk.Entry(form, width=24)
        target_entry.insert(0, str(values.get("target", "")))
        target_entry.grid(row=0, column=1, sticky="we", pady=(0, 8))

        add_label("Ping 间隔 (秒):", 1)
        interval_entry = tk.Entry(form, width=10)
        interval_entry.insert(0, number_text(values.get("interval", 1)))
        interval_entry.grid(row=1, column=1, sticky="w", pady=(0, 8))

        add_label("超时时间 (秒):", 2)
        timeout_entry = tk.Entry(form, width=10)
        timeout_entry.insert(0, number_text(values.get("timeout", 2)))
        timeout_entry.grid(row=2, column=1, sticky="w", pady=(0, 8))

        add_label("颜色阈值 (毫秒):", 3)
        threshold_frame = tk.Frame(form)
        threshold_frame.grid(row=3, column=1, sticky="w", pady=(0, 8))
        tk.Label(threshold_frame, text="绿 ≤").pack(side=tk.LEFT)
        good_entry = tk.Entry(threshold_frame, width=7)
        good_entry.insert(0, number_text(thresholds.get("good",
                                                        DEFAULT_CONFIG["thresholds"]["good"])))
        good_entry.pack(side=tk.LEFT, padx=(3, 12))
        tk.Label(threshold_frame, text="黄 ≤").pack(side=tk.LEFT)
        fair_entry = tk.Entry(threshold_frame, width=7)
        fair_entry.insert(0, number_text(thresholds.get("fair",
                                                        DEFAULT_CONFIG["thresholds"]["fair"])))
        fair_entry.pack(side=tk.LEFT, padx=(3, 0))

        tk.Label(form, text="超过黄色阈值的延迟显示为红色；超时/无响应显示 ×",
                 anchor="w", fg="#666666").grid(row=4, column=0, columnspan=2,
                                                sticky="w", pady=(0, 10))

        show_taskbar = tk.BooleanVar(value=bool(values.get("show_in_taskbar", True)))
        tk.Checkbutton(form, text="在任务栏显示数值挂件", variable=show_taskbar,
                       anchor="w").grid(row=5, column=0, columnspan=2, sticky="w")

        result = {"values": None}

        def on_save():
            parsed, error = parse_settings(target_entry.get(), interval_entry.get(),
                                           timeout_entry.get(), good_entry.get(),
                                           fair_entry.get(), show_taskbar.get())
            if error:
                messagebox.showwarning("提示", error, parent=dlg)
                return
            result["values"] = parsed
            on_close()

        def on_cancel():
            on_close()

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=(0, 14))
        tk.Button(btn_frame, text="保存", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消", command=on_cancel, width=10).pack(side=tk.LEFT, padx=5)

        dlg.bind("<Return>", lambda event: on_save())
        dlg.bind("<Escape>", lambda event: on_cancel())

        center_window(dlg)
        target_entry.focus_set()
        target_entry.select_range(0, tk.END)

        self._dialog = dlg
        dlg.wait_window(dlg)

        if result["values"] is not None:
            self._apply_settings(result["values"])

    def open_settings(self, icon=None, item=None):
        if not self._tk_root or not self._tk_root.winfo_exists():
            return
        with self.config_lock:
            values = {
                "target": self.config["target"],
                "interval": self.config.get("interval", 1),
                "timeout": self.config.get("timeout", 2),
                "show_in_taskbar": bool(self.config.get("show_in_taskbar", True)),
                "thresholds": dict(self.config.get("thresholds") or {}),
            }
        self._tk_root.after(0, lambda: self._open_dialog(values))

    def update_ping(self):
        history = deque(maxlen=SAMPLE_WINDOW)
        last_target = None
        while not self.stop_event.is_set():
            target = self._get_target()
            if target != last_target:
                history.clear()  # 换了目标，旧样本没有参考价值
                last_target = target

            timeout = self.config.get("timeout", 2)
            history.append(probe(target, timeout))
            state, latency, lost, total = summarize(history, self._thresholds())
            self.state = state
            self.current_latency = latency
            self.loss = (lost, total)

            self.icon.icon = generate_icon(state, latency)

            if state == UNKNOWN:
                title = "Ping Monitor - 等待测量"
            elif state == DOWN:
                title = "Ping Monitor - 超时/无响应"
            else:
                title = f"Ping Monitor - {int(latency)} ms"
            self.icon.title = title

            latency_text = status_text(state, latency, lost, total)
            self.icon.menu = Menu(
                MenuItem("设置...", self.open_settings, default=True),
                MenuItem("在任务栏显示数值", self.toggle_taskbar, checked=self._taskbar_checked),
                MenuItem(Menu.SEPARATOR, None, enabled=False),
                MenuItem(f"当前目标: {target}", None, enabled=False),
                MenuItem(f"延迟: {latency_text}", None, enabled=False),
                MenuItem(f"Ping 间隔: {self.config.get('interval', 1)}s", None, enabled=False),
                MenuItem(thresholds_text(self._thresholds()), None, enabled=False),
                MenuItem(Menu.SEPARATOR, None, enabled=False),
                MenuItem("退出", self.quit),
            )

            self._update_taskbar()

            self.stop_event.wait(self.config.get("interval", 1))

    def quit(self, icon=None, item=None):
        self.stop_event.set()
        widget = self.taskbar_widget
        self.taskbar_widget = None
        if widget is not None:
            widget.close()
        if self._tk_root and self._tk_root.winfo_exists():
            self._tk_root.after(0, self._tk_root.quit)
        self.icon.stop()

    def run(self):
        threading.Thread(target=self.update_ping, daemon=True).start()
        self.icon.run()


if __name__ == "__main__":
    enable_dpi_awareness()
    print("正在启动 Ping Monitor...")
    print(f"默认目标: {load_config()['target']}")
    monitor = PingTrayMonitor()
    monitor.run()
