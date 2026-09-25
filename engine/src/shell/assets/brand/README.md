# 引擎品牌资源

运行时文件放这里（由 `plans/design/` 的设计源导出后复制进来）：

- `icon.ico`：Windows 窗口 / 任务栏图标，建议包含 16/32/48/256 多尺寸；
- `icon.png`：备用 PNG（窗口图标；将来首页 logo 也可从这里取）；
  启动时先试 `icon.ico`，加载失败会自动退到 `icon.png`。

不要在运行时目录放 SVG：引擎只读 PNG/ICO；SVG 是设计源，留在
`plans/design/uwe-icon-concepts/`。

打包时把 `engine/src/shell/assets` 映射到 `shell/assets`
（例如 PyInstaller `--add-data "engine/src/shell/assets;shell/assets"`），
`shell/resources.py` 会在打包模式下从 `_MEIPASS/shell/assets` 读取。
