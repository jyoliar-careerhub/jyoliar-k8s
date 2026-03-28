# PostgreSQL 유저 관리 방안 브레인스토밍

**작성일**: 2026-03-28 21:32 (KST)
**최종 수정일**: 2026-03-28 22:00 (KST)
**대상 서비스**: careerhub-postgresql (CloudNativePG)

---

## 요약

MongoDB의 `posting-app`(readWrite) / `posting-migrator`(dbOwner) 패턴을 PostgreSQL에 적용하기 위한 방안을 비교 분석한다. CloudNativePG(CNPG)는 `spec.managed.roles`, `bootstrap.initdb`, 그리고 `Database` CRD 세 가지 메커니즘을 제공하며, 이를 조합하는 방식에 따라 6가지 옵션이 도출된다.

## 가정 및 제약

- **CNPG Operator**: Helm chart v0.23.0 (operator v1.25.x 추정)
- **PostgreSQL**: 18.3
- **기존 클러스터 상태**: `bootstrap.initdb` 미설정, `managed.roles` 미설정
- **비밀번호 관리**: External Secrets Operator + Password Generator 패턴 사용 중 (MongoDB와 동일)
- **목표 유저**:
  | 유저 | 역할 | PostgreSQL 권한 |
  |------|------|----------------|
  | schema-deployer (migrator) | DDL 실행, 스키마 마이그레이션 | DB owner 또는 CREATE/ALTER/DROP |
  | app-user | CRUD 전용 | SELECT, INSERT, UPDATE, DELETE |

## 현재 상태 분석

### MongoDB 패턴 (참조)
```
posting-migrator → dbOwner role → 스키마 변경 전권
posting-app      → readWrite role → 데이터 CRUD만
```
- MongoDBCommunity CRD 내 `spec.users[]`에서 선언적으로 관리
- Password Generator + ExternalSecret으로 비밀번호 자동 생성
- 연결 문자열은 Operator가 Secret으로 자동 생성

### PostgreSQL 현재 상태
- `cluster.yaml`: `managed.roles` 없음, `bootstrap` 없음
- CNPG 기본 동작: `postgres` superuser만 존재
- Pooler가 `rw` 모드로 설정되어 있으나 전용 유저 미정의

---

## 옵션 비교

### 옵션 A: bootstrap.initdb owner + managed.roles (권장)

CNPG의 두 메커니즘을 모두 활용하는 방식.

**구조:**
- `bootstrap.initdb.owner` → migrator 유저 (DB 소유자 = DDL 전권)
- `spec.managed.roles` → app 유저 (CRUD 전용)
- `bootstrap.initdb.postInitApplicationSQL` → app 유저에게 GRANT

```yaml
spec:
  bootstrap:
    initdb:
      database: careerhub
      owner: careerhub-migrator
      secret:
        name: careerhub-migrator-password
      postInitApplicationSQL:
        - CREATE ROLE careerhub_app LOGIN
        - GRANT CONNECT ON DATABASE careerhub TO careerhub_app
        - GRANT USAGE ON SCHEMA public TO careerhub_app
        - GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO careerhub_app
        - ALTER DEFAULT PRIVILEGES FOR ROLE careerhub_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO careerhub_app
        - GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO careerhub_app
        - ALTER DEFAULT PRIVILEGES FOR ROLE careerhub_migrator IN SCHEMA public GRANT USAGE ON SEQUENCES TO careerhub_app

  managed:
    roles:
      - name: careerhub-app
        ensure: present
        login: true
        superuser: false
        passwordSecret:
          name: careerhub-app-password
```

**장점:**
- CNPG의 설계 의도에 가장 부합 (owner = DDL 유저, managed role = 제한된 유저)
- migrator는 DB owner로서 CREATE TABLE, ALTER TABLE 등 DDL 전권 보유
- app 유저는 managed role로 선언적 관리 (비밀번호 교체, 속성 변경 자동 반영)
- `ALTER DEFAULT PRIVILEGES`로 향후 생성되는 테이블에도 자동으로 권한 부여

**단점:**
- `bootstrap.initdb`는 **최초 클러스터 생성 시에만 실행** — 기존 클러스터에 적용 불가
- postInitApplicationSQL의 GRANT는 1회성 → 이후 스키마 변경 시 권한 갭 발생 가능
- owner 유저의 비밀번호는 `bootstrap.initdb.secret`으로만 설정 (managed.roles와 별개 관리)

**적합한 상황:** 새 클러스터를 구축하거나 기존 클러스터를 재생성할 수 있는 경우

**리스크 완화:**
- `ALTER DEFAULT PRIVILEGES`를 사용하면 migrator가 생성하는 새 테이블에 자동 GRANT
- 복잡한 SQL은 `postInitApplicationSQLRefs`로 ConfigMap/Secret에서 참조 가능

---

### 옵션 B: managed.roles 2개 + postInitApplicationSQL

두 유저 모두 managed.roles로 선언하고, 권한은 bootstrap SQL로 설정.

```yaml
spec:
  bootstrap:
    initdb:
      database: careerhub
      owner: careerhub-migrator
      secret:
        name: careerhub-migrator-password
      postInitApplicationSQL:
        - GRANT CONNECT ON DATABASE careerhub TO careerhub_app
        - GRANT USAGE ON SCHEMA public TO careerhub_app
        - GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO careerhub_app
        - ALTER DEFAULT PRIVILEGES FOR ROLE careerhub_migrator IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO careerhub_app
        - GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO careerhub_app
        - ALTER DEFAULT PRIVILEGES FOR ROLE careerhub_migrator IN SCHEMA public GRANT USAGE ON SEQUENCES TO careerhub_app

  managed:
    roles:
      - name: careerhub-migrator
        ensure: present
        login: true
        superuser: false
        createdb: false
        passwordSecret:
          name: careerhub-migrator-password
      - name: careerhub-app
        ensure: present
        login: true
        superuser: false
        passwordSecret:
          name: careerhub-app-password
```

**장점:**
- 두 유저 모두 managed.roles로 관리 → 비밀번호 교체, 속성 변경 등 일관된 lifecycle
- 역할 정의가 CRD에 집중되어 가시성 높음

**단점:**
- `bootstrap.initdb.owner`와 `managed.roles`의 migrator가 **같은 이름**일 때 충돌 가능성
  - CNPG 문서: "Role creation will occur after the database bootstrapping is complete"
  - owner는 bootstrap에서 생성, managed.roles는 이후 reconcile → 같은 이름이면 managed.roles가 인수
- owner의 secret과 managed.roles의 passwordSecret이 **같은 Secret을 가리켜야** 일관성 유지
- bootstrap SQL은 여전히 1회성

**적합한 상황:** migrator 유저의 비밀번호도 managed.roles로 교체 관리하고 싶은 경우

**리스크 완화:**
- owner와 managed.roles에서 동일한 Secret을 참조하도록 설정
- CNPG 공식 문서에서 이 패턴을 명시적으로 권장하지는 않으므로 테스트 필요

---

### 옵션 C: managed.roles 2개만 (bootstrap 없음, 수동 GRANT)

Cluster CRD에 bootstrap 없이 managed.roles만 선언하고, GRANT는 migration job에서 처리.

```yaml
spec:
  managed:
    roles:
      - name: careerhub-migrator
        ensure: present
        login: true
        superuser: false
        passwordSecret:
          name: careerhub-migrator-password
      - name: careerhub-app
        ensure: present
        login: true
        superuser: false
        passwordSecret:
          name: careerhub-app-password
```

Migration job (ArgoCD PreSync hook)에서:
```sql
-- postgres superuser로 실행
GRANT ALL PRIVILEGES ON DATABASE careerhub TO "careerhub-migrator";
GRANT CONNECT ON DATABASE careerhub TO "careerhub-app";
-- ... 이하 GRANT 문
```

**장점:**
- **기존 클러스터에 즉시 적용 가능** (재생성 불필요)
- managed.roles만 추가하면 CNPG가 유저 생성/관리
- GRANT 로직을 migration tool (Flyway, golang-migrate 등)에 통합 → 앱 코드와 함께 버전 관리

**단점:**
- bootstrap을 사용하지 않으므로 DB 이름과 owner가 CNPG 기본값(`app` DB, `app` owner)
- migrator에게 DDL 권한을 부여하려면 postgres superuser로 GRANT 실행 필요
  - managed.roles는 role 속성만 관리, **object-level GRANT는 관리하지 않음**
- 권한 설정이 CRD 밖에 분산됨

**적합한 상황:** 기존 운영 중인 클러스터에 유저를 추가해야 하는 경우

**리스크 완화:**
- migration job을 ArgoCD PreSync hook으로 설정하면 앱 배포 전 권한 보장
- GRANT 문을 idempotent하게 작성 (`IF NOT EXISTS` 등)

---

### 옵션 D: bootstrap.initdb만 (managed.roles 없음)

bootstrap SQL에서 두 유저 모두 생성하고 관리.

```yaml
spec:
  bootstrap:
    initdb:
      database: careerhub
      owner: careerhub-migrator
      secret:
        name: careerhub-migrator-password
      postInitApplicationSQL:
        - CREATE ROLE "careerhub-app" LOGIN PASSWORD 'PLACEHOLDER'
        - GRANT CONNECT ON DATABASE careerhub TO "careerhub-app"
        - GRANT USAGE ON SCHEMA public TO "careerhub-app"
        - GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "careerhub-app"
        - ALTER DEFAULT PRIVILEGES FOR ROLE "careerhub-migrator" IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "careerhub-app"
```

**장점:**
- 간단한 설정, 한 곳에서 모든 초기 설정 완료
- 추가 리소스(managed.roles) 불필요

**단점:**
- app 유저 비밀번호가 **YAML에 하드코딩**되거나 postInitApplicationSQLRefs로 우회 필요 → 보안 위험
- 비밀번호 교체 불가 (bootstrap은 1회성)
- app 유저의 lifecycle을 CNPG가 관리하지 않음 (속성 drift 감지/복구 없음)
- CNPG 공식 문서에서 비권장하는 패턴

**적합한 상황:** 해당 없음 (비권장)

---

### 옵션 E: Pooler 유저 분리 (비주류 옵션)

PgBouncer Pooler를 유저별로 분리하여 접근 경로 자체를 격리.

```yaml
# migrator 전용 pooler (또는 direct 접속)
apiVersion: postgresql.cnpg.io/v1
kind: Pooler
metadata:
  name: careerhub-postgresql-pooler-migrator
spec:
  cluster:
    name: careerhub-postgresql
  instances: 1
  type: rw
  pgbouncer:
    poolMode: session  # DDL에는 session mode 필요

# app 전용 pooler
apiVersion: postgresql.cnpg.io/v1
kind: Pooler
metadata:
  name: careerhub-postgresql-pooler-app
spec:
  cluster:
    name: careerhub-postgresql
  instances: 1
  type: rw
  pgbouncer:
    poolMode: transaction
```

**장점:**
- 네트워크 레벨에서 접근 분리
- migrator는 session mode (DDL 호환), app은 transaction mode (성능 최적)
- Pooler별 연결 제한으로 리소스 격리

**단점:**
- Pooler는 **인증이 아닌 연결 관리** 도구 → 권한 분리의 본질적 해결책이 아님
- 유저 생성/권한 설정은 여전히 별도로 필요 (다른 옵션과 조합 필수)
- 관리 복잡도 증가 (Pooler 리소스 2개)

**적합한 상황:** 다른 옵션과 조합하여 연결 모드까지 최적화하고 싶은 경우

---

### 옵션 F: Database CRD + managed.roles (신규 권장)

CNPG 1.25에서 도입된 `Database` CRD를 활용하는 방식. **별도의 Kubernetes 리소스**로 데이터베이스를 선언적으로 관리한다.

> **전제조건**: CNPG Operator 1.25+ 필요. Helm chart v0.23.0 = operator 1.25.x이므로 사용 가능.

**구조:**
- `spec.managed.roles` (Cluster CRD) → migrator, app 두 유저 선언
- `Database` CRD → 데이터베이스 소유자 및 스키마 소유자 선언적 관리
- GRANT → migration job(PreSync hook)에서 테이블 수준 권한 설정

```yaml
# 1. Cluster CRD에 managed.roles 추가
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: careerhub-postgresql
spec:
  # ... 기존 설정 유지 ...
  managed:
    roles:
      - name: careerhub-migrator
        ensure: present
        login: true
        superuser: false
        createdb: false
        passwordSecret:
          name: careerhub-migrator-password
      - name: careerhub-app
        ensure: present
        login: true
        superuser: false
        passwordSecret:
          name: careerhub-app-password
```

```yaml
# 2. Database CRD (별도 리소스)
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: careerhub-db
spec:
  name: careerhub
  owner: careerhub-migrator          # migrator가 DB 소유자
  cluster:
    name: careerhub-postgresql
  databaseReclaimPolicy: retain
  schemas:
    - name: public
      owner: careerhub-migrator      # migrator가 스키마 소유자
      ensure: present
```

```sql
-- 3. Migration job (ArgoCD PreSync hook)에서 GRANT 실행
-- migrator로 접속하여 실행 (DB owner이므로 GRANT 권한 있음)
GRANT CONNECT ON DATABASE careerhub TO "careerhub-app";
GRANT USAGE ON SCHEMA public TO "careerhub-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "careerhub-app";
ALTER DEFAULT PRIVILEGES FOR ROLE "careerhub-migrator" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "careerhub-app";
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "careerhub-app";
ALTER DEFAULT PRIVILEGES FOR ROLE "careerhub-migrator" IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO "careerhub-app";
```

**장점:**
- **기존 클러스터에 즉시 적용 가능** — bootstrap 재실행 불필요
- DB 소유권 + 스키마 소유권이 **선언적이고 지속적으로 reconcile** (bootstrap의 1회성 한계 해소)
- 유저 lifecycle도 managed.roles로 관리 (비밀번호 교체, 속성 drift 자동 복구)
- Database CRD는 별도 K8s 리소스 → GitOps에서 독립적으로 관리 가능
- `databaseReclaimPolicy: retain`으로 실수로 리소스 삭제해도 DB 보존
- 향후 extension, FDW도 같은 CRD에서 선언적 관리 가능

**단점:**
- 테이블 수준 GRANT는 여전히 CRD 밖에서 처리 필요 (migration job)
  - 단, `ALTER DEFAULT PRIVILEGES`로 향후 테이블에도 자동 적용되므로 최초 1회만 실행하면 됨
- CNPG 1.25 이상 필요 (chart 0.23.0은 해당)
- Database CRD와 Cluster CRD 간 배포 순서 의존성 (Cluster → managed.roles → Database CRD)
  - ArgoCD sync wave 또는 phase로 제어 가능

**적합한 상황:** 신규/기존 클러스터 모두. 가장 선언적이고 GitOps-native한 접근.

**옵션 A와의 핵심 차이:**
| 측면 | 옵션 A (bootstrap) | 옵션 F (Database CRD) |
|------|-------|-------|
| DB 소유자 설정 | bootstrap.initdb.owner (1회성) | Database CRD spec.owner (**지속 reconcile**) |
| 스키마 소유자 | postInitApplicationSQL (1회성) | Database CRD spec.schemas (**지속 reconcile**) |
| 기존 클러스터 적용 | X | **O** |
| migrator 비밀번호 관리 | bootstrap.secret (별도) | managed.roles.passwordSecret (**통합**) |

**리스크 완화:**
- 배포 순서: ArgoCD ApplicationSet의 phase/wave로 Cluster → Database CRD 순서 보장
- GRANT 누락: migration job에 `ALTER DEFAULT PRIVILEGES` 포함으로 자동화
- Database CRD가 Cluster보다 먼저 적용되는 경우: CNPG가 Cluster 준비 완료까지 대기 후 reconcile

---

## 옵션 간 트레이드오프 비교

| 기준 | A (bootstrap+managed) | B (managed 2개+bootstrap) | C (managed만) | D (bootstrap만) | E (Pooler 분리) | **F (Database CRD+managed)** |
|------|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|
| 기존 클러스터 적용 | X | X | **O** | X | O (조합) | **O** |
| 새 클러스터 적합 | **O** | O | O | X | O (조합) | **O** |
| 비밀번호 자동 교체 | app만 | **둘 다** | **둘 다** | X | - | **둘 다** |
| DB/스키마 소유권 선언적 | 1회성 | 1회성 | X | 1회성 | X | **지속 reconcile** |
| 테이블 GRANT 선언적 | 1회성 | 1회성 | X | 1회성 | X | migration job |
| 보안 수준 | **높음** | 높음 | 보통 | 낮음 | 높음 (조합) | **높음** |
| 구현 복잡도 | 보통 | 높음 | **낮음** | 낮음 | 높음 | 보통 |
| CNPG 공식 권장 | O | 부분적 | O | X | O | **O** |

## 상황별 추천

### 신규/기존 클러스터 모두 (최우선 권장)
**옵션 F (Database CRD + managed.roles)** 권장

- 기존 클러스터에도 즉시 적용 가능 (bootstrap 재실행 불필요)
- DB 소유권 + 스키마 소유권이 **지속적으로 reconcile** (1회성이 아님)
- 유저 lifecycle도 managed.roles로 통합 관리
- 테이블 수준 GRANT만 migration job에서 처리 (`ALTER DEFAULT PRIVILEGES`로 자동화)

### 새 클러스터 + bootstrap을 선호하는 경우
**옵션 A (bootstrap.initdb owner + managed.roles)**

- CNPG 전통적 설계 의도에 가장 부합
- 한 번의 bootstrap으로 초기 설정 완료
- 단, DB 재생성이 어려운 환경에서는 옵션 F가 유리

### 장기적으로 가장 견고한 구성
**옵션 F + 옵션 E 조합**

- 유저 권한 분리 + 연결 모드 최적화
- migrator: direct 접속 또는 session mode pooler
- app: transaction mode pooler

## 비밀번호 관리 방안

기존 MongoDB 패턴을 그대로 적용 가능:

```yaml
# 1. Password Generator
apiVersion: generators.external-secrets.io/v1alpha1
kind: Password
metadata:
  name: careerhub-app-password-gen
spec:
  length: 32
  digits: 8
  symbols: 0
  allowRepeat: true

# 2. ExternalSecret → Kubernetes Secret (basic-auth 타입)
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: careerhub-app-password
spec:
  refreshPolicy: CreatedOnce
  target:
    name: careerhub-app-password
    template:
      type: kubernetes.io/basic-auth
      metadata:
        labels:
          cnpg.io/reload: "true"
      data:
        username: careerhub-app
        password: "{{ .password }}"
  dataFrom:
    - sourceRef:
        generatorRef:
          apiVersion: generators.external-secrets.io/v1alpha1
          kind: Password
          name: careerhub-app-password-gen
```

**핵심 차이점 (MongoDB vs PostgreSQL):**
- MongoDB: Operator가 connectionString Secret 자동 생성
- PostgreSQL (CNPG): `basic-auth` 타입 Secret 필요, `cnpg.io/reload: "true"` 라벨 필수
- Pooler 사용 시 연결 endpoint는 Pooler Service 이름으로 접근

## 최종 결론

Database CRD(v1.28)를 포함해 CNPG의 모든 선언적 메커니즘을 조사한 결과, **테이블 수준 CRUD 권한(SELECT, INSERT, UPDATE, DELETE)을 선언적으로 부여하는 기능은 존재하지 않았다.** Database CRD가 제공하는 것은 DB/스키마의 소유권 설정까지이며, 비소유자 role에 대한 세분화된 권한 부여는 반드시 SQL GRANT로 처리해야 한다.

migrator/app 유저를 분리하면 GRANT 관리 부담이 추가되는데, 단일 DB 수준의 규모에서는 과도한 복잡도다. 따라서 **유저를 하나만 두고 DB owner로 설정하여 스키마 배포(DDL)와 앱 실행(CRUD) 모두 수행하도록 결정했다.**

### 채택 구성

- **Database CRD**: DB 소유자를 단일 유저로 설정, 스키마 소유권 선언적 관리
- **Cluster CRD `managed.roles`**: 단일 유저 선언 (비밀번호 lifecycle 관리)
- **별도 GRANT 불필요**: DB owner이므로 모든 DDL/DML 권한 보유

```yaml
# Cluster CRD
spec:
  managed:
    roles:
      - name: careerhub
        ensure: present
        login: true
        passwordSecret:
          name: careerhub-password

# Database CRD
spec:
  name: careerhub
  owner: careerhub
  cluster:
    name: careerhub-postgresql
```

## 참고 자료

- [CloudNativePG Declarative Role Management (v1.28)](https://cloudnative-pg.io/docs/1.28/declarative_role_management/)
- [CloudNativePG Declarative Database Management (v1.28)](https://cloudnative-pg.io/docs/1.28/declarative_database_management/) — Database CRD 공식 문서
- [CloudNativePG Bootstrap Documentation (v1.28)](https://cloudnative-pg.io/docs/1.28/bootstrap/)
- [Safe Application Users - Best Practice (GitHub Discussion #2856)](https://github.com/cloudnative-pg/cloudnative-pg/discussions/2856)
- [CloudNativePG User Management Guide](https://oneuptime.com/blog/post/2026-01-21-cloudnativepg-user-management/view)
- [CloudNativePG 1.25 Release Notes](https://www.postgresql.org/about/news/cloudnativepg-1250-and-1242-released-2985/) — Database CRD 도입 버전
