"""하루 활동 수집: 파일 변경, Claude Code 세션, git 커밋, 수동 메모. LLM 없이 결정적으로 동작한다."""
import collections
import datetime as dt
import heapq
import json
import os
import re
import subprocess

from .common import KST, ROOT

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
NOTE_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) \| (.*)$")


class ProjectIndex:
    def __init__(self, projects):
        self.items = sorted(((p["path"], p["id"]) for p in projects), key=lambda x: -len(x[0]))

    def lookup(self, rel):
        for path, pid in self.items:
            if rel == path or rel.startswith(path + "/"):
                return pid
        return None


def _excluded(rel, cfg):
    return any(rel == x or rel.startswith(x.rstrip("/") + "/") for x in cfg["scan"].get("exclude_paths") or [])


def _bucket(rel):
    """등록되지 않은 경로를 묶는 단위: 위에서 두 단계 폴더."""
    parts = rel.split("/")
    return "/".join(parts[:2]) if len(parts) > 1 else "(study 최상위)"


def scan_files(cfg, index, start, end):
    root = str(cfg["study_root"])
    ex_dirs = set(cfg["scan"]["exclude_dirs"])
    ex_suf = tuple(cfg["scan"]["exclude_suffixes"])
    n_sample = cfg["scan"]["max_sample_files"]
    s_ts, e_ts = start.timestamp(), end.timestamp()

    changes = {}
    last = {}
    repos = []
    for dp, dns, fns in os.walk(root):
        if ".git" in dns or ".git" in fns:
            repos.append(dp)
        reldir = os.path.relpath(dp, root)
        reldir = "" if reldir == "." else reldir
        dns[:] = [d for d in dns
                  if d not in ex_dirs and not _excluded(f"{reldir}/{d}" if reldir else d, cfg)]
        pid = index.lookup(reldir) if reldir else None
        for fn in fns:
            if fn.endswith(ex_suf):
                continue
            try:
                m = os.lstat(os.path.join(dp, fn)).st_mtime
            except OSError:
                continue
            if m >= e_ts:
                continue  # 지난 날짜를 다시 돌릴 때 그날 뒤의 변경은 무시
            if pid and m > last.get(pid, 0):
                last[pid] = m
            if m < s_ts:
                continue
            rel = f"{reldir}/{fn}" if reldir else fn
            key = pid or "?" + _bucket(rel)
            c = changes.setdefault(key, {"count": 0, "ext": collections.Counter(), "recent": []})
            c["count"] += 1
            c["ext"][os.path.splitext(fn)[1].lower() or "(없음)"] += 1
            heapq.heappush(c["recent"], (m, rel))
            if len(c["recent"]) > n_sample:
                heapq.heappop(c["recent"])

    projects, unregistered = {}, {}
    for key, c in changes.items():
        item = {
            "count": c["count"],
            "ext": dict(c["ext"].most_common(6)),
            "recent": [rel for _, rel in sorted(c["recent"], reverse=True)],
        }
        (unregistered if key.startswith("?") else projects)[key.lstrip("?")] = item
    last_activity = {
        pid: dt.datetime.fromtimestamp(m, KST).date().isoformat() for pid, m in last.items()
    }
    return projects, unregistered, last_activity, repos


def _parse_ts(s):
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _human_text(content):
    """사람이 직접 친 요청이면 그 텍스트, 아니면 None (도구 결과, 명령 래퍼, 시스템 알림 등)."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    text = text.strip()
    if not text or text.startswith("<") or text.startswith("[Request interrupted"):
        return None
    return text


def _clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_session(path, cfg, index, start, end):
    root = str(cfg["study_root"])
    scfg = cfg["sessions"]
    title, cwd = None, None
    turns, edited = [], collections.OrderedDict()
    for line in open(path, encoding="utf-8", errors="ignore"):
        if '"ai-title"' in line:
            try:
                title = json.loads(line).get("aiTitle") or title
            except ValueError:
                pass
            continue
        if '"type":"user"' not in line and '"type":"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") not in ("user", "assistant") or d.get("isSidechain") or d.get("isMeta"):
            continue
        ts = _parse_ts(d.get("timestamp"))
        if ts is None or not (start <= ts < end):
            continue
        cwd = d.get("cwd") or cwd
        content = (d.get("message") or {}).get("content")
        if d["type"] == "user":
            text = _human_text(content)
            if text:
                turns.append({"t": ts.astimezone(KST).strftime("%m-%d %H:%M"),
                              "prompt": _clip(text, scfg["prompt_chars"]), "reply": ""})
            continue
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and turns and b.get("text", "").strip():
                turns[-1]["reply"] = _clip(b["text"], scfg["reply_chars"])
            elif b.get("type") == "tool_use" and b.get("name") in EDIT_TOOLS:
                fp = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("notebook_path")
                if fp and fp.startswith(root + "/"):  # 임시 폴더·메모리 파일은 빼고 연구 폴더 안만
                    rel = os.path.relpath(fp, root)
                    if not _excluded(rel, cfg):
                        edited[rel] = True
    if not turns or not cwd or not (cwd == root or cwd.startswith(root + "/")):
        return None

    votes = collections.Counter(index.lookup(f) for f in edited)
    votes.pop(None, None)
    cwd_rel = os.path.relpath(cwd, root)
    if _excluded(cwd_rel, cfg):
        return None
    guess = votes.most_common(1)[0][0] if votes else index.lookup(cwd_rel if cwd_rel != "." else "")
    kept = turns[-scfg["max_turns_per_session"]:]
    return {
        "session": os.path.basename(path)[:8],
        "title": title,
        "cwd": cwd_rel,
        "project_guess": guess,
        "n_prompts": len(turns),
        "dropped_prompts": len(turns) - len(kept),
        "turns": kept,
        "edited_files": list(edited)[:25],
        "n_edited_files": len(edited),
    }


def collect_sessions(cfg, index, start, end):
    base = cfg["claude_logs"]
    prefix = "-" + str(cfg["study_root"]).strip("/").replace("/", "-")
    out = []
    if not base.is_dir():
        return out
    for pdir in sorted(base.iterdir()):
        if not pdir.is_dir() or not pdir.name.startswith(prefix):
            continue
        for f in pdir.glob("*.jsonl"):  # 하위 폴더(subagents)는 보지 않는다
            if f.stat().st_mtime < start.timestamp():
                continue
            s = parse_session(f, cfg, index, start, end)
            if s:
                out.append(s)
    out.sort(key=lambda s: s["turns"][0]["t"])
    return out


def collect_commits(cfg, index, repos, start, end):
    root = str(cfg["study_root"])
    out = []
    for repo in repos:
        try:
            r = subprocess.run(
                ["git", "-C", repo, "log", "--all", "--no-merges",
                 f"--since={start.isoformat()}", f"--until={end.isoformat()}",
                 "--format=%h%x09%aI%x09%s"],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        rel = os.path.relpath(repo, root)
        for line in r.stdout.splitlines():
            h, when, subj = (line.split("\t", 2) + ["", ""])[:3]
            out.append({"repo": rel, "project": index.lookup(rel), "hash": h,
                        "time": when[:16], "subject": subj})
    return out


def collect_notes(start, end):
    path = ROOT / "inbox.md"
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = NOTE_RE.match(line.strip())
        if not m:
            continue
        t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
        if start <= t < end:
            out.append({"t": m.group(1)[5:], "text": m.group(2)})
    return out


def collect(cfg, projects, day, start, end, partial):
    index = ProjectIndex(projects)
    files, unregistered, last_activity, repos = scan_files(cfg, index, start, end)
    sessions = collect_sessions(cfg, index, start, end)
    commits = collect_commits(cfg, index, repos, start, end)
    notes = collect_notes(start, end)
    return {
        "date": day.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat(), "partial": partial},
        "generated_at": dt.datetime.now(KST).isoformat(timespec="seconds"),
        "sessions": sessions,
        "file_changes": files,
        "unregistered_changes": unregistered,
        "git_commits": commits,
        "notes": notes,
        "last_activity": last_activity,
    }
