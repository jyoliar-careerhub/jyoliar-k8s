# Zitadel 초기 구성 — 수동 잔여 항목 절차 기록

**작성일**: 2026-08-12 15:48 (KST)
**최종 수정일**: 2026-08-12 17:35 (KST)
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
