# E-Hentai Batch Downloader

一个独立的 Python GUI/CLI 批量下载器，用来按搜索词、上传者、标签或列表 URL 批量预览和下载 E-Hentai / ExHentai gallery。

当前实现不依赖 Android SDK，也不依赖 EhViewer 本体。运行时只用 Python 标准库；Windows 二进制发布包通过 PyInstaller 构建。

## 功能

- 搜索、Uploader、Tag、列表 URL 四种来源。
- 本地标题过滤：`Title Contains` 和 Python `Title Regex`。
- 大类过滤：支持 Doujinshi、Manga、Non-H 等多选。
- Cookie 文件或 GUI 内输入 Cookie Header。
- E-Hentai / ExHentai 切换。
- 系统代理、直连、HTTP 代理三种代理模式。
- 系统 DNS 或 EhViewer 内置 hosts 映射。
- 请求失败重试、退避等待、失败后继续。
- 默认不额外等待图片下载；可选并发下载多个 gallery 或单个 gallery 内多页。
- 缓存 gallery page token，重复下载同一 gallery 时减少预览页请求。
- GUI 自动保存上次设置，Cookie Header 除外。
- 对 `IncompleteRead` 等中途断开的响应会自动重试，不再让 GUI 子进程崩溃。
- 写入后会校验 PNG/JPEG/GIF/WebP 完整性，截断图片会删除并重试；已存在的坏图也不会被跳过。

## GUI 运行

在 Windows 上双击：

```powershell
.\run_eh_batch_gui.bat
```

或直接运行：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_gui.py
```

建议先点 `Preview` 确认匹配结果，再点 `Start Download`。

GUI 设置会自动保存到：

```text
%APPDATA%\EHBatchDownloader\settings.json
```

为了避免 token 长期落盘，`Cookie Header` 输入框里的原始 cookie 不会被保存；推荐保存的是 `Cookie File` 路径。

打包版默认下载目录位于 exe 同级的 `eh_downloads`，旧版本误保存到 `_internal\eh_downloads` 的设置会自动迁移。

## Cookie 文件

Cookie 文件不要提交到 GitHub。推荐命名为 `eh_cookies.txt`，该文件已被 `.gitignore` 忽略。

最简单格式是浏览器请求里的 Cookie Header 内容，不要写 `Cookie:` 前缀：

```text
ipb_member_id=你的值; ipb_pass_hash=你的值; igneous=你的值
```

也支持 JSON：

```json
{
  "ipb_member_id": "你的值",
  "ipb_pass_hash": "你的值",
  "igneous": "你的值"
}
```

也支持浏览器导出的 Netscape `cookies.txt`。

## CLI 示例

只预览 uploader 的第一页：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --uploader "なつみきよし" --site e --max-list-pages 1 --dry-run --cookie-file .\eh_cookies.txt
```

搜索并只保留中文翻译标题：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --search "touhou project" --title-contains "中文翻译" --keep-going --cookie-file .\eh_cookies.txt
```

使用 HTTP 代理、重试和 2 个 gallery 并发：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --search "touhou project" --proxy-mode http --proxy-url http://127.0.0.1:7890 --retries 3 --gallery-workers 2 --keep-going --cookie-file .\eh_cookies.txt
```

接近 EhViewer 默认速度的下载方式是单 gallery 队列 + 单 gallery 内 3 页并发：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --search "touhou project" --delay 0 --page-workers 3 --gallery-workers 1 --keep-going --cookie-file .\eh_cookies.txt
```

只保留 Doujinshi、Manga、Non-H 大类，同时按 tag 和标题过滤：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --search 'language:chinese$ parody:"touhou project$"' --category doujinshi --category manga --category non-h --title-contains "白杨汉化组" --dry-run --cookie-file .\eh_cookies.txt
```

GUI 中默认全选全部大类；如果只要 Doujinshi、Manga、Non-H，可以点 `Doujinshi/Manga/Non-H` 快捷按钮。

## 任务状态和失败重试

建议给长期任务设置稳定的 `Job Name`，例如 `touhou-baiyang-nonh`。每次非 dry-run 下载结束后，程序会在输出目录写入：

```text
.eh_batch_state\<job>-latest.json
.eh_batch_state\<job>-failures.txt
.eh_batch_state\<job>-history.jsonl
```

`latest.json` 供程序读取，`failures.txt` 方便人直接查看，`history.jsonl` 用来保留每次运行记录。

只重试上次失败的 gallery：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --retry-failed --job-name touhou-baiyang-nonh --output F:\eh_downloads --cookie-file .\eh_cookies.txt --keep-going
```

查看所有任务最近失败：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --list-failures --output F:\eh_downloads
```

## 多任务运行

可以参考 `tasks.example.json` 建立自己的任务文件，每个任务负责一种搜索条件或分类。运行所有 enabled 任务一次：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --task-file .\tasks.json --run-tasks
```

也可以让程序常驻并按任务里的 `interval_minutes` 循环：

```powershell
C:\Users\hoshizora\.conda\envs\pytorch\python.exe .\eh_batch_downloader.py --task-file .\tasks.json --schedule-tasks
```

更稳的长期方案是用 Windows 任务计划程序定时调用 `--run-tasks`，程序只负责每次执行和记录状态。

## Title Regex 例子

`Title Regex` 使用 Python 正则，匹配的是 Preview 日志里显示的标题。

```text
中文翻译.*白杨汉化组
Reitaisai|Meikasai|C86
^\(Reitaisai
(?=.*Touhou)(?=.*中文翻译)(?=.*白杨汉化组)
```

`Title Contains` 是简单标题子串匹配。站点搜索词和 tag 是服务端过滤；`Title Contains` / `Title Regex` 是本地二次过滤。

## 测试

```powershell
.\scripts\run_tests.ps1
```

这个脚本会执行语法检查、下载器自测和 `unittest` 离线测试。

## Windows 打包

首次打包可让脚本安装 PyInstaller：

```powershell
.\scripts\build_windows.ps1 -InstallDeps
```

打包完成后会生成：

```text
dist_release\EHBatchDownloader-windows-x64.zip
```

默认使用 `--onedir` 模式，稳定性更好。需要单文件 exe 时：

```powershell
.\scripts\build_windows.ps1 -OneFile
```

## GitHub 维护建议

这个目录可以作为一个新的 Python 项目仓库维护。发布前确认不要提交：

- Cookie 文件、账号 token、下载结果。
- Android SDK、Gradle 缓存、EhViewer 源码压缩包。
- 本地测试下载目录。

建议先用 `Preview` 和 `--max-image-pages 1` 做小规模验证，再扩大下载范围。
