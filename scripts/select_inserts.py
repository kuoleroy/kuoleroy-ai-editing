#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插入素材（含视频片段）选点与质检 —— kuoleroy-ai-editing v1.3 阶段 1。

纯 Python 3 标准库；ffprobe 为**可选**依赖，缺失时只记录、不猜测时长。
本脚本不写剪映工程（工程构建交给 build_insert_draft.py）、不渲染成片、不自动定稿：
AI 给候选，人工确认后 --apply。

用法（<SKILL_DIR> 为技能包目录，不依赖当前工作目录）：
  # 1) 扫描素材 + 按字幕句边界生成候选（不改 plan.json）
  python <SKILL_DIR>/scripts/select_inserts.py --project <项目目录> --scan
  # 2) 人工编辑 输出/insert_points_候选.json（语义、源入出点、授权状态）后写入任务单并校验
  python <SKILL_DIR>/scripts/select_inserts.py --project <项目目录> --apply
  # 3) 工程生成前 / 验收前只复核已有插入点
  python <SKILL_DIR>/scripts/select_inserts.py --project <项目目录> --verify

大素材库用法（默认即为两阶段，无需额外开关）：
  - 阶段 A 全库只扫文件名建索引（905 个实测 ~17ms），--include 可按主题关键词筛选；
  - 阶段 B 只对最终入选的 3–5 个素材调用 ffprobe 读时长/帧率/分辨率（~37ms/个）；
  - 因此上千素材也无需 --max-assets 限量；--no-probe 可彻底跳过探测，但源信息需人工填。

约定：
  - 候选默认 license_status=unknown，校验必 FAIL，强制人工确认授权（裁水印 != 取得授权）。
  - 视频片段 volume 必须为 0（静音≠删音轨），且必须带源入出点并留 >=0.2s 余量。
  - 切点必须落在字幕句子边界（句首/句末 ±0.15s），不切在词中间。
  - 校验不过返回退出码 2，自动化必须停止，不得继续批量或交付。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MIN_DURATION = 1.7
MAX_DURATION = 2.4
SOURCE_MARGIN = 0.2
BOUNDARY_TOL = 0.15
OPENING_LIMIT = 3.0
DEFAULT_DURATION = 1.9


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法解析 {path.name}：{exc}") from exc


def parse_rate(value: str):
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return round(float(num) / float(den), 3) if float(den) else None
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def probe_one(path: Path, ffprobe: str) -> dict:
    """读时长/帧率/分辨率/音轨。失败只记录，不猜数值。"""
    info = {"duration": None, "fps": None, "resolution": None,
            "has_audio": None, "probe": "skipped"}
    if not ffprobe:
        return info
    cmd = [ffprobe, "-v", "error", "-show_entries",
           "format=duration:stream=codec_type,width,height,avg_frame_rate",
           "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        if r.returncode != 0:
            info["probe"] = "failed"
            info["probe_error"] = (r.stderr or "").strip()[:200]
            return info
        data = json.loads(r.stdout or "{}")
        info["probe"] = "ok"
        try:
            info["duration"] = round(float(data.get("format", {}).get("duration")), 3)
        except (TypeError, ValueError):
            pass
        streams = data.get("streams", [])
        info["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
        for s in streams:
            if s.get("codec_type") == "video":
                w, h = s.get("width"), s.get("height")
                info["resolution"] = f"{w}x{h}" if w and h else None
                info["fps"] = parse_rate(s.get("avg_frame_rate"))
                break
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        info["probe"] = "failed"
        info["probe_error"] = str(exc)[:200]
    return info


def scan_assets(folder: Path, include=None, max_assets: int = 0) -> list:
    """阶段 A：只扫文件名建索引，不调用 ffprobe（905 个文件实测约 17ms）。

    大素材库的正确用法：先按文件名关键词把全库索引建起来，语义排序后
    只对最终入选的 3–5 个文件做精确探测（见 probe_fn），避免上千次 ffprobe。
    """
    if not folder.is_dir():
        return []
    keywords = [k.strip() for k in (include or []) if k and k.strip()]
    found = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            kind = "video"
        elif ext in IMAGE_EXT:
            kind = "image"
        else:
            continue
        if keywords and not any(k in p.name for k in keywords):
            continue
        found.append({"path": str(p), "name": p.name, "type": kind,
                      "stem": p.stem, "probe": "pending"})
        if max_assets and len(found) >= max_assets:
            break
    return found


def rel_source(project: Path, path: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return path.as_posix()


def boundary_times(events: list) -> list:
    times = []
    for e in events:
        for key in ("start", "end"):
            if key in e and isinstance(e[key], (int, float)):
                times.append(float(e[key]))
    return times


# 全库只扫文件名时，必须压低「天/人/不/一」这类高频无意义单字，
# 否则「三伏天」「人多」会因单字交集挤掉真正相关的「失眠」。
STOP_CHARS = set(
    "一二三四五六七八九十不人了是在有和我你他她它们这那就都很还也把被给让使对与及等的"
    "个中之以上下多少天地大小年月日时分秒视频剪辑广告参考证明一句话男生女生年轻人"
)


def topic_of(stem: str) -> str:
    """从「剪辑视频_主题_编号」里取出主题，去掉统一前缀与尾部编号。"""
    s = re.sub(r"^(剪辑视频|视频)[_-]?", "", str(stem))
    s = re.sub(r"[_-]?\d{2,}$", "", s)
    return s


def semantic_score(stem: str, text: str) -> int:
    """文件名主题与字幕的 2-gram 匹配为主、有效单字为辅；仅用于排序候选。

    纯单字交集在大素材库会严重误命中（实测全库直扫 5 条里错 4 条），
    连续二字词命中给 3 分，非停用单字给 1 分。
    """
    chars = [c for c in re.findall(r"[\u4e00-\u9fff]", topic_of(stem))
             if c not in STOP_CHARS]
    if not chars:
        return 0
    clean = re.sub(r"[^\u4e00-\u9fff]", "", str(text))
    bigrams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    return 3 * sum(1 for b in bigrams if b in clean) + \
        sum(1 for c in set(chars) if c in clean)


def anchor_text(events: list, t: float) -> str:
    if not events:
        return ""
    best = min(events, key=lambda e: min(abs(float(e["start"]) - t),
                                         abs(float(e["end"]) - t)))
    return str(best.get("text", ""))


def build_candidates(project: Path, plan: dict, events: list, index: list,
                     default_duration: float, max_points: int, probe_fn=None) -> tuple:
    warnings = []
    final = float(plan.get("delivery", {}).get("final_duration") or 0)
    last_end = max((float(e["end"]) for e in events), default=0.0)
    limit = final if final > 0 else last_end
    if not final:
        warnings.append("任务单未填 delivery.final_duration，暂用字幕末点定位插入窗口；交付前必须改成最终成片时长")
    if limit <= 0:
        return [], warnings + ["未拿到成片时长（delivery.final_duration）且字幕事件为空，无法定位插入窗口"]

    # 合法切点：片头 + 每个字幕事件的句末；片尾 0.5s 内不插
    points = []
    if events:
        head = float(events[0]["start"])
        # 片头特例：口播在 0.5s 内开口时插入卡从 0 起（声音先入、画面后切）
        points.append(round(head if head >= 0.5 else 0.0, 3))
    for e in events:
        t = round(float(e["end"]) + 0.02, 3)
        if 0.5 <= t <= limit - 0.5:
            points.append(t)
    points = sorted(set(points))

    # 同一轨零重叠：相邻点至少间隔 default_duration
    spaced = []
    for t in points:
        if not spaced or t - spaced[-1] >= default_duration:
            spaced.append(t)
    points = spaced[:max_points]


    if len(index) < 3:
        warnings.append(f"素材只有 {len(index)} 个，规范要求 >=3 处且不重复，需补素材")
    if len(points) < 3:
        warnings.append(f"只找到 {len(points)} 个合法句边界窗口，需检查字幕时长或成片时长")

    # 逐字稿全文作语义上下文，避免只看单句锚点（一句常只有 4–6 字，容易误命中）
    full_text = "".join(str(e.get("text", "")) for e in events)
    used = set()
    items = []
    for t in points:
        anchor = anchor_text(events, t)
        pool = [a for a in index if a["path"] not in used]
        if not pool:
            warnings.append(f"{t}s 处无可用素材（素材已用尽），候选数不足")
            break
        pool.sort(key=lambda a: (-(2 * semantic_score(a["stem"], anchor) +
                                  semantic_score(a["stem"], full_text)), a["name"]))
        picked = pool[0]
        used.add(picked["path"])
        # 阶段 B：懒探测，只有真正入选的素材才读时长/帧率/分辨率
        if probe_fn:
            probe_fn(picked)

        src_in = src_out = None
        if picked["type"] == "video":
            src_in = 0.0
            src_out = round(default_duration + 0.3, 3)
            duration_src = picked.get("duration")
            if duration_src:
                src_out = round(min(src_out, max(duration_src - 0.1,
                                                  default_duration + SOURCE_MARGIN)), 3)
                if src_out - src_in < default_duration + SOURCE_MARGIN:
                    warnings.append(f"{picked['name']} 可截取时长不足，source_out 需人工改")
            if picked.get("probe") != "ok":
                warnings.append(f"{picked['name']} 未经 ffprobe 探测，源入出点为占位值，需人工核对")

        items.append({
            "type": picked["type"],
            "source": rel_source(project, Path(picked["path"])),
            "source_in": src_in,
            "source_out": src_out,
            "target_in": t,
            "duration": default_duration,
            "volume": 0,
            "fit": "fullscreen",
            "hard_subtitle_clean": True,
            "license_status": "unknown",
            "semantic_anchor": anchor,
            "source_fps": picked.get("fps"),
            "source_resolution": picked.get("resolution"),
            "note": "候选：需人工确认语义、源入出点与授权状态后 --apply",
        })
    return items, warnings


def write_plan_md(path: Path, items: list, warnings: list) -> None:
    lines = ["# 素材插入方案（候选，待人工确认）", "",
             "> 由 `select_inserts.py --scan` 生成。AI 只给候选，**不自动定稿**。",
             "> 请逐条核对语义、源入出点、授权状态，编辑 `insert_points_候选.json` 后再 `--apply`。",
             "> 授权默认 `unknown` 会被质检判 FAIL；裁掉水印不等于取得授权。", ""]
    if warnings:
        lines += ["## 待处理警告", ""] + [f"- {w}" for w in warnings] + [""]
    lines += ["| # | 目标时间 | 类型 | 素材 | 源入/出 | 时长 | 字幕锚点 | 授权 |",
              "|---|---|---|---|---|---|---|---|"]
    for i, it in enumerate(items, 1):
        src = f"{it['source_in']}–{it['source_out']}" if it["type"] == "video" else "—"
        lines.append(f"| {i} | {it['target_in']}s | {it['type']} | `{it['source']}` | "
                     f"{src} | {it['duration']}s | {it['semantic_anchor']} | "
                     f"{it['license_status']} |")
    lines += ["", "## 约束", "",
              f"- 每条 {MIN_DURATION}–{MAX_DURATION}s；同轨零重叠；素材不重复；开头 3s 内必须有插入",
              f"- 视频片段 `volume=0`（静音，不是删音轨）；源片段留 >= {SOURCE_MARGIN}s 余量",
              "- 切点落在字幕句子边界之后/句首，不切在词中间；保护词不跨画面",
              "- `fit=pip` 需额外声明 `safe_area=top12_bottom22` 并人工确认不压医生脸",
              "- 动态片段整屏替换优先；源帧率与工程 30fps 不一致时登记待补项", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def verify(project: Path, plan: dict, events: list, min_inserts: int) -> dict:
    items = plan.get("assets", {}).get("insert_points", []) or []
    checks = {}
    failed = []
    warnings = []
    bounds = boundary_times(events)

    def add(name: str, ok: bool, reason: str = ""):
        checks[name] = bool(ok)
        if not ok:
            failed.append({"item": name, "reason": reason})

    add("count", len(items) >= min_inserts,
        f"插入 {len(items)} 处，要求 >= {min_inserts} 处")
    add("opening", any(float(it.get("target_in", 1e9)) <= OPENING_LIMIT for it in items),
        f"开头 {OPENING_LIMIT}s 内没有插入素材")

    valid = True
    for i, it in enumerate(items, 1):
        tag = f"第{i}条 {it.get('source', '?')}"
        kind = it.get("type")
        if kind not in ("video", "image"):
            valid = False
            failed.append({"item": "type", "reason": f"{tag}: type 必须是 video/image"})
        dur = it.get("duration")
        try:
            dur = float(dur)
            if not (MIN_DURATION - 1e-6 <= dur <= MAX_DURATION + 1e-6):
                valid = False
                failed.append({"item": "duration",
                               "reason": f"{tag}: 时长 {dur}s 不在 {MIN_DURATION}–{MAX_DURATION}s"})
        except (TypeError, ValueError):
            valid = False
            failed.append({"item": "duration", "reason": f"{tag}: duration 不是数值"})
        if it.get("volume") != 0:
            valid = False
            failed.append({"item": "muted", "reason": f"{tag}: volume 必须为 0（静音≠删音轨）"})
        if it.get("license_status") != "approved":
            valid = False
            failed.append({"item": "license",
                           "reason": f"{tag}: 授权={it.get('license_status')}，未确认授权不得使用"})
        fit = it.get("fit", "fullscreen")
        if fit not in ("fullscreen", "pip"):
            valid = False
            failed.append({"item": "fit", "reason": f"{tag}: fit 只能是 fullscreen/pip"})
        if fit == "pip" and it.get("safe_area") != "top12_bottom22":
            valid = False
            failed.append({"item": "safe_area",
                           "reason": f"{tag}: 画中画须声明 safe_area=top12_bottom22 并人工确认不压脸"})
        src = Path(str(it.get("source", "")))
        full = src if src.is_absolute() else project / src
        if not str(it.get("source", "")).strip() or not full.exists():
            valid = False
            failed.append({"item": "source_exists", "reason": f"{tag}: 素材文件不存在"})
        if kind == "video":
            try:
                margin = float(it["source_out"]) - float(it["source_in"])
                need = float(dur) + SOURCE_MARGIN
                if margin + 1e-6 < need:
                    valid = False
                    failed.append({"item": "source_margin",
                                   "reason": f"{tag}: 可截取 {margin}s < 需要 {round(need, 3)}s（留 0.2s 余量）"})
            except (KeyError, TypeError, ValueError):
                valid = False
                failed.append({"item": "source_margin",
                               "reason": f"{tag}: 视频片段必须给 source_in/source_out 数值"})
        t = it.get("target_in")
        try:
            t = float(t)
            # 片头 0.5s 内的插入卡没有前置句边界，按实战「开头最重要」豁免
            head_exception = t <= 0.5
            if bounds and not head_exception and not any(abs(t - b) <= BOUNDARY_TOL for b in bounds):
                valid = False
                failed.append({"item": "sentence_boundary",
                               "reason": f"{tag}: {t}s 不在任何字幕句边界 ±{BOUNDARY_TOL}s 内"})
        except (TypeError, ValueError):
            valid = False
            failed.append({"item": "sentence_boundary", "reason": f"{tag}: target_in 不是数值"})

    checks["per_item"] = valid

    spans = []
    for it in items:
        try:
            spans.append((float(it["target_in"]), float(it["target_in"]) + float(it["duration"])))
        except (KeyError, TypeError, ValueError):
            pass
    spans.sort()
    overlap = any(spans[i][1] > spans[i + 1][0] + 1e-6 for i in range(len(spans) - 1))
    add("no_overlap", not overlap, "同轨插入区间存在重叠")
    sources = [it.get("source") for it in items]
    dup = len(sources) != len(set(sources))
    add("no_duplicate", not dup, "同一素材被重复使用")
    if not events:
        warnings.append("未读到字幕事件，句边界检查已跳过")

    return {"ok": not failed, "count": len(items), "checks": checks,
            "failed_checks": failed, "warnings": warnings}


def main() -> int:
    ap = argparse.ArgumentParser(description="插入素材（含视频片段）选点与质检")
    ap.add_argument("--project", required=True, help="项目目录（含 输出/剪辑任务单.json）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true", help="扫描素材并生成候选（默认）")
    mode.add_argument("--apply", action="store_true", help="把候选写入任务单并校验")
    mode.add_argument("--verify", action="store_true", help="只校验任务单里已有的插入点")
    ap.add_argument("--assets", help="素材目录，默认 <项目>/素材")
    ap.add_argument("--no-probe", action="store_true", help="跳过 ffprobe（自测/手工填写源信息时用）")
    ap.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="单条插入默认时长（秒）")
    ap.add_argument("--max-points", type=int, default=5, help="最多生成几个候选点")
    ap.add_argument("--min-inserts", type=int, default=3, help="最少插入处数")
    ap.add_argument("--include", action="append", default=[],
                    help="素材文件名关键词，可重复（大素材库先按主题筛选，避免全量 ffprobe）")
    ap.add_argument("--max-assets", type=int, default=0,
                    help="候选池上限，0=全库；只影响语义排序范围，不影响文件名扫描速度")
    args = ap.parse_args()

    project = Path(args.project).resolve()
    plan_path = project / "输出" / "剪辑任务单.json"
    subs_path = project / "输出" / "字幕" / "字幕.json"
    out_dir = project / "输出"
    try:
        plan = read_json(plan_path)
        if not isinstance(plan, dict):
            return emit({"ok": False, "error": f"缺任务单：{plan_path}"}, 2)
        subs = read_json(subs_path, {}) or {}
        events = subs.get("events", []) or []
    except ValueError as exc:
        return emit({"ok": False, "error": str(exc)}, 2)

    action = "apply" if args.apply else ("verify" if args.verify else "scan")
    if action == "verify":
        result = verify(project, plan, events, args.min_inserts)
        result["mode"] = "verify"
        (out_dir / "insert_points_质检.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return emit({"mode": action, "ok": result["ok"], "count": result["count"],
                     "checks": result["checks"], "failed_checks": result["failed_checks"]},
                    0 if result["ok"] else 2)

    if action == "apply":
        cand = out_dir / "insert_points_候选.json"
        items = read_json(cand)
        if not isinstance(items, list):
            return emit({"ok": False, "error": f"缺候选文件：{cand}（先跑 --scan 并人工确认）"}, 2)
        plan.setdefault("assets", {})["insert_points"] = items
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verify(project, plan, events, args.min_inserts)
        result["mode"] = "apply"
        (out_dir / "insert_points_质检.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return emit({"mode": action, "ok": result["ok"], "count": result["count"],
                     "checks": result["checks"], "failed_checks": result["failed_checks"]},
                    0 if result["ok"] else 2)

    # scan
    assets_dir = Path(args.assets).resolve() if args.assets else project / "素材"
    ffprobe = "" if args.no_probe else shutil.which("ffprobe")

    # 阶段 A：全库只扫文件名（905 个实测 17ms），不做任何逐文件探测
    t0 = time.perf_counter()
    index = scan_assets(assets_dir, args.include, args.max_assets)
    scan_ms = round((time.perf_counter() - t0) * 1000, 1)

    warnings = []
    if args.include:
        warnings.append("按文件名关键词筛选：" + "、".join(args.include))
    if args.max_assets:
        warnings.append(f"候选池上限 {args.max_assets} 个（仅影响语义排序范围，不影响扫描速度）")
    if str(assets_dir).startswith("\\\\"):
        warnings.append("素材目录是 UNC 路径，Git Bash 下探测可能不稳，必要时改用 PowerShell 先复制到本地")

    # 阶段 B：懒探测回调，只对真正入选的 3–5 个素材读时长/帧率/分辨率
    probe_fn = None
    if ffprobe:
        def probe_fn(entry):
            entry.update(probe_one(Path(entry["path"]), ffprobe))
    elif not args.no_probe:
        warnings.append("未找到 ffprobe，未读取时长/帧率；源入出点为占位值，需人工核对")

    t1 = time.perf_counter()
    items, gen_warn = build_candidates(project, plan, events, index,
                                       args.duration, args.max_points, probe_fn)
    probe_ms = round((time.perf_counter() - t1) * 1000, 1)
    warnings.extend(gen_warn)
    probed = [a for a in index if a.get("probe") in ("ok", "failed")]
    if ffprobe:
        bad = [a["name"] for a in probed if a["probe"] == "failed"]
        if bad:
            warnings.append("入选素材 ffprobe 探测失败，源信息需人工填写：" + "、".join(bad[:5]))
        warnings.append(f"文件名索引 {len(index)} 个 / 精确探测仅 {len(probed)} 个入选素材"
                        f"（扫描 {scan_ms}ms + 探测 {probe_ms}ms）")
    elif args.no_probe:
        warnings.append("已按 --no-probe 跳过精确探测：source_out/fps 为占位值，交付前必须人工核对")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "素材索引.json").write_text(
        json.dumps({"assets_dir": str(assets_dir), "index_mode": "filename_only",
                    "scan_ms": scan_ms, "probe_ms": probe_ms,
                    "ffprobe": bool(ffprobe), "count": len(index),
                    "probed_count": len(probed), "items": index}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out_dir / "insert_points_候选.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    write_plan_md(out_dir / "素材插入方案.md", items, warnings)
    return emit({"mode": "scan", "ok": True, "assets": len(index),
                 "index_mode": "filename_only", "probed_count": len(probed),
                 "scan_ms": scan_ms, "probe_ms": probe_ms,
                 "candidates": len(items), "ffprobe": bool(ffprobe),
                 "files": ["输出/素材索引.json", "输出/insert_points_候选.json",
                           "输出/素材插入方案.md"],
                 "warnings": warnings,
                 "note": "全库只扫文件名，只对入选素材精确探测；候选未写入任务单，"
                         "人工确认后把 license_status 改 approved 并跑 --apply"})


if __name__ == "__main__":
    raise SystemExit(main())
