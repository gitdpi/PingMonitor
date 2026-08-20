# Ping Monitor 📡

Windows 托盘实时 Ping 监控工具，在任务栏显示 Ping 延迟数值和状态颜色。

## ✨ 功能特点

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
git clone <your-repo-url>
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
    "timeout": 2
}
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `target` | Ping 目标 IP 或域名 | `8.8.8.8` |
| `interval` | Ping 间隔（秒） | `1` |
| `timeout` | 超时时间（秒） | `2` |

**修改方式**：右键托盘图标 → "设置目标地址..."

## 📦 从源码打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包
pyinstaller --onefile --noconsole --name PingMonitor --collect-all pystray main.py

# 产物位于 dist/PingMonitor.exe
```

## 📁 项目结构

```
ping/
├── main.py              # 主程序
├── config.json          # 配置文件（自动生成）
├── requirements.txt     # 依赖列表
└── README.md            # 说明文档
```

## 🛠️ 技术栈

- **Python 3.10+**
- **pystray** - Windows 托盘图标
- **Pillow** - 动态图标生成
- **ping3** - Ping 请求
- **Tkinter** - 设置对话框

## 📝 License

MIT
