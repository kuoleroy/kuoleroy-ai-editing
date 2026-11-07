# 工具链、环境与跨软件适配

## 1. 已验证的技术路线

```
逐字稿 + 原片 + 参考工程
        ↓  学习风格 / ASR 词级时间戳
     plan.json（标准任务单）
        ↓  pyJianYingDraft + jy_tools.py build
   剪映加密草稿 + 注册 root_meta_info.json
        ↓  jy_tools.py read 解密回读
   轨道 / 坐标 / 素材路径 / 字幕条数验收
        ↓
   独立可编辑工程 + 另存成片 + 封面 + 验收报告
```

最佳路线：**`pyJianYingDraft + 剪映草稿加解密 + 标准化 plan.json + 工程回读验收`**。理由：支持批量、版本可追踪、易固定公司模板、不依赖按钮位置或窗口分辨率。

不建议：用鼠标坐标/固定按钮模拟剪映界面做批量生产；用 FFmpeg 直出 MP4 冒充工程；依赖需要剪映宿主授权令牌的官方 `DraftEditing / XML`（可作后续升级方向）。

## 2. 组件清单

| 组件 | 用途 | 验证方式 |
|---|---|---|
| 剪映 JianyingPro 11.x | 工程宿主 | 打开剪映确认版本与草稿首页 |
| `videoeditor.dll` | 剪映 11.x 草稿加解密 | 由剪映安装提供，路径随版本变化 |
| `pyJianYingDraft`（支持加密草稿的分支） | 构建视频/音频/文本轨道 | `python -c "import pyJianYingDraft"` |
| `jy_tools.py` | `probe / frames / silence / build / read / list / text` | `python jy_tools.py list` 能列出本机草稿 |
| 便携 Python 3.11 | 运行上述脚本 | `python --version` |
| FFmpeg 6.x | 素材探测、抽帧、静音检测、封面、辅助质检 | `ffmpeg -version`、`jy_tools.py probe <视频>` |
| faster-whisper（可选） | 口播识别与词级时间戳；**长音频必须分块**（25s 窗 / 20s 步长）再合并，否则漏后半段 | 转写一个短音频验证，并确认 ASR 首尾覆盖全片 |
| rembg / 图像生成（可选） | 封面人像抠图、底图生成 | 产出一张 1080×1920 封面验证；提示词见 `assets/封面生成提示词.md`，人像须用本人授权真人素材垫图、中文文案后期叠加 |

FFmpeg 的定位：**素材分析与辅助质检，不代替最终工程**。

## 3. 本机路径速查（示例，换机必须重新自检）

| 项 | 路径 |
|---|---|
| 剪映草稿目录（实际） | `<DRAFTS>` |
| 剪映草稿索引 | `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\root_meta_info.json` |
| 默认草稿目录 | `%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\` |
| 工具链根目录 | `<TOOLS>` |
| 技能与脚本 | `<TOOLS>/SKILL.md`、`<TOOLS>/scripts/jy_tools.py` |
| 便携 Python / FFmpeg | `<TOOLS>/python/python.exe`、`<TOOLS>/ffmpeg/ffmpeg.exe` |
| 成片输出 | `<OUTPUT>\<批次或片名>\` |
| 个人素材库 | `<ASSETS>`（按病症分类） |
| 团队公共盘 | `<PUBLIC_LIBRARY>`（**只读**） |
| 曲库 | `<BGM_LIBRARY>\` |

环境变量：`JY_TOOLS_HOME` 指向工具链根目录，`JY_DRAFT_DIR` 指向真实草稿目录（脚本内可覆盖）。

**2026-11-07 本机实测修正**（上表为团队示例，与本机不一致）：真实草稿目录是 `<DRAFTS>`，该目录下**没有** `root_meta_info.json`；`pyJianYingDraft` 已装在 `<PYTHON_SITE_PACKAGES>`，FFmpeg/ffprobe 在 PATH 中。剪映 11.7 现存两个工程的 `draft_content.json` 与 `draft_meta_info.json` 都是**加密内容**，`videoeditor.dll` 的 `decrypt` 返回 `output_len=0`，回读验收链仍断。`build_insert_draft.py` 写出的是明文工程，是否可被 11.7 打开需人工验证一次。**换机一律用 `--drafts-dir` 显式指定，不要沿用示例中的盘符。**

**命令解析（v1.4）**：技能包脚本一律用 `<SKILL_DIR>/scripts/xxx.py` 绝对路径调用——智能体的 cwd 通常是项目目录，写 `scripts/xxx.py` 会找不到文件；`jy_tools.py` 用 `JY_TOOLS_HOME` 定位，草稿目录用 `JY_DRAFT_DIR`。上表路径仅为示例，换机必须重新自检，不硬编码盘符。大素材库扫描走 `select_inserts.py`（全库文件名索引 + 入选才 ffprobe），工程构建走 `build_insert_draft.py`（默认 dry-run，`--build` 才写）。

## 4. 智能体调用剪映技能的规范

1. **先完整读取本机 `SKILL.md`**，不能只读摘要。
2. 定位 `jy_tools.py`、便携 Python、FFmpeg；缺项先安装，不猜按钮位置。
3. 生成前确认目标草稿未在剪映中打开（含后台驻留进程）。
4. 先写标准化 `plan.json`（分镜 / 时间 / 字幕 / BGM / 音量 / 坐标）。
5. `jy_tools.py build` 写草稿并注册到剪映首页；任何加密写入或注册失败立即停止并报告。
6. `jy_tools.py read` 解密回读，核对时长、视频分镜数、音频轨、文本条数、素材路径、坐标。
7. 确认工程目录与 `draft_info.json` / `draft_content.json`、`draft_meta_info.json`、`draft_cover.jpg` 齐全。
8. 覆盖旧工程前必须备份；不能把单轨 MP4 草稿当成交付。

## 5. 新机部署（每台剪辑电脑一次）

1. 拷贝完整工具包：`jianying-skill/`、`.codex/skills/jianying-editing/`（或 WorkBuddy 的 `~/.workbuddy/skills/kuoleroy-ai-editing/`）、`jianying-tools/python/`、`jianying-tools/ffmpeg/`、模型目录（有 ASR 需求时）、`plan.json` 模板、坐标参数、回读检查脚本、团队提示词与使用说明。
2. 目标机要求：Windows 10/11、最新版剪映、本机 `videoeditor.dll`、Python 3.11、FFmpeg、`pyJianYingDraft` 及 `jy_tools.py` 依赖；优先复用工具包内便携版，不重复下载或覆盖已有版本。
3. **必须实测，不能只检查文件是否存在**：
   - `python --version`、`ffmpeg -version`；
   - `jy_tools.py list` 读取本机剪映草稿；
   - `jy_tools.py probe <一个视频>`；
   - 生成一个最小测试工程（真实视频轨 + 文本 + 音频）；
   - `jy_tools.py read` 解密回读，确认已注册到剪映首页；
   - 测试工程验证后可保留或安全清理。
4. 输出部署报告：组件与版本、Codex/WorkBuddy 是否可用、剪映版本与草稿目录、`videoeditor.dll` 路径、Python / FFmpeg 路径、测试工程名与绝对路径、剩余风险或缺失项。
5. 用一张真实素材做「一条团队试剪」，确认剪辑人员能打开工程并直接修改分镜、标题、字幕、BGM，再进入批量。

## 6. 跨软件适配（工具无关原则）

本技能的**规范层**（输入五文件夹、字幕规则、参数基线、命名版本、验收清单、红线）与具体剪辑软件无关，适用于剪映、爱剪辑、Premiere、达芬奇等任何以「工程文件 + 可编辑轨道」为核心的工具。

落地适配层只需实现四类能力，替换 `jy_tools.py` 即可：

| 能力 | 要求 |
|---|---|
| 读参考工程 | 提取轨道结构、片段时长、字号、坐标、音量、素材路径 |
| 写工程 | 按 `plan.json` 生成独立工程，轨道分离，文本不烧死 |
| 注册/可见 | 工程能在软件首页打开；素材路径为稳定本地路径 |
| 回读验收 | 重新读取工程，核对时长、轨道数、文本条数、坐标、素材路径 |

若目标软件没有可编程工程格式，则必须先明确这一限制并报告，**不得用「直接渲染 MP4」悄悄替代工程交付**；也**不得靠鼠标坐标猜按钮**来批量生产。

## 7. 环境故障

| 现象 | 处理 |
|---|---|
| 工作目录被移动/清理导致工具链失效 | 重新自检路径，更新 `JY_TOOLS_HOME`，不硬编码旧路径 |
| `root_meta_info.json` 不可写 | 工程本体仍完整，靠软件扫描草稿目录或「导入草稿」 |
| 加密草稿跨机读不出 | 只在本机拷贝+解密，不做跨机 zip 还原 |
| 剪映已关但文件被锁 | 检查后台进程，生成独立新工程，不覆盖 |
| 剪映 11.x 无法脚本导出 | 导出方式不限，工程文件 + 另存成片即可 |
