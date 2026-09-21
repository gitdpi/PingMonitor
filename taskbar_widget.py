# -*- coding: utf-8 -*-
"""Windows 任务栏延迟挂件。

实现要点：

* Win11 的任务栏由 XAML 渲染。把窗口 SetParent 到 Shell_TrayWnd 虽然会成功，但 GDI 子窗口
  会被任务栏的 XAML 图层完全盖住，因此挂件是贴在任务栏空白区（任务栏按钮区右侧、通知区域
  左侧）的独立置顶窗口，周期性重新贴合位置。
* 背景要透明、文字要保持彩色，普通窗口做不到（颜色键透明会在抗锯齿边缘留下杂色），
  所以用分层窗口（WS_EX_LAYERED + UpdateLayeredWindow）直接送一张 RGBA 位图。
* 任务栏和"隐藏的图标"溢出面板出现时会把其它 topmost 窗口压到下面，挂件会看不见且不会
  自己恢复。把任务栏设为挂件的 owner 后，挂件始终跟随任务栏的层级，不再被压下去。
* Windows 会在 owner 销毁时连带销毁 owned 窗口（Explorer 重启、任务栏自动隐藏），
  所以每次同步都检查窗口与 owner 是否仍然有效，失效就重建。
"""

import ctypes
import threading
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont

__all__ = ["TaskbarWidget"]

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_kernel32 = ctypes.windll.kernel32

_GWL_EXSTYLE = -20
_GWLP_HWNDPARENT = -8
_WS_EX_LAYERED = 0x00080000
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOPMOST = 0x00000008
_WS_POPUP = 0x80000000
_WS_VISIBLE = 0x10000000
_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_PM_REMOVE = 0x0001
_QS_ALLINPUT = 0x04FF

_SM_CXSCREEN = 0
_SM_CYSCREEN = 1

_TASKBAR_CLASS = "Shell_TrayWnd"
_TRAY_CLASS = "TrayNotifyWnd"
_REBAR_CLASS = "ReBarWindow32"

_WIDGET_SIZE = 88  # 挂件宽度（物理像素），纵向任务栏时作为高度
_MARGIN = 8
_FONT_FILE = "arialbd.ttf"
_MIN_BAR_SIZE = 24
_SYNC_INTERVAL = 0.3  # 秒
_WINDOW_CLASS = "PingMonitorTaskbarWidget"


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                              ctypes.c_ulonglong, ctypes.c_longlong)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


_user32.DefWindowProcW.restype = ctypes.c_longlong
_user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]
_user32.RegisterClassW.restype = wintypes.WORD
_user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowExW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.UpdateLayeredWindow.restype = wintypes.BOOL
_user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                        ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                        ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
                                        ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD]
_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                 ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
_user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
_user32.MsgWaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
                                              wintypes.BOOL, wintypes.DWORD, wintypes.DWORD]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong]

_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.CreateDIBSection.restype = wintypes.HANDLE
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.SelectObject.restype = wintypes.HANDLE
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HANDLE]


def _window_rect(hwnd):
    rect = _RECT()
    if not hwnd or not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_fullscreen_foreground():
    """前台是否全屏窗口：全屏时任务栏被遮住，挂件也应隐藏。"""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or _class_name(hwnd) in ("Progman", "WorkerW"):
        return False
    rect = _window_rect(hwnd)
    if rect is None:
        return False
    return (rect.left <= 0 and rect.top <= 0
            and rect.right >= _user32.GetSystemMetrics(_SM_CXSCREEN)
            and rect.bottom >= _user32.GetSystemMetrics(_SM_CYSCREEN))


def _load_font(font_px):
    try:
        return ImageFont.truetype(_FONT_FILE, font_px)
    except IOError:
        return ImageFont.load_default()


def _render(text, color, width, height, font_px):
    """渲染一张透明背景、彩色文字的 RGBA 图。"""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(font_px)
    box = draw.textbbox((0, 0), text, font=font)
    x = (width - (box[2] - box[0])) // 2 - box[0]
    y = (height - (box[3] - box[1])) // 2 - box[1]
    draw.text((x, y), text, font=font, fill=(color[0], color[1], color[2], 255))
    return img


def _push(hwnd, img, x, y):
    """把 RGBA 图送进分层窗口（含逐像素 alpha）。"""
    width, height = img.size
    bgra = img.tobytes("raw", "BGRA")

    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB

    screen_dc = _user32.GetDC(None)
    mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
    bits = ctypes.c_void_p()
    bitmap = _gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    if not bitmap:
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(None, screen_dc)
        return False

    ctypes.memmove(bits, bgra, len(bgra))
    old = _gdi32.SelectObject(mem_dc, bitmap)
    size = wintypes.SIZE(width, height)
    src = wintypes.POINT(0, 0)
    dst = wintypes.POINT(x, y)
    blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)
    ok = _user32.UpdateLayeredWindow(hwnd, screen_dc, ctypes.byref(dst), ctypes.byref(size),
                                     mem_dc, ctypes.byref(src), 0, ctypes.byref(blend), _ULW_ALPHA)

    _gdi32.SelectObject(mem_dc, old)
    _gdi32.DeleteObject(bitmap)
    _gdi32.DeleteDC(mem_dc)
    _user32.ReleaseDC(None, screen_dc)
    return bool(ok)


def _wnd_proc(hwnd, msg, wparam, lparam):
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_wnd_proc_ref = _WNDPROC(_wnd_proc)
_class_registered = False
_class_lock = threading.Lock()


def _ensure_class():
    global _class_registered
    with _class_lock:
        if _class_registered:
            return
        name = ctypes.create_unicode_buffer(_WINDOW_CLASS)
        wc = _WNDCLASSW()
        wc.lpfnWndProc = _wnd_proc_ref
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.lpszClassName = ctypes.cast(name, wintypes.LPCWSTR)
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(err)
        _class_registered = True


class TaskbarWidget:
    """贴在任务栏上的延迟数值挂件：透明背景、彩色文字、置顶且不抢焦点。"""

    def __init__(self, enabled=True):
        self._lock = threading.Lock()
        self._text = "--"
        self._color = (128, 128, 128)
        self._enabled = bool(enabled)
        self._closed = False
        self._stop = threading.Event()

        self._hwnd = None
        self._owner = None
        self._state = None

        self._thread = threading.Thread(target=self._run, name="taskbar-widget", daemon=True)
        self._thread.start()

    # --- 对外接口（可从任意线程调用）------------------------------------------------

    def update(self, text, color):
        """更新显示文本与文字颜色。"""
        with self._lock:
            self._text = text
            self._color = (int(color[0]), int(color[1]), int(color[2]))

    def set_enabled(self, enabled):
        """开启/关闭任务栏显示。"""
        with self._lock:
            self._enabled = bool(enabled)

    def close(self):
        self._closed = True
        self._stop.set()
        hwnd = self._hwnd
        if hwnd and _user32.IsWindow(hwnd):
            _user32.PostMessageW(hwnd, 0, 0, 0)  # 唤醒正在等待消息的渲染线程
        self._thread.join(timeout=2.0)

    # --- 渲染线程 -----------------------------------------------------------------

    def _run(self):
        try:
            _ensure_class()
            self._create_window()
        except Exception as e:
            print(f"任务栏挂件创建失败: {e}")
            return

        msg = wintypes.MSG()
        while not self._stop.is_set():
            while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            try:
                self._sync()
            except Exception:
                pass
            if self._stop.is_set():
                break
            # 等新消息或超时。不能用 Event.wait：任务栏重排 z-order 时会同步等待本窗口响应，
            # 死等 0.3 秒会把 Explorer 一起拖住（表现为点击"隐藏的图标"卡顿）。
            _user32.MsgWaitForMultipleObjects(0, None, False, int(_SYNC_INTERVAL * 1000),
                                              _QS_ALLINPUT)

        if self._hwnd and _user32.IsWindow(self._hwnd):
            _user32.DestroyWindow(self._hwnd)
        self._hwnd = None

    def _create_window(self):
        taskbar = _user32.FindWindowW(_TASKBAR_CLASS, None)
        bar = _window_rect(taskbar)
        height = (bar.bottom - bar.top) if bar else 48
        self._hwnd = _user32.CreateWindowExW(
            _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST,
            _WINDOW_CLASS, "Ping Monitor", _WS_POPUP | _WS_VISIBLE,
            0, 0, _WIDGET_SIZE, height, taskbar, None, _kernel32.GetModuleHandleW(None), None)
        if not self._hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._owner = taskbar
        self._state = None

    def _sync(self):
        if self._closed:
            return
        if not self._hwnd or not _user32.IsWindow(self._hwnd):
            # owner（任务栏）销毁会连带销毁挂件，Explorer 重启后在这里重建
            self._create_window()

        taskbar = _user32.FindWindowW(_TASKBAR_CLASS, None)
        if taskbar and taskbar != self._owner:
            # 让挂件始终跟随任务栏层级，避免被"隐藏的图标"面板压到下面
            _user32.SetWindowLongPtrW(self._hwnd, _GWLP_HWNDPARENT, ctypes.c_void_p(taskbar))
            self._owner = taskbar

        with self._lock:
            enabled = self._enabled
            text = self._text
            color = self._color

        geometry = self._measure() if enabled else None
        if geometry is None:
            if _user32.IsWindowVisible(self._hwnd):
                _user32.ShowWindow(self._hwnd, 0)  # SW_HIDE
            self._state = None
            return

        x, y, width, height, font_px = geometry
        state = (text, color, x, y, width, height, font_px)
        if state == self._state:
            return
        if not _user32.IsWindowVisible(self._hwnd):
            _user32.ShowWindow(self._hwnd, 5)  # SW_SHOW
        if _push(self._hwnd, _render(text, color, width, height, font_px), x, y):
            self._state = state

    def _measure(self):
        """计算挂件几何位置与字号；任务栏不可用时返回 None。"""
        taskbar = _user32.FindWindowW(_TASKBAR_CLASS, None)
        if not taskbar or not _user32.IsWindowVisible(taskbar):
            return None
        bar = _window_rect(taskbar)
        if bar is None:
            return None
        bar_width = bar.right - bar.left
        bar_height = bar.bottom - bar.top
        if bar_width < _MIN_BAR_SIZE or bar_height < _MIN_BAR_SIZE:
            return None

        screen_width = _user32.GetSystemMetrics(_SM_CXSCREEN)
        screen_height = _user32.GetSystemMetrics(_SM_CYSCREEN)
        if (bar.right <= 0 or bar.left >= screen_width
                or bar.bottom <= 0 or bar.top >= screen_height):
            return None  # 任务栏自动隐藏中
        if _is_fullscreen_foreground():
            return None

        tray = _user32.FindWindowExW(taskbar, None, _TRAY_CLASS, None)
        tray_rect = _window_rect(tray)

        if bar_width >= bar_height:
            # 横向任务栏（在屏幕底部或顶部）：贴在通知区域左侧
            right_edge = tray_rect.left if tray_rect else bar.right
            left_limit = bar.left
            rebar_rect = _window_rect(_user32.FindWindowExW(taskbar, None, _REBAR_CLASS, None))
            if rebar_rect:
                left_limit = rebar_rect.right
            x = right_edge - _WIDGET_SIZE - _MARGIN
            if x < left_limit + _MARGIN:
                x = left_limit + _MARGIN
            return (x, bar.top, _WIDGET_SIZE, bar_height, max(12, int(bar_height * 0.5)))

        # 纵向任务栏（在屏幕左侧或右侧）：贴在通知区域上方
        bottom_edge = tray_rect.top if tray_rect else bar.bottom
        size = min(bar_width, _WIDGET_SIZE)
        y = bottom_edge - size - _MARGIN
        if y < bar.top + _MARGIN:
            y = bar.top + _MARGIN
        return (bar.left, y, bar_width, size, max(12, int(size * 0.5)))
