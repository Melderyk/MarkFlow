# 墨页 MarkFlow

本地 PDF / DOCX 转 Markdown 桌面工具：离线中英文 OCR、标题与代码整理、图片导出、图床接口、对照编辑和长文档分章阅读。

**Windows 10 / 11 x64 · 中文界面 · Python 3.12 · MIT**

[下载 Windows 免安装版](https://github.com/Melderyk/MarkFlow/releases/latest) · [更新记录](CHANGELOG.md) · [贡献指南](CONTRIBUTING.md) · [隐私与安全](SECURITY.md)

![MarkFlow 转换预览](docs/screenshot.png)

界面示例使用项目生成的测试文件，输出目录路径已隐去。

English: MarkFlow is a Windows desktop PDF/DOCX-to-Markdown converter with local Chinese/English OCR, code blocks, an outline, image hosting adapters and chapter exports. Text conversion runs locally. Images are uploaded only when you choose hosting or explicitly start an upload. Download the portable build from Releases; source setup is below.

当前版本 **0.2.0**：新增本地／图床两种图片导出模式、图床上传重试、手动修改代码语言、软件内可折叠大纲与质量提示；修复子控件区域拖拽导入，并为长文档增加分段预览和分章阅读版。保留此前的代码语言识别、OCR 布局和标题修复。

## 使用免安装版

1. 将 `MarkFlow-v0.2.0-windows-x64.zip` **完整解压**到一个可写目录。
2. 双击 `MarkFlow.exe`。程序需要同目录的 `_internal`，不要只移动 EXE。
3. 点击“添加 PDF / Word”，或把文件拖入窗口。
4. 选择输出目录、Markdown 格式，点击“开始转换”。
5. 对照原文检查结果。在右侧编辑 Markdown，点击“保存修改”或按 Ctrl+S。
6. 需要一个总文件时，点击“合并已转换文档”。合并顺序为队列中的添加顺序。

适用于 Windows 10 / 11 x64。运行时无需安装 Python、Word 或 OCR 服务。OCR 模型随程序提供，使用 CPU 在本机运行。本地图片模式不联网；选择图床模式或点击“上传 / 重试图片”会向所配置的服务上传图片。不发送 PDF / Word 原文件或单独发送 Markdown 文本，不调用云端 AI。扫描代码的原页核对图包含该页内容，也属于导出图片，选择图床时会一同上传。

## 图床与本地图片

在左侧选择“本地图片”或“图床链接＋本地备份”，再开始转换。转换后也可点击“上传 / 重试图片”，上传当前文档中的本地图片并保存当前编辑内容。

| 接口模板 | 配置方式 |
|---|---|
| PicGo / PicList 本机服务 | 先在 PicGo / PicList 中配置存储，再开启本机上传服务；默认地址 `http://127.0.0.1:36677/upload`，使用已选中的图床 |
| Lsky Pro / 兰空 | 填写站点的完整上传 API 地址，通常为 `/api/v1/upload`，以及 Token |
| EasyImages / 简单图床 | 填写站点的上传 API 地址，通常为 `/api/index.php`，密钥通过 `token` 表单字段提交 |
| S.EE | 提供上传接口模板，填写 API Token；不同服务版本可调整返回链接路径 |
| SM.MS 兼容接口 | 面向仍提供该兼容接口的站点；服务可用性需实际测试 |
| 自定义 HTTP 接口 | 支持 multipart 文件上传、鉴权请求头或 token 表单字段、额外表单字段以及 JSON 返回链接路径 |

接口字段均可修改，`data.url`、`data.links.url`、`result.0` 表示从响应 JSON 中读取图片网址。先点击“上传测试图片”确认配置；这会实际上传一张 16×16 测试图片。PicGo 可通过其图床和插件配置连接多种存储，MarkFlow 不直接管理这些云厂商的账号。

上传成功后替换 Markdown 图片链接，失败的图片保留本地链接并显示提示；网络或服务临时错误会自动重试。相同文件和同一配置命中本地缓存时复用链接。`.images.json` 记录本地图片与远程网址的对应关系，`.assets` 始终作为本地备份。软件内预览优先使用本地副本。密钥可选择只在当前会话使用，或由 Windows DPAPI 加密保存在本机；源码和发行 ZIP 不包含个人配置、密钥或上传缓存。

## 长文档阅读与核对

- **分章阅读版**：默认勾选。正文至少 5 万字符且包含两个以上一级标题时，在保留完整 `.md` 的同时生成 `.chapters` 文件夹。打开其中的 `目录.md`，按章阅读；章末包含上一章、下一章和目录链接。保存编辑时同步已生成的章节。
- **软件内预览**：长文档按围栏代码边界安全分段，使用上／下一段浏览；点击大纲可定位对应预览分段。相同内容不重复排版，预览图片按窗口缩放，导出图片保留原分辨率。
- **可折叠大纲**：位于 Markdown 结果旁，点击箭头折叠，点击标题定位；“大纲 / 检查”按钮可隐藏导航以增大编辑空间。
- **手动代码语言**：选择代码块，或把光标放入代码块，选择语言后点击“应用语言”，再保存；正文不变，支持撤销。
- **质量提示**：在“检查”页查看未闭合代码围栏、疑似语言标签、标题跳级、丢失的本地图片与转换警告。OCR 警告带有页码时可跳转 PDF 原页。提示覆盖可检测的问题，不保证发现全部 OCR 错字。
- **拖拽导入**：可以把 PDF / DOCX 拖到队列、编辑区、预览区或输出路径输入框，不会把文件路径插入正文。请正常启动程序；Windows 会限制不同权限级别程序之间的拖拽。

长文档中的代码高亮、图片解码和排版都可能影响阅读器流畅度。分章可降低单次打开的文档规模；图床改变图片地址，仍需解码图片，不能保证消除 Typora 卡顿。尚未测量 Typora 滚动帧率。

## 包含的功能

| 功能 | 实现 |
|---|---|
| DOCX | 提取标题、段落、基础强调、列表、超链接、表格和内嵌图片 |
| 文字 PDF | 优先按 PDF 书签还原标题层级；没有书签时参考文档字体；提取正文、代码块、链接、简单表格和图片 |
| 扫描 PDF | 按页检测扫描内容，本地 OCR 识别中文和英文；支持文字页与扫描页混合的 PDF |
| 基础格式 | 标题、加粗、斜体、列表、链接；PDF 灰底等宽代码区保留换行和缩进，Word 支持 Code / Source Code / 代码样式 |
| 图片 | 提取到与 Markdown 同名的 `.assets` 文件夹，用相对路径引用 |
| 简单表格 | 生成 Markdown 表格，处理单元格中的竖线与换行 |
| 桌面操作 | 中文窗口，文件选择与拖拽导入，输出目录选择 |
| 批量 | 顺序转换、进度状态、失败原因、停止与重试；单个文件失败后继续处理后续文件 |
| 预览与编辑 | PDF 原页面分页预览；Word 内容预览；Markdown 编辑和渲染预览 |
| 输出 | UTF-8 `.md` 文件 + 配套图片文件夹；同名文件自动增加编号 |
| 合并 | 合并成功转换的文件并生成目录，包含当前编辑内容 |
| 格式适配 | GitHub / 通用、Obsidian、Typora；通用模式使用锚点目录，Obsidian 使用标题链接，Typora 使用 `[TOC]` |
| 本地 | 转换和 OCR 无需网络；图床模式按配置上传图片 |
| Windows | x64 文件夹式免安装程序 |

三种输出格式的正文语法相同。本地图片使用标准相对链接，图床图片使用远程网址。合并文件中的本地图片引用各原文档的图片文件夹；分享或迁移本地模式文档时，请将相关 `.md` 与 `.assets` 目录一并带走。

Typora 的“大纲”会根据 `#` 至 `######` 标题显示层级。如果左侧大纲不能折叠，请在“偏好设置 → 文件 → 大纲”勾选“允许折叠和展开侧边栏的大纲视图”。完整单篇文档保留正文顺序；合并文件插入目录，分章阅读版另有独立章节目录。

PDF 中跨页连续的代码面板会衔接为一个代码块。代码标签按内容区分 `c`、`bash`、`asm`、`powershell`；例如 `gcc -c ...` 是编译命令，标记为 `bash`。运行输出、文件名和操作步骤保留 `text`。匹配不到正文的书签会显示在转换提示中。重复页眉页脚经抽样识别后从正文移除。

扫描页结合灰色背景、彩色文字、文字坐标和代码语法识别代码区域，估计缩进与空行。颜色只是辅助信号，不能单独确定语言。OCR 实测仍可能遗漏独立括号、混淆 `0/O` 或丢失空格；识别出代码时会附上原页 PNG 并提示核对。文字型 PDF 优先直接提取字符，无需 OCR。

## 适用范围与已知限制

- Word 预览展示内容结构，不复现 Word 的分页与打印版式。旧版 `.doc` 需要先另存为 `.docx`。
- PDF 有书签时用书签层级匹配正文；没有书签时使用字体与坐标推断，双栏、复杂排版和特殊字体可能需要人工整理。Markdown 标准最多支持六级标题。
- 代码识别主要面向可提取文字的等宽字体、浅色背景面板；扫描图片中的代码经过 OCR 后仍需核对。原 PDF 中本来空白或缺失的内容不会自动补写。
- 简单表格主要面向 DOCX 表格与文字 PDF 的清晰网格表格。扫描页 OCR 输出文字，不保证恢复表格结构。
- OCR 的准确度受清晰度、旋转和字体影响。金额、编号和专有名词请对照原文校对。
- PDF 图片导出针对内嵌位图；矢量图和独立绘图元素不作为图片导出。DOCX 的 EMF / WMF 会保留原文件，阅读器支持情况不同。
- 密码保护 PDF 会提示失败。公式、复杂合并单元格、脚注、修订和文本框不属于第一版专项处理范围。
- 软件未做 Windows 代码签名；正式发布前可另行配置签名证书。

## 源码开发

需要 Windows x64、Python 3.12。可先用 VS Code 打开 `MarkFlow.code-workspace`，再在此目录的终端中运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

`requirements-lock.txt` 记录本次验证所用的完整开发依赖版本，可用于重建相同环境。

测试：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -q
```

生成测试样本与打包：

```powershell
.\.venv\Scripts\python.exe tests\samples.py 示例文件
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm MarkFlow.spec
```

开发自检入口（会将转换结果、界面截图和 JSON 报告写入指定目录，并禁止 Python socket 联网）：

```powershell
.\dist\MarkFlow\MarkFlow.exe --self-test .\selfcheck
```

`markflow/core.py` 是独立转换核心，`markflow/pdf_layout.py` 负责 PDF 字符几何、书签与代码块，`markflow/code_languages.py` 负责语言判断，`markflow/ocr_layout.py` 负责扫描代码布局，`markflow/image_host.py` 负责图床适配，`markflow/markdown_tools.py` 负责导航检查和分章，`markflow/app.py` 是 PySide6 桌面界面。设置保存在程序目录的 `markflow-settings.json` 中，输出默认位于程序目录的“导出”文件夹。

依赖许可证见免安装目录中的 `THIRD_PARTY_LICENSES`。PySide6 / Qt 按其 LGPL 等许可证使用，保留动态链接库及第三方声明。


## 开源许可

项目原创代码、图标和生成示例采用 [MIT License](LICENSE)。第三方组件及 OCR 模型保留各自的许可证，详见 [NOTICE.md](NOTICE.md) 和 [THIRD_PARTY_LICENSES](THIRD_PARTY_LICENSES/INDEX.md)。MIT 授权不覆盖第三方组件。

发行 ZIP 包含第三方声明与 Qt LGPL 文本。Qt / PySide6 / Shiboken6 以动态库形式分发，未修改；可替换兼容动态库。请保留声明并遵循对应组件的许可证。

提交问题时请先使用项目自带示例复现。请勿在公开 Issue 中上传含个人、机密或无权分享内容的 PDF、Token 或配置文件。
