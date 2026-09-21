import ctypes
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageFont
from pystray import Icon, Menu, MenuItem
from ping3 import ping

from taskbar_widget import TaskbarWidget


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


def load_config():
    default = {"target": "8.8.8.8", "interval": 1, "timeout": 2, "show_in_taskbar": True}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                return {**default, **config}
        except (json.JSONDecodeError, IOError):
            pass
    return default


def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except IOError as e:
        print(f"Failed to save config: {e}")


def get_color(latency_ms):
    if latency_ms is None:
        return (128, 128, 128)
    if latency_ms <= 50:
        return (34, 197, 94)
    if latency_ms <= 100:
        return (234, 179, 8)
    return (239, 68, 68)


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


def generate_icon(latency_ms):
    size = 128
    color = get_color(latency_ms)

    img = Image.new("RGB", (size, size), color)
    draw = ImageDraw.Draw(img)

    text = format_latency(latency_ms, "?")
    if latency_ms is None:
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

        self._tk_root = None
        self._dialog = None
        self.taskbar_widget = None

        self.icon.icon = generate_icon(None)
        self.icon.title = f"Ping Monitor - {self.config['target']}"
        self.icon.menu = Menu(
            MenuItem("设置目标地址...", self.open_settings, default=True),
            MenuItem("在任务栏显示数值", self.toggle_taskbar, checked=self._taskbar_checked),
            MenuItem(Menu.SEPARATOR, None, enabled=False),
            MenuItem(f"当前目标: {self.config['target']}", None, enabled=False),
            MenuItem("Ping 间隔: 1s", None, enabled=False),
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

    def _update_taskbar(self):
        widget = self.taskbar_widget
        if widget is None:
            return
        widget.update(format_latency(self.current_latency), get_color(self.current_latency))

    def _get_target(self):
        with self.config_lock:
            return self.config["target"]

    def _set_target(self, value):
        with self.config_lock:
            self.config["target"] = value
            save_config(self.config)

    def _close_dialog(self):
        try:
            if self._dialog and self._dialog.winfo_exists():
                self._dialog.destroy()
        except Exception:
            pass
        self._dialog = None

    def _open_dialog(self, initial_value):
        if self._dialog and self._dialog.winfo_exists():
            self._dialog.lift()
            self._dialog.focus_force()
            return

        dlg = tk.Toplevel(self._tk_root)
        dlg.title("Ping Monitor - 设置")
        dlg.geometry("340x170")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        def on_close():
            self._close_dialog()

        dlg.protocol("WM_DELETE_WINDOW", on_close)

        tk.Label(dlg, text="目标地址 (IP或域名):").pack(pady=(20, 5))

        entry = tk.Entry(dlg, width=32)
        entry.insert(0, initial_value)
        entry.pack(pady=5, padx=20)
        entry.focus_set()
        entry.select_range(0, tk.END)

        result = {"value": None}

        def on_save():
            val = entry.get().strip()
            if not val:
                messagebox.showwarning("警告", "目标地址不能为空", parent=dlg)
                return
            result["value"] = val
            on_close()

        def on_cancel():
            result["value"] = initial_value
            on_close()

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=10)
        tk.Button(btn_frame, text="保存", command=on_save, width=10).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="取消", command=on_cancel, width=10).pack(side=tk.LEFT, padx=5)

        self._dialog = dlg
        dlg.wait_window(dlg)

        if result["value"] is not None and result["value"] != initial_value:
            self._set_target(result["value"])

    def open_settings(self, icon=None, item=None):
        if not self._tk_root or not self._tk_root.winfo_exists():
            return
        target = self._get_target()
        self._tk_root.after(0, lambda: self._open_dialog(target))

    def update_ping(self):
        while not self.stop_event.is_set():
            try:
                target = self._get_target()
                latency = ping(target, timeout=self.config.get("timeout", 2), unit="ms")
                self.current_latency = latency
            except Exception as e:
                print(f"Ping error: {e}")
                self.current_latency = None

            self.icon.icon = generate_icon(self.current_latency)

            if self.current_latency is None:
                title = "Ping Monitor - 超时/错误"
            else:
                title = f"Ping Monitor - {int(self.current_latency)} ms"
            self.icon.title = title

            target = self._get_target()
            latency_text = "超时" if self.current_latency is None else f"{int(self.current_latency)} ms"
            self.icon.menu = Menu(
                MenuItem("设置目标地址...", self.open_settings, default=True),
                MenuItem("在任务栏显示数值", self.toggle_taskbar, checked=self._taskbar_checked),
                MenuItem(Menu.SEPARATOR, None, enabled=False),
                MenuItem(f"当前目标: {target}", None, enabled=False),
                MenuItem(f"延迟: {latency_text}", None, enabled=False),
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
