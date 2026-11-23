#!/usr/bin/env bash
# cron이 매일 21:00 KST(= 12:00 UTC)에 부른다. 로그는 logs/nightly.log
DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
mkdir -p "$DIR/logs"
exec 9>"$DIR/logs/.lock"
flock -n 9 || { echo "$(date -Is) 이미 실행 중" >> "$DIR/logs/nightly.log"; exit 1; }
{
  echo "=== $(TZ=Asia/Seoul date '+%F %T KST') ==="
  "$DIR/bin/lb" run
  echo "exit=$?"
} >> "$DIR/logs/nightly.log" 2>&1
