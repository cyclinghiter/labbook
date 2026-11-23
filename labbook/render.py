"""일지 markdown과 메일 HTML을 만든다. 요약(LLM)이 없으면 원본 통계만으로 만든다."""
import datetime as dt

import markdown

from .common import WEEKDAY_KO, day_label

TABLE_CSS = {
    "<table>": '<table style="border-collapse:collapse;font-size:13px;margin:6px 0">',
    "<th>": '<th style="border:1px solid #ccc;padding:4px 8px;background:#f3f3f3;text-align:left">',
    "<td>": '<td style="border:1px solid #ccc;padding:4px 8px;vertical-align:top">',
}


def _project_stats(raw):
    """프로젝트별 세션·요청·파일·커밋 수."""
    stats = {}

    def get(pid):
        return stats.setdefault(pid, {"sessions": 0, "prompts": 0, "files": 0, "commits": 0})

    for s in raw["sessions"]:
        st = get(s["project_guess"] or "기타")
        st["sessions"] += 1
        st["prompts"] += s["n_prompts"]
    for pid, c in raw["file_changes"].items():
        get(pid)["files"] = c["count"]
    for c in raw["git_commits"]:
        get(c["project"] or "기타")["commits"] += 1
    return stats


def _stat_text(st):
    parts = []
    if st["sessions"]:
        parts.append(f"세션 {st['sessions']} · 요청 {st['prompts']}")
    if st["files"]:
        parts.append(f"파일 {st['files']}")
    if st["commits"]:
        parts.append(f"커밋 {st['commits']}")
    return ", ".join(parts)


def _dday(deadline, day):
    if not deadline:
        return ""
    d = deadline if isinstance(deadline, dt.date) else dt.date.fromisoformat(str(deadline))
    n = (d - day).days
    return f"{d:%m-%d} (D{'-' if n >= 0 else '+'}{abs(n)})"


def _mmdd(iso):
    return iso[5:] if iso else "—"


def _esc(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def subject(cfg, raw, summary, projects):
    day = dt.date.fromisoformat(raw["date"])
    names = [p["id"] for p in (summary or {}).get("projects", [])][:3]
    if not names:
        stats = _project_stats(raw)
        names = sorted(stats, key=lambda k: -(stats[k]["prompts"] * 20 + stats[k]["files"]))[:3]
    tail = " · " + ", ".join(names) if names else " · 활동 없음"
    return f"{cfg['mail']['subject_prefix']} {day:%m-%d}({WEEKDAY_KO[day.weekday()]}){tail}"


def render_markdown(cfg, raw, summary, summary_error, projects):
    day = dt.date.fromisoformat(raw["date"])
    w = raw["window"]
    stats = _project_stats(raw)
    by_id = {p["id"]: p for p in projects}
    n_prompts = sum(s["n_prompts"] for s in raw["sessions"])
    n_files = sum(c["count"] for c in raw["file_changes"].values()) + sum(
        c["count"] for c in raw["unregistered_changes"].values())

    L = [f"# 연구일지 {day_label(day)}", ""]
    span = f"{w['start'][5:16].replace('T', ' ')} ~ {w['end'][5:16].replace('T', ' ')} KST"
    if w.get("partial"):
        span += " (진행 중, 부분 집계)"
    L += [f"> 집계 {span} · 세션 {len(raw['sessions'])} · 요청 {n_prompts} · 변경 파일 {n_files}"
          f" · 커밋 {len(raw['git_commits'])} · 메모 {len(raw['notes'])}", ""]

    # ── 오늘 한 일 ──
    L += ["## 오늘 한 일", ""]
    if summary:
        L += [f"**{summary.get('headline', '').strip()}**", ""]
        for item in summary.get("projects", []):
            p = by_id.get(item["id"], {})
            st = stats.get(item["id"])
            head = f"### {item['id']}"
            if p.get("goal"):
                head += f" — {p['goal']}"
            L += [head]
            if st:
                L += [f"<sub>{_stat_text(st)}</sub>"]
            L += [""]
            L += [f"- {x}" for x in item.get("done", [])]
            L += [f"- ⚠ {x}" for x in item.get("issues", [])]
            L += [f"- → {x}" for x in item.get("next", [])]
            L += [""]
        if summary.get("other"):
            L += ["### 기타", ""] + [f"- {x}" for x in summary["other"]] + [""]
    else:
        if summary_error:
            L += [f"_요약 생성 실패 → 원본 통계만 싣는다. ({summary_error})_", ""]
        if not stats and not raw["notes"]:
            L += ["기록된 활동 없음.", ""]
        for pid, st in sorted(stats.items(), key=lambda kv: -(kv[1]["prompts"] * 20 + kv[1]["files"])):
            L += [f"### {pid}", f"<sub>{_stat_text(st)}</sub>", ""]
            for s in raw["sessions"]:
                if (s["project_guess"] or "기타") == pid:
                    L += [f"- [{t['t'][-5:]}] {t['prompt'][:120]}" for t in s["turns"][:5]]
            for c in raw["git_commits"]:
                if (c["project"] or "기타") == pid:
                    L += [f"- 커밋 `{c['hash']}` {c['subject']}"]
            L += [""]

    if raw["notes"]:
        L += ["### 메모", ""] + [f"- [{n['t'][-5:]}] {n['text']}" for n in raw["notes"]] + [""]

    # ── 프로젝트 현황 ──
    last = raw["last_activity"]
    active = [p for p in projects if p["status"] == "active"]
    L += ["## 진행 중 프로젝트", "", "| 프로젝트 | 목표 | 마지막 활동 | 다음 할 일 | 마감 |", "|---|---|---|---|---|"]
    for p in active:
        nxt = "<br>".join(_esc(x) for x in p["next"][:3]) or "—"
        L += [f"| **{p['id']}** | {_esc(p['goal'])} | {_mmdd(last.get(p['id']))} | {nxt} | {_dday(p.get('deadline'), day) or '—'} |"]
    L += [""]
    paused = [p for p in projects if p["status"] == "paused"]
    if paused:
        L += ["보류: " + ", ".join(f"{p['id']}({_mmdd(last.get(p['id']))})" for p in paused), ""]

    stale_cut = (day - dt.timedelta(days=cfg["stale_days"])).isoformat()
    stale = [p for p in active if last.get(p["id"], "0000") < stale_cut]
    if stale:
        L += [f"**{cfg['stale_days']}일 넘게 활동 없는 active 프로젝트:** "
              + ", ".join(f"{p['id']}({last.get(p['id'], '기록 없음')})" for p in stale), ""]

    # ── 로드맵 제안 ──
    sugg = (summary or {}).get("roadmap_suggestions") or []
    if sugg:
        L += ["## 로드맵 변경 제안", "", "_반영하려면 `_labbook/projects.yaml`을 고친다._", ""]
        L += [f"- **{s['id']}**: {s['suggestion']}" for s in sugg] + [""]

    # ── 등록되지 않은 폴더 ──
    unreg = raw["unregistered_changes"]
    if unreg:
        L += ["## projects.yaml에 없는 폴더의 변경", ""]
        L += [f"- `{k}`: 파일 {v['count']}개" for k, v in sorted(unreg.items(), key=lambda kv: -kv[1]["count"])[:8]]
        L += [""]

    L += ["---", f"<sub>labbook · {raw['generated_at']} 생성</sub>", ""]
    return "\n".join(L)


def render_html(md_text):
    body = markdown.markdown(md_text, extensions=["tables", "sane_lists"])
    for k, v in TABLE_CSS.items():
        body = body.replace(k, v)
    return ('<html><body style="font-family:-apple-system,\'Malgun Gothic\',\'Apple SD Gothic Neo\',sans-serif;'
            'font-size:14px;line-height:1.55;color:#222;max-width:860px">' + body + "</body></html>")
