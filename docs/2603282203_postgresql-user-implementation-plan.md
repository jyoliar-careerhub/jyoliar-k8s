# CareerHub PostgreSQL 단일 유저 + Database CRD 구현 계획

**작성일**: 2026-03-28 22:03 (KST)
**최종 수정일**: 2026-03-28 22:03 (KST)
**대상 서비스**: careerhub-postgresql (CloudNativePG)

---

## Context

CareerHub PostgreSQL 클러스터에 애플리케이션 유저를 추가한다. CNPG의 CRUD 권한 선언적 관리 부재로 인해, migrator/app 분리 대신 **단일 유저 `auth-app`를 DB owner로** 설정하여 DDL과 CRUD를 모두 수행하도록 한다. `managed.roles`로 유저를, `Database` CRD로 데이터베이스를 선언적으로 관리한다.

## 변경 대상 파일

### 새로 생성

1. **`gitops/base/services/careerhub/postgresql/password.yaml`**
   - Password Generator + ExternalSecret (MongoDB의 `db-user-passwords.yaml` 패턴)
   - `kubernetes.io/basic-auth` 타입, `cnpg.io/reload: "true"` 라벨
   - sync-wave: `-1` (비밀번호 먼저 생성)

2. **`gitops/base/services/careerhub/postgresql/database.yaml`**
   - `kind: Database` CRD
   - `spec.name: auth`, `spec.owner: auth-app`
   - `spec.cluster.name: careerhub-postgresql`
   - `databaseReclaimPolicy: retain`

### 수정

3. **`gitops/base/services/careerhub/postgresql/cluster.yaml`**
   - `spec.managed.roles` 추가: `auth-app` 유저 (login, passwordSecret 참조)

4. **`gitops/base/services/careerhub/postgresql/kustomization.yaml`**
   - resources에 `password.yaml`, `database.yaml` 추가

### 변경 없음

- `pooler.yaml`, overlay 파일들 — 수정 불필요
- ApplicationSet — phase 구조 변경 불필요 (PostgreSQL은 이미 phase 1)

## 상세 구현

### 1. password.yaml

MongoDB와의 차이: ExternalSecret에 `target.template` 사용 → `basic-auth` 타입 + `cnpg.io/reload` 라벨 + `username` 필드 포함.

```yaml
apiVersion: generators.external-secrets.io/v1alpha1
kind: Password
metadata:
  name: auth-app-pg-password
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
spec:
  length: 32
  digits: 8
  symbols: 0
  noUpper: false
  allowRepeat: true
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: auth-app-pg-password
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
spec:
  refreshPolicy: CreatedOnce
  target:
    name: auth-app-pg-password
    template:
      type: kubernetes.io/basic-auth
      metadata:
        labels:
          cnpg.io/reload: "true"
      data:
        username: auth-app
        password: "{{ .password }}"
  dataFrom:
    - sourceRef:
        generatorRef:
          apiVersion: generators.external-secrets.io/v1alpha1
          kind: Password
          name: auth-app-pg-password
```

### 2. database.yaml

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: careerhub-auth-db
spec:
  name: auth
  owner: auth-app
  cluster:
    name: careerhub-postgresql
  databaseReclaimPolicy: retain
```

### 3. cluster.yaml 수정

기존 내용 끝에 `managed.roles` 추가:

```yaml
  managed:
    roles:
      - name: auth-app
        ensure: present
        login: true
        passwordSecret:
          name: auth-app-pg-password
```

### 4. kustomization.yaml 수정

`password.yaml`을 맨 위에 배치 (sync-wave로 순서 제어되지만 가독성을 위해).

```yaml
resources:
  - password.yaml
  - cluster.yaml
  - database.yaml
  - pooler.yaml
```

## 배포 순서 (ArgoCD가 자동 관리)

1. **wave -1**: Password Generator + ExternalSecret → K8s Secret 생성
2. **wave 0**: Cluster (managed.roles가 Secret 참조 → `auth-app` role 생성)
3. **wave 0**: Database CRD (owner `auth-app` 참조 → DB 생성, role 미존재 시 재시도)

## 검증

```bash
# 1. Operator 업그레이드 확인
kubectl get deployment -n cnpg-system --kubeconfig .kube/baremetal.config

# 2. Secret 생성 확인
kubectl get secret auth-app-pg-password -n careerhub-prod --kubeconfig .kube/baremetal.config

# 3. Cluster에 role 생성 확인
kubectl get cluster careerhub-postgresql -n careerhub-prod \
  -o jsonpath='{.status.managedRolesStatus}' --kubeconfig .kube/baremetal.config

# 4. Database CRD 상태 확인
kubectl get database careerhub-auth-db -n careerhub-prod \
  -o jsonpath='{.status}' --kubeconfig .kube/baremetal.config

# 5. PostgreSQL 접속 테스트
kubectl exec -it careerhub-postgresql-1 -n careerhub-prod \
  --kubeconfig .kube/baremetal.config -- psql -U auth-app -d auth -c '\conninfo'
```
