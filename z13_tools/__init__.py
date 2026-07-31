"""ThinkPad Z13/Z16 Gen 2 工具箱 —— Python 包。

核心硬件库（haptic / brightness）与 GUI 包（z13_tools.gui）。
注意：本 __init__ 刻意不导入 GUI 子包，保证 `from z13_tools import haptic`
等 CLI / 监控脚本的导入链不依赖 PyGObject（haptic 与 CLI 仅标准库）。
"""

__version__ = "0.2.0"
