# 운영 리포트 — 뉴스레터 미발행 및 백필 복구

- 발생 범위: 집현전 뉴스레터 archive/card-news cron
- 영향 일자: 2026-05-18, 2026-05-19
- 조사/복구 일자: 2026-05-19 KST
- 상태: archive 백필 완료, 다음 정기 cron 복구 완료, card-news는 pre-publication trust gate 차단 상태 보존
- 운영리포팅 Discord thread: `1506262371423879298`

## 1. 증상

- 2026-05-18, 2026-05-19 뉴스레터 archive가 Discord에 발행되지 않았다.
- EC2의 `wiki/raw/newsletters/2026-05-18/items.json`,
  `wiki/raw/newsletters/2026-05-19/items.json`도 최초 조사 시 누락되어 있었다.
- `logs/newsletter-archive-and-cardnews.log`는 2026-05-17 이후 정기 실행 로그가 갱신되지 않았다.

## 2. 디버깅 수행 결과

### 2.1 cron은 실행되었다

EC2 syslog에서 다음 cron 실행을 확인했다.

- 2026-05-18 23:00 UTC: `daily-jiphyeonjeon-briefing.sh`,
  `newsletter-archive-and-cardnews.sh`
- 이는 2026-05-19 08:00 KST 정기 발행 시점이다.

### 2.2 runner 파일이 삭제되어 있었다

crontab은 다음 경로를 가리키고 있었다.

```text
/home/ubuntu/.openclaw/workspace/scripts/newsletter-archive-and-cardnews.sh
/home/ubuntu/.openclaw/workspace/scripts/daily-jiphyeonjeon-briefing.sh
```

하지만 원격 `scripts/` 디렉터리에 해당 runner 파일이 없었다. cron은 실행됐지만
명령이 즉시 실패했고, EC2에 MTA가 없어 stdout/stderr가 메일로도 전달되지 않았다.

### 2.3 구조적 원인

- 기존 cron runner는 원격에서 임시 생성된 파일이었다.
- repo에 같은 파일이 없었기 때문에 workspace deploy의 `rsync --delete scripts/`가
  원격 runner를 삭제할 수 있었다.
- 따라서 “cron은 남아 있지만 실행 대상 파일이 사라지는” silent failure가 발생했다.

### 2.4 백필 중 추가 발견

- 2026-05-18 archive 게시 후 card-news 단계에서
  `DISCORD_CARD_NEWS_CHANNEL_ID` 미설정으로 한 차례 실패했다.
- 이후 runner에 안전한 기본 channel fallback을 추가했다.
- 2026-05-18 card-news 재시도는 pre-publication trust gate가 차단했다.
  - `reason_codes`: `editor_duplicate_groups`
  - Advisor evidence gate: pass
  - Editor duplicate group count: 1
- trust gate 차단은 의도된 보호 동작이므로 강제 우회하지 않았다.

## 3. 복구/백필 결과

### 2026-05-18

- archive 생성: 완료
- archive 파일: `wiki/raw/newsletters/2026-05-18/items.json`
- archive 크기: 217,640 bytes
- Discord archive thread: `1506262414818283621` (재시도 시 이전 5/18 thread 1개 purge)
- card-news: pre-publication trust gate 차단으로 미발행
  - summary: `reports/jiphyeonjeon-trust-gates/20260519T114909Z-card-news-summary.json`
  - `reason_codes`: `editor_duplicate_groups`

### 2026-05-19

- archive 생성: 완료
- archive 파일: `wiki/raw/newsletters/2026-05-19/items.json`
- archive 크기: 217,640 bytes
- Discord archive thread: `1506262961944133732`
- card-news: pre-publication trust gate 차단으로 미발행
  - summary: `reports/jiphyeonjeon-trust-gates/20260519T115120Z-card-news-summary.json`
  - `reason_codes`: `editor_duplicate_groups`

## 4. 재발 방지 반영

다음 파일을 추가/수정했다.

- `scripts/newsletter-archive-and-cardnews.sh`
- `scripts/daily-jiphyeonjeon-briefing.sh`
- `skills/discord-openclaw-bridge/project/scripts/run-newsletter-archive-and-cardnews.sh`
- `skills/discord-openclaw-bridge/project/scripts/run-daily-jiphyeonjeon-briefing.sh`
- `skills/discord-openclaw-bridge/install-newsletter-archive-cron.sh`
- `runtime/jobs.yaml`
- `tests/test_runtime_manifests.py`
- `skills/discord-openclaw-bridge/README.md`

변경 요약:

1. cron entrypoint를 repo에 존재하는 stable wrapper로 고정했다.
2. 실제 newsletter/card-news runner도 repo 내부 committed file로 관리한다.
3. installer가 EC2에 wrapper와 runner를 `rsync`한 뒤 `bash -n`으로 검증하고 cron을 교체한다.
4. `NEWSLETTER_ARCHIVE_DRY_RUN=1`, `DAILY_BRIEFING_DRY_RUN=1` dry-run 모드를 추가했다.
5. `DISCORD_NEWSLETTER_ARCHIVE_CHANNEL_ID`, `DISCORD_CARD_NEWS_CHANNEL_ID` 기본 fallback을 runner에 추가했다.

## 5. 검증 증거

- 로컬:
  - `bash -n` runner/installer 통과
  - `python3 -m unittest tests.test_runtime_manifests` → 7 tests OK
- EC2:
  - 4개 runner 파일 실행권한 확인
  - remote `bash -n` 통과
  - bridge import 확인
  - dry-run 실행 확인
  - crontab 복구 확인:

```cron
0 23 * * * /home/ubuntu/.openclaw/workspace/scripts/daily-jiphyeonjeon-briefing.sh
0 23 * * * /home/ubuntu/.openclaw/workspace/scripts/newsletter-archive-and-cardnews.sh
```

## 6. self-replicating 운영 학습 항목

향후 에이전트는 newsletter/card-news 장애를 감지하면 다음 순서로 자기복제식 점검을 수행한다.

1. **증상 확인**
   - 해당 날짜 `wiki/raw/newsletters/YYYY-MM-DD/items.json` 존재/크기 확인
   - Discord archive thread 발행 로그 확인
2. **스케줄 확인**
   - `crontab -l`
   - `/var/log/syslog`의 `CRON ... newsletter-archive-and-cardnews.sh` 실행 여부 확인
3. **runner 존재성 확인**
   - crontab이 가리키는 top-level wrapper 존재/실행권한 확인
   - wrapper가 가리키는 skill runner 존재/실행권한 확인
   - `bash -n`으로 둘 다 검증
4. **로그 단절 패턴 판정**
   - syslog에는 cron 실행이 있는데 app log가 갱신되지 않으면 runner 삭제/권한/경로 문제를 1순위로 본다.
5. **설정 확인**
   - secret 값은 출력하지 말고 env key 존재 여부만 확인한다.
   - channel id fallback 또는 `.env` 누락 여부 확인
6. **복구**
   - `install-newsletter-archive-cron.sh`로 wrapper/runner/cron을 재설치
   - `NEWSLETTER_ARCHIVE_DRY_RUN=1`로 side effect 없는 체인 검증
   - 필요 시 날짜별 `NEWSLETTER_DATE=YYYY-MM-DD` 백필 실행
7. **품질 게이트 존중**
   - card-news가 trust gate에서 차단되면 강제 우회하지 않는다.
   - `reports/jiphyeonjeon-trust-gates/*-summary.json`의 `reason_codes`를 운영 리포트에 남긴다.

## 7. 후속 권장 조치

- Guard ops digest가 newsletter archive freshness도 감시하도록 확장한다.
- `crontab target exists + bash -n` 검사를 `openclaw-ops-readiness-check`에 추가한다.
- `newsletter-archive-and-cardnews.log`가 26시간 이상 갱신되지 않으면 운영리포팅에 자동 이슈를 남긴다.
