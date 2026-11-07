#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kuoleroy-ai-editing v1.4 回归自测（纯标准库，从任意工作目录运行）。

用法:
  python <SKILL_DIR>/scripts/selftest.py

覆盖：
  1. 正常流程 / 强制对齐 / 保护词不跨行 / 末句覆盖片尾 / skill_dir 路径解析
  2. 无 ASR 词级时间戳 -> 拦截
  3. 逐字稿与 ASR 不一致 -> 插值被拦，且文本仍逐字来自逐字稿
  4. 人工核对后 --allow-interpolation 显式放行
  5. ASR 只覆盖前半段（长音频未分块）-> 拦截，不外推假时间轴
  6. 缺 --duration -> 拦截
  7. 首句保留 ASR 实际起点（不再强制归零）
  8. 短句不跨标点拼接，单字行判 FAIL
  9. qc_delivery 默认严格退出码 / --no-strict / 旧 --strict 兼容
 10. deferred_items 在批量阶段未清零 -> FAIL
 11. 剪映 11.7 的 draft_content.json 也能读到轨道
 12. 插入素材：扫描候选 -> 未授权拦截 -> 人工确认后应用 -> 复核通过
 13. license_status 非 approved 的素材被拦截
 14. 单条时长越界 / 视频源片段余量不足被拦截
 15. 同轨区间重叠 / 素材重复使用被拦截
 16. 未静音 / 切点不在句边界 / 素材文件缺失被拦截
 17. 工程构建 dry-run：片段是视频、带源入出点、静音、且不写任何工程
 18. 插入点门禁未过 -> 禁止构建工程
 19. 同名工程绝不覆盖（红线：版本不覆盖），原工程内容不变
 20. --build 缺 --drafts-dir 明确报错
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL_DIR / "scripts"
ASSETS = SKILL_DIR / "assets"
SCRIPT_TXT = ASSETS / "示例逐字稿.txt"
WORDS_JSON = ASSETS / "示例词级时间戳.json"
PROTECT_TXT = ASSETS / "保护词表-医疗示例.txt"
PY = sys.executable
DURATION = 42.6  # 示例 ASR 末点 42.08s，片尾留 0.52s


def run(script: str, *args, cwd: Path = None):
    return subprocess.run([PY, str(SCRIPTS / script), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(cwd) if cwd else None)


def read_qc(out: Path) -> dict:
    return json.loads((out / "字幕_质检.json").read_text(encoding="utf-8"))


def read_events(out: Path) -> list:
    return json.loads((out / "字幕.json").read_text(encoding="utf-8"))["events"]


def write_words(path: Path, words: list) -> Path:
    path.write_text(json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8")
    return path


def subs_cmd(case: Path, out: Path, script: Path, words: Path, *extra) -> subprocess.CompletedProcess:
    return run("build_subtitles.py", "--script", script, "--out", out,
               "--words", words, "--duration", DURATION, *extra, cwd=case)


def case_normal(case: Path):
    case.mkdir(parents=True)
    out = case / "字幕"
    r = subs_cmd(case, out, SCRIPT_TXT, WORDS_JSON, "--protect", PROTECT_TXT)
    q = read_qc(out)
    ev = read_events(out)
    checks = [
        r.returncode == 0,
        q["pass"] is True,
        q["alignment"]["status"] == "exact",
        q["checks"]["text_verbatim"] is True,
        q["checks"]["tail_reaches_end"] is True,
        q["checks"]["time_coverage"] is True,
        q["count"] == 20,
        any("气血亏虚" in e["text"] for e in ev),
        any("下焦湿热" in e["text"] for e in ev),
    ]
    init = run("init_project.py", case / "项目", "--doctor", "示例甲", "--topic", "失眠", cwd=case)
    checks.append(init.returncode == 0 and "skill_dir" in init.stdout)
    return all(checks), f"rc={r.returncode} count={q['count']} 对齐={q['alignment']['status']}"


def case_no_asr(case: Path):
    case.mkdir(parents=True)
    out = case / "字幕"
    empty = write_words(case / "empty.json", [])
    r = subs_cmd(case, out, SCRIPT_TXT, empty)
    q = read_qc(out)
    ok = r.returncode == 2 and not q["pass"] and not q["checks"]["asr_present"]
    return ok, f"rc={r.returncode} pass={q['pass']} asr_present={q['checks']['asr_present']}"


def case_mismatch(case: Path):
    case.mkdir(parents=True)
    out = case / "字幕"
    text = SCRIPT_TXT.read_text(encoding="utf-8").replace("整夜失眠", "整夜失眠啊", 1)
    s = case / "稿.txt"
    s.write_text(text, encoding="utf-8")
    r = subs_cmd(case, out, s, WORDS_JSON)
    q = read_qc(out)
    ok = (r.returncode == 2 and not q["pass"]
          and not q["checks"]["no_interpolation"]
          and q["alignment"]["interpolated"] >= 1
          and q["text_verbatim"] is True
          and len(q["alignment"]["differences"]) > 0)
    return ok, f"rc={r.returncode} 插值={q['alignment']['interpolated']} 逐字={q['text_verbatim']}"


def case_allow(case: Path):
    case.mkdir(parents=True)
    out = case / "字幕"
    text = SCRIPT_TXT.read_text(encoding="utf-8").replace("整夜失眠", "整夜失眠啊", 1)
    s = case / "稿.txt"
    s.write_text(text, encoding="utf-8")
    r = subs_cmd(case, out, s, WORDS_JSON, "--allow-interpolation", "5")
    q = read_qc(out)
    ok = r.returncode == 0 and q["pass"] is True
    return ok, f"rc={r.returncode} 显式放行后 pass={q['pass']}"


def case_half_asr(case: Path):
    """模拟日志里『单次转写漏后半段』：只保留前 18 个词。"""
    case.mkdir(parents=True)
    out = case / "字幕"
    words = json.loads(WORDS_JSON.read_text(encoding="utf-8"))["words"][:18]
    half = write_words(case / "half.json", words)
    r = subs_cmd(case, out, SCRIPT_TXT, half)
    q = read_qc(out)
    ok = (r.returncode == 2 and not q["pass"]
          and not q["checks"]["time_coverage"]
          and q["coverage"]["tail_gap"] > 20
          and q["alignment"]["char_match_rate"] < 0.5)
    return ok, (f"rc={r.returncode} tail_gap={q['coverage']['tail_gap']} "
                f"匹配率={q['alignment']['char_match_rate']}")


def case_no_duration(case: Path):
    case.mkdir(parents=True)
    r = run("build_subtitles.py", "--script", SCRIPT_TXT, "--out", case / "字幕",
            "--words", WORDS_JSON, cwd=case)
    ok = r.returncode != 0 and "--duration" in (r.stderr + r.stdout)
    return ok, f"rc={r.returncode}（缺时长被拦截）"


def case_first_offset(case: Path):
    case.mkdir(parents=True)
    out = case / "字幕"
    s = case / "稿.txt"
    s.write_text("整夜失眠", encoding="utf-8")
    w = write_words(case / "w.json", [{"word": "整夜失眠", "start": 0.30, "end": 1.10}])
    r = run("build_subtitles.py", "--script", s, "--out", out, "--words", w,
            "--duration", 2.0, cwd=case)
    ev = read_events(out)
    ok = r.returncode == 0 and abs(ev[0]["start"] - 0.30) < 0.01
    return ok, f"rc={r.returncode} 首句 start={ev[0]['start']}（ASR 实际 0.30）"


def case_short_clause(case: Path):
    case.mkdir(parents=True)
    out = case / "字幕"
    s = case / "稿.txt"
    s.write_text("对，是，嗯，好，可以。", encoding="utf-8")
    w = write_words(case / "w.json", [{"word": "对是嗯好可以", "start": 0.0, "end": 1.5}])
    r = run("build_subtitles.py", "--script", s, "--out", out, "--words", w,
            "--duration", 2.0, cwd=case)
    ev = read_events(out)
    q = read_qc(out)
    texts = [e["text"] for e in ev]
    ok = (r.returncode == 2 and "对是嗯" not in texts
          and not q["checks"]["no_single_char"] and q["text_verbatim"] is True)
    return ok, f"rc={r.returncode} 断句={texts} 单字行拦截={not q['checks']['no_single_char']}"


def make_project(case: Path) -> Path:
    case.mkdir(parents=True, exist_ok=True)
    proj = case / "项目"
    run("init_project.py", proj, "--doctor", "示例甲", "--topic", "失眠", cwd=case)
    run("build_subtitles.py", "--script", SCRIPT_TXT, "--out", proj / "输出" / "字幕",
        "--words", WORDS_JSON, "--protect", PROTECT_TXT,
        "--duration", DURATION, cwd=case)
    return proj


def case_qc_strict(case: Path):
    proj = make_project(case)
    strict = run("qc_delivery.py", "--project", proj, cwd=case)
    legacy = run("qc_delivery.py", "--project", proj, "--strict", cwd=case)
    loose = run("qc_delivery.py", "--project", proj, "--no-strict", cwd=case)
    ok = strict.returncode == 1 and legacy.returncode == 1 and loose.returncode == 0
    return ok, f"默认={strict.returncode} 旧--strict={legacy.returncode} --no-strict={loose.returncode}"


def case_deferred(case: Path):
    proj = make_project(case)
    plan = proj / "输出" / "剪辑任务单.json"
    data = json.loads(plan.read_text(encoding="utf-8"))
    data["acceptance"]["deferred_items"] = ["插入素材 0 处", "BGM 未渲染"]
    data["acceptance"]["batch_started"] = True
    plan.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    r = run("qc_delivery.py", "--project", proj, cwd=case)
    result = json.loads((proj / "输出" / "验收结果.json").read_text(encoding="utf-8"))
    items = [c for c in result["checks"] if c["item"] == "样板待补项"]
    ok = r.returncode == 1 and items and items[0]["status"] == "FAIL"
    return ok, f"rc={r.returncode} 待补项={items[0]['status'] if items else '缺失'}"


def case_draft_content(case: Path):
    """剪映 11.7 用 draft_content.json：轨道核对必须仍然生效。"""
    proj = make_project(case)
    plan = json.loads((proj / "输出" / "剪辑任务单.json").read_text(encoding="utf-8"))
    name = plan["delivery"]["draft_name"]
    (proj / "输出" / "成片").mkdir(parents=True, exist_ok=True)
    (proj / "输出" / "封面").mkdir(parents=True, exist_ok=True)
    (proj / "输出" / "成片" / f"{name}.mp4").write_bytes(b"")
    (proj / "输出" / "封面" / f"{name}.jpg").write_bytes(b"")
    drafts = case / "drafts"
    d = drafts / name
    d.mkdir(parents=True)
    (d / "draft_content.json").write_text(
        json.dumps({"tracks": [{"name": f"轨{i}"} for i in range(6)], "duration": 42600000}),
        encoding="utf-8")
    (d / "draft_meta_info.json").write_text("{}", encoding="utf-8")
    (d / "draft_cover.jpg").write_bytes(b"")
    run("qc_delivery.py", "--project", proj, "--drafts", drafts, cwd=case)
    result = json.loads((proj / "输出" / "验收结果.json").read_text(encoding="utf-8"))
    items = [c for c in result["checks"] if c["item"].startswith("工程轨道")]
    ok = bool(items) and items[0]["status"] == "PASS" and "draft_content.json" in items[0]["detail"]
    return ok, (items[0]["detail"] if items else "未找到工程轨道检查项")


# ---------- v1.3 插入素材（含视频片段）回归 ----------

def make_insert_project(case: Path):
    proj = make_project(case)
    folder = proj / "素材"
    folder.mkdir(parents=True, exist_ok=True)
    names = ["鸡蛋", "血管", "油炸", "睡眠", "检查"]
    for n in names:
        (folder / f"{n}.mp4").write_bytes(b"")
    return proj, folder, names


def valid_insert_items(proj: Path, names: list, count: int = 5) -> list:
    """按字幕句边界造一批合法插入点，供各用例再单独破坏一个字段。"""
    subs = json.loads((proj / "输出" / "字幕" / "字幕.json").read_text(encoding="utf-8"))
    points = [0.0]
    for e in subs["events"]:
        t = round(float(e["end"]) + 0.02, 3)
        if 0.5 <= t <= 40 and t - points[-1] >= 1.9:
            points.append(t)
        if len(points) >= count:
            break
    items = []
    for i, t in enumerate(points[:count]):
        items.append({
            "type": "video", "source": f"素材/{names[i]}.mp4",
            "source_in": 0.0, "source_out": 2.2,
            "target_in": t, "duration": 1.9, "volume": 0,
            "fit": "fullscreen", "hard_subtitle_clean": True,
            "license_status": "approved", "semantic_anchor": "",
            "source_fps": None, "source_resolution": None, "note": "自测",
        })
    return items


def put_items(proj: Path, items: list):
    plan_path = proj / "输出" / "剪辑任务单.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["assets"]["insert_points"] = items
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def run_verify(proj: Path, case: Path):
    r = run("select_inserts.py", "--project", proj, "--verify", cwd=case)
    try:
        return r, json.loads(r.stdout)
    except ValueError:
        return r, {}


def case_inserts_flow(case: Path):
    proj, _, names = make_insert_project(case)
    scan = run("select_inserts.py", "--project", proj, "--scan", "--no-probe", cwd=case)
    scan_data = json.loads(scan.stdout)
    cand = proj / "输出" / "insert_points_候选.json"
    items = json.loads(cand.read_text(encoding="utf-8"))
    bad = run("select_inserts.py", "--project", proj, "--apply", cwd=case)
    for it in items:
        it["license_status"] = "approved"
    cand.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    good = run("select_inserts.py", "--project", proj, "--apply", cwd=case)
    ver, _ = run_verify(proj, case)
    plan = json.loads((proj / "输出" / "剪辑任务单.json").read_text(encoding="utf-8"))
    inserted = plan["assets"]["insert_points"]
    ok = (scan.returncode == 0 and scan_data["candidates"] >= 3
          and bad.returncode == 2 and good.returncode == 0 and ver.returncode == 0
          and len(inserted) >= 3
          and all(i["type"] == "video" and i["volume"] == 0 for i in inserted)
          and (proj / "输出" / "素材插入方案.md").exists())
    return ok, (f"scan={scan.returncode}/{scan_data['candidates']}候选 "
                f"未授权apply={bad.returncode} 已授权apply={good.returncode} verify={ver.returncode}")


def case_inserts_license(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    items[0]["license_status"] = "rejected"
    put_items(proj, items)
    r, d = run_verify(proj, case)
    failed = [f["item"] for f in d.get("failed_checks", [])]
    ok = r.returncode == 2 and "license" in failed
    return ok, f"rc={r.returncode} 拦截项={sorted(set(failed))}"


def case_inserts_duration_margin(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    items[0]["duration"] = 3.0          # 超出 1.7-2.4s
    items[1]["source_out"] = 1.8        # 1.8 < 1.9 + 0.2 余量
    put_items(proj, items)
    r, d = run_verify(proj, case)
    failed = [f["item"] for f in d.get("failed_checks", [])]
    ok = r.returncode == 2 and "duration" in failed and "source_margin" in failed
    return ok, f"rc={r.returncode} 拦截项={sorted(set(failed))}"


def case_inserts_overlap_duplicate(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    items[1]["target_in"] = round(items[0]["target_in"] + 0.2, 3)
    items[2]["source"] = items[0]["source"]
    put_items(proj, items)
    r, d = run_verify(proj, case)
    failed = [f["item"] for f in d.get("failed_checks", [])]
    ok = r.returncode == 2 and "no_overlap" in failed and "no_duplicate" in failed
    return ok, f"rc={r.returncode} 拦截项={sorted(set(failed))}"


def case_inserts_mute_boundary_source(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    items[0]["volume"] = 100                 # 必须静音
    items[1]["target_in"] = 9.999           # 不在句边界
    items[2]["source"] = "素材/不存在.mp4"   # 文件不存在
    put_items(proj, items)
    r, d = run_verify(proj, case)
    failed = [f["item"] for f in d.get("failed_checks", [])]
    need = {"muted", "sentence_boundary", "source_exists"}
    ok = r.returncode == 2 and need.issubset(set(failed))
    return ok, f"rc={r.returncode} 拦截项={sorted(set(failed))}"


def case_build_dry_run(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    put_items(proj, items)
    r = run("build_insert_draft.py", "--project", proj, "--dry-run",
            "--no-motion-check", cwd=case)
    data = json.loads(r.stdout)
    plan_path = proj / "输出" / "工程构建" / "构建计划.json"
    build = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
    segs = build.get("segments", [])
    ok = (r.returncode == 0 and data.get("ok") and data.get("draft_dir") is None
          and build.get("gate", {}).get("ok") and len(segs) == len(items)
          and all(s["material_type"] == "video" and s["volume"] == 0
                  and len(s.get("source_timerange_us", [])) == 2 for s in segs)
          and (proj / "输出" / "工程构建" / "剪映打开验证.md").exists())
    return ok, (f"rc={r.returncode} 计划片段={len(segs)} "
                f"未写工程={data.get('draft_dir') is None}")


def case_build_gate(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    items[0]["license_status"] = "unknown"
    put_items(proj, items)
    r = run("build_insert_draft.py", "--project", proj, "--build",
            "--drafts-dir", case, "--no-motion-check", cwd=case)
    data = json.loads(r.stdout)
    ok = (r.returncode == 2 and not data.get("ok") and data.get("stage") == "verify"
          and not (case / "构建计划.json").exists())
    return ok, f"rc={r.returncode} stage={data.get('stage')} 未进构建"


def case_build_no_overwrite(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    put_items(proj, items)
    drafts = case / "草稿"
    drafts.mkdir()
    target = drafts / "同名工程"
    target.mkdir()
    marker = target / "draft_content.json"
    marker.write_text("SENTINEL", encoding="utf-8")
    r = run("build_insert_draft.py", "--project", proj, "--build",
            "--drafts-dir", drafts, "--draft-name", "同名工程",
            "--no-motion-check", cwd=case)
    data = json.loads(r.stdout)
    ok = (r.returncode == 2 and "版本不覆盖" in str(data.get("error", ""))
          and marker.read_text(encoding="utf-8") == "SENTINEL")
    return ok, f"rc={r.returncode} 原工程未被改动={marker.read_text(encoding='utf-8') == 'SENTINEL'}"


def case_build_missing_dir(case: Path):
    proj, _, names = make_insert_project(case)
    items = valid_insert_items(proj, names)
    put_items(proj, items)
    plan_path = proj / "输出" / "剪辑任务单.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["delivery"]["draft_dir"] = ""
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    r = run("build_insert_draft.py", "--project", proj, "--build",
            "--no-motion-check", cwd=case)
    data = json.loads(r.stdout)
    ok = r.returncode == 2 and "--drafts-dir" in str(data.get("error", ""))
    return ok, f"rc={r.returncode} 明确报缺草稿目录"


CASES = [
    ("01 正常流程/强制对齐/保护词/片尾", case_normal),
    ("02 无 ASR 时间戳被拦截", case_no_asr),
    ("03 逐字稿与 ASR 不一致被拦截", case_mismatch),
    ("04 人工核对后显式放行", case_allow),
    ("05 ASR 只覆盖前半段被拦截", case_half_asr),
    ("06 缺 --duration 被拦截", case_no_duration),
    ("07 首句保留 ASR 实际起点", case_first_offset),
    ("08 短句不跨标点/单字行拦截", case_short_clause),
    ("09 验收默认严格退出码", case_qc_strict),
    ("10 批量阶段待补项未清零", case_deferred),
    ("11 11.7 draft_content.json 轨道核对", case_draft_content),
    ("12 插入素材 扫描→人工授权→应用→复核", case_inserts_flow),
    ("13 未确认授权的素材被拦截", case_inserts_license),
    ("14 时长越界/源片段余量不足被拦截", case_inserts_duration_margin),
    ("15 区间重叠/素材重复使用被拦截", case_inserts_overlap_duplicate),
    ("16 未静音/非句边界/素材文件缺失被拦截", case_inserts_mute_boundary_source),
    ("17 工程构建 dry-run 计划正确且不写工程", case_build_dry_run),
    ("18 插入点门禁未过禁止构建工程", case_build_gate),
    ("19 同名工程绝不覆盖（红线）", case_build_no_overwrite),
    ("20 --build 缺草稿目录明确报错", case_build_missing_dir),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="kuoleroy-ai-editing v1.4 回归自测（20 项）")
    ap.add_argument("--verbose", action="store_true", help="失败时打印子进程输出")
    args = ap.parse_args()
    print(f"技能包：{SKILL_DIR}")
    print(f"Python ：{sys.version.split()[0]}\n")
    results = []
    with tempfile.TemporaryDirectory(prefix="kuoleroy-selftest-") as td:
        root = Path(td)
        for i, (name, fn) in enumerate(CASES, 1):
            try:
                ok, detail = fn(root / f"case{i:02d}")
            except Exception as exc:  # noqa: BLE001 - 自测要报告而非中断
                ok, detail = False, f"异常：{type(exc).__name__}: {exc}"
            results.append((name, ok, detail))
            print(f"  {'PASS' if ok else 'FAIL'}  {name}  —  {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结论：{passed}/{len(results)} 通过")
    if passed != len(results):
        print("失败项：" + "、".join(n for n, ok, _ in results if not ok))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
