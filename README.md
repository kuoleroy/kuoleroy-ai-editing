# kuoleroy-ai-editing

面向短视频团队的 **AI 辅助剪辑技能包**，用于 WorkBuddy、Codex 等智能体。它把「五文件夹输入 → 一条视频一个可编辑工程 + 成片 + 封面 + 验收报告」的流程固化为可重复执行的规范与脚本，核心是**用标准化任务单（plan.json）驱动工程生成，并用机器门禁替代人工口头检查**，而不是靠鼠标点击剪辑器界面。

> 本仓库为公开发布版：真实姓名、内部盘符、账户路径、团队标识均已替换为示例值或占位符（`<DRAFTS>` / `<ASSETS>` / `<OUTPUT>` / `<PUBLIC_LIBRARY>` / `<TOOLS>` 等）。参数基线、门禁逻辑与脚本功能与内部版一致。

## 它解决什么问题

批量生产口播短视频时最容易翻车的几件事：字幕与人声不同步、插入素材盖掉字幕、成片没有对应可编辑工程、剪映里找不到草稿、素材授权无人确认、验收「看起来通过」实际缺项。本技能包把这些历史故障沉淀为六条红线、参数基线、故障库和可执行质检门禁。

**六条红线**：① 工程文件是硬性交付物（不接受 FFmpeg 直出 MP4 代替）；② 一视频一独立工程、版本不覆盖；③ 不猜按钮、只写文件与脚本驱动的流程；④ 公共素材盘只读；⑤ 先剪 1 条样板、通过后才批量；⑥ 验收以回读与质检结果为准。

## 快速开始

把整个 `kuoleroy-ai-editing` 文件夹复制到以下任一目录即可（**目录名必须与 `SKILL.md` 的 `name` 一致**）：

```
~/.workbuddy/skills/kuoleroy-ai-editing/     # WorkBuddy 用户级技能
.codex/skills/kuoleroy-ai-editing/           # Codex 工作区技能
```

所有脚本用 `<SKILL_DIR>/scripts/xxx.py` 绝对路径调用——智能体的 cwd 通常是项目目录，写相对路径 `scripts/xxx.py` 会找不到文件。

一键回归自测（20 项，任意工作目录可跑，纯标准库）：

```bash
python <SKILL_DIR>/scripts/selftest.py
```

三分钟手工自测：

```bash
python <SKILL_DIR>/scripts/init_project.py ./selftest --doctor 示例甲 --topic 失眠
python <SKILL_DIR>/scripts/build_subtitles.py \
  --script <SKILL_DIR>/assets/示例逐字稿.txt \
  --out ./selftest/输出/字幕 \
  --words <SKILL_DIR>/assets/示例词级时间戳.json \
  --protect <SKILL_DIR>/assets/保护词表-医疗示例.txt \
  --duration 42.60
python <SKILL_DIR>/scripts/qc_delivery.py --project ./selftest
```

第三条在自测环境下返回退出码 1（缺成片 / 封面 / 工程）是**预期行为**，说明验收门禁生效。详见 `assets/安装与分发说明.md`。

## 工作流

| 阶段 | 脚本 | 作用 |
|---|---|---|
| 1 初始化 | `init_project.py` | 五文件夹结构 + 生成 `输出/剪辑任务单.json` |
| 2 字幕 | `build_subtitles.py` | 逐字稿 ↔ ASR 词级时间戳**字符级强制对齐**、断句、保护词、质检；禁止按字数估时与外推时间轴 |
| 3 风格卡 | `style_profile.py` | 从明文剪映草稿提取风格参数 |
| 4 插入素材 | `select_inserts.py` | 全库只扫文件名建索引 → 主题打分 → 只对入选素材做 ffprobe；产出候选，**人工确认语义 / 源入出点 / 授权**后写入任务单 |
| 5 工程 | `build_insert_draft.py` | 把通过门禁的插入点写成剪映草稿的 `VideoSegment(source_timerange, volume=0)`，可带主视频轨与 SRT 字幕轨；默认 dry-run |
| 6 验收 | `qc_delivery.py` | 工程 / 成片 / 封面 / 字幕 / 命名 / 版本检查，默认严格退出 |
| — | `selftest.py` | 20 项回归，升级分发前必跑 |

典型链路：

```bash
# 1) 扫描素材，只出候选，不改任务单
python <SKILL_DIR>/scripts/select_inserts.py --project <项目> --scan \
  --assets <素材库> --include 失眠 --include 睡眠
# 2) 人工编辑 输出/insert_points_候选.json，把 license_status 改为 approved
python <SKILL_DIR>/scripts/select_inserts.py --project <项目> --apply
python <SKILL_DIR>/scripts/select_inserts.py --project <项目> --verify
# 3) 先看计划，再构建
python <SKILL_DIR>/scripts/build_insert_draft.py --project <项目>
python <SKILL_DIR>/scripts/build_insert_draft.py --project <项目> --build \
  --drafts-dir <DRAFTS> --draft-name <新工程名> \
  --main-video <口播原片.mp4> --srt <项目>/输出/字幕/字幕.srt
```

**硬门禁**（任一不过即返回非零退出码并停止）：无词级时间戳、逐字稿与 ASR 文本不一致、ASR 覆盖不足、缺最终时长、插入素材 < 3 处、单条时长越界、源片段余量不足、未静音、切点不在句边界、素材重叠或重复、`license_status != approved`、字幕 / 插入点超出片尾清理后的最终时间轴、同名工程已存在。自动化必须**同时**检查 stdout 的 `ok` 字段与退出码。

## 依赖

- **核心脚本**：仅 Python 3 标准库，任何机器可直接运行。
- **构建剪映工程**（`build_insert_draft.py --build`）：`pyJianYingDraft`、`JianyingPro 11.x`；需自行准备草稿目录。
- **可选**：`ffmpeg` / `ffprobe`（素材精确探测、动态性 framemd5 校验、自适应缩放）、`faster-whisper`（词级时间戳）。
- 剪映专用加密写入与首页注册不在本包内，由本机工具链适配层负责，详见 `references/environment.md`。

## 已知边界（诚实清单）

当前版本**没有**实现：剪映 11.7 加密工程的解密回读双路径、七轨层级中的「插入素材」独立规范条目与全屏安全区、节奏压缩 `time_map.json` 的一等公民支持、59.94→30fps 帧率统一、封面自动出图与叠字、BGM / 顶标题 / 免责声明轨的自动构建、成片渲染与导出、素材授权状态的外部核验。这些仍需在剪映里人工确认或后续迭代。

脚本 `ok: true` 只证明**工程结构与门禁正确**，不等于剪映一定能打开、也不等于成片已导出。首次换机务必按 `输出/工程构建/剪映打开验证.md` 人工打开一次。

## 目录

```
SKILL.md                     技能入口、红线、参数与脚本索引
references/
  company-standard.md       参数基线与验收标准
  environment.md            环境自检、路径解析
  pitfalls.md               历史故障库
  prompt-cards.md           可直接复制的提示词卡
  对话提取与规范原文.md      规范来源与证据（脱敏版）
assets/
  剪辑任务单模板.json        plan.json 标准模板
  交付验收清单.md            人工验收勾选项
  封面生成提示词.md          封面提示词与三条合规护栏
  安装与分发说明.md          安装、自测、版本变更
  示例逐字稿.txt / 示例词级时间戳.json / 保护词表-医疗示例.txt
scripts/                     7 个脚本，见上表
```

## 医疗合规

本包只约束流程，不生成医疗结论。逐字稿不得由 AI 改写，封面 / 标题不出现药名与「根治 / 包好 / 特效 / 治愈」等绝对化用语，不提供剂量与疗效建议；免责声明使用团队统一模板，肖像与医院标识须有授权。AI 生成的人像不得冒充真实医生。**裁掉水印不等于取得授权**，素材授权状态必须逐条人工确认。

## 许可

MIT，见 [LICENSE](LICENSE)。规范文档与示例数据均为脱敏后的整理成果，使用前请自行核对是否适用于你的生产环境。
