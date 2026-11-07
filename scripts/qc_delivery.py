#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 剪辑交付验收：工程 / 成片 / 封面 / 字幕 / 命名 / 版本 / 输入结构。

用法:
  python <SKILL_DIR>/scripts/qc_delivery.py --project <项目目录> --drafts <剪映草稿目录>
  python <SKILL_DIR>/scripts/qc_delivery.py --project <项目目录> --no-strict  # 仅排查

输出：输出/验收报告.md、输出/验收结果.json，并在 stdout 打印 JSON 摘要。
纯 Python 标准库；剪映加密工程的可打开性与轨道可编辑性仍需人工在剪映打开确认。

v1.1（P0 修复）:
  - 存在 FAIL 时默认返回退出码 1（--no-strict 才放开），--strict 保留为兼容 no-op
  - 同时识别 draft_info.json / draft_content.json（剪映 11.7 实际用后者）
  - 字幕验收纳入逐字一致、末句覆盖片尾、ASR 时间轴覆盖、对齐插值复核
  - plan.json 的 deferred_items 显式登记样板待补项；批量已开始仍未清零 -> FAIL
"""
import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

INPUT_FOLDERS = ["封面模板", "工程文件", "数字人原片", "素材", "文案"]
DRAFT_REQUIRED = [["draft_info.json", "draft_content.json"], "draft_meta_info.json", "draft_cover.jpg"]
DRAFT_CONTENT_FILES = ["draft_info.json", "draft_content.json", "draft.json"]
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.*医生[—–-]{1,2}.+")
VERSION_RE = re.compile(r"-(一改|二改|三改|样板v\d+|可编辑工程)")


def add(checks, item, status, detail):
    checks.append({"item": item, "status": status, "detail": detail})


def find_one(folder: Path, patterns) -> list:
    if not folder.is_dir():
        return []
    return sorted({p for pat in patterns for p in folder.glob(pat) if p.is_file()})


def check_inputs(project: Path, checks) -> None:
    for name in INPUT_FOLDERS:
        p = project / name
        if not p.is_dir():
            add(checks, f"输入目录 {name}", "FAIL", "目录缺失")
        else:
            n = sum(1 for f in p.rglob("*") if f.is_file())
            add(checks, f"输入目录 {name}", "PASS" if n else "WARN", f"{n} 个文件")


def check_subtitles(out: Path, checks) -> None:
    subs = find_one(out, ["*.ass", "*.srt", "字幕/*.ass", "字幕/*.srt"])
    add(checks, "字幕文件", "PASS" if subs else "FAIL",
        ", ".join(p.name for p in subs) if subs else "缺少 .ass/.srt")
    qc_files = find_one(out, ["*质检*.json", "*字幕*qc*.json", "字幕/*质检*.json", "字幕/*qc*.json"])
    if not qc_files:
        add(checks, "字幕质检结果", "FAIL", "缺少字幕质检 JSON（先运行 build_subtitles.py）")
        return
    try:
        q = json.loads(qc_files[0].read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        add(checks, "字幕质检结果", "FAIL", f"无法解析: {exc}")
        return
    ok = bool(q.get("pass"))
    add(checks, "字幕质检结果", "PASS" if ok else "FAIL",
        f"{q.get('count', '?')} 条，最长 {q.get('max_chars', '?')} 字，"
        f"超长 {len(q.get('over_limit', []))}，标点 {len(q.get('punctuation', []))}，"
        f"重叠 {len(q.get('overlap', []))}")
    timeline = q.get("timeline", {})
    add(checks, "音画对齐", "PASS" if timeline.get("aligned") else "FAIL",
        f"时间轴来源 {timeline.get('source', '未知')}")

    # v1.1：字幕脚本新增门禁在交付验收里逐项落地
    if "text_verbatim" in q:
        add(checks, "字幕文本逐字一致", "PASS" if q["text_verbatim"] else "FAIL",
            "逐字来自逐字稿" if q["text_verbatim"] else "字幕文本与逐字稿不一致或有改写")
    if "tail_reaches_end" in q:
        add(checks, "末句覆盖片尾", "PASS" if q["tail_reaches_end"] else "FAIL",
            f"最终成片 {q.get('final_duration', '?')}s / 末句延长 {q.get('tail_pad_seconds', '?')}s")
    sub_checks = q.get("checks", {})
    if "time_coverage" in sub_checks:
        cov = q.get("coverage", {})
        add(checks, "ASR 时间轴覆盖", "PASS" if sub_checks["time_coverage"] else "FAIL",
            f"head_gap={cov.get('head_gap')}s tail_gap={cov.get('tail_gap')}s"
            "（长音频须分块转写，25s 窗 / 20s 步长）")
    align = q.get("alignment", {})
    if align.get("interpolated"):
        add(checks, "对齐差异复核", "WARN",
            f"{align['interpolated']} 字为插值时间轴，status={align.get('status')}；"
            "见质检 JSON 的 alignment.differences")
    if q.get("single_char"):
        add(checks, "逐字闪字幕", "WARN", f"存在 {len(q['single_char'])} 条单字字幕，需人工确认")
    if q.get("warnings"):
        add(checks, "字幕警告", "WARN", "；".join(q["warnings"]))


def check_delivery(project: Path, out: Path, checks) -> list:
    mp4_all = find_one(out, ["*.mp4", "成片/*.mp4"])
    covers = find_one(out, ["*.jpg", "*.png", "*.jpeg", "封面/*.jpg", "封面/*.png", "封面/*.jpeg"])
    plans = find_one(out, ["剪辑任务单.json", "plan.json", "*任务单*.json"])
    add(checks, "成片 MP4", "PASS" if mp4_all else "FAIL",
        f"{len(mp4_all)} 个" + ("：" + ", ".join(p.name for p in mp4_all[:3]) if mp4_all else ""))
    add(checks, "封面", "PASS" if covers else "FAIL",
        f"{len(covers)} 个" + ("（尺寸需人工/脚本另行核对）" if covers else ""))
    add(checks, "剪辑任务单 plan.json", "PASS" if plans else "FAIL",
        plans[0].name if plans else "缺少任务单")

    for mp4 in mp4_all:
        stem = mp4.stem
        if NAME_RE.match(stem) or ("医生" in stem and "——" in stem):
            add(checks, f"成片命名 {stem[:28]}", "PASS", "符合 日期-医生——主题")
        else:
            add(checks, f"成片命名 {stem[:28]}", "WARN", "不符合 日期-XXX医生——主题（主题≤6字）")

    versions = {m.group(1) for p in mp4_all for m in [VERSION_RE.search(p.stem)] if m}
    if "三改" in versions and not ({"一改", "二改"} & versions):
        add(checks, "历史版本保留", "WARN", "存在 -三改 但未发现 -一改/-二改，请确认旧版是否保留")
    else:
        add(checks, "历史版本保留", "PASS", "、".join(sorted(versions)) if versions else "无改版后缀（首版）")

    # v1.1：样板待补项显式登记，不再用 WARN 把半成品糊成"合格"
    acceptance = {}
    for p in plans:
        try:
            acceptance = json.loads(p.read_text(encoding="utf-8")).get("acceptance", {}) or {}
        except (ValueError, OSError):
            pass
        if acceptance:
            break
    deferred = acceptance.get("deferred_items", []) or []
    batch_started = bool(acceptance.get("batch_started"))
    if deferred:
        add(checks, "样板待补项", "FAIL" if batch_started else "WARN",
            ("批量已开始但待补项未清零：" if batch_started else "样板阶段待补（批量前必须清零）：")
            + "、".join(str(x) for x in deferred[:8]))
    if batch_started and not acceptance.get("sample_approved"):
        add(checks, "样板验收", "FAIL", "batch_started=true 但 sample_approved=false")

    notes = find_one(out, ["*剪辑说明*", "*说明*.md", "说明/*.md"])
    add(checks, "剪辑说明", "PASS" if notes else "WARN", notes[0].name if notes else "建议补充剪辑说明")
    return mp4_all


def check_drafts(drafts: Path, mp4_all: list, out: Path, checks) -> None:
    if not drafts.is_dir():
        add(checks, "剪映草稿目录", "FAIL", f"目录不存在: {drafts}")
        return
    names = [p for p in drafts.iterdir() if p.is_dir()]
    add(checks, "剪映草稿目录", "PASS", f"{len(names)} 个草稿目录：{drafts}")

    plan_name = ""
    for p in find_one(out, ["剪辑任务单.json", "plan.json"]):
        try:
            plan_name = json.loads(p.read_text(encoding="utf-8")).get("delivery", {}).get("draft_name", "")
        except (ValueError, OSError):
            pass
        if plan_name:
            break

    targets = []
    for mp4 in mp4_all:
        key = plan_name or VERSION_RE.sub("", mp4.stem).strip("-")
        matched = [d for d in names if key and (key in d.name or d.name in key)]
        if matched:
            targets.append((mp4.name, matched[0]))
    if not targets and plan_name:
        matched = [d for d in names if plan_name in d.name]
        if matched:
            targets.append((plan_name, matched[0]))

    if not targets:
        add(checks, "工程与成片一一对应", "FAIL", "未找到与成片名称匹配的独立剪映工程目录")
        return
    add(checks, "工程与成片一一对应", "PASS", f"匹配 {len(targets)}/{len(mp4_all)} 条")

    for _, d in targets[:3]:
        for req in DRAFT_REQUIRED:
            candidates = req if isinstance(req, list) else [req]
            hit = [c for c in candidates if (d / c).exists()]
            add(checks, f"工程文件 {d.name[:20]}/{candidates[0]}", "PASS" if hit else "FAIL",
                hit[0] if hit else "缺失（或为加密草稿需 read 回读确认）")
        # 剪映 11.7 实际使用 draft_content.json；只读 draft_info.json 会静默跳过轨道核对
        info = next((d / n for n in DRAFT_CONTENT_FILES if (d / n).exists()), None)
        if info:
            try:
                data = json.loads(info.read_text(encoding="utf-8"))
                n = len(data.get("tracks", []) or [])
                add(checks, f"工程轨道 {d.name[:20]}", "PASS" if n >= 3 else "WARN",
                    f"{n} 条轨道（读取 {info.name}）")
            except (ValueError, UnicodeDecodeError):
                add(checks, f"工程回读 {d.name[:20]}", "WARN",
                    "工程为加密/非明文格式：用加密前留存的明文备份核对结构，"
                    "并确认剪映实际可打开（勿依赖工具解密回读）")
    add(checks, "工程可编辑性", "NA", "需人工在剪映打开确认轨道独立、标题字幕未烧死、素材无红色缺失")
    add(checks, "公共盘只读", "NA", "需人工确认 D 盘公共素材库未被修改")


def to_markdown(checks: list, project: Path, strict: bool) -> str:
    lines = ["# AI 剪辑交付验收报告", "", f"- 项目：`{project}`", "",
             "| 检查项 | 结果 | 说明 |", "|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['item']} | {c['status']} | {str(c['detail']).replace('|', '/')} |")
    fails = [c for c in checks if c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    lines += ["", f"- FAIL：{len(fails)} 项；WARN：{len(warns)} 项",
              "- 结论：" + ("**不合格，需修复后重新验收**" if fails else "**通过**（WARN/NA 项需人工确认）"),
              "- 退出码：" + ("**1（严格模式，FAIL 即阻断）**" if fails and strict
                             else ("0（FAIL 已用 --no-strict 放行，仅供排查）" if fails else "0")),
              "", "> 生成脚本：qc_delivery.py；规范依据：references/company-standard.md", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 剪辑交付验收（v1.1 起默认严格）")
    ap.add_argument("--project", required=True, help="项目目录")
    ap.add_argument("--drafts", default="", help="剪映草稿目录（可选，强烈建议提供）")
    ap.add_argument("--strict", action="store_true",
                    help="兼容旧用法：v1.1 起默认即严格，此参数保留为 no-op")
    ap.add_argument("--no-strict", action="store_true",
                    help="仅排查时放宽：存在 FAIL 仍返回 0（禁止用于正式交付）")
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()
    out = project / "输出"
    checks = []
    check_inputs(project, checks)
    check_subtitles(out, checks)
    mp4_all = check_delivery(project, out, checks)
    if args.drafts:
        check_drafts(Path(args.drafts).expanduser().resolve(), mp4_all, out, checks)
    else:
        add(checks, "剪映工程验收", "FAIL", "未提供 --drafts；工程文件是硬性交付物，必须验收")

    report = {
        "project": str(project),
        "fail": sum(1 for c in checks if c["status"] == "FAIL"),
        "warn": sum(1 for c in checks if c["status"] == "WARN"),
        "pass": sum(1 for c in checks if c["status"] == "PASS"),
        "checks": checks,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "验收结果.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    strict = not args.no_strict
    (out / "验收报告.md").write_text(to_markdown(checks, project, strict), encoding="utf-8")

    summary = {"ok": report["fail"] == 0, "fail": report["fail"], "warn": report["warn"],
               "pass": report["pass"], "strict": strict,
               "report": str(out / "验收报告.md"),
               "json": str(out / "验收结果.json"),
               "fail_items": [c["item"] for c in checks if c["status"] == "FAIL"]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if (report["fail"] and strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
