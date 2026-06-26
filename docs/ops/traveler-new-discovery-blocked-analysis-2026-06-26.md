# 운영 분석 — 집현전-여행자 신규 탐색 불능 후보

- 조사일: 2026-06-26 KST
- 범위: 집현전-여행자 scout → source discovery → collection report 런타임 경로, 관련 코드/테스트
- 결론: 현재 코드에는 cron entrypoint 복구, provider retry/static fallback, evidence gate가 들어와 있지만, **오래 남은 `pending_deep_research` scout 요청이 같은 topic의 신규 scout 생성을 계속 막는 상태 고착**은 아직 운영 리스크로 남아 있다.
- 상태: 문서화 및 수정 후보 보고. 이 문서는 원격 운영 상태를 직접 변경하지 않는다.

## 1. 런타임 로그 관점

기존 장애 기록(`docs/ops/traveler-collection-report-incident-2026-05-20.md`)에서 확인된 핵심 런타임 사실은 다음과 같다.

- `discord-jiphyeonjeon-traveler.service`는 active였고 slash-command bot ready 로그가 있었다.
- 문제 중심은 standalone Discord bot이 아니라 daily cron/report 파이프라인이었다.
- 당시 EC2 crontab은 stable wrapper 위치(`/home/ubuntu/.openclaw/workspace/scripts/traveler-collection-report.sh`)를 호출했지만 파일이 누락되어 있었다.
- 복구 후 runner 로그는 `traveler-scout done`, `traveler-source-discovery done`, `traveler-collection-report done (exit=0 scout_exit=0 discovery_exit=0)` 형태로 각 단계 exit code를 남긴다.

현재 runner(`skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh`)는 다음 순서로 실행된다.

1. `discord_openclaw_bridge.traveler_scout`
2. `discord_openclaw_bridge.traveler_source_discovery`
3. `discord_openclaw_bridge.post_traveler_collection_report`

따라서 신규 탐색 불능을 운영 로그로 판별할 때는 단순히 최종 report exit=0만 보면 부족하다. 아래 조합을 함께 봐야 한다.

- scout status: `requests_created`, `requests_skipped_existing`, `skipped_existing_topics`, `request_ids`
- source discovery status: `requests_seen`, `requests_processed`, `reviewed_count`, `accepted_count`, `error_count`, `provider_results[].error_kind`
- report status: `candidate_count`, `miner_request_state`, `miner_request_item_count`

특히 `traveler-scout done (exit=0)`이면서 `requests_created=0`과 `requests_skipped_existing>0`이 반복되면, 신규 탐색이 성공한 것이 아니라 기존 pending row에 의해 억제된 상태일 수 있다.

## 2. 코드 경로 관점

### 2.1 scout 단계: pending topic이 신규 request 생성을 막음

`skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/traveler_scout.py`

- `_pending_scout_topic_ids()`는 research queue에서 `status == "pending_deep_research"`이고 `discovery_mode == "autonomous_scout"`인 row의 `scout_topic_id`를 수집한다.
- `create_scout_requests(..., skip_existing_pending=True)`는 해당 topic id가 pending set에 있으면 새 request를 만들지 않고 `skipped_existing`에 넣는다.
- 이 정책은 정상 중복 방지에는 맞지만, discovery가 한 번 멈추거나 오래된 pending row가 남으면 같은 topic의 신규 탐색을 무기한 막을 수 있다.

고착 시나리오:

1. scout가 topic A를 `pending_deep_research`로 enqueue한다.
2. discovery가 cron 누락, provider threshold 실패, 예외, 또는 상태 파일 문제로 해당 row를 완료 상태로 바꾸지 못한다.
3. 다음 날 scout는 topic A를 `requests_skipped_existing`으로 분류하고 새 request를 만들지 않는다.
4. report 단계는 새 evidence-backed candidate가 없으면 `신규 추가 수집 후보 없음`을 정상 보고처럼 게시할 수 있다.

### 2.2 source discovery 단계: threshold/fallback 조건

`skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/traveler_source_discovery.py`

- 기본 처리량은 `JIPHYEONJEON_TRAVELER_DISCOVERY_MAX_REQUESTS`이며 기본값은 `3`이다.
- pending request를 처리한 뒤 request별로 `completed_source_discovery`, `completed_no_candidates`, `failed_source_discovery` 중 하나로 마킹한다.
- `request_reviewed < min_sources_to_review`이면 원칙적으로 실패 처리한다.
- 다만 deep research가 켜져 있고 retryable provider error(rate limit/network 등)와 evidence-backed static candidate가 있으면 fallback을 허용한다.

남은 리스크:

- retryable error가 아니라 parse/unexpected error가 섞이면 fallback을 막는 현재 정책은 안전하지만, 운영 관점에서는 accepted=0 반복의 원인이 될 수 있다.
- `max_requests=3`과 scout topic 수가 늘어나면 앞쪽 pending이 오래 머물 때 뒤쪽 topic 처리 지연이 생긴다. 현재 테스트는 완료된 요청 이후 다음 run에서 later topic이 처리되는지 확인하지만, **stale pending 자체를 aged-out하거나 별도 경고로 승격하는 테스트는 없다.**

### 2.3 report 단계: fetched evidence만 보고 후보로 사용

`skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_traveler_collection_report.py`

- source queue와 scout queue를 합쳐 읽는다.
- seed/intake/review/approved export와 URL/host 중복 비교 후 `보류`는 제외한다.
- `build_report_items()`는 `evidence.status == "fetched"`가 아닌 후보를 제외한다.

이 정책은 저품질 후보 게시를 막는 안전장치다. 그러나 upstream discovery가 evidence fetch에 실패하거나 stale pending 때문에 새 후보를 못 만들면 최종 보고는 `신규 추가 수집 후보 없음`으로 귀결된다. 이때 report만 보면 "탐색 불능"과 "정상적으로 후보 없음"이 구분되지 않는다.

## 3. 테스트 관점

이미 존재하는 방어 테스트:

- `test_scout_skips_existing_pending_topic`: 같은 pending topic 중복 enqueue 방지.
- `test_scout_requests_do_not_starve_later_topics_across_daily_runs`: 완료된 scout 요청 후 다음 run에서 뒤 topic까지 처리되는지 확인.
- `test_discovery_requires_many_sources_before_recording`: 최소 검토 출처 수 미달 시 기록 차단.
- `test_discovery_allows_evidence_backed_static_fallback_when_network_providers_rate_limit`: provider rate limit + static/evidence fallback 허용.
- `test_discovery_does_not_fallback_for_parse_provider_errors`, `test_discovery_does_not_fallback_when_retryable_and_parse_errors_mix`: 안전하지 않은 fallback 차단.
- `test_traveler_runner_uses_runtime_scout_topics`: runner/installer/stable wrapper가 runtime scout topics를 참조하는지 확인.

부족한 테스트:

- stale `pending_deep_research` row가 일정 시간 이상 오래되면 scout status에 명시 경고를 남기는지.
- stale pending이 신규 topic 생성을 무기한 막지 않도록 TTL, stale duplicate budget, 또는 forced enqueue 정책이 동작하는지.
- 최종 report가 `candidate_count=0`일 때 upstream scout/discovery 상태 요약을 포함해 "정상 무후보"와 "탐색 불능 가능성"을 구분하는지.

## 4. 수정 후보

### 후보 A — stale pending 진단 필드 추가 (가장 작고 안전)

- 위치: `traveler_scout.py`
- 방식:
  - `_pending_scout_topic_ids()`를 topic id set만 반환하지 말고 pending row의 `created_at`, `request_id`도 함께 반환하도록 확장한다.
  - `create_scout_requests()` status에 `stale_pending_topics`와 `oldest_pending_age_hours`를 기록한다.
  - 기본 임계값 예: `JIPHYEONJEON_TRAVELER_SCOUT_STALE_PENDING_HOURS=36`.
- 장점: queue mutation 정책을 바꾸지 않아 안전하다.
- 단점: 신규 탐색 불능을 자동 해소하지는 않는다.

### 후보 B — stale pending topic은 새 scout request를 1개까지 허용

- 위치: `traveler_scout.py`
- 방식:
  - pending row가 stale threshold를 넘으면 같은 topic이라도 새 request를 생성한다.
  - status에는 `requests_created_due_to_stale_pending`을 남긴다.
- 장점: 신규 탐색 불능을 직접 완화한다.
- 리스크: discovery가 계속 실패하면 중복 pending이 누적될 수 있으므로 topic별 stale override는 하루 1개 등 budget이 필요하다.

### 후보 C — discovery status 실패/무후보를 report에 요약

- 위치: `post_traveler_collection_report.py`
- 방식:
  - `traveler-source-discovery-last-status.json`과 `traveler-scout-last-status.json`을 읽어 운영 메모에 upstream 상태를 요약한다.
  - `candidate_count=0`일 때 `requests_skipped_existing>0` 또는 `error_count>0`이면 "신규 후보 없음" 대신 "탐색 제한/점검 필요" 문구를 포함한다.
- 장점: Discord 운영자가 false green을 덜 보게 된다.
- 단점: report schema와 snapshot path 의존성이 늘어난다.

### 후보 D — guard/audit issue로 승격

- 위치: `guard_ops.py` 또는 `audit_ops.py`
- 방식:
  - scout/discovery/report status를 함께 읽어 `traveler_discovery_stalled` 같은 warning을 낸다.
  - 조건 예: 최근 2회 이상 `requests_created=0 && requests_skipped_existing>0 && accepted_count=0` 또는 source discovery `error_count>0 && accepted_count=0`.
- 장점: daily report 자체를 바꾸지 않고 운영 알림으로 분리 가능하다.
- 단점: audit snapshot 수집 경로가 먼저 안정화되어야 한다.

## 5. 권장 순서

1. 후보 A로 stale pending 진단을 먼저 추가한다.
2. 운영 로그에서 실제 stale pending이 반복되는지 확인한다.
3. 반복이 확인되면 후보 C 또는 D로 false-green 보고를 줄인다.
4. 그래도 신규 탐색 공백이 지속되면 후보 B를 budget 제한과 함께 적용한다.

## 6. 확인 명령

로컬 정적/단위 검증:

```bash
python -m pytest skills/discord-openclaw-bridge/project/tests/test_traveler_scout.py \
  skills/discord-openclaw-bridge/project/tests/test_traveler_source_discovery.py \
  skills/discord-openclaw-bridge/project/tests/test_post_traveler_collection_report.py
python -m unittest tests.test_runtime_manifests
bash -n scripts/traveler-collection-report.sh
bash -n skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh
bash -n skills/discord-openclaw-bridge/install-traveler-collection-report-cron.sh
```

운영 로그/상태 점검:

```bash
tail -80 ~/.openclaw/workspace/logs/traveler-collection-report.log
jq . ~/.openclaw/workspace/state/traveler-scout-last-status.json
jq . ~/.openclaw/workspace/state/traveler-source-discovery-last-status.json
jq . ~/.openclaw/workspace/state/traveler-collection-report-last-status.json
```
