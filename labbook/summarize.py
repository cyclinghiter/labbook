"""수집 결과를 claude -p로 요약한다. 실패하면 (None, 오류)를 돌려주고, 메일은 원본 통계로 나간다."""
import json
import os
import subprocess
import tempfile

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "done": {"type": "array", "items": {"type": "string"}},
                    "issues": {"type": "array", "items": {"type": "string"}},
                    "next": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "done"],
            },
        },
        "other": {"type": "array", "items": {"type": "string"}},
        "roadmap_suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "suggestion": {"type": "string"}},
                "required": ["id", "suggestion"],
            },
        },
    },
    "required": ["headline", "projects"],
}

INSTRUCTIONS = """\
너는 한 연구자의 하루 연구 활동을 정리하는 비서다. 아래 <activity>는 {date} 하루(집계 구간 {start} ~ {end} KST)의 기록이다.
- sessions: Claude Code 세션. turns의 prompt는 연구자가 친 요청, reply는 그 요청에 대한 마지막 답의 앞부분이다.
- file_changes / unregistered_changes: 그 구간에 수정된 파일 수, 확장자 분포, 최근 파일 경로.
- git_commits, notes(연구자가 직접 남긴 메모).
<projects>는 프로젝트 목록과 현재 로드맵이다. 활동이 있던 프로젝트는 로드맵 문서 앞부분(roadmap_excerpt)도 붙어 있다.

정리 규칙:
1. 기록에 있는 사실만 쓴다. 추측해서 채우지 않는다. 수치·버전·파일명은 기록 그대로 쓴다.
2. 프로젝트별로 묶는다. 세션의 project_guess는 힌트일 뿐이다. 요청 내용과 수정 파일을 보고 실제 프로젝트 id를 정한다. <projects>에 없는 일은 other에 넣는다.
3. done: 그날 한 일. 결과 중심으로 항목당 한 문장, 프로젝트당 2–6개. 비슷한 요청은 하나로 합친다.
4. issues: 막힌 점, 연구자 결정을 기다리는 것. 기록에 드러날 때만.
5. next: 기록에서 드러난 다음 할 일. 기록에 없으면 비운다.
6. 세션·커밋·메모 없이 파일 변경만 있는 프로젝트는 done 한 줄로 "파일 N개 변경(주로 .xxx)"처럼 쓴다. 계산 출력으로 보이면 그렇게 적는다.
7. roadmap_suggestions: 오늘 기록을 근거로 projects.yaml의 status/goal/next/deadline을 바꿀 만할 때만. 근거를 짧게 붙인다.
8. headline: 하루를 한 문장으로.
9. 한국어, 간결체("~했다", "~함"). 과장하지 않는다.
"""


def _excerpt(study_root, rel, n=2500):
    if not rel:
        return None
    try:
        with open(os.path.join(study_root, rel), encoding="utf-8", errors="ignore") as f:
            return f.read(n)
    except OSError:
        return None


def build_prompt(raw, projects, cfg):
    active_ids = set(raw["file_changes"]) | {s["project_guess"] for s in raw["sessions"]} | {
        c["project"] for c in raw["git_commits"]}
    plist = []
    for p in projects:
        item = {k: p.get(k) for k in ("id", "path", "status", "goal", "next", "deadline") if p.get(k)}
        if p["id"] in active_ids and p.get("roadmap_file"):
            item["roadmap_excerpt"] = _excerpt(cfg["study_root"], p["roadmap_file"])
        plist.append(item)
    activity = {k: raw[k] for k in ("sessions", "file_changes", "unregistered_changes", "git_commits", "notes")}
    head = INSTRUCTIONS.format(date=raw["date"], start=raw["window"]["start"][:16].replace("T", " "),
                               end=raw["window"]["end"][:16].replace("T", " "))
    return (head + "\n<projects>\n" + json.dumps(plist, ensure_ascii=False, indent=1) + "\n</projects>\n"
            + "\n<activity>\n" + json.dumps(activity, ensure_ascii=False, indent=1) + "\n</activity>\n")


def _parse_output(stdout):
    data = json.loads(stdout)
    events = data if isinstance(data, list) else [data]
    result = next((e for e in reversed(events) if isinstance(e, dict) and e.get("type") == "result"), None)
    if result is None:
        raise ValueError("result 이벤트 없음")
    if result.get("is_error"):
        raise ValueError(f"claude 오류: {str(result.get('result'))[:300]}")
    out = result.get("structured_output")
    if out is None:
        out = json.loads(result.get("result") or "")
    return out


def summarize(raw, projects, cfg):
    scfg = cfg["summarizer"]
    cmd = [scfg["command"], "-p", "--tools", "", "--no-session-persistence",
           "--output-format", "json", "--json-schema", json.dumps(SCHEMA)]
    if scfg.get("model"):
        cmd += ["--model", scfg["model"]]
    prompt = build_prompt(raw, projects, cfg)
    # 빈 임시 폴더에서 돌려 CLAUDE.md·프로젝트 맥락이 섞이지 않게 한다. 세션도 저장하지 않는다.
    with tempfile.TemporaryDirectory(prefix="labbook-") as tmp:
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=scfg["timeout_sec"], cwd=tmp)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"claude 실행 실패: {e}"
    if r.returncode != 0:
        return None, f"claude 종료 코드 {r.returncode}: {(r.stderr or r.stdout)[-300:]}"
    try:
        return _parse_output(r.stdout), None
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"claude 출력 해석 실패: {e}"
