# 变更记录

## 2026-09-21 · 任务栏延迟挂件

### 新增

**任务栏挂件**（`taskbar_widget.py`）

- 在任务栏空白区（通知区域左侧、任务栏按钮区右侧）显示延迟数字，配色与托盘图标一致：
  绿 ≤50ms、黄 ≤100ms、红 >100ms、灰为超时
- 透明背景 + 彩色文字：用分层窗口（`WS_EX_LAYERED` + `UpdateLayeredWindow`）直接送 RGBA 位图。
  普通窗口做不到，颜色键透明（`-transparentcolor`）会在抗锯齿的文字边缘留下底色杂边，已弃用
- 不进 Alt+Tab（`WS_EX_TOOLWINDOW`）、点击不抢焦点（`WS_EX_NOACTIVATE`）
- 每 0.3 秒重新贴合任务栏，跟随任务栏位置、高度、DPI 缩放变化；任务栏按钮区变宽时自动让位
- 前台窗口全屏、任务栏自动隐藏或不可见时自动隐藏

**`show_in_taskbar` 配置项**（`main.py`）

- 控制是否显示任务栏挂件，默认 `true`，老配置文件缺这个键也能正常工作
- 右键托盘图标 → “在任务栏显示数值” 可随时开关，即时生效并写回配置文件

**DPI 感知**（`main.py`）

- 启动时声明 SYSTEM_DPI_AWARE，挂件按物理像素渲染，避免被系统缩放后发虚

### 修复

**点击“隐藏的图标”后任务栏挂件消失**（`taskbar_widget.py`）

- 现象：点开托盘溢出面板后挂件看不见了，收起面板也不恢复
- 原因：任务栏和溢出面板是 Win11 的 XAML 顶层窗口，出现时会把自己提升到 topmost 组顶部，
  把挂件压到任务栏内容层之下（窗口既没隐藏也没移动，但看不见）
- 修复：把任务栏设为挂件的 owner，层级始终跟随任务栏；Explorer 重启导致 owner 失效时，
  下次同步会检测到并重建窗口重新关联

**点击“隐藏的图标”卡顿**（`taskbar_widget.py`）

- 现象：点开/收起溢出面板时卡一下
- 原因：渲染线程主循环用 `Event.wait(0.3)` 死等，这段时间完全不处理窗口消息；
  任务栏重排 z-order 时会**同步等待**挂件窗口响应，把 Explorer 一起拖住
  （实测窗口消息响应延迟 avg=191ms / max=205ms）
- 修复：等待改用 `MsgWaitForMultipleObjects`，有消息立刻醒来处理；
  `close()` 用 `PostMessage` 唤醒线程（修复后 avg=0ms / max=0ms）

### 说明

**为什么不用 DeskBand / 把窗口挂到任务栏**：Win11 的任务栏由 XAML 渲染，把窗口 `SetParent`
到 `Shell_TrayWnd` 虽然会成功（`GetParent` 返回任务栏句柄），但 GDI 子窗口会被任务栏的
XAML 图层整体盖住，实测在 Windows 11 25H2 上完全不可见；Win11 也已移除任务栏工具栏
（ToolBars）支持。因此挂件采用独立的置顶窗口方案。

---

# 运行与打包

## 运行

### 方式一：源码运行（需要 Python 3.10+）

```bash
pip install -r requirements.txt

python main.py        # 带控制台窗口，方便看报错
pythonw main.py       # 无控制台，适合日常使用
```

### 方式二：直接运行打包好的 exe

```bash
dist\PingMonitor.exe
```

无需 Python 环境。exe 读取**自身所在目录**的 `config.json`，所以要挪动位置时连配置文件一起挪。

启动后没有主窗口，程序常驻托盘：任务栏挂件显示实时延迟，右键托盘图标可设置目标地址、
开关任务栏显示、退出。

### 配置

首次运行若同目录没有 `config.json`，会使用内置默认值；修改目标地址或切换任务栏显示时会自动写入。

```json
{
    "target": "baidu.com",
    "interval": 1,
    "timeout": 2,
    "show_in_taskbar": true
}
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `target` | Ping 目标 IP 或域名 | `8.8.8.8` |
| `interval` | Ping 间隔（秒） | `1` |
| `timeout` | 超时时间（秒） | `2` |
| `show_in_taskbar` | 是否在任务栏显示数值挂件 | `true` |

## 打包

项目自带 `PingMonitor.spec`（onefile、无控制台、已 `collect_all('pystray')`），新增模块会被
自动分析进包，不需要改 spec。

```bash
# 安装 PyInstaller
pip install pyinstaller

# 方式一：用 spec 打包（推荐，产物 dist/PingMonitor.exe）
pyinstaller --noconfirm PingMonitor.spec

# 方式二：不用 spec 时的等效命令
pyinstaller --onefile --noconsole --name PingMonitor --collect-all pystray main.py
```

如果 PyInstaller 是装在项目内的独立目录（例如 `_pyinstaller_deps`）而不是全局环境，
打包时要把它加进模块搜索路径：

```powershell
$env:PYTHONPATH = "$PWD\_pyinstaller_deps"
python -m PyInstaller --noconfirm PingMonitor.spec
```

打包后建议把 `config.json` 复制到 exe 旁边，否则程序会用内置默认目标 `8.8.8.8`。

> 在受限沙箱里运行打包产物时可能报 `Failed to create parent directory structure` ——
> onefile 的 exe 需要先把自己解包到 `%TEMP%`，沙箱不允许写该位置。这是环境限制，
> 正常桌面环境下双击运行不受影响。
