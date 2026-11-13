# 变更记录

## 2026-11-13 · 设置弹框统一所有配置项，并改为屏幕居中

### 变更

**设置弹框覆盖 `config.json` 的全部可配项**（`main.py`）

- 原来只能改目标地址，其余参数得手改配置文件；现在弹框里能改：目标地址、Ping 间隔、超时时间、
  颜色阈值（绿 ≤ / 黄 ≤）、是否在任务栏显示挂件
- 校验集中在 `parse_settings()`：目标非空、四个数值必须是数字且有限、间隔 ≥0.2 秒、超时 >0、
  阈值 >0、绿阈值必须小于黄阈值；不合格弹提示且不写盘
- 保存时只写回真正变化的键（`_apply_settings`），没改就完全不碰 `config.json`；
  目标/间隔/超时/阈值由 Ping 循环逐轮读取，下一轮自动生效；挂件开关立刻 `set_enabled`
- 托盘菜单项 "设置目标地址..." 改为 "设置..."，与实际功能一致

**弹框居中**（`main.py`、`taskbar_widget.py`）

- 原本是写死的 `geometry("340x170")`（左上角定位），内容一多就会跑偏；现在先按客户区尺寸定身、
  再量外框（新增 `taskbar_widget.frame_rect()`），用**外框**尺寸算居中位置
  （`center_position()` / `center_window()`），窗口比屏幕还大时钳到 `+0+0`
- 坑：Tk 的 `geometry("WxH+X+Y")` 里 X/Y 定位的是**外框**左上角，而 WxH 是**客户区**尺寸。
  按客户区尺寸居中会让整个窗口偏右下（125% 缩放下实测偏 9px/24px），所以必须量外框；
  `winfo_id()` 给的是客户区句柄，要 `GetAncestor(..., GA_ROOT)` 才是外框
- 支持 `Esc` 取消、`Enter` 保存

### 测试

- `test_colors.py` 从 38 项扩到 55 项：新增 `parse_settings` 的 10 个校验分支、
  `number_text`/`tidy_number` 的数值显示、`center_position`/`center_geometry` 的居中与钳零
- 真实弹框集成验证（125% 缩放屏，物理 1920×1080）：外框 432×327 落在 `(744,376)` 即正中
  （按客户区算的旧做法会落在 `(753,400)`）；5 个输入框预填当前配置、勾选状态正确；
  保存后 `config.json` 五个键全部更新、取消勾选后挂件被立刻关闭、
  Ping 循环切到新目标（127.0.0.1 → 0.28ms）

## 2026-11-13 · 颜色判定重做：阈值可配置、中位数抗抖、浅色任务栏适配

### 变更

**阈值放宽并写进配置**（`main.py`）

- 默认从 `50/100ms` 改为 `80/150ms`。100ms 当红线对公网目标太严：实测本机 `ping baidu.com`
  就是 100–110ms，老配置会让目标一直显示红色，颜色的示警作用被磨掉
- `config.json` 新增 `thresholds: {"good": 80, "fair": 150}`，可只覆盖其中一个键；
  非法值（非数字、≤0、`good` ≥ `fair`）整体退回默认并打印提示，老配置文件不用改
- 托盘菜单里加了"颜色规则: ≤80 绿 / ≤150 黄 / >150 红"，顺带把写死的"Ping 间隔: 1s"改成读配置

**判色改用最近 3 次采样的中位数**（`main.py`）

- 单次采样在阈值附近抖动会让颜色每秒红黄横跳；现在用最近 3 次的中位数判色，显示的数字也是中位数
- 切换目标地址时清空采样窗口，旧目标的样本不会污染新目标

**丢包参与判色**（`main.py`）

- 最近 3 次里有 1 次超时 → 至少黄；2 次及以上 → 红。"慢而稳"和"快但丢包"不再显示成同一档
- 托盘菜单/提示显示 `丢包 1/3`

**"全部超时"从灰色改成红色**（`main.py`）

- 灰色现在只表示"还没测到"（`?` / `--`），最严重的状态不再用最不显眼的颜色
- 图标和挂件用 `×` 表示无响应，与数字区分开

**浅色任务栏适配**（`main.py`）

- 挂件是"透明背景 + 彩色文字"，文字对比度取决于任务栏底色。原来那套高饱和色在浅色任务栏下
  绿色只有 2.0:1、黄色 1.7:1（几乎看不见），而这台机器的默认主题恰好就是浅色任务栏
- 读注册表 `SystemUsesLightTheme`（缓存 5 秒）区分深浅主题；浅色时换深一档的配色，
  四种状态在两种底色下对比度均 ≥4.5:1；深色主题下红色调亮到 `#F05252`（4.7:1）
- 托盘图标底色微调：红 `#DC2626`（白字 4.8:1，原 `#EF4444` 只有 3.8:1）、未知灰 `#6B7280`
  （白字 4.8:1，原 `#808080` 只有 4.0:1）

### 修复

**ping3 返回 `False` 被当成 0ms 绿灯**（`main.py`）

- ping3 的约定：超时返回 `None`，出错（解析失败、不可达等）返回 `False`。老代码把 `False`
  直接当延迟值用：`format_latency(False)` 得到 `"0"`，`get_color(False)` 落在绿色档，
  于是"完全不通"显示成绿色的 `0`
- 新增 `probe()`，把 `False`、异常、≤0 统一当无响应处理

### 测试

**新增 `test_colors.py`**（`python -m unittest -v test_colors`，38 项断言，不需要联网）

- 阈值边界、最近 3 次中位数吸收抖动、丢包升档、全丢判"无响应"、窗口只保留最近 3 次
- ping3 的 `False`/`None`/`0`/异常四种返回都归为无响应
- 配置容错：老配置缺键、只覆盖一个阈值、阈值倒置/相等/负数/非数字/非对象、坏 JSON、保存后回读
- 配色对比度：两套挂件配色在深/浅任务栏底色下均 ≥4.5:1；未知必须是无彩色灰、无响应必须是红；
  主题切换取对应配色（`taskbar_is_light` 被替换，不依赖本机主题）
- 做过变异验证：把 `<=` 改成 `<`、把浅色主题的绿色调淡，测试都能报错，不是摆设

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
