#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐字稿 -> 团队规范字幕（断句 + 逐字稿/ASR 强制对齐 + 机器质检）。

用法:
  python <SKILL_DIR>/scripts/build_subtitles.py \
      --script 文案/逐字稿.txt --out 输出/字幕 \
      --words asr-words.json --duration 71.94 \
      --protect <SKILL_DIR>/assets/保护词表-医疗示例.txt

--duration 必须是「片尾清理后的最终交付成片时长」（先 probe、去尾叹气后再取），
不是原始素材容器时长；否则末句会挂空字幕。

v1.1（P0 修复）:
  - 逐字稿与 ASR 文本做字符级序列对齐（difflib），不再按字符序号硬映射时间轴
  - 禁止时间轴外推；ASR 首尾覆盖不足 / 字符匹配率过低 -> 质检 FAIL
  - --duration 必填；末句延长到最终成片结尾后 tail_pad 秒
  - 无词级时间戳 / 对齐插值 / 文本不逐字 / 单字行 / 保护词被切分 / 时间重叠 -> FAIL
  - 默认保留 ASR 首句实际起点（不再强制归零）
  - 断句只在句内合并碎片，默认不跨标点拼接

words JSON 兼容三种格式: [{"word","start","end"}, ...] / {"words":[...]} /
{"segments":[{"words":[...]}]}。纯 Python 3 标准库实现。
"""
import argparse
import difflib
import json
import math
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PUNCT = set("，。！？；：、,.!?;:\"'“”‘’（）()【】[]{}<>《》—…·~ \t\r\n")
PUNCT_RE = re.compile(r"[，。！？；：、,.!?;:\"'“”‘’（）()【】\[\]{}<>《》—…·~\t\r\n ]+")
SPLIT_RE = re.compile(r"[，。！？；：、,.!?;:\n\r]+")


def clean(text: str) -> str:
    return "".join(ch for ch in text if ch not in PUNCT)


def load_protect(paths) -> list:
    words = []
    for p in paths:
        f = Path(p).expanduser()
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            w = clean(line.strip())
            if w and not w.startswith("#"):
                words.append(w)
    return sorted(set(words), key=len, reverse=True)


def split_protect(text: str, protect: list) -> list:
    """切成 [(文本, 是否保护词)]，保护词作为不可拆分单元。"""
    if not protect:
        return [(text, False)]
    pattern = "(" + "|".join(re.escape(w) for w in protect) + ")"
    parts = re.split(pattern, text)
    protect_set = set(protect)
    return [(part, part in protect_set) for part in parts if part]


def balanced_split(text: str, max_chars: int) -> list:
    """把超长句均衡切成若干段，避免一字一条。"""
    n = math.ceil(len(text) / max_chars)
    if n <= 1:
        return [text]
    base, rem = divmod(len(text), n)
    out, pos = [], 0
    for i in range(n):
        size = base + (1 if i < rem else 0)
        out.append(text[pos:pos + size])
        pos += size
    return [x for x in out if x]


def merge_fragments(parts: list, max_chars: int) -> list:
    """句内把保护词切分产生的 1-2 字碎片并入相邻片段。

    只在单句内调用，绝不跨标点拼接（历史事故：『对，是，嗯』被拼成『对是嗯』）。
    """
    out, pending = [], ""
    for text, is_protect in parts:
        text = pending + text
        pending = ""
        if not is_protect and len(text) <= 2:
            pending = text
            continue
        out.append((text, is_protect))
    if pending:
        if out and len(out[-1][0]) + len(pending) <= max_chars:
            out[-1] = (out[-1][0] + pending, out[-1][1])
        else:
            out.append((pending, False))
    return out


def build_lines(raw: str, protect: list, max_chars: int, merge: bool = False) -> list:
    """先按标点分句，再在句内处理保护词与碎片；默认一行一个语义片段、不跨句拼接。

    --merge 时才允许跨标点合并相邻短片段（需人工复检语义）。
    """
    sentences = [s for s in (clean(seg) for seg in SPLIT_RE.split(raw)) if s]
    lines = []
    for sent in sentences:
        parts = merge_fragments(split_protect(sent, protect), max_chars)
        for text, _is_protect in parts:
            if len(text) > max_chars:
                lines.extend(balanced_split(text, max_chars))
            else:
                lines.append(text)

    if merge:
        merged, buf = [], ""
        for text in lines:
            if buf and len(buf) + len(text) > max_chars:
                merged.append(buf)
                buf = text
            else:
                buf += text
        if buf:
            merged.append(buf)
        lines = merged
    return lines


def load_word_timeline(path) -> list:
    """返回 [(ASR 字符, start, end)]；保留 ASR 原字用于与逐字稿做序列对齐。

    兼容 whisper / faster-whisper 常见输出；长音频须先分块转写再合并，
    否则时间戳只覆盖前半段（本项目实测：25s 窗 / 20s 步长）。
    """
    if not path:
        return []
    f = Path(path).expanduser()
    data = json.loads(f.read_text(encoding="utf-8"))
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if isinstance(data.get("words"), list):
            items = data["words"]
        elif isinstance(data.get("segments"), list):
            for seg in data["segments"]:
                if isinstance(seg, dict) and isinstance(seg.get("words"), list):
                    items.extend(seg["words"])
                elif isinstance(seg, dict) and "start" in seg:
                    items.append({"word": seg.get("text", ""), "start": seg["start"], "end": seg["end"]})
    timeline = []
    for w in items:
        if not isinstance(w, dict):
            continue
        text = clean(str(w.get("word", w.get("text", ""))))
        if not text:
            continue
        try:
            start = float(w.get("start", 0))
            end = float(w.get("end", start))
        except (TypeError, ValueError):
            continue
        if end < start:
            end = start
        step = (end - start) / len(text) if end > start else 0.0
        for i, ch in enumerate(text):
            timeline.append((ch, start + i * step,
                             start + (i + 1) * step if step else end))
    return timeline


def align_to_script(script_text: str, timeline: list) -> tuple:
    """逐字稿 ← ASR 字符级强制对齐，返回 (逐字稿每字的时间, 对齐统计)。

    - equal            : 直接沿用 ASR 时间
    - replace 等长     : 医学术语/同音字校正，沿用 ASR 时间，记 corrected（不阻断）
    - replace 不等长   : 用 ASR 块总时长均分，记 interpolated（需人工确认）
    - insert           : 稿有 ASR 无，前后锚点插值，记 interpolated（需人工确认）
    - delete           : ASR 多识别的口头语，记 asr_only（不阻断）
    """
    asr_text = "".join(ch for ch, _, _ in timeline)
    stats = {
        "asr_chars": len(asr_text),
        "script_chars": len(script_text),
        "matched": 0,
        "corrected": 0,
        "interpolated": 0,
        "asr_only": 0,
        "differences": [],
    }
    aligned = [None] * len(script_text)

    def note(kind, a_text, s_text, at):
        if len(stats["differences"]) < 12:
            stats["differences"].append({
                "type": kind,
                "asr": a_text[:24],
                "script": s_text[:24],
                "at_second": round(at, 3) if at is not None else None,
            })

    matcher = difflib.SequenceMatcher(None, asr_text, script_text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        a_len, s_len = i2 - i1, j2 - j1
        a_start = timeline[i1][1] if i1 < len(timeline) else (timeline[-1][2] if timeline else 0.0)
        a_end = timeline[i2 - 1][2] if i2 > i1 and i2 - 1 < len(timeline) else a_start

        if tag == "equal":
            for k in range(s_len):
                aligned[j1 + k] = (timeline[i1 + k][1], timeline[i1 + k][2])
            stats["matched"] += s_len

        elif tag == "delete":
            stats["asr_only"] += a_len
            note("asr_only", asr_text[i1:i2], "", a_start)

        elif tag == "replace":
            if s_len == a_len:
                for k in range(s_len):
                    aligned[j1 + k] = (timeline[i1 + k][1], timeline[i1 + k][2])
                stats["corrected"] += s_len
                note("corrected", asr_text[i1:i2], script_text[j1:j2], a_start)
            elif a_len > 0:
                step = max(a_end - a_start, 0.0) / s_len
                for k in range(s_len):
                    aligned[j1 + k] = (a_start + k * step, a_start + (k + 1) * step)
                stats["interpolated"] += s_len
                note("replace_length_mismatch", asr_text[i1:i2], script_text[j1:j2], a_start)

        elif tag == "insert":
            before = timeline[i1 - 1][2] if i1 > 0 else (timeline[0][1] if timeline else 0.0)
            after = timeline[i1][1] if i1 < len(timeline) else before + 0.08 * s_len
            if after < before:
                after = before + 0.08 * s_len
            step = (after - before) / s_len
            for k in range(s_len):
                aligned[j1 + k] = (before + k * step, before + (k + 1) * step)
            stats["interpolated"] += s_len
            note("script_only", "", script_text[j1:j2], before)

    # 禁止外推：所有稿字符都必须拿到时间；单调化，避免对齐产生倒挂
    last_start = timeline[0][1] if timeline else 0.0
    for idx, slot in enumerate(aligned):
        if slot is None:
            slot = (last_start, last_start + 0.05)
        s, e = float(slot[0]), float(slot[1])
        if s < last_start:
            s = last_start
        if e < s:
            e = s
        aligned[idx] = (s, e)
        last_start = s

    stats["char_match_rate"] = round(
        (stats["matched"] + stats["corrected"]) / max(1, len(script_text)), 4)
    stats["asr_start"] = round(timeline[0][1], 3) if timeline else None
    stats["asr_end"] = round(timeline[-1][2], 3) if timeline else None
    if stats["interpolated"]:
        stats["status"] = "needs_review"
    elif stats["corrected"] or stats["asr_only"]:
        stats["status"] = "aligned_with_corrections"
    else:
        stats["status"] = "exact"
    return aligned, stats


def build_events(lines: list, timeline: list, duration: float, tail_pad: float,
                 zero_first: bool = False) -> tuple:
    """按对齐后的逐字时间生成字幕事件；时间轴不足时直接报错，绝不外推。"""
    script_text = "".join(lines)
    if timeline:
        aligned, stats = align_to_script(script_text, timeline)
        meta = {
            "aligned": True,
            "source": "asr_word_level_force_aligned",
            "status": stats["status"],
            "alignment": stats,
            "coverage": {
                "final_duration": duration,
                "asr_start": stats["asr_start"],
                "asr_end": stats["asr_end"],
                "head_gap": stats["asr_start"],
                "tail_gap": round(duration - stats["asr_end"], 3) if stats["asr_end"] is not None else None,
            },
        }
    else:
        aligned = []
        meta = {
            "aligned": False,
            "source": "estimated_char_count_placeholder",
            "status": "asr_missing",
            "alignment": {"interpolated": 0, "char_match_rate": 0.0, "differences": [],
                          "asr_start": None, "asr_end": None},
            "coverage": {"final_duration": duration, "asr_start": None, "asr_end": None,
                         "head_gap": None, "tail_gap": None},
        }

    events = []
    if aligned:
        if len(aligned) != len(script_text):
            raise ValueError("对齐后时间轴长度与逐字稿不一致，禁止外推生成假时间戳")
        pos = 0
        for line in lines:
            if pos + len(line) > len(aligned):
                raise ValueError("ASR 时间轴不足（长音频未分块转写？），禁止外推")
            a = aligned[pos][0]
            b = aligned[pos + len(line) - 1][1]
            events.append({"text": line, "start": round(a, 3), "end": round(max(b, a + 0.05), 3)})
            pos += len(line)
        if pos != len(aligned):
            raise ValueError("逐字稿与对齐时间轴长度不一致，禁止外推")
    else:
        # 仅用于排查文本断句；qc 必判 FAIL，禁止交付
        per = duration / max(1, len(script_text))
        pos = 0
        for line in lines:
            a, b = pos * per, (pos + len(line)) * per
            events.append({"text": line, "start": round(a, 3), "end": round(b, 3)})
            pos += len(line)

    if zero_first and events:
        events[0]["start"] = 0.0

    for i in range(len(events) - 1):
        if events[i]["end"] > events[i + 1]["start"]:
            events[i]["end"] = events[i + 1]["start"]
    if events:
        events[-1]["end"] = round(max(events[-1]["end"] + tail_pad, duration + tail_pad), 3)
    for i, e in enumerate(events, 1):
        e["index"] = i
        e["chars"] = len(e["text"])
    return events, meta


def qc(events: list, max_chars: int, tail_pad: float, raw: str, meta: dict,
       duration: float, protect: list, allow_interpolation: int = 0,
       max_head_gap: float = 2.0, max_tail_gap: float = 2.0,
       min_match_rate: float = 0.85) -> dict:
    over = [{"index": e["index"], "text": e["text"], "chars": e["chars"]}
            for e in events if e["chars"] > max_chars]
    punct = [{"index": e["index"], "text": e["text"]}
             for e in events if PUNCT_RE.search(e["text"])]
    overlap = [{"index": events[i]["index"], "text": events[i]["text"]}
               for i in range(len(events) - 1) if events[i]["end"] > events[i + 1]["start"] + 0.001]
    single = [{"index": e["index"], "text": e["text"]} for e in events if e["chars"] == 1]

    joined = "".join(e["text"] for e in events)
    text_exact = joined == clean(raw)
    protect_split = [w for w in protect
                     if w in joined and not any(w in e["text"] for e in events)]
    tail_ok = bool(events) and duration > 0 and events[-1]["end"] >= duration + tail_pad - 0.01

    alignment = meta.get("alignment", {})
    coverage = meta.get("coverage", {})
    interp = int(alignment.get("interpolated", 0))
    char_rate = float(alignment.get("char_match_rate", 0.0))
    head_gap = coverage.get("head_gap")
    tail_gap = coverage.get("tail_gap")
    coverage_ok = bool(meta.get("aligned")) and head_gap is not None and tail_gap is not None \
        and head_gap <= max_head_gap and tail_gap <= max_tail_gap
    interpolation_ok = interp <= allow_interpolation
    match_rate_ok = char_rate >= min_match_rate

    checks = {
        "asr_present": bool(meta.get("aligned")),
        "time_coverage": coverage_ok,
        "char_match_rate": match_rate_ok,
        "no_interpolation": interpolation_ok,
        "text_verbatim": text_exact,
        "no_single_char": not single,
        "protect_words_intact": not protect_split,
        "no_over_limit": not over,
        "no_punctuation": not punct,
        "no_overlap": not overlap,
        "tail_reaches_end": tail_ok,
    }
    result = {
        "count": len(events),
        "max_chars": max((e["chars"] for e in events), default=0),
        "limit": max_chars,
        "over_limit": over,
        "punctuation": punct,
        "overlap": overlap,
        "single_char": single,
        "protect_words_split": protect_split,
        "text_verbatim": text_exact,
        "tail_reaches_end": tail_ok,
        "tail_pad_seconds": tail_pad,
        "final_duration": duration,
        "checks": checks,
        "pass": all(checks.values()),
        "warnings": [],
        # 兼容 qc_delivery.py 的读取字段
        "timeline": {"aligned": bool(meta.get("aligned")), "source": meta.get("source")},
        "alignment": {
            "status": meta.get("status"),
            "interpolated": interp,
            "corrected": int(alignment.get("corrected", 0)),
            "asr_only": int(alignment.get("asr_only", 0)),
            "char_match_rate": char_rate,
            "asr_start": alignment.get("asr_start"),
            "asr_end": alignment.get("asr_end"),
            "differences": alignment.get("differences", []),
        },
        "coverage": coverage,
        "limits": {"allow_interpolation": allow_interpolation,
                   "max_head_gap": max_head_gap, "max_tail_gap": max_tail_gap,
                   "min_match_rate": min_match_rate},
    }
    if not checks["asr_present"]:
        result["warnings"].append("未提供 ASR 词级时间戳：时间轴仅为占位，音画未校准，禁止交付")
    if not checks["time_coverage"]:
        result["warnings"].append(
            "ASR 时间轴未覆盖全片（长音频需分块转写，25s 窗 / 20s 步长）；"
            f"head_gap={head_gap}s tail_gap={tail_gap}s final_duration={duration}s")
    if not checks["char_match_rate"]:
        result["warnings"].append(f"逐字稿/ASR 字符匹配率 {char_rate} 低于 {min_match_rate}，请核对 ASR 或逐字稿")
    if interp:
        result["warnings"].append(
            f"有 {interp} 个字符为插值时间轴，见 alignment.differences；"
            "确认是医学术语/口头语差异后，用 --allow-interpolation 显式放行并写入剪辑说明")
    if single:
        result["warnings"].append("存在单字字幕行：请从逐字稿删除口头语或手动合并，禁止机械一字一条")
    if protect_split:
        result["warnings"].append("保护词被切断: " + ", ".join(protect_split))
    return result


def ts_srt(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_ass(sec: float) -> str:
    cs = int(round(sec * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def write_srt(path: Path, events: list) -> None:
    out = []
    for e in events:
        out += [str(e["index"]), f"{ts_srt(e['start'])} --> {ts_srt(e['end'])}", e["text"], ""]
    path.write_text("\n".join(out), encoding="utf-8")


def write_ass(path: Path, events: list, ass_y: int) -> None:
    head = [
        "[Script Info]", "Title: AI剪辑-团队规范字幕", "ScriptType: v4.00+",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709",
        "PlayResX: 1080", "PlayResY: 1920", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Microsoft YaHei,55,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,2,10,10,10,1", "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for e in events:
        head.append(f"Dialogue: 0,{ts_ass(e['start'])},{ts_ass(e['end'])},Default,,0,0,0,,{{\\pos(540,{ass_y})\\b1}}{e['text']}")
    path.write_text("\n".join(head) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="逐字稿生成团队规范字幕 + 强制对齐 + 质检")
    ap.add_argument("--script", required=True, help="逐字稿 txt（字幕唯一文本依据）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--name", default="字幕", help="输出文件基名")
    ap.add_argument("--words", default="", help="ASR 词级时间戳 JSON（长音频须先分块转写）")
    ap.add_argument("--duration", required=True, type=float,
                    help="片尾清理后的最终交付成片时长（秒），必填；从 probe / ffprobe 取")
    ap.add_argument("--protect", action="append", default=[], help="保护词表文件，可重复指定")
    ap.add_argument("--max-chars", type=int, default=11)
    ap.add_argument("--merge", action="store_true", help="允许跨标点合并相邻短片段（默认不合并）")
    ap.add_argument("--tail-pad", type=float, default=0.6)
    ap.add_argument("--ass-y", type=int, default=1110, help="ASS 字幕纵坐标（1080x1920 下胸口约 1110）")
    ap.add_argument("--allow-interpolation", type=int, default=0,
                    help="允许插值字符数（默认 0；核对 differences 后显式放行）")
    ap.add_argument("--max-head-gap", type=float, default=2.0, help="ASR 首字允许的最大片头空隙（秒）")
    ap.add_argument("--max-tail-gap", type=float, default=2.0, help="ASR 末字允许的最大片尾空隙（秒）")
    ap.add_argument("--min-match-rate", type=float, default=0.85, help="逐字稿/ASR 字符匹配率下限")
    ap.add_argument("--zero-first", action="store_true",
                    help="显式把首句起点归零（默认保留 ASR 口播实际起点）")
    ap.add_argument("--keep-first-offset", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--allow-fail", action="store_true", help="质检不通过时也返回 0（仅供排查）")
    args = ap.parse_args()

    if args.duration <= 0:
        print(json.dumps({"ok": False, "error": "--duration 必须 > 0，且应为片尾清理后的最终成片时长"},
                         ensure_ascii=False))
        return 2

    raw = Path(args.script).expanduser().read_text(encoding="utf-8")
    protect = load_protect(args.protect)
    lines = build_lines(raw, protect, args.max_chars, args.merge)
    timeline = load_word_timeline(args.words)
    try:
        events, meta = build_events(lines, timeline, args.duration, args.tail_pad, args.zero_first)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "hint": "长音频请用 faster-whisper 分块转写（25s 窗 / 20s 步长）后合并词级时间戳"},
                         ensure_ascii=False))
        return 2

    report = qc(events, args.max_chars, args.tail_pad, raw, meta, args.duration, protect,
                args.allow_interpolation, args.max_head_gap, args.max_tail_gap,
                args.min_match_rate)
    report["merge_mode"] = args.merge
    report["protect_words"] = protect
    long_protect = [w for w in protect if len(w) > args.max_chars]
    if long_protect:
        report["warnings"].append("保护词超过单行字数上限，需人工拆分: " + ", ".join(long_protect))

    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    base = out / args.name
    (base.with_suffix(".json")).write_text(
        json.dumps({"meta": meta, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(base.with_suffix(".srt"), events)
    write_ass(base.with_suffix(".ass"), events, args.ass_y)
    qc_path = out / f"{args.name}_质检.json"
    qc_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "ok": report["pass"], "out": str(out),
        "files": [str(base.with_suffix(".json")), str(base.with_suffix(".srt")),
                  str(base.with_suffix(".ass")), str(qc_path)],
        "count": report["count"], "max_chars": report["max_chars"],
        "aligned": meta["aligned"], "timeline_source": meta["source"],
        "alignment_status": meta["status"],
        "char_match_rate": report["alignment"]["char_match_rate"],
        "interpolated": report["alignment"]["interpolated"],
        "text_verbatim": report["text_verbatim"],
        "tail_reaches_end": report["tail_reaches_end"],
        "over_limit": len(report["over_limit"]), "punctuation": len(report["punctuation"]),
        "overlap": len(report["overlap"]), "single_char": len(report["single_char"]),
        "failed_checks": [k for k, v in report["checks"].items() if not v],
        "warnings": report["warnings"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["pass"] or args.allow_fail else 2


if __name__ == "__main__":
    raise SystemExit(main())
