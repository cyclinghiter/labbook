"""labbook CLI.

  lb run [--date YYYY-MM-DD] [--no-llm] [--no-mail] [--no-commit] [--print]
  lb note "오늘 측정실에서 RLSA 근접장 측정"
  lb projects
  lb mail-test
"""
import argparse
import datetime as dt
import json
import sys

from . import collect, gitops, mailer, render, summarize
from .common import KST, ROOT, load_config, load_projects, resolve_window


def cmd_run(args):
    cfg, projects = load_config(), load_projects()
    day, start, end, partial = resolve_window(cfg, args.date)
    print(f"[labbook] {day} 집계 {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} KST" + (" (부분)" if partial else ""))
    ok_all = True

    raw = collect.collect(cfg, projects, day, start, end, partial)
    n_prompts = sum(s["n_prompts"] for s in raw["sessions"])
    print(f"  수집: 세션 {len(raw['sessions'])}, 요청 {n_prompts}, 변경 프로젝트 {len(raw['file_changes'])}, "
          f"커밋 {len(raw['git_commits'])}, 메모 {len(raw['notes'])}")

    summary, err = None, None
    has_activity = raw["sessions"] or raw["file_changes"] or raw["git_commits"] or raw["notes"]
    if args.no_llm:
        err = "--no-llm"
    elif has_activity:
        summary, err = summarize.summarize(raw, projects, cfg)
        print("  요약: " + ("완료" if summary else f"실패 — {err}"))
        ok_all &= summary is not None
    raw["summary"], raw["summary_error"] = summary, err
    # 마지막 활동일: 파일 수정 시각 + 요약이 그날 일했다고 판단한 프로젝트 + 커밋
    touched = {c["project"] for c in raw["git_commits"]} | {p["id"] for p in (summary or {}).get("projects", [])}
    for pid in touched - {None}:
        raw["last_activity"][pid] = max(raw["last_activity"].get(pid, ""), raw["date"])

    md = render.render_markdown(cfg, raw, summary, None if args.no_llm else err, projects)
    subject = render.subject(cfg, raw, summary, projects)
    if args.print or partial:
        print(f"\n제목: {subject}\n\n{md}")
    if partial:
        print("  (아직 끝나지 않은 날이라 미리보기만 하고 저장·발송·커밋은 하지 않음)")
        return 0 if ok_all else 1
    raw_path = ROOT / "raw" / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}.json"
    md_path = ROOT / "journal" / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path.write_text(md, encoding="utf-8")
    print(f"  저장: {md_path.relative_to(ROOT)}")

    if not args.no_mail:
        ok, msg = mailer.send(cfg, subject, md, render.render_html(md))
        print("  " + msg)
        ok_all &= ok
    if not args.no_commit:
        ok, msg = gitops.commit([raw_path, md_path], f"journal: {day.isoformat()}", push=cfg["git"]["push"])
        print("  " + msg)
        ok_all &= ok
    return 0 if ok_all else 1


def cmd_note(args):
    text = " ".join(args.text).strip() or sys.stdin.read().strip()
    if not text:
        print("메모 내용이 없다", file=sys.stderr)
        return 1
    path = ROOT / "inbox.md"
    if not path.exists():
        path.write_text("# 메모 (lb note로 추가, 그날 일지에 실린다)\n\n", encoding="utf-8")
    now = dt.datetime.now(KST)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {now:%Y-%m-%d %H:%M} | {' '.join(text.split())}\n")
    print(f"메모 추가: {now:%m-%d %H:%M}")
    return 0


def cmd_projects(args):
    cfg, projects = load_config(), load_projects()
    day, start, end, _ = resolve_window(cfg, None)
    _, _, last, _ = collect.scan_files(cfg, collect.ProjectIndex(projects), start, dt.datetime.now(KST))
    for st in ("active", "paused", "support", "done"):
        group = [p for p in projects if p["status"] == st]
        if not group:
            continue
        print(f"\n[{st}]")
        for p in group:
            print(f"  {p['id']:<26} {last.get(p['id'], '—'):<11} {p['goal']}")
    return 0


def cmd_mail_test(args):
    cfg = load_config()
    ok, msg = mailer.send(cfg, f"{cfg['mail']['subject_prefix']} 발송 테스트",
                          "labbook 메일 발송 테스트입니다.", "<p>labbook 메일 발송 테스트입니다.</p>")
    print(msg)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lb", description="연구 일지 (labbook)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="수집 → 요약 → 일지 저장 → 메일 → 커밋")
    r.add_argument("--date", help="일지 날짜 YYYY-MM-DD (기본: 가장 최근에 끝난 하루)")
    r.add_argument("--no-llm", action="store_true", help="요약 없이 원본 통계로만")
    r.add_argument("--no-mail", action="store_true")
    r.add_argument("--no-commit", action="store_true")
    r.add_argument("--print", action="store_true", help="일지를 화면에 출력")
    r.set_defaults(fn=cmd_run)
    n = sub.add_parser("note", help="한 줄 메모 추가 (Claude 밖에서 한 일)")
    n.add_argument("text", nargs="*")
    n.set_defaults(fn=cmd_note)
    sub.add_parser("projects", help="프로젝트 목록과 마지막 활동일").set_defaults(fn=cmd_projects)
    sub.add_parser("mail-test", help="메일 설정 확인").set_defaults(fn=cmd_mail_test)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
