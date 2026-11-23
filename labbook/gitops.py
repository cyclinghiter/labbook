import subprocess

from .common import ROOT


def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120)


def commit(paths, message, push=False):
    _git("add", "--", *[str(p) for p in paths])
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return True, "커밋할 변경 없음"
    r = _git("commit", "-q", "-m", message)
    if r.returncode != 0:
        return False, f"커밋 실패: {r.stderr.strip()[-300:]}"
    msg = f"커밋: {message}"
    if push:
        if not _git("remote").stdout.strip():
            return True, msg + " (원격 없음, push 건너뜀)"
        r = _git("push", "-q")
        if r.returncode != 0:
            return False, msg + f" / push 실패: {r.stderr.strip()[-300:]}"
        msg += " + push"
    return True, msg
