#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已通过质检的插入点写成剪映草稿工程 —— kuoleroy-ai-editing v1.4 阶段 2。

职责边界（重要）：
  - 只负责「把 assets.insert_points 里的视频/图片片段挂进剪映草稿的独立轨道」。
  - 不负责主视频剪辑、成片渲染、封面、导出；完整工程仍需现有 jy_tools.py 链路补齐。
  - 不改六条红线、不改 company-standard.md 参数基线；轨道层级可由 --main-video / --srt 组合，
    人工需在剪映确认「插入素材轨在视频分镜之上、字幕轨之下」，避免盖掉字幕。
  - 默认 dry-run 只出构建计划；--build 才真正写工程，且**绝不覆盖同名已有工程**。

用法（<SKILL_DIR> 为技能包目录，不依赖当前工作目录）：
  # 1) 先看计划（不写任何工程，不需要装 pyJianYingDraft）
  python <SKILL_DIR>/scripts/build_insert_draft.py --project <项目目录>
  # 2) 人工确认后构建到剪映草稿目录
  python <SKILL_DIR>/scripts/build_insert_draft.py --project <项目目录> --build \
      --drafts-dir "<剪映草稿目录>" --draft-name "<新工程名>"
  # 3) 可选：带主视频轨与字幕轨，生成能直接预览的较完整工程
  ... --build --drafts-dir ... --main-video <口播原片.mp4> --srt <字幕.srt>

依赖：pyJianYingDraft（仅 --build 需要）、ffmpeg/ffprobe（可选，动检与自适应缩放需要）。
退出码：0 成功 / 2 门禁或构建失败。自动化必须同时看 stdout 的 ok 字段与退出码。
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from select_inserts import read_json, verify, boundary_times  # noqa: E402

DEFAULT_W, DEFAULT_H, DEFAULT_FPS = 1080, 1920, 30
INSERT_TRACK = "插入素材"
MAIN_TRACK = "视频分镜"
SUBTITLE_TRACK = "字幕"


def emit(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def resolve_source(project: Path, value: str) -> Path:
    p = Path(str(value))
    return p if p.is_absolute() else project / p


def ffprobe_duration(path: Path, ffprobe: str):
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return round(float((r.stdout or "").strip()), 3) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def probe_main_video(path: Path, ffprobe: str) -> dict:
    """读主视频（口播原片）的时长/分辨率/帧率/音轨；探测不到就如实留空，不猜数值。"""
    info = {"path": str(path), "name": path.name, "duration": None,
            "width": None, "height": None, "fps": None, "has_audio": None,
            "probe": "ok" if ffprobe else "unavailable"}
    if not ffprobe:
        return info
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height,avg_frame_rate,sample_rate,channels",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode != 0:
            info["probe"] = "failed"
            return info
        j = json.loads(r.stdout or "{}")
        info["duration"] = round(float(j.get("format", {}).get("duration") or 0), 3) or None
        for s in j.get("streams", []):
            if s.get("codec_type") == "video" and info["width"] is None:
                info["width"], info["height"] = s.get("width"), s.get("height")
                rate = str(s.get("avg_frame_rate") or "0/1")
                try:
                    num, den = rate.split("/")
                    info["fps"] = round(float(num) / float(den), 3) if float(den) else None
                except (ValueError, ZeroDivisionError):
                    info["fps"] = None
            elif s.get("codec_type") == "audio":
                info["has_audio"] = True
                info["sample_rate"] = s.get("sample_rate")
                info["channels"] = s.get("channels")
        if info["has_audio"] is None:
            info["has_audio"] = False
    except (OSError, subprocess.SubprocessError, ValueError):
        info["probe"] = "failed"
    return info


def srt_last_end(path: Path):
    """取 SRT 最后一条字幕的结束秒；解析不到返回 None（不静默当作 0）。"""
    if not path or not path.is_file():
        return None
    try:
        txt = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    stamp = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                       r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
    ends = [int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
            for _, _, _, _, h, m, s, ms in stamp.findall(txt)]
    return max(ends) if ends else None


def resolve_timeline(plan: dict, main: dict, items: list, args, events: list) -> dict:
    """决定工程最终时间轴，并校验字幕/插入点是否落在「片尾清理后」的轴上。

    历史故障：原片 73.728s，片尾清理后 71.94s；按容器时长铺主视频会让末句与
    插入点整体漂移。若还做过节奏压缩（time_map.json），所有时间轴必须整体重排，
    本脚本不接受未重排的字幕/插入点。
    """
    t = {"errors": [], "warnings": []}
    final = float(plan.get("delivery", {}).get("final_duration") or 0)
    raw = float(main.get("duration") or 0)
    t["raw_duration"] = raw or None
    t["declared_final_duration"] = final or None
    if not raw and not final:
        t["errors"].append("主视频时长与 plan.delivery.final_duration 都拿不到，拒绝按猜测铺时间轴")
        t["ok"] = False
        return t
    if final and raw and final > raw + 0.05:
        t["errors"].append(
            f"final_duration={final}s 超过原片容器时长 {raw}s，不可能靠裁切得到，"
            "请核对片尾清理结果或改填真实成片时长")
    if not final:
        final = raw
        t["warnings"].append("plan.delivery.final_duration 未填，暂按原片容器时长铺轨；"
                             "片尾清理后必须回填并重建工程")
    t["final_duration"] = round(final, 3)
    t["trim_tail"] = round(raw - final, 3) if raw and raw - final > 0.05 else 0.0
    if t["trim_tail"]:
        t["warnings"].append(
            f"主视频按片尾清理裁掉 {t['trim_tail']}s（{raw}→{final}s）；"
            "字幕/插入点必须基于清理后的时间轴，不能沿用原始 ASR 时间")

    over = [it for it in items
            if float(it.get("target_in", 0)) + float(it.get("duration", 0)) > final + 0.05]
    if over:
        t["errors"].append(f"{len(over)} 条插入素材超出最终时长 {final}s，"
                           "请先在剪辑时间轴上重排插入点")
    sub_end = max((float(e.get("end", 0)) for e in events), default=0.0)
    t["subtitle_last_end"] = round(sub_end, 3) or None
    if sub_end > final + 0.10:
        t["errors"].append(f"字幕末句 end={sub_end:.2f}s 超出最终时长 {final}s，"
                           "疑似用了未清理的原始时间轴")
    if args.srt:
        srt_end = srt_last_end(Path(str(args.srt)))
        t["srt_last_end"] = round(srt_end, 3) if srt_end is not None else None
        if srt_end is not None and srt_end > final + 0.10:
            t["errors"].append(f"SRT 末条 end={srt_end:.2f}s 超出最终时长 {final}s 过多")
        if srt_end is None:
            t["warnings"].append(f"SRT 未解析到时间轴，无法校验越界：{args.srt}")

    fps = main.get("fps")
    t["main_fps"] = fps
    if fps and abs(float(fps) - args.fps) > 0.05:
        t["warnings"].append(
            f"原片 {fps}fps 与工程 {args.fps}fps 不一致，可能出现 judder；"
            "阶段 0 的帧率规则尚未写入规范层，此处只登记不拦截")
    if main.get("width") and (main["width"], main["height"]) != (args.width, args.height):
        t["warnings"].append(f"原片 {main['width']}x{main['height']} 与画布 "
                             f"{args.width}x{args.height} 不一致，需在剪映确认铺满/裁切")
    t["has_audio"] = main.get("has_audio")
    if main.get("has_audio") is False:
        t["warnings"].append("主视频未探测到音轨：医生口播原片应保留原声，请核对素材")
    if main.get("probe") != "ok":
        t["warnings"].append("主视频 ffprobe 探测不完整，帧率/音轨/分辨率未能核实")
    t["ok"] = not t["errors"]
    return t


def frame_hash(ffmpeg: str, src: Path, t: float):
    """抽一帧算 framemd5；用于证明插进去的是会动的视频，而不是静帧图片。"""
    if not ffmpeg:
        return None
    try:
        r = subprocess.run(
            [ffmpeg, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(src),
             "-frames:v", "1", "-f", "framemd5", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        if r.returncode != 0:
            return None
        rows = [x for x in (r.stdout or "").splitlines() if x.strip() and not x.startswith("#")]
        return rows[-1].split(",")[-1].strip() if rows else None
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def check_motion(ffmpeg: str, src: Path, src_in: float, duration: float) -> dict:
    a = frame_hash(ffmpeg, src, src_in + 0.05)
    b = frame_hash(ffmpeg, src, src_in + max(0.2, duration * 0.6))
    if not a or not b:
        return {"status": "unknown", "reason": "ffmpeg 抽帧失败或未安装，动态性未验证"}
    return {"status": "moving" if a != b else "static",
            "frame_a_md5": a, "frame_b_md5": b,
            "reason": "相邻两帧不同，确认为动态视频" if a != b
                      else "两帧完全相同：可能是静帧/纯色画面，需人工确认素材是否可用"}


def fit_scale(resolution: str, width: int, height: int):
    """按 cover 计算等比缩放倍率，避免方屏/横屏素材留黑边。"""
    try:
        w, h = (int(x) for x in str(resolution).lower().split("x"))
        if w <= 0 or h <= 0:
            return None
        return round(max(width / w, height / h), 4)
    except (ValueError, AttributeError):
        return None


def build_plan(project: Path, plan: dict, items: list, args, events: list) -> tuple:
    """生成与 pyJianYingDraft 无关的构建计划，dry-run 也可跑。"""
    ffmpeg = shutil.which("ffmpeg")
    probes = []
    warnings = []
    for i, it in enumerate(items, 1):
        src = resolve_source(project, it.get("source", ""))
        row = {
            "index": i,
            "type": it.get("type"),
            "source": str(src),
            "material_type": "video" if it.get("type") == "video" else "photo",
            "target_timerange_us": [int(round(float(it["target_in"]) * 1_000_000)),
                                    int(round(float(it["duration"]) * 1_000_000))],
            "volume": 0,
            "track": INSERT_TRACK,
        }
        if it.get("type") == "video":
            sin, sout = float(it["source_in"]), float(it["source_out"])
            row["source_timerange_us"] = [int(round(sin * 1_000_000)),
                                          int(round((sout - sin) * 1_000_000))]
            if args.motion_check:
                row["motion"] = check_motion(ffmpeg, src, sin, float(it["duration"]))
                if row["motion"]["status"] == "static":
                    warnings.append(f"第{i}条 {src.name} 相邻两帧相同，疑似静帧，需人工确认")
            scale = None if args.no_auto_fit else fit_scale(it.get("source_resolution"),
                                                            args.width, args.height)
            row["fit_scale"] = scale
            if scale is None and not args.no_auto_fit:
                warnings.append(f"第{i}条 {src.name} 缺源分辨率，未计算自适应缩放，需在剪映手动铺满")
            if it.get("source_fps") and abs(float(it["source_fps"]) - args.fps) > 0.05:
                warnings.append(
                    f"第{i}条 {src.name} 源帧率 {it['source_fps']}fps 与工程 {args.fps}fps 不一致，"
                    "可能出现 judder，需登记待补项（帧率规则尚未写入规范层）")
        probes.append(row)

    # 主视频（口播原片）协同：探测真实参数并决定最终时间轴
    main_info, timeline = None, None
    if args.main_video:
        main_path = Path(str(args.main_video)).resolve()
        if not main_path.is_file():
            return {"ok": False, "stage": "main_video",
                    "error": f"主视频不存在：{main_path}"}, [f"主视频不存在：{main_path}"]
        main_info = probe_main_video(main_path, shutil.which("ffprobe"))
        timeline = resolve_timeline(plan, main_info, items, args, events)
        warnings.extend(timeline["warnings"])

    stage = []
    if args.main_video:
        stage.append({"track": MAIN_TRACK, "type": "video", "source": str(Path(args.main_video).resolve())})
    stage.append({"track": INSERT_TRACK, "type": "video/photo", "segments": len(items)})
    if args.srt:
        stage.append({"track": SUBTITLE_TRACK, "type": "srt", "source": str(Path(args.srt).resolve())})
    elif args.main_video:
        warnings.append("未提供 --srt：完整工程仍需字幕轨，当前仅主视频 + 插入素材两层")

    warnings.append("剪映 11.7 已实测会打开并加密保存本脚本产出的明文工程；换机仍需按"
                    " 剪映打开验证.md 人工确认一次")
    warnings.append("pyJianYingDraft 不生成 root_meta_info.json；若剪映首页看不到新工程，"
                    "需按 environment.md 的注册步骤处理或由剪映扫描目录发现")
    return {"ok": timeline is None or timeline["ok"],
            "mode": "dry-run" if not args.build else "build",
            "canvas": {"width": args.width, "height": args.height, "fps": args.fps},
            "draft_name": args.draft_name, "drafts_dir": str(args.drafts_dir or ""),
            "track_order_bottom_to_top": [s["track"] for s in stage],
            "main_video": main_info, "timeline": timeline,
            "segments": probes, "warnings": warnings}, warnings


def do_build(project: Path, plan: dict, items: list, args, build: dict) -> tuple:
    # 先做不依赖第三方库的快速失败校验，避免「缺库」掩盖真正的路径/覆盖问题
    drafts_dir = Path(str(args.drafts_dir)).resolve()
    if not drafts_dir.is_dir():
        return {"ok": False, "error": f"剪映草稿目录不存在：{drafts_dir}",
                "hint": "用 --drafts-dir 指定真实草稿目录；本机实测为 <DRAFTS>"}, 2
    name = str(build["draft_name"])
    target = drafts_dir / name
    if target.exists() and not args.allow_replace:
        return {"ok": False, "error": f"同名工程已存在，按红线「版本不覆盖」拒绝写入：{target}",
                "hint": "换一个 --draft-name，或显式 --allow-replace（会先删除同名目录，谨慎）"}, 2

    try:
        import pyJianYingDraft as jy
    except ImportError as exc:
        return {"ok": False, "error": "缺少 pyJianYingDraft，无法构建剪映工程",
                "install": "python -m pip install pyJianYingDraft",
                "detail": str(exc)}, 2

    try:
        folder = jy.DraftFolder(str(drafts_dir))
        script = folder.create_draft(name, args.width, args.height, fps=args.fps,
                                     allow_replace=args.allow_replace)
        sec = jy.SEC
        notes = []

        # 1) 主视频轨（可选）：底层
        if args.main_video:
            main = Path(args.main_video).resolve()
            if not main.is_file():
                return {"ok": False, "error": f"主视频不存在：{main}"}, 2
            tl = build.get("timeline") or {}
            want = float(tl.get("final_duration") or 0)
            if want <= 0:  # 没有 timeline（例如未启用 ffprobe 的降级路径）时仍给明确兜底
                dur = ffprobe_duration(main, shutil.which("ffprobe"))
                final = float(plan.get("delivery", {}).get("final_duration") or 0)
                want = min([x for x in (dur, final) if x and x > 0], default=0.0)
            if want <= 0:
                return {"ok": False,
                        "error": "无法确定主视频时长：请填 delivery.final_duration 或安装 ffprobe"}, 2
            main_ref = script.append_track(jy.TrackSpec(jy.TrackType.video, name=MAIN_TRACK))
            script.add_segment(
                jy.VideoSegment(jy.VideoMaterial(str(main), material_name=main.name),
                                jy.Timerange(0, int(round(want * sec))),
                                source_timerange=jy.Timerange(0, int(round(want * sec))),
                                volume=1.0),
                main_ref)
            notes.append(f"主视频轨 {want}s（{main.name}，片尾清理后时长）")
            if tl.get("trim_tail"):
                notes.append(f"按 plan.delivery.final_duration 裁掉片尾 {tl['trim_tail']}s")

        # 2) 插入素材轨：位于主视频之上
        insert_ref = script.append_track(jy.TrackSpec(jy.TrackType.video, name=INSERT_TRACK))
        built = []
        for i, it in enumerate(items, 1):
            src = resolve_source(project, it.get("source", ""))
            mat = jy.VideoMaterial(str(src), material_name=src.name)
            target_tr = jy.Timerange(int(round(float(it["target_in"]) * sec)),
                                     int(round(float(it["duration"]) * sec)))
            kwargs = {"volume": 0.0}
            if it.get("type") == "video":
                sin = float(it["source_in"])
                kwargs["source_timerange"] = jy.Timerange(
                    int(round(sin * sec)),
                    int(round((float(it["source_out"]) - sin) * sec)))
            if not args.no_auto_fit:
                scale = fit_scale(it.get("source_resolution"), args.width, args.height)
                if scale:
                    kwargs["clip_settings"] = jy.ClipSettings(scale_x=scale, scale_y=scale)
            script.add_segment(jy.VideoSegment(mat, target_tr, **kwargs), insert_ref)
            built.append({"index": i, "source": src.name, "type": it.get("type"),
                          "target_in": it["target_in"], "duration": it["duration"],
                          "volume": 0})
        notes.append(f"插入素材轨 {len(built)} 段（全部静音、独立轨）")

        # 3) 字幕轨（可选）：最上层，避免被插入素材盖住
        if args.srt:
            srt = Path(args.srt).resolve()
            if srt.is_file():
                script.import_srt(str(srt), SUBTITLE_TRACK)
                notes.append(f"字幕轨由 SRT 导入（{srt.name}）")
            else:
                notes.append(f"警告：SRT 不存在，已跳过字幕轨：{srt}")

        script.save()
    except Exception as exc:  # noqa: BLE001 - 构建失败必须给出明确原因而非堆栈
        return {"ok": False, "error": f"构建剪映工程失败：{type(exc).__name__}: {exc}",
                "hint": "常见原因：素材编解码无法探测、时间越界、同名工程冲突"}, 2

    content = target / "draft_content.json"
    report_dir = project / "输出" / "工程构建"
    report_dir.mkdir(parents=True, exist_ok=True)
    if content.exists():
        (report_dir / "draft_content_明文备份.json").write_bytes(content.read_bytes())

    # 回读校验：确认写进去的确实是视频素材 + 源入出点 + 静音
    audit = {"ok": False, "reason": "未生成 draft_content.json"}
    try:
        data = json.loads(content.read_text(encoding="utf-8"))
        tracks = data.get("tracks", [])
        videos = data.get("materials", {}).get("videos", [])
        segs = [s for t in tracks if t.get("name") == INSERT_TRACK for s in t.get("segments", [])]
        audit = {
            "ok": True,
            "tracks": [{"name": t.get("name"), "type": t.get("type"),
                        "segments": len(t.get("segments", []))} for t in tracks],
            "insert_segments": len(segs),
            "all_muted": all(s.get("volume") == 0 for s in segs),
            "all_have_source_timerange": all(bool(s.get("source_timerange")) for s in segs),
            "materials": [{"type": v.get("type"), "name": v.get("material_name")}
                          for v in videos],
            "note": "明文回读仅证明工程结构正确；剪映能否打开、是否可编辑仍需人工确认",
        }
    except (OSError, ValueError) as exc:
        audit = {"ok": False, "reason": str(exc)}

    return {"ok": True, "mode": "build", "draft_dir": str(target),
            "draft_content": str(content), "notes": notes, "audit": audit,
            "files": ["输出/工程构建/构建计划.json", "输出/工程构建/构建报告.json",
                      "输出/工程构建/draft_content_明文备份.json",
                      "输出/工程构建/剪映打开验证.md"]}, 0


def write_verify_md(path: Path, build: dict, report: dict) -> None:
    draft = report.get("draft_dir") or "<尚未构建>"
    lines = [
        "# 剪映工程打开验证（人工，必做）", "",
        "> 本脚本只保证「明文工程结构正确」，不保证剪映 11.7 能打开。",
        "> 现存工程是加密 `draft_content.json`，本脚本产出的是明文工程，两者兼容性需实测一次。", "",
        "## 工程位置", "", f"- `{draft}`", "",
        "## 验证步骤", "",
        "1. 完全退出剪映（运行中热改草稿索引会被覆盖）；",
        "2. 重新打开剪映，看首页是否出现该工程；看不到 → 检查 `root_meta_info.json` 注册或让剪映扫描草稿目录；",
        "3. 打开工程，确认时间线上有独立轨道：",
        "   " + " → ".join(build.get("track_order_bottom_to_top", []) or ["插入素材"]),
        "4. 逐个拖动播放插入点，确认**画面会动**（不是静帧）、已静音、没有盖住字幕与顶标题；",
        "5. 确认素材路径没有红色缺失提示；缩放铺满、无黑边、没有压住医生脸；",
        "6. 若剪映保存后把明文工程重新加密，属正常；回读验收按 environment.md 的双路径方案处理。", "",
        "## 若打不开", "",
        "- 先用剪映自带「导入草稿」或复制目录到草稿根路径重试；",
        "- 仍失败：说明 11.7 只接受加密工程，需要阶段 0 的「回读/写入双路径」方案，",
        "  即改由 `jy_tools.py` 适配层在加密前写入，或改用剪映界面手动导入 SRT/素材。",
        "- **不要**用本脚本去改写任何已有工程目录。", "",
        "## 本次构建警告", "",
    ]
    for w in build.get("warnings", []):
        lines.append(f"- {w}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="把插入点写成剪映草稿工程（阶段 2 适配层）")
    ap.add_argument("--project", required=True, help="项目目录（含 输出/剪辑任务单.json）")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只出构建计划（默认）")
    mode.add_argument("--build", action="store_true", help="真正写入剪映草稿目录")
    ap.add_argument("--drafts-dir", help="剪映草稿根目录；默认读 plan.json 的 delivery.draft_dir")
    ap.add_argument("--draft-name", help="新工程名；默认读 plan.json 的 delivery.draft_name")
    ap.add_argument("--main-video", help="可选：主视频（口播原片）路径，用于建底层视频轨")
    ap.add_argument("--srt", help="可选：字幕 SRT，用于建最上层字幕轨")
    ap.add_argument("--width", type=int, default=DEFAULT_W)
    ap.add_argument("--height", type=int, default=DEFAULT_H)
    ap.add_argument("--fps", type=int, default=DEFAULT_FPS)
    ap.add_argument("--no-auto-fit", action="store_true", help="不按源分辨率计算 cover 缩放")
    ap.add_argument("--no-motion-check", action="store_true", help="跳过相邻帧动态性校验")
    ap.add_argument("--allow-replace", action="store_true",
                    help="允许覆盖同名工程（默认禁止，红线：版本不覆盖）")
    args = ap.parse_args()
    args.motion_check = not args.no_motion_check

    project = Path(args.project).resolve()
    plan_path = project / "输出" / "剪辑任务单.json"
    try:
        plan = read_json(plan_path)
    except ValueError as exc:
        return emit({"ok": False, "error": str(exc)}, 2)
    if not isinstance(plan, dict):
        return emit({"ok": False, "error": f"缺任务单：{plan_path}"}, 2)

    # 复用阶段 1 门禁：授权/时长/静音/源余量/句边界/零重叠/不重复，任一不过即停
    subs = read_json(project / "输出" / "字幕" / "字幕.json", {}) or {}
    events = subs.get("events", []) or []
    # 门禁串联：字幕质检未通过时，其时间轴不可用于插入点选段与工程构建。
    # 真实项目实测：逐字稿 91.19% 匹配率会触发 needs_review，此前会被静默放行。
    sub_qc = read_json(project / "输出" / "字幕" / "字幕_质检.json", {}) or {}
    if not sub_qc.get("pass", False):
        return emit({"ok": False, "stage": "subtitle_qc",
                     "error": "字幕质检未通过（needs_review / FAIL），禁止用未校准时间轴生成插入点与工程",
                     "hint": "先跑 build_subtitles.py 修正逐字稿↔ASR 差异；确需放行须人工逐条核对后"
                             "用 --allow-interpolation N 重生成字幕"}, 2)
    gate = verify(project, plan, events, 3)
    if not gate["ok"]:
        return emit({"ok": False, "stage": "verify",
                     "error": "插入点质检未通过，禁止构建工程（先跑 select_inserts.py --verify）",
                     "failed_checks": gate["failed_checks"]}, 2)
    items = plan.get("assets", {}).get("insert_points", []) or []
    if not items:
        return emit({"ok": False, "error": "任务单里没有 insert_points，先跑 select_inserts.py --scan/--apply"}, 2)

    delivery = plan.get("delivery", {}) or {}
    args.drafts_dir = args.drafts_dir or delivery.get("draft_dir") or ""
    args.draft_name = args.draft_name or delivery.get("draft_name") \
        or f"{project.name}-插入片段"
    # 主视频可由任务单驱动（delivery.main_video），CLI 优先
    args.main_video = args.main_video or delivery.get("main_video") or None
    if not args.srt:
        auto_srt = project / "输出" / "字幕" / "字幕.srt"
        args.srt = str(auto_srt) if args.main_video and auto_srt.exists() else None

    out_dir = project / "输出" / "工程构建"
    out_dir.mkdir(parents=True, exist_ok=True)
    build, warnings = build_plan(project, plan, items, args, events)
    if not build.get("ok"):
        (out_dir / "构建计划.json").write_text(
            json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")
        return emit({"ok": False, "stage": build.get("stage", "timeline"),
                     "error": build.get("error") or "原片与字幕/插入点时间轴不一致，禁止构建工程",
                     "timeline": build.get("timeline"),
                     "hint": "核对 delivery.final_duration 与片尾清理/节奏压缩后的时间轴；"
                             "计划已写入 输出/工程构建/构建计划.json"}, 2)
    build["gate"] = {"ok": True, "count": gate["count"], "checks": gate["checks"]}
    build["sentence_boundaries"] = len(boundary_times(events))
    (out_dir / "构建计划.json").write_text(
        json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.build and not args.drafts_dir:
        return emit({"ok": False, "error": "--build 需要 --drafts-dir（或任务单 delivery.draft_dir）",
                     "hint": "本机实测草稿目录：<DRAFTS>"}, 2)

    report, code = (do_build(project, plan, items, args, build) if args.build else
                    ({"ok": True, "mode": "dry-run", "draft_dir": None,
                      "notes": ["仅生成构建计划，未写任何工程"], "audit": None,
                      "files": ["输出/工程构建/构建计划.json", "输出/工程构建/剪映打开验证.md"]}, 0))
    build["report"] = report
    (out_dir / "构建计划.json").write_text(
        json.dumps(build, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "构建报告.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_verify_md(out_dir / "剪映打开验证.md", build, report)

    if not report.get("ok"):
        # do_build 的错误返回里没有 mode 字段，不能用 report["mode"]（否则会崩而不是返回退出码 2）
        summary = {"ok": False, "mode": "build" if args.build else "dry-run", **{
            k: v for k, v in report.items()
            if k in ("error", "install", "detail", "hint")}}
    else:
        summary = {"ok": True, "mode": report["mode"],
                   "draft_dir": report.get("draft_dir"),
                   "segments": len(items), "audit": report.get("audit"),
                   "warnings": warnings,
                   "files": report.get("files")}
    return emit(summary, code)


if __name__ == "__main__":
    raise SystemExit(main())
