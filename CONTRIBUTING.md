# 开发说明

保持业务逻辑在 `photo_renamer.py`，界面调用服务层，不复制算法。
本项目规模较小，入口保留根目录，暂不为目录形式重写包结构。

```powershell
python -m pip install -r requirements-gui.txt -r requirements-tui.txt -r requirements-build.txt
python -m unittest discover -s tests
git diff --check
```

测试仅使用生成的临时文件或素材副本。GUI 默认离屏运行；Windows 原生截图使用
`QT_QPA_PLATFORM=windows`，测试截图写入 `build/ui-checks/`。

文件写入改动必须验证不覆盖目标、重复执行、失败记录、身份核验和中断恢复。
日期改动必须保持内部元数据优先，不通过全读网盘媒体或全文件哈希简化实现。
不提交 EXE、ZIP、媒体、运行日志、个人路径截图或密钥，二进制通过 Releases 分发。
