---
name: kuoleroy-ai-editing
description: "面向短视频团队的 AI 辅助剪辑工作流，用于批量制作医生数字人口播短视频。当用户要求剪视频、AI 剪辑、剪映工程或草稿生成、字幕断句与音画同步、逐字稿转字幕、素材匹配、封面制作、BGM 配置、成片与工程验收、剪辑参数标准化或团队剪辑规范时，应使用本技能。适用于 WorkBuddy、Codex 等智能体，产出以可编辑工程文件为硬性交付物，不依赖任何剪辑器的按钮位置。"
agent_created: true
---

# 短视频团队 · 通用 AI 剪辑规范

## 定位

把公司已验证的剪辑流程固化为可重复执行的规范：从「五文件夹输入」到「一条视频一个可编辑工程 + 成片 + 封面 + 验收报告」。核心方法是用标准化的任务单（plan.json）驱动工程文件生成，再用解密回读与字幕质检做验收，而不是靠鼠标点击剪辑器界面。

本技能的规则提炼自公司真实 Codex 剪辑对话（含多次改版、故障与最终红线），完整证据与参数出处见同批交付的《AI剪辑对话提取与执行规范.md》。

## 何时使用

- 用户给出「剪一条 / 批量剪 / 按这个模板剪 / 先剪一条看看」等剪辑请求；
- 用户提供「数字人原片 + 逐字稿 + 参考工程 + 素材 + 封面模板」五类材料；
- 用户抱怨字幕断句、音画不同步、标题位置、剪映里找不到草稿、素材重复等剪辑问题；
- 用户要求统一团队剪辑参数、新人上手、验收标准、剪辑手册或提示词；
- 用户要求在新电脑部署 AI 剪辑能力。

## 六条硬性红线

1. **工程文件必须在**：每个成片对应一个可在剪辑器打开并继续精调的独立工程；成片导出方式不限，工程是硬性交付物。
2. **一视频一工程**：不把多条视频塞进同一工程，不用单轨 MP4 草稿冒充工程。
3. **轨道可编辑**：原视频 / 插入素材 / 顶标题 / 字幕 / 免责声明 / BGM 尽量独立轨道；标题与字幕不能烧死在画面里。
4. **不猜按钮、不偷换链路**：走「任务单 → 生成工程 → 注册 → 回读验收」；剪映自动化失败时必须停止并报告，不得悄悄改用 FFmpeg 直出 MP4 代替工程。
5. **公共盘只读**：团队公共盘只复制、不修改；素材复制到个人素材库并按病症分类后使用。
6. **版本不覆盖**：保留全部历史版本，改版用 `-一改 / -二改 / -三改`；覆盖被打开或人工修改过的草稿前必须备份并征得确认。

## 工作流

### 路径与命令解析（v1.1）

- `<SKILL_DIR>` = 本技能包安装目录（含 `SKILL.md` 的那一层）。WorkBuddy 默认 `%USERPROFILE%\.workbuddy\skills\kuoleroy-ai-editing`，Codex 默认 `%USERPROFILE%\.codex\skills\kuoleroy-ai-editing`。
- **不要假设当前工作目录是技能包目录**：智能体的 cwd 通常是项目目录，`python scripts/xxx.py` 会找不到文件。所有脚本一律用 `<SKILL_DIR>/scripts/xxx.py` 调用；脚本内部靠自身位置定位 `assets/`，不依赖 cwd。
- 外部工具 `jy_tools.py` 用 `JY_TOOLS_HOME` 解析，真实草稿目录用 `JY_DRAFT_DIR`（见 `references/environment.md`）；换机先自检，不硬编码盘符。
- **退出码即门禁**：`build_subtitles.py` 质检不通过返回 `2`，`select_inserts.py` 插入点校验不通过返回 `2`，`build_insert_draft.py` 门禁不过/构建失败返回 `2`，`qc_delivery.py` 存在 FAIL 返回 `1`（默认严格）。自动化必须同时检查 stdout 的 `ok` 字段与退出码，非 0 立即停止，不得继续批量或交付；`--allow-fail` / `--no-strict` / `--allow-replace` 仅供排查，生产禁用。
- 文档里出现的 `references/...`、`assets/...` 相对路径均相对 `<SKILL_DIR>` 解析，不要用项目目录去拼。

### 步骤 0 · 环境与输入自检

- 按 `references/environment.md` 检查：剪映版本、真实草稿目录、`videoeditor.dll`、便携 Python、FFmpeg、`jy_tools.py`（或等价工程生成工具）。未通过则先部署，不要开始剪片。
- 按 `<SKILL_DIR>/scripts/init_project.py --check <项目目录>` 校验五文件夹：`封面模板/`、`工程文件/`、`数字人原片/`、`素材/`、`文案/`。**缺项先报告，不要脑补**。
- 确认目标草稿没有在剪映中打开（含后台驻留进程）。

### 步骤 1 · 学习参考风格

- 用 `jy_tools.py read` 解密回读参考工程，再用 `<SKILL_DIR>/scripts/style_profile.py` 提取轨道结构、平均片段时长、字号、坐标、BGM 音量，生成该医生的风格卡 JSON。
- **不同医生可以有不同参数**，不要一套模板套全部；加密草稿只能在本机读，不要跨机打包还原。
- 材料价值排序：`工程文件 > 成片视频 > 视频链接`；最理想是「原素材 + 工程 + 成片」三者齐全。
- 参数基线见 `references/company-standard.md`；与参考工程冲突时以该医生风格卡为准，并在验收报告注明偏差。

### 步骤 2 · 生成任务单

- 复制 `assets/剪辑任务单模板.json` 到项目 `输出/` 目录，填入分镜、字幕、素材插入点、BGM、音量、坐标、命名，以及片尾清理后的最终成片时长 `delivery.final_duration`。
- 未确认的 BGM 先列 4–5 首候选供用户试听，**用户选定后才加入渲染**。
- 样板阶段未完成的规范项（插入素材、BGM、美颜、风格卡等）登记到 `acceptance.deferred_items`，批量开始前必须清零。

### 步骤 3 · 生成字幕（最容易出错的环节）

```bash
python <SKILL_DIR>/scripts/build_subtitles.py \
  --script 文案/逐字稿.txt --out 输出/字幕 \
  --words asr-words.json --duration 71.94 \
  --protect <SKILL_DIR>/assets/保护词表-医疗示例.txt
```

- **长音频先分块转写**：单次 Whisper 转写会漏掉后半段；按 25s 窗 / 20s 步长切分再合并词级时间戳，确保 ASR 首尾覆盖全片。
- 脚本做**逐字稿 ↔ ASR 字符级强制对齐**：等长的医学术语校正沿用原时间；长度变化或「稿有 ASR 无」的字记入 `alignment.differences`，需人工核对后用 `--allow-interpolation` 显式放行。**禁止按字数估时或外推时间轴**。
- `--duration` 取**片尾清理后的最终成片时长**（任务单 `delivery.final_duration`），不是原始素材容器时长；末句延长 0.6 秒到成片结尾之后。
- 硬规则：文本逐字来自逐字稿；去标点；单行；每行 ≤11 字；语义短句与保护词不跨画面；首句对齐口播实际起点；相邻事件无重叠；关闭剪辑器「自动换行」；断句不跨标点拼接，单字行判不合格（口头语应在逐字稿阶段删除）。
- 质检不通过（无时间戳 / ASR 覆盖不足 / 匹配率过低 / 插值未复核 / 单字行 / 保护词被切 / 末句未覆盖片尾）→ 退出码 `2`，停止交付。详见 `references/pitfalls.md`。
- 医学术语用 `assets/保护词表-医疗示例.txt` 扩充保护词表（成语、辨证名词、专有短语）。

### 步骤 4 · 素材、封面、包装

- 按逐字稿语义匹配插入素材，每条 ≥3 处（建议 5 处），开头最重要；素材竖屏化、静音、清除平台硬字幕；不重复使用同一素材；切素材点落在句子边界之后。
- **插入素材（含视频片段）统一走 `<SKILL_DIR>/scripts/select_inserts.py` 三段式**：`--scan` 按字幕句边界出候选（**不改任务单**）→ 人工编辑 `输出/insert_points_候选.json` 确认语义、源入出点、授权 → `--apply` 写入 `plan.json` 并质检 → 工程生成前 `--verify` 复核。命令：`python <SKILL_DIR>/scripts/select_inserts.py --project <项目目录> --scan|--apply|--verify`。
- **大素材库两阶段扫描（v1.4）**：阶段 A 全库**只扫文件名**建索引（实测 905 个 mp4 约 19ms，`--include 关键词` 可按主题收窄）；阶段 B 只对最终入选的 3–5 个素材调 ffprobe 读时长/帧率/分辨率（约 37ms/个）。禁止对上千素材逐个探测（约 33s），也不要用 `--no-probe` 猜源入出点（会因容器时长余量不足导致 `VideoSegment` 越界）。文件名语义打分用主题 2-gram + 停用字过滤，避免「三伏天/人多」被「天/人」单字误命中。
- `plan.json` 的 `assets.insert_points` 是对象数组，字段定义见模板 `insert_points_schema`：**AI 只给候选、不自动定稿**；候选默认 `license_status=unknown`，校验必 FAIL 以强制人工确认授权（裁掉水印 ≠ 取得授权）。视频片段必须 `volume=0`（静音 ≠ 删音轨）、带源入出点且留 ≥0.2s 余量、单条 1.7–2.4s；同轨零重叠、素材不重复、切点落在字幕句边界、开头 3s 内有插入。退出码 2 立即停止批量与交付。
- 素材来源顺序：项目 `素材/` → 团队素材库（只读复制）→ 外部可商用来源；记录来源清单。
- 封面按 `assets/封面生成提示词.md` 执行：优先级「沿用账号封面模板（只换文案）→ 成片抽帧 + 抠图白边 → 无模板时才用提示词生成底图」；1080×1920、底图 + 人像抠图白边 + 超大主题字 + 黄色两行标题 + 半透明横条；文字放进灰框并垂直居中、不出现药名。
- **封面三条护栏**：① 人像必须来自本人授权真人照片或成片抽帧，禁止文生图凭空生成「医生脸」冒充真人；② 中文文案不让模型直出，AI 只出空白标题条，文案后期叠加且逐字取自 `plan.json` 的 `cover` 字段；③ 医疗合规人工复核（无绝对化用语、无疗效/剂量建议、肖像与医院标识有授权）。底图来源与授权写进 `cover.portrait_source` / `cover.portrait_license`。
- 提示词是文本资产、不是出图脚本：本技能不自动出封面，出图与叠字后须按 `assets/交付验收清单.md` E 节人工验收；`draft_cover.jpg`（工程缩略图）与独立交付封面 JPG 两者都要有。
- 顶标题全片常驻，用吸引点击的问句；美颜只作用于医生口播画面，不作用于插入素材。

### 步骤 5 · 生成工程与成片

- **视频片段进剪映工程（v1.4 阶段 2）**：`<SKILL_DIR>/scripts/build_insert_draft.py --project <项目目录>` 默认 dry-run 只出 `输出/工程构建/构建计划.json`；人工确认后加 `--build --drafts-dir <剪映草稿目录> --draft-name <新工程名>` 才写工程。它复用 `select_inserts.py` 的全部门禁，用 `pyJianYingDraft` 把每条插入点写成 `VideoSegment(source_timerange, volume=0)` 并挂到独立「插入素材」轨；可选 `--main-video` 建底层视频轨、`--srt` 建最上层字幕轨。产出 `draft_content_明文备份.json`、`构建报告.json`、`剪映打开验证.md`。**同名工程一律拒绝覆盖**（红线：版本不覆盖），门禁不过或构建失败返回退出码 2。
- **边界**：该脚本只负责把插入片段挂进工程，不负责主视频剪辑、成片渲染、封面与导出；完整六/七轨工程仍按 `references/environment.md` 由 `jy_tools.py` 链路补齐。剪映 11.7 现存工程是加密 `draft_content.json`，本脚本产出明文工程，**能否被 11.7 打开必须人工在剪映验证一次**，且 `root_meta_info.json` 需另行注册或靠剪映扫描目录发现。
- 按 `references/environment.md` 的团队提示词调用本机剪映技能：`plan.json` → `jy_tools.py build` → 注册 `root_meta_info.json`。
- 工程必须包含独立视频分镜轨、原声轨、BGM 轨、顶标题轨、字幕轨、免责声明轨；顶标题 `transform_y=0.84`，正文胸口字幕 `-0.20`，免责声明 `≈-0.91`。
- 成片另存并与工程分开；每次改版生成新版本文件，不覆盖旧版。
- **先交 1 条样板给用户验收，通过后才能批量处理其余视频**。

### 步骤 6 · 回读与质检

- `jy_tools.py read` 解密回读工程，逐条核对：能否打开、时长、分镜数、轨道、文本条数、素材路径、坐标、必备文件（`draft_info.json` / `draft_content.json`、`draft_meta_info.json`、`draft_cover.jpg`）。
- 运行 `<SKILL_DIR>/scripts/qc_delivery.py --project <项目目录> --drafts <剪映草稿目录>` 做交付验收，生成 `输出/验收报告.md`。**默认严格：存在 FAIL 返回退出码 `1`**，失败项必须修复或明确报告，不得略过；`--no-strict` 仅供排查。
- 用关键帧对照成片与工程画面（历史上曾因两套坐标换算不一致导致标题压到人物脸上）。

### 步骤 7 · 交付

- 每条交付：独立工程目录 + 可预览 MP4 + 封面 + `plan.json` + 字幕文件 + 剪辑说明 + 验收报告。
- 输出汇总表：工程名、完整路径、时长、轨道结构、成片路径、封面路径、验收结果、剩余风险。
- 批量时一条一工程、保留旧版本；任何加密写入、注册或回读失败都停止并报告。

## 脚本（核心脚本纯 Python 3 标准库；工程构建需 pyJianYingDraft）

| 脚本 | 用途 | 典型命令 |
|---|---|---|
| `<SKILL_DIR>/scripts/init_project.py` | 搭建/校验五文件夹项目结构，生成任务单 | `python <SKILL_DIR>/scripts/init_project.py <项目目录> --doctor 示例乙 --topic 前列腺增生` |
| `<SKILL_DIR>/scripts/build_subtitles.py` | 逐字稿 → 断句字幕 + 强制对齐 + 质检（v1.1） | `python <SKILL_DIR>/scripts/build_subtitles.py --script 文案.txt --out 输出/字幕 --words asr-words.json --duration <最终成片时长> --protect <SKILL_DIR>/assets/保护词表-医疗示例.txt` |
| `<SKILL_DIR>/scripts/style_profile.py` | 从剪映明文草稿 JSON 提取风格参数卡 | `python <SKILL_DIR>/scripts/style_profile.py --draft draft_content.json --out 风格卡.json` |
| `<SKILL_DIR>/scripts/qc_delivery.py` | 工程/成片/封面/字幕/命名/版本交付验收（默认严格退出） | `python <SKILL_DIR>/scripts/qc_delivery.py --project <项目目录> --drafts <剪映草稿目录>` |
| `<SKILL_DIR>/scripts/select_inserts.py` | 插入素材（含视频片段）候选、授权确认、硬约束质检；全库只扫文件名 + 入选才 ffprobe（v1.4） | `python <SKILL_DIR>/scripts/select_inserts.py --project <项目目录> --scan --assets <素材库> --include 失眠` |
| `<SKILL_DIR>/scripts/build_insert_draft.py` | 把插入点写成剪映草稿工程的 `VideoSegment`（v1.4 阶段 2） | `python <SKILL_DIR>/scripts/build_insert_draft.py --project <项目目录> --build --drafts-dir <剪映草稿目录> --draft-name <新工程名>` |
| `<SKILL_DIR>/scripts/selftest.py` | 回归自测 20 项，任意工作目录可跑 | `python <SKILL_DIR>/scripts/selftest.py` |

除 `selftest.py` 打印人读汇总外，所有脚本均以 JSON 输出结果，便于智能体直接读取；`--help` 查看完整参数。字幕/选点/验收/自测脚本仍是纯标准库、任何机器可跑；**只有 `build_insert_draft.py --build` 需要 `pyJianYingDraft`**，动检与自适应缩放用到可选的 ffmpeg/ffprobe，缺依赖会明确报错而不猜数值。剪映专用加密与注册操作仍交给本机 `jy_tools.py`。**每次升级技能包先跑 `selftest.py`，20 项全绿再分发。**

## 参考资料（按需加载）

- `references/company-standard.md`：参数基线、输入/交付/命名规范、字幕规则、验收清单（**执行剪辑前必读**）。
- `references/pitfalls.md`：历史真实故障与根因、修正方案、排查顺序。
- `references/environment.md`：工具链清单、路径、新机部署、跨软件适配、剪映技能调用规范。
- `references/prompt-cards.md`：可直接发给团队/新电脑/批量任务的提示词卡。
- `references/对话提取与规范原文.md`：完整对话时间线、参数出处与证据行号索引（需要追溯「为什么这么定」时查阅）。

## 资源文件

- `assets/剪辑任务单模板.json`：plan.json 标准模板（含 `cover` 封面字段）。
- `assets/封面生成提示词.md`：封面底图提示词与三条护栏（公开版已脱敏）。
- `assets/交付验收清单.md`：人工验收勾选项。
- `assets/安装与分发说明.md`：全员安装与升级说明。
- `assets/保护词表-医疗示例.txt`：字幕断句保护词示例。

## 失败处理原则

- 输入缺项、草稿被占用、素材缺失、加密写入/注册/回读失败、字幕质检不通过或验收脚本返回非 0 → **停止并报告具体错误、影响和需要补充的文件**。
- 不推测剪辑器按钮位置，不把未验证的界面操作写进教程，不把「待确认」写成既成事实。
- 样板阶段未完成的规范项必须登记在 `acceptance.deferred_items`；批量已开始而待补项未清零，验收判不合格，不得用 WARN 把半成品写成合格。
- 同类修改出现 3 次以上，按「三次法则」升级为团队参数，并更新 `references/company-standard.md` 与风格卡。
