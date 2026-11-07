#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化 / 校验公司 AI 剪辑项目的五文件夹结构，并生成标准任务单。

用法:
  python <SKILL_DIR>/scripts/init_project.py <项目目录> [--doctor 示例乙 --topic 前列腺增生]
  python <SKILL_DIR>/scripts/init_project.py <项目目录> --check

<SKILL_DIR> 是技能包安装目录（本脚本所在 scripts/ 的上级），
不要假设当前工作目录就是技能包目录。纯 Python 3 标准库实现。
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = Path(__file__).resolve().parent.parent
INPUT_FOLDERS = ["封面模板", "工程文件", "数字人原片", "素材", "文案"]
OUTPUT_FOLDERS = ["输出/成片", "输出/封面", "输出/字幕", "输出/说明"]
TASK_FILE = "输出/剪辑任务单.json"


def load_template() -> dict:
    """模板缺失时明确报错，不用残缺字典生成无法使用的任务单。"""
    tpl = SKILL_DIR / "assets" / "剪辑任务单模板.json"
    if not tpl.exists():
        raise FileNotFoundError(
            f"找不到任务单模板：{tpl}；请确认技能包完整（<SKILL_DIR>/assets/剪辑任务单模板.json）")
    return json.loads(tpl.read_text(encoding="utf-8"))


def check(project: Path) -> dict:
    rows = []
    for name in INPUT_FOLDERS:
        p = project / name
        exists = p.is_dir()
        files = [f for f in p.rglob("*") if f.is_file()] if exists else []
        rows.append({"folder": name, "exists": exists, "files": len(files)})
    missing = [r["folder"] for r in rows if not r["exists"]]
    empty = [r["folder"] for r in rows if r["exists"] and r["files"] == 0
             and r["folder"] in ("数字人原片", "文案", "工程文件")]
    return {
        "project": str(project),
        "folders": rows,
        "missing": missing,
        "empty_required": empty,
        "ready": not missing and not empty,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="初始化/校验 AI 剪辑项目结构")
    ap.add_argument("project", help="项目目录")
    ap.add_argument("--check", action="store_true", help="只检查，不创建任何文件")
    ap.add_argument("--doctor", default="", help="医生姓名（不含『医生』后缀）")
    ap.add_argument("--topic", default="", help="视频主题（<=6 字）")
    ap.add_argument("--date", default=date.today().isoformat(), help="交付日期 YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的任务单")
    args = ap.parse_args()

    project = Path(args.project).expanduser().resolve()
    result = check(project)
    result["skill_dir"] = str(SKILL_DIR)

    if args.check:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 1

    for name in INPUT_FOLDERS + OUTPUT_FOLDERS:
        (project / name).mkdir(parents=True, exist_ok=True)

    task_path = project / TASK_FILE
    task_written = False
    if not task_path.exists() or args.force:
        try:
            task = load_template()
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": str(exc), "skill_dir": str(SKILL_DIR)},
                             ensure_ascii=False, indent=2))
            return 2
        task["project"] = str(project)
        task["doctor"] = args.doctor
        task["topic"] = args.topic
        task["date"] = args.date
        if args.doctor and args.topic:
            task["delivery"]["draft_name"] = f"{args.date}-{args.doctor}医生——{args.topic}"
        task["delivery"]["plan"] = TASK_FILE
        task_path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        task_written = True

    result = check(project)
    result["skill_dir"] = str(SKILL_DIR)
    result["task_file"] = str(task_path)
    result["task_written"] = task_written
    result["created_folders"] = INPUT_FOLDERS + OUTPUT_FOLDERS
    result["pending_inputs"] = result.pop("empty_required", [])
    result["initialized"] = True
    result["note"] = ("目录已就绪；pending_inputs 为待放入材料，用 --check 复核后再开始剪片；"
                      "样板阶段未完成的规范项请登记到任务单 acceptance.deferred_items")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
