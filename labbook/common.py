import datetime as dt
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent  # _labbook/
KST = dt.timezone(dt.timedelta(hours=9), "KST")  # 한국은 서머타임이 없어 고정 오프셋으로 충분
WEEKDAY_KO = "월화수목금토일"


def load_config():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["study_root"] = Path(os.path.expanduser(cfg["study_root"])).resolve()
    cfg["claude_logs"] = Path(os.path.expanduser(cfg["claude_logs"]))
    return cfg


def load_projects():
    with open(ROOT / "projects.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    projects = data.get("projects") or []
    for p in projects:
        p["path"] = str(p["path"]).strip("/")
        p.setdefault("status", "active")
        p.setdefault("goal", "")
        p["next"] = p.get("next") or []
    return projects


def _cutoff(cfg):
    h, m = map(int, cfg["day_cutoff"].split(":"))
    return dt.time(h, m)


def resolve_window(cfg, date_str=None, now=None):
    """일지 날짜 D의 집계 구간 [D-1 cutoff, D cutoff)를 KST로 돌려준다.

    date_str가 없으면 가장 최근에 끝난 하루. 아직 끝나지 않은 날이면 끝을 지금으로 자르고 partial=True.
    """
    now = now or dt.datetime.now(KST)
    cut = _cutoff(cfg)
    if date_str:
        day = dt.date.fromisoformat(date_str)
    else:
        day = now.date()
        if now < dt.datetime.combine(day, cut, KST):
            day -= dt.timedelta(days=1)
    start = dt.datetime.combine(day - dt.timedelta(days=1), cut, KST)
    end = dt.datetime.combine(day, cut, KST)
    partial = end > now
    if partial:
        end = now.replace(microsecond=0)
    return day, start, end, partial


def day_label(day):
    return f"{day.isoformat()} ({WEEKDAY_KO[day.weekday()]})"


def dated_path(base, day, suffix):
    p = ROOT / base / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}{suffix}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def read_env():
    env = {}
    path = ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env
