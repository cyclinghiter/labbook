# labbook — 연구 일지 자동화

매일 21:00 KST에 `study/`에서 그날 한 일을 모아 요약한다. 결과는 `journal/`에 저장하고, `hjm614@korea.ac.kr`로 메일을 보낸 뒤 git에 커밋한다.
연구 폴더는 읽기만 한다. 쓰는 곳은 이 폴더(`study/_labbook/`)뿐이다.

## 하루의 정의
전날 21:00 ~ 당일 21:00 (KST). 밤늦게 한 작업은 다음 날 일지에 들어간다.

## 흐름 (`lb run`)
1. **수집** (`labbook/collect.py`, LLM 없이 2–3초) → `raw/YYYY/MM/DATE.json`
   - Claude Code 세션 로그 (`~/.claude/projects/-home-nol-study*`): 사람이 친 요청과 그 답의 앞부분, 수정한 파일
   - 파일 수정 시각: 프로젝트별 변경 수, 확장자, 최근 파일
   - 하위 git repo들의 커밋
   - `lb note`로 남긴 메모 (`inbox.md`)
2. **요약** (`labbook/summarize.py`): `claude -p`로 실행하고 JSON 스키마로 출력 형식을 고정한다. 실패하면 원본 통계만으로 메일을 보낸다.
3. **일지** (`labbook/render.py`) → `journal/YYYY/MM/DATE.md`. 메일은 같은 내용을 HTML로 만든 것이다.
4. **메일** (`labbook/mailer.py`): Gmail SMTP. 계정은 `.env`에 둔다.
5. **커밋** (`labbook/gitops.py`): `journal: DATE`. `config.yaml`의 `git.push: true`면 push도 한다.

## 사용법
```bash
lb run                              # 가장 최근에 끝난 하루 (cron이 부르는 것)
lb run --date 2026-11-23 --no-mail --print   # 다시 만들어 보기
lb run --date 2026-11-24 --no-mail           # 아직 안 끝난 날: 지금까지 미리보기 (저장 안 함)
lb note "측정실에서 RLSA 근접장 측정 3회"      # Claude 밖에서 한 일
lb projects                         # 프로젝트 상태와 마지막 활동일
lb mail-test                        # 메일 설정 확인
```

## 직접 관리하는 파일
- `projects.yaml`: 프로젝트 목록과 로드맵 (status, goal, next, deadline, roadmap_file). 메일의 "로드맵 변경 제안"을 보고 직접 고친다.
- `config.yaml`: 받는 주소, 하루 경계, 제외 경로 등.
- `.env`: Gmail 계정 (git에 올리지 않음). 형식은 `.env.example` 참고.

## cron
```
0 12 * * * /home/nol/study/_labbook/bin/nightly.sh    # 12:00 UTC = 21:00 KST
```
로그는 `logs/nightly.log`.

## 주의
- `raw/`에는 Claude에게 보낸 요청 원문이 들어간다. 원격 저장소는 반드시 **private**으로 둔다.
- 요약기는 빈 임시 폴더에서 `--no-session-persistence`로 돌기 때문에, 요약 작업 자체는 다음 날 일지에 섞이지 않는다.
