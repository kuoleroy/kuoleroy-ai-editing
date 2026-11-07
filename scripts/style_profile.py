#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从剪映草稿 JSON（明文）提取风格参数卡。

用法:
  python <SKILL_DIR>/scripts/style_profile.py --draft <draft_content.json 或草稿目录> --out 风格卡.json

注意：剪映 11.x 草稿主文件是加密的。先用本机 `jy_tools.py read` 导出明文 JSON，
再交给本脚本；本脚本不负责解密，也不依赖第三方库。
"""
import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DRAFT_FILES = ["draft_info.json", "draft_content.json", "draft.json"]


def find_json(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        for name in DRAFT_FILES:
            p = path / name
            if p.exists():
                return p
    raise FileNotFoundError("找不到明文草稿 JSON；加密草稿请先用 jy_tools.py read 导出")


def extract_text(content) -> str:
    if isinstance(content, str):
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                return str(obj.get("text", ""))
        except (ValueError, TypeError):
            return content
    if isinstance(content, dict):
        return str(content.get("text", ""))
    return ""


def build_profile(data: dict, source: Path) -> dict:
    duration_us = data.get("duration", 0) or 0
    duration = duration_us / 1e6 if duration_us > 100000 else float(duration_us)
    tracks = data.get("tracks", []) or []
    materials = data.get("materials", {}) or {}
    texts = {t.get("id"): t for t in materials.get("texts", []) if isinstance(t, dict)}

    track_rows = []
    for t in tracks:
        segs = t.get("segments", []) or []
        ys, sizes, samples = [], [], []
        for s in segs[:8]:
            clip = s.get("clip", {}) or {}
            tr = clip.get("transform", {}) or {}
            if isinstance(tr, dict) and tr.get("y") is not None:
                ys.append(tr.get("y"))
            mat = texts.get(s.get("material_id"))
            if mat:
                if mat.get("font_size") is not None:
                    sizes.append(mat.get("font_size"))
                txt = extract_text(mat.get("content"))
                if txt and len(samples) < 3:
                    samples.append(txt[:24])
        track_rows.append({
            "name": t.get("name", ""),
            "type": t.get("type", ""),
            "segments": len(segs),
            "transform_y": ys[:3],
            "font_size": sizes[:3],
            "text_samples": samples,
        })

    video_segs = [r["segments"] for r in track_rows if r["type"] in ("video", "0") or "视频" in r["name"]]
    avg_shot = None
    if duration > 0 and video_segs and max(video_segs) > 0:
        avg_shot = round(duration / max(video_segs), 2)

    y_values = [y for r in track_rows for y in r["transform_y"]]
    size_values = [s for r in track_rows for s in r["font_size"]]

    return {
        "source": str(source),
        "canvas": data.get("canvas_config", {}),
        "duration_seconds": round(duration, 3),
        "track_count": len(track_rows),
        "video_segment_count": max(video_segs) if video_segs else 0,
        "avg_shot_seconds": avg_shot,
        "tracks": track_rows,
        "material_counts": {
            "videos": len(materials.get("videos", []) or []),
            "audios": len(materials.get("audios", []) or []),
            "texts": len(materials.get("texts", []) or []),
        },
        "observed_transform_y": sorted(set(round(float(y), 3) for y in y_values if isinstance(y, (int, float)))),
        "observed_font_size": sorted(set(round(float(s), 2) for s in size_values if isinstance(s, (int, float)))),
        "company_baseline": {
            "canvas": "1080x1920 / 30fps",
            "title_y": 0.84,
            "subtitle_y": -0.20,
            "disclaimer_y": -0.91,
            "avg_shot_seconds": "1.7-2.4",
            "bgm_volume_percent": "6-19",
            "subtitle_max_chars": 11,
        },
        "note": "不同医生分别建卡；与基线冲突时以本卡为准，并在验收报告注明偏差。",
    }


def to_markdown(p: dict) -> str:
    lines = [
        "# 剪辑风格卡", "",
        f"- 来源：`{p['source']}`",
        f"- 时长：{p['duration_seconds']} 秒；轨道数：{p['track_count']}；平均镜头：{p['avg_shot_seconds']} 秒",
        f"- 画布：{json.dumps(p['canvas'], ensure_ascii=False)}",
        f"- 素材数：{json.dumps(p['material_counts'], ensure_ascii=False)}",
        f"- 观测坐标：{p['observed_transform_y']}；观测字号：{p['observed_font_size']}", "",
        "## 轨道", "", "| 名称 | 类型 | 片段数 | 坐标样例 | 字号样例 | 文本样例 |", "|---|---|---|---|---|---|",
    ]
    for t in p["tracks"]:
        lines.append("| {name} | {type} | {segments} | {y} | {fs} | {tx} |".format(
            name=t["name"], type=t["type"], segments=t["segments"],
            y=", ".join(str(x) for x in t["transform_y"]),
            fs=", ".join(str(x) for x in t["font_size"]),
            tx=" / ".join(t["text_samples"]).replace("|", "\\|")))
    lines += ["", "> " + p["note"], ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="提取剪映明文草稿的风格参数")
    ap.add_argument("--draft", required=True, help="明文 JSON 文件或草稿目录")
    ap.add_argument("--out", required=True, help="输出风格卡 JSON 路径")
    ap.add_argument("--md", default="", help="可选：同时输出 Markdown 摘要路径")
    args = ap.parse_args()

    source = find_json(Path(args.draft).expanduser().resolve())
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "文件不是明文 JSON（剪映 11.x 加密草稿需先解密回读）",
                          "detail": str(exc)}, ensure_ascii=False))
        return 2

    profile = build_profile(data, source)
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        md = Path(args.md).expanduser().resolve()
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(to_markdown(profile), encoding="utf-8")

    print(json.dumps({
        "ok": True, "out": str(out), "md": args.md or None,
        "duration_seconds": profile["duration_seconds"],
        "track_count": profile["track_count"],
        "avg_shot_seconds": profile["avg_shot_seconds"],
        "observed_transform_y": profile["observed_transform_y"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
