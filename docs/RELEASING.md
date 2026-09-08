# 构建与发布

当前发布 Windows x64 桌面预览包；macOS/Linux 必须独立构建并实机验证。

```powershell
python -m pip install -r requirements-gui.txt -r requirements-tui.txt -r requirements-build.txt
ffprobe -version
./scripts/build-desktop.ps1
./scripts/package-desktop.ps1
```

构建输出 `dist/photo_renamer_desktop/`。打包脚本读取 `VERSION`，复制说明和许可文件，
生成 ZIP 与 SHA256。使用真实 ffprobe 二进制，不使用包管理器 shim。
更新时由用户决定如何合并自定义 JSON，不能直接覆盖已有规则。

发布前运行单元测试、原生窗口检查，以及解压后的 EXE 自检：
`photo_renamer_desktop.exe --smoke-test OUTPUT_DIRECTORY`。
该入口只处理临时生成文件，输出 `smoke.json` 和截图。
客户发布前另需干净电脑、网络异常、签名和许可验收。

`tests.yml` 检查推送与 PR；`build.yml` 手动构建 Windows 桌面包。
预览 Release 上传已验证的本地产物，标签不触发另一组不同构建。
源码提交后，在相应 commit 创建 VERSION 标签与预发布 Release，上传 ZIP 和 SHA256。
不强制改写历史来删除旧 EXE。
