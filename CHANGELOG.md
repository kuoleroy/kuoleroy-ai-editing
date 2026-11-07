# 变更记录

## v1.5 · 原片协同门禁 + 公开发布（2026-11-07）

- `build_insert_draft.py` 支持由任务单 `delivery.main_video` 驱动主视频轨，新增主视频 ffprobe 全参数探测（时长 / 分辨率 / 帧率 / 音轨）。
- 把原本 `min(原片时长, 最终时长)` 的**静默截断**改为显式时间轴决议：记录 `raw_duration` / `final_duration` / `trim_tail`，最终时长超过原片容器时长直接判 FAIL。
- 新增硬门禁：插入点、字幕 JSON 与 SRT 末条都必须落在「片尾清理后的最终时间轴」内，超出即退出码 2，防止沿用原始 ASR 时间造成音画漂移。
- 原片帧率与工程帧率不一致、画布尺寸不一致、缺音轨时登记警告并写入构建计划（帧率统一规则仍待规范层批准，暂不拦截）。
- `剪辑任务单模板.json` 新增 `delivery.main_video`；`draft_dir` 去掉写死的盘符，改为换机必填空值 + 说明。
- 公开版完成脱敏：真实姓名、内部盘符、账户路径、团队标识替换为示例值 / 占位符；新增 `README.md`、`LICENSE`、`CHANGELOG.md`、`.gitignore`。
- 实测结论补充：剪映 11.7 会打开并加密保存本脚本产出的明文工程（生成 `draft_cover.jpg`、`.backup/load.bak`、`.locked`），换机仍需人工确认一次。

## v1.4 · 视频片段自动进剪映工程（阶段 2）

- 新增 `scripts/build_insert_draft.py`：把通过门禁的插入点写成 `VideoSegment(source_timerange, volume=0)` 并挂到独立「插入素材」轨；可选主视频轨与 SRT 字幕轨；cover 自适应缩放；相邻帧 framemd5 动检（防静帧冒充视频片段）；明文回读审计；同名工程拒绝覆盖；默认 dry-run。
- `select_inserts.py` 改为**全库只扫文件名 + 只对入选素材 ffprobe** 的两阶段扫描（905 个素材实测 19ms 建索引 + 5 个探测 193ms，原逐文件探测约 33s）；新增 `--include` 主题关键词；语义打分升级为 2-gram + 停用字过滤。
- `selftest.py` 16 → 20 项；修复「构建期报错会抛 KeyError 而不是返回退出码 2」。

## v1.3 · 插入素材能力（阶段 1）

- 新增 `scripts/select_inserts.py`（`--scan` / `--apply` / `--verify`）；`insert_points` 升级为带字段说明的对象数组契约。
- 硬约束：≥3 处、开头 3s 内有插入、单条 1.7–2.4s、源片段 ≥0.2s 余量、`volume=0`、`license_status=approved`、句边界切点、零重叠、不重复；候选授权默认 `unknown` 必 FAIL，强制人工确认。
- AI 只给候选、不自动定稿；`selftest.py` 11 → 16 项。

## v1.2 · 封面能力

- 新增 `assets/封面生成提示词.md`（由封面样本反推，按规范改造）：三条护栏 = 不凭空生成真人医生脸、中文文案不直出、医疗合规人工复核。
- `plan.json` 的 `cover` 增补底图来源 / 授权 / 风格变体 / 合规复核字段；验收清单 E 节增补 6 项。提示词是文本资产，不自动出图。

## v1.1 · P0 修复

- `build_subtitles.py` 改为逐字稿 / ASR 字符级强制对齐（difflib）、禁止时间轴外推、增加 ASR 首尾覆盖与匹配率门禁、`--duration` 必填、断句不跨标点、单字行判不合格、默认保留 ASR 首句实际起点。
- `qc_delivery.py` 默认严格退出（FAIL 返回 1）、兼容剪映 11.7 的 `draft_content.json`、支持 `acceptance.deferred_items`。
- `init_project.py` 重写（缺模板明确报错）；所有命令统一 `<SKILL_DIR>/scripts/`；新增 `selftest.py`（11 项）。

## v1.0

- 初始版本：六条红线、参数基线、历史故障库、五文件夹流程、字幕与验收脚本、安装分发说明。
