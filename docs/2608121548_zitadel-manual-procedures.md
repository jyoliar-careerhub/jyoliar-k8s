# Zitadel 초기 구성 — 수동 잔여 항목 절차 기록

**작성일**: 2026-08-12 15:48 (KST)
**최종 수정일**: 2026-08-27 21:40 (KST)
**대상 서비스**: zitadel, auth-service

---

Zitadel 내부 구성은 원칙적으로 구성 Job(`gitops/base/services/careerhub/apps/zitadel/config/`)이 선언으로 수렴시킨다.
이 문서는 Job에 담기 부적합해 **수동으로 남긴 항목**의 절차 기록이다(ops-workflow#55 Requirements).
어떤 항목이 수동으로 남았는지는 티켓 코멘트에도 명시된다.

## 1. Google OAuth client에 Zitadel 콜백 URI 추가 — 1회성, 완료(2026-08-12)

Google client는 GCP Console 수동 생성물이라 IaC 밖이다(`jyoliar-platform-terraform/oauth/variables.tf` 주석 참조).
Zitadel(IdP 템플릿 + login v2 세대)이 쓰는 콜백은 다음 하나다:

```
https://id.careerhub.jyo-liar.com/idps/callback
```

> 정본은 Console의 해당 IdP 상세 화면에 표시되는 콜백 URI다 — 등록 전 대조한다.
> (구세대 login v1의 `/ui/login/login/externalidp/callback`은 이 배포와 무관하다.)

절차: GCP Console → APIs & Services → Credentials → 해당 OAuth 2.0 Client → Authorized redirect URIs에 위 URI 추가.
재구축 시에도 client를 재사용하는 한 다시 할 필요가 없다.
client를 새로 만들면 기존 사용자의 Google 동의·식별에는 영향이 없으나(Google sub는 public subject — 계정 귀속 불변), SSM의 `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` 갱신과 이 절차의 재수행이 필요하다.

## 2. 기존 사용자 이전 — 1회성 (사용자 1명)

### 실제로 밟은 경로 (2026-08-12) — 자동 생성 + 원장 `zitadel_sub` 기입

당초 계획은 "Zitadel 사용자 pre-create + Google IdP 연결에 기존 sub 심기"였으나, **기존 사용자가 브로커 등록 직후 기존 Google 계정으로 첫 로그인을 해 Zitadel이 사용자를 자동 생성**했다(브로커 선언의 `isAutoCreation: true`).
Google sub 연결은 자동 생성 시점에 보존되므로(브로커가 IdP 연결로 저장), pre-create는 불필요해졌다 — Google이 public subject라 어느 경로든 같은 계정은 같은 sub로 식별된다(배치 실측 discussion `260811_1452` 판정 ②).

남는 수동 단계는 **원장(auth DB)에 Zitadel userId를 기입**하는 것 하나다.
발급 훅의 원장 조회 키가 `user.zitadel_sub` 컬럼이라(auth-service 마이그레이션 `00003`), 이 기입이 있어야 훅이 클레임을 싣는다.
(당초 이 기입을 정체성 Feature ops-workflow#47 관할로 봤으나, 기존 사용자 1명분은 이 티켓에서 수행하기로 판정 — 2026-08-12, 사람.)

```bash
# ① Zitadel userId 확인 — Console → Users → 해당 사용자 상세의 ID
#    (또는 로그인 후 발급 토큰의 sub 클레임)

# ② 원장 기입 — careerhub-postgresql의 auth DB에서
UPDATE "user" SET zitadel_sub = '<ZITADEL_USER_ID>'
WHERE authorized_by = 'GOOGLE' AND status = 'ACTIVE'
RETURNING user_id, email, zitadel_sub;
-- 기존 GOOGLE 사용자가 1명이라는 전제의 WHERE다. RETURNING이 1행이어야 한다.
```

### 검증

1. 시크릿 창에서 careerhub-web 클라이언트의 OIDC 플로우로 로그인해 토큰을 발급받는다(로그인 UI 단독 세션으로는 훅이 발화하지 않는다 — 발급 시점 트리거).
2. 액세스 토큰(JWT)에 `careerhub:authorities`·`careerhub:agreements_fulfilled` 클레임이 실렸는지 확인.
3. 토큰 `sub` = 기입한 Zitadel userId인지 확인.

### 만약 pre-create가 다시 필요해지면 (참고 — 재구축 등으로 자동 생성 전에 이전해야 할 때)

`POST /v2/users/human`에 `idpLinks: [{idpId: <Google IdP id>, userId: <Google sub>, userName: <email>}]`을 실어 생성하면 첫 로그인 전에 연결을 심을 수 있다.
Google sub는 원장의 `authorized_id`(`SELECT email, authorized_id FROM "user" WHERE authorized_by = 'GOOGLE'`), IdP id는 구성 Job 로그 또는 `/admin/v1/idps/templates/_search`로 확인한다.

### 재구축 시

Zitadel DB를 재초기화하면 이 절차도 되살아난다 — 사용자 원장은 Zitadel DB 안이다.
(백업 경로: PostgreSQL 덤프 + masterkey 보존, ADR-0030 Consequences.)

## 3. 앱 자격증명 Secret — 자동 관리와 회전 절차 (참고)

앱 자격증명(client_id·client_secret)의 k8s Secret 기록은 **수동 항목이 아니다** — 선언(`zitadel-config.yaml`)의 `credentialsSecret`을 구성 Job이 자동으로 채운다.
여기 적는 것은 유출·회전 시의 수동 트리거뿐이다.

- client_id는 비밀이 아니고 언제든 관리 API로 재조회 가능하다. client_secret(secret 인증 앱에만 존재 — careerhub-web은 ops-workflow#75에서 confidential(`WEB`+`BASIC`)로 전환해 존재한다)은 생성·재발급 응답에 **1회만 노출**되고 조회 API가 없다 — 그래서 Job이 그 시점에 포착해 Secret으로 남긴다.
- **회전(유출 대응)**: 해당 k8s Secret에서 `client_secret` 키를 지우고(또는 Secret 자체를 지우고) Job을 재실행하면 된다 — Job은 선언된 자격증명 Secret이 없거나 **선언이 요구하는 키를 갖추지 못했으면** checksum 무변경이어도 재수렴하고, secret 인증 앱이면 `_generate_client_secret`로 재발급해(기존 secret 즉시 무효화) Secret에 다시 싣는다.

```bash
# 키만 회전 — client_id는 유지되어 소비자의 다른 설정을 건드리지 않는다
kubectl patch secret zitadel-app-careerhub-web -n careerhub-prod \
  --type=json -p '[{"op":"remove","path":"/data/client_secret"}]'
# 혹은 통째로: kubectl delete secret zitadel-app-careerhub-web -n careerhub-prod
# 그다음 ArgoCD에서 zitadel app을 sync(PostSync hook Job 재실행) — 또는 다음 배포를 기다린다.
```

- Secret 삭제 없이 재발급만 하고 싶다면 관리 API를 직접 불러도 되지만(회전 코멘트의 PAT 절차 참조), 그 경우 응답의 새 secret을 k8s Secret에 손으로 반영해야 한다 — 키 삭제+재실행 경로가 더 안전하다.

### client_secret을 개발기로 옮기기 — 수동 (ops-workflow#75)

careerhub-web은 아직 어디에도 배포돼 있지 않아 로그인 검증처가 localhost 개발기뿐이다.
public client일 때는 개발기가 client_id만 알면 됐지만, confidential이 된 뒤로는 **클러스터 Secret의 값을 개발기로 옮기는 단계**가 생긴다. 값을 아는 것은 사람뿐이어야 하므로(agent의 ServiceAccount는 Secret 읽기가 RBAC로 거부된다) 이 이관은 수동이다.

```bash
kubectl -n careerhub-prod get secret zitadel-app-careerhub-web \
  -o jsonpath='{.data.client_secret}' | base64 -d
```

값은 careerhub-web의 `.env.local`에 `ZITADEL_CLIENT_ID`·`ZITADEL_CLIENT_SECRET`으로 넣는다(`src/lib/auth/config.ts`가 읽는 이름 — secret이 있으면 교환·갱신이 Basic 인증으로 전환된다).
값이 대화·터미널 로그를 거쳤다면 위 회전 절차로 즉시 무효화하는 편이 싸다.

### public → confidential 전환 시 앱 재생성이 필요한가 — 필요 없음 (2026-08-27 확인)

`appType`·`authMethodType` 변경은 기존 앱의 in-place 갱신으로 처리된다. 스크립트가 기존 앱에 이미 보내는 `PUT .../oidc_config`(UpdateOIDCApplication)가 두 필드를 변경 이벤트에 그대로 싣고, 입력 검증은 response type ↔ grant type 정합만 본다 — appType·authMethodType 조합은 compliance **경고**로만 응답에 실릴 뿐 거부 사유가 아니다(Zitadel v4.15.3 `internal/command/project_application_oidc.go` · `internal/domain/application_oidc.go`).
전환 직후 k8s Secret에는 `client_secret` 키가 없으므로 위 재발급 경로가 이어서 발화한다.

> 만약 어떤 이유로 PUT이 거부된다면 재생성 경로로 간다: Zitadel Console에서 해당 앱을 지우고 Job을 재실행하면 스크립트의 생성 분기가 새 앱을 만들고 생성 응답의 client_secret을 포착한다. **client_id가 바뀌므로** 소비자(개발기 `.env.local` 등)의 client_id도 함께 갱신해야 한다.
