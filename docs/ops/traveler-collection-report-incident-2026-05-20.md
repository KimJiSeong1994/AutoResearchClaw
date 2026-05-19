# 운영 리포트 — 집현전-여행자 자동 운영 중단 및 복구

- 발생 범위: 집현전-여행자 daily collection report / scout / source discovery cron
- 영향 일자: 2026-05-18, 2026-05-19 KST 추정
- 조사/복구 일자: 2026-05-20 KST
- 상태: EC2 stable wrapper/runner 복구 완료, 실제 Traveler 파이프라인 실행 및 Discord 보고/광부 요청 전송 완료
- 운영리포팅 Discord thread: `1506312650190225618`

## 1. 현상

- 집현전-여행자 daily collection report가 2026-05-17 이후 갱신되지 않았다.
- EC2 crontab에는 Traveler cron이 남아 있었지만 실행 대상 파일이 사라져 있었다.
- `state/traveler-collection-report-last-status.json`은 2026-05-16 보고 상태에 머물러 있었다.

## 2. 디버깅 내용

### 2.1 서비스 상태

- `discord-jiphyeonjeon-traveler.service`는 active 상태였다.
- 2026-05-19 13:56 UTC ready 로그가 확인되어 slash-command bot 자체는 살아 있었다.
- 따라서 장애 중심은 standalone bot이 아니라 daily cron/report 파이프라인으로 좁혀졌다.

### 2.2 cron entrypoint 누락

- EC2 crontab은 다음 파일을 호출했다.

```cron
0 13 * * * /home/ubuntu/.openclaw/workspace/scripts/traveler-collection-report.sh
```

- 하지만 EC2의 해당 파일은 누락되어 있었다.
- 실제 committed runner인 `skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh`는 존재했다.
- 뉴스레터 장애와 같은 구조로, cron은 남아 있지만 원격 `scripts/` 복사본이 workspace cleanup/deploy 과정에서 사라질 수 있는 상태였다.

### 2.3 마지막 정상/부분 실행 흔적

- `logs/traveler-collection-report.log` 마지막 주요 실행은 2026-05-17 KST였다.
- `state/traveler-source-discovery-last-status.json`은 2026-05-17 12:14 UTC 상태였다.
- `state/traveler-collection-report-last-status.json`은 2026-05-16 13:00 UTC 상태였다.

## 3. 복구 내용

다음 파일을 추가/수정했다.

- `scripts/traveler-collection-report.sh`
- `skills/discord-openclaw-bridge/install-traveler-collection-report-cron.sh`
- `tests/test_runtime_manifests.py`

변경 요약:

1. repo에 stable cron wrapper `scripts/traveler-collection-report.sh`를 추가했다.
2. installer가 wrapper와 committed runner를 모두 EC2에 배포하도록 수정했다.
3. installer가 원격 `bash -n`으로 wrapper/runner를 검증한 뒤 cron을 교체하도록 했다.
4. cron은 계속 stable wrapper를 호출하고, wrapper가 committed runner로 위임하도록 했다.
5. runtime manifest test에 Traveler wrapper/installer 검증을 추가했다.

## 4. EC2 반영 및 실행 결과

- EC2 배포 파일:
  - `/home/ubuntu/.openclaw/workspace/scripts/traveler-collection-report.sh`
  - `/home/ubuntu/.openclaw/workspace/skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh`
  - `/home/ubuntu/.openclaw/workspace/runtime/traveler-scout-topics.json`
- 원격 `bash -n` 검증 통과.
- crontab 복구 확인:

```cron
0 13 * * * /home/ubuntu/.openclaw/workspace/scripts/traveler-collection-report.sh
```

- 실제 실행 결과:
  - scout: exit=0
  - source discovery: exit=0
  - collection report: exit=0
  - Traveler Discord thread: `1506311869621731458`
  - Miner request message: `1506311871928602767`
  - report status: `candidate_count=1`, `miner_request_state=sent`

## 5. 실행 중 관찰된 비치명 이슈

- arXiv / Semantic Scholar provider에서 429 rate limit이 발생했다.
- 파이프라인은 static fallback과 evidence gate를 사용해 계속 진행했고, 최종 source discovery/report는 성공했다.
- 향후 provider backoff 또는 API key/rate budget 분리가 있으면 품질과 안정성이 개선될 수 있다.

## 6. self-reflecting 운영 학습 항목

향후 Traveler 장애를 감지하면 다음 순서로 점검한다.

1. `crontab -l`에서 Traveler cron target을 확인한다.
2. cron target 파일 존재/실행권한을 확인한다.
3. wrapper가 committed runner로 위임하는지 확인한다.
4. wrapper와 runner 모두 `bash -n`으로 검증한다.
5. `logs/traveler-collection-report.log`의 마지막 `done (exit=0 scout_exit=0 discovery_exit=0)`를 확인한다.
6. `state/traveler-scout-last-status.json`, `state/traveler-source-discovery-last-status.json`, `state/traveler-collection-report-last-status.json` 갱신 시간을 확인한다.
7. provider 429는 장애와 구분한다. fallback 이후 report가 게시됐으면 운영 성공으로 본다.
8. cron은 원격 임시 복사본이 아니라 repo committed stable wrapper를 호출해야 한다.

## 7. 검증

- 로컬:
  - `bash -n scripts/traveler-collection-report.sh`
  - `bash -n skills/discord-openclaw-bridge/install-traveler-collection-report-cron.sh`
  - `bash -n skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh`
  - `python3 -m unittest tests.test_runtime_manifests` → 7 tests OK
- EC2:
  - wrapper/runner 파일 존재 및 실행권한 확인
  - remote `bash -n` 통과
  - actual run complete: `traveler-collection-report done (exit=0 scout_exit=0 discovery_exit=0)`
