# 구성 Job 스크립트의 스모크 테스트

**클러스터에 배포되는 리소스가 아니다.** 이 디렉터리는 어느 kustomization에도 나열되지 않고,
앱 발견도 ApplicationSet list generator의 명시 경로로만 일어난다 — 둘 다 이 경로를 모른다.
쉘의 재실행 checksum도 선언+py+sh만 읽으므로, 테스트 수정은 Job 재실행을 유발하지 않는다.

`apply-zitadel-config.sh` + `apply_zitadel_config.py`의 회귀 안전망이다.
이 스크립트는 실배포에서 세 번 깨졌고(#28 healthz가 200 + 플레인 텍스트 · #29 무변경
갱신이 400 "No changes" · #30 HTTPClient DenyList), 그때 실측으로 배운 API 기벽이
mock 라우트에 박제되어 있다. 스크립트를 고치면 이 테스트를 돌려라.

## 실행

```bash
# 정본 — Job 실이미지에서 (python·PyYAML·jq·bash 버전이 운영과 같다)
docker run --rm -v "$(git rev-parse --show-toplevel):/r" alpine/k8s:1.34.9 \
  python3 /r/gitops/base/services/careerhub/apps/zitadel/config/tests/smoke_test.py

# 로컬 — bash·jq·PyYAML이 있으면 그대로도 돈다
python3 smoke_test.py
```

테스트 러너 자체는 표준 라이브러리만 쓴다. mock Zitadel API 서버와 kubectl shim(임시
디렉터리에 생성)을 띄우고 entrypoint 쉘을 통째로 실행한다 — 단위가 아니라 스크립트
전체의 스모크다.

## 시나리오

1. **생성 경로** — 빈 인스턴스에 IdP·프로젝트·앱·target·execution 생성. 앱 생성 응답의
   clientId가 자격증명 Secret으로 생성되는지, fail-closed(`interruptOnError`)·target id
   매핑이 선언대로인지 검증.
2. **갱신 경로** — 전부 기존 존재. 무변경 갱신의 400 "No changes"(gRPC code 9)를 수렴
   성공으로 삼키는지, 기존 자격증명 Secret에 병합 patch만 하는지, public(NONE) 앱에
   client_secret 재발급을 호출하지 않는지 검증.
3. **재발급 경로** — secret 인증(BASIC) 앱 + k8s Secret에 client_secret 없음.
   `_generate_client_secret` 1회 호출과 patch에 client_secret이 실리는지 검증.
4. **skip·회전 가드** — checksum 일치 + Secret 존재 → API를 대지 않고 적용 생략.
   checksum 일치 + Secret 부재(회전 삭제) → 재수렴해 Secret 재생성.

fixture 선언 2종은 실선언(overlays/.../zitadel-config.yaml)의 축약 고정본이다 —
실선언 변경을 추적할 의무는 없고, 스크립트 로직이 읽는 구조만 유지하면 된다.
