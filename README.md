# Ping Monitor 📡

Windows 实时 Ping 监控工具，在**任务栏挂件**和**系统托盘图标**上同步显示 Ping 延迟数值和状态颜色。

## ✨ 功能特点

- **任务栏挂件**：在任务栏空白区（通知区域左侧）用透明背景的彩色数字显示延迟，一眼可见，不受托盘图标 16×16 尺寸限制
- **实时 Ping 显示**：托盘图标实时显示 Ping 延迟数值
- **颜色编码**：
  - 🟢 ≤ 50ms（优秀）
  - 🟡 51-100ms（良好）
  - 🔴 > 100ms（较差）
  - ⚫ 超时/错误
- **动态图标**：彩色方块 + 延迟数字，一眼识别网络状态
- **右键菜单**：快速设置目标地址、查看当前延迟、退出
- **配置持久化**：自动保存设置，下次启动自动恢复
- **后台运行**：无主窗口，不占用桌面空间

## 🚀 快速开始

### 方式一：直接运行（需要 Python 环境）

```bash
# 克隆项目
git clone https://github.com/gitdpi/PingMonitor.git
cd ping

# 安装依赖
pip install -r requirements.txt

# 运行（无窗口模式）
pythonw main.py
```

### 方式二：使用打包好的 EXE

直接双击 `PingMonitor.exe` 即可运行，无需安装 Python。

## ⚙️ 配置说明

首次运行会在程序同目录下生成 `config.json`：

```json
{
    "target": "8.8.8.8",
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

**修改方式**：右键托盘图标 → "设置目标地址..."；任务栏挂件可用右键菜单里的
"在任务栏显示数值"随时开关（勾选状态即时保存到配置文件）。

## 📦 从源码打包

项目自带 `PingMonitor.spec`（onefile、无控制台、已 `collect_all('pystray')`），新增模块会被自动
分析进包，不需要改 spec。

```bash
# 安装 PyInstaller
pip install pyinstaller

# 方式一：用 spec 打包（推荐）
pyinstaller --noconfirm PingMonitor.spec

# 方式二：不用 spec 时的等效命令
pyinstaller --onefile --noconsole --name PingMonitor --collect-all pystray main.py

# 产物位于 dist/PingMonitor.exe
```

如果 PyInstaller 装在项目内的独立目录（例如 `_pyinstaller_deps`）而不是全局环境，
需要把它加进模块搜索路径：

```powershell
$env:PYTHONPATH = "$PWD\_pyinstaller_deps"
python -m PyInstaller --noconfirm PingMonitor.spec
```

打包后建议把 `config.json` 复制到 exe 旁边，否则程序会使用内置默认目标 `8.8.8.8`。

> 在受限沙箱里运行打包产物可能报 `Failed to create parent directory structure`：onefile 的 exe
> 需要先把自己解包到 `%TEMP%`，沙箱不允许写该位置。这是环境限制，正常桌面环境下不受影响。

## 📁 项目结构

```
ping/
├── main.py              # 主程序（托盘图标、Ping 循环、设置对话框）
├── taskbar_widget.py    # 任务栏挂件（分层窗口、文字渲染与定位）
├── config.json          # 配置文件（自动生成）
├── requirements.txt     # 依赖列表
├── CHANGELOG.md         # 变更记录、运行与打包说明
└── README.md            # 说明文档
```

## 🧩 任务栏挂件说明

挂件是贴在任务栏上的透明置顶窗口，位于通知区域左侧、任务栏按钮区右侧的空白处，每 0.3 秒重新贴合
一次，因此能跟随任务栏位置、高度、DPI 缩放变化；任务栏按钮区变宽时会自动让位，不会遮挡任务栏图标。
显示内容只有彩色的延迟数字（无底色），颜色与托盘图标一致：绿 ≤50ms、黄 ≤100ms、红 >100ms、灰为超时。

**为什么不用 DeskBand / 挂载到任务栏**：Win11 的任务栏由 XAML 渲染，
把窗口 `SetParent` 到 `Shell_TrayWnd` 虽然会成功（`GetParent` 返回任务栏句柄），
但 GDI 子窗口会被任务栏的 XAML 图层整体盖住，实测在 Windows 11 25H2 上完全不可见；
Win11 也已移除任务栏工具栏（ToolBars）支持。因此采用独立的置顶窗口方案。

**透明背景怎么做的**：普通窗口做不到"背景透明 + 文字保持彩色"——用颜色键透明（`-transparentcolor`）
会让抗锯齿的文字边缘混进底色，出现一圈杂边。挂件改用分层窗口
（`WS_EX_LAYERED` + `UpdateLayeredWindow`），直接把 Pillow 渲染好的 RGBA 位图送进窗口，
逐像素 alpha，边缘干净。窗口还带 `WS_EX_TOOLWINDOW`（不进 Alt+Tab）和 `WS_EX_NOACTIVATE`（点击不抢焦点）。

**为什么不会闪烁**：任务栏和"隐藏的图标"溢出面板出现时会把自己提升到 topmost 组顶部，
把其它置顶窗口压到任务栏内容层之下（窗口既没隐藏也没移动，但看不见）。挂件把任务栏设为
自己的 owner，层级始终跟随任务栏，因此打开/关闭隐藏图标面板时不会闪一下再出现。
Explorer 重启后 owner 失效，挂件会检测到并在下次同步时重建窗口、重新关联。

**自动隐藏规则**：前台窗口全屏（全屏视频、游戏等）、任务栏自动隐藏或不可见时，
挂件会一并隐藏，避免浮在画面上。

**可调参数**（`taskbar_widget.py` 顶部常量）：

| 常量 | 含义 | 默认 |
|------|------|------|
| `_WIDGET_SIZE` | 挂件宽度（物理像素，用于右对齐定位） | `88` |
| `_MARGIN` | 与通知区域左侧的间距 | `8` |
| `_SYNC_INTERVAL` | 重新贴合的间隔（秒） | `0.3` |

字号按任务栏高度的 50% 自动计算，任务栏变高（含 DPI 变化）时会跟着变。

**已知限制**：

- 只处理主显示器上的任务栏，副屏任务栏不显示挂件
- 挂件是置顶窗口，若有其他程序用置顶窗口覆盖任务栏区域，可能压在挂件之上
- 数值刷新频率跟随 `interval` 配置（默认 1 秒）

## 🛠️ 技术栈

- **Python 3.10+**
- **pystray** - Windows 托盘图标
- **Pillow** - 托盘图标与挂件文字渲染
- **ping3** - Ping 请求
- **Tkinter** - 设置对话框
- **ctypes / Win32** - 分层窗口、任务栏定位与 owner 关联、全屏检测、DPI 感知

## 📝 License

MIT
