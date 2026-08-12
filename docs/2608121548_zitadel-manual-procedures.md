# Zitadel 초기 구성 — 수동 잔여 항목 절차 기록

**작성일**: 2026-08-12 15:48 (KST)
**최종 수정일**: 2026-08-12 15:48 (KST)
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

기존 사용자를 Zitadel에 만들고 Google IdP 연결에 **기존 Google sub를 심는다**.
이렇게 하면 해당 사용자가 기존 Google 계정으로 로그인할 때 새 계정이 생기지 않고 이 사용자로 식별된다(배치 실측 discussion `260811_1452` 판정 ②).
앱 DB 측 식별자 치환은 이 절차의 범위 밖이다 — 정체성 Feature(ops-workflow#47) 관할.

### 필요한 원본 값 (사람이 앱 DB에서 취득)

```sql
-- careerhub-postgresql의 auth DB
SELECT email, authorized_id FROM "user" WHERE authorized_by = 'GOOGLE';
```

- `authorized_id` = Google sub (IdP 연결에 심을 값)
- `email` = Zitadel 사용자 프로필 값

### 실행

```bash
NS=careerhub-prod
BASE=https://id.careerhub.jyo-liar.com
PAT=$(kubectl get secret iam-admin-pat -n "$NS" -o jsonpath='{.data.pat}' | base64 -d)

# Google IdP의 Zitadel 내 ID 확인 (구성 Job 로그의 "IdP 생성: Google (google) → <id>"로도 확인 가능)
IDP_ID=$(curl -sS -X POST "$BASE/admin/v1/idps/templates/_search" \
  -H "Authorization: Bearer $PAT" -H "Content-Type: application/json" \
  -d '{"queries":[{"idpNameQuery":{"name":"Google"}}]}' | jq -r '.result[0].id')

# 사용자 생성 + IdP 연결 (치환: EMAIL, GOOGLE_SUB, GIVEN_NAME, FAMILY_NAME)
curl -sS -X POST "$BASE/v2/users/human" \
  -H "Authorization: Bearer $PAT" -H "Content-Type: application/json" \
  -d '{
    "username": "EMAIL",
    "profile": {"givenName": "GIVEN_NAME", "familyName": "FAMILY_NAME"},
    "email": {"email": "EMAIL", "isVerified": true},
    "idpLinks": [{"idpId": "'"$IDP_ID"'", "userId": "GOOGLE_SUB", "userName": "EMAIL"}]
  }'
```

### 검증

1. 시크릿 창에서 `https://id.careerhub.jyo-liar.com/ui/v2/login` → Google로 로그인.
2. Console(Users)에서 새 사용자가 **생기지 않고** 위에서 만든 사용자로 로그인됐는지 확인.
3. 발급 토큰의 `sub`(= Zitadel user ID)가 위 생성 응답의 `userId`와 일치하는지 확인.

### 재구축 시

Zitadel DB를 재초기화하면 이 절차도 되살아난다 — 사용자 원장은 Zitadel DB 안이다.
(백업 경로: PostgreSQL 덤프 + masterkey 보존, ADR-0030 Consequences.)
