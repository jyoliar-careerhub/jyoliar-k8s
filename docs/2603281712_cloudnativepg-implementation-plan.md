# CloudNativePG 구현 계획

**작성일**: 2026-03-28 17:12 (KST)
**최종 수정일**: 2026-03-28 17:12 (KST)
**대상 서비스**: CareerHub (신규 서비스용 PostgreSQL)

---

## 배경

CareerHub에 새로운 서비스를 추가하기 위해 PostgreSQL이 필요하다. CloudNativePG Operator와 PostgreSQL Cluster의 base 매니페스트를 작성한다.

### 범위

- Operator 및 PostgreSQL Cluster base 매니페스트 작성
- overlay는 환경 구축 시 별도 작업으로 추가
- 백업 구성은 차후 작업으로 미룸

### 주요 결정사항

| 항목 | 결정 |
|------|------|
| PostgreSQL 버전 | 18.3 (최신 stable) |
| Operator Chart 버전 | 0.23.0 |
| 인스턴스 수 | 2 (Primary 1 + Replica 1) — overlay에서 설정 |
| 스토리지 | PGDATA 10Gi, WAL 2Gi — overlay에서 설정 |
| 리소스 (CPU/Memory) | 추후 결정 — overlay에서 설정 |
| 네임스페이스 | Operator: `cnpg-system`, Cluster: `careerhub-prod` |
| PgBouncer Pooler | 필수 포함 (Failover 투명성) |
| 백업 | 차후 작업 |

---

## 생성 파일 목록

```
gitops/base/controllers/cloudnativepg/
├── kustomization.yaml
└── application.yaml        # ArgoCD Application (Helm chart 참조)

gitops/base/services/careerhub/postgresql/
├── kustomization.yaml
├── cluster.yaml             # Cluster CRD
└── pooler.yaml              # Pooler CRD (PgBouncer)
```

총 5개 파일 신규 생성. 기존 파일 수정 없음.

---

## Part A: CloudNativePG Operator

경로: `gitops/base/controllers/cloudnativepg/`

### application.yaml

`prometheus-operator/application.yaml` 패턴을 따르는 ArgoCD Application.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: cloudnativepg-helm
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  destination:
    namespace: cnpg-system
    server: https://kubernetes.default.svc
  project: controllers
  source:
    chart: cloudnative-pg
    repoURL: https://cloudnative-pg.github.io/charts
    targetRevision: 0.23.0
    helm:
      releaseName: cnpg
  syncPolicy:
    syncOptions:
      - ServerSideApply=true
      - CreateNamespace=true
    automated:
      prune: true
      selfHeal: true
```

### kustomization.yaml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - application.yaml
```

---

## Part B: PostgreSQL Cluster + Pooler

경로: `gitops/base/services/careerhub/postgresql/`

### cluster.yaml

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: careerhub-postgresql
spec:
  instances: OVERRIDE_REQUIRED
  imageName: ghcr.io/cloudnative-pg/postgresql:18.3

  storage:
    size: OVERRIDE_REQUIRED
    storageClass: longhorn

  walStorage:
    size: OVERRIDE_REQUIRED
    storageClass: longhorn

  resources: OVERRIDE_REQUIRED

  affinity:
    enablePodAntiAffinity: true
    topologyKey: kubernetes.io/hostname
    nodeAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          preference:
            matchExpressions:
              - key: node.longhorn.io/create-default-disk
                operator: In
                values:
                  - "true"

  monitoring:
    enablePodMonitor: true
```

### pooler.yaml

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Pooler
metadata:
  name: careerhub-postgresql-pooler
spec:
  cluster:
    name: careerhub-postgresql
  instances: OVERRIDE_REQUIRED
  type: rw
  pgbouncer:
    poolMode: transaction
```

### kustomization.yaml

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - cluster.yaml
  - pooler.yaml
```

---

## overlay 적용 시 참고사항

환경 구축 시 overlay에서 수행할 작업:

### controllers overlay

1. `controllers-appproj.yaml`의 `sourceRepos`에 추가:
   ```yaml
   - https://cloudnative-pg.github.io/charts
   ```

2. `controllers-appset.yaml`의 `generators.list.elements`에 추가:
   ```yaml
   - name: cloudnativepg
     path: gitops/overlays/{환경명}/controllers/cloudnativepg
     namespace: cnpg-system
   ```

3. overlay 디렉토리 생성:
   ```
   gitops/overlays/{환경명}/controllers/cloudnativepg/
   └── kustomization.yaml    # base 참조
   ```

### services overlay

1. Cluster patch (환경별 값 설정):
   ```yaml
   apiVersion: postgresql.cnpg.io/v1
   kind: Cluster
   metadata:
     name: careerhub-postgresql
   spec:
     instances: 2
     storage:
       size: 10Gi
     walStorage:
       size: 2Gi
     resources:
       requests:
         cpu: "{추후 결정}"
         memory: "{추후 결정}"
       limits:
         cpu: "{추후 결정}"
         memory: "{추후 결정}"
   ```

2. Pooler patch:
   ```yaml
   apiVersion: postgresql.cnpg.io/v1
   kind: Pooler
   metadata:
     name: careerhub-postgresql-pooler
   spec:
     instances: 1
   ```

---

## 검증

```bash
# 개별 빌드 검증
kubectl kustomize gitops/base/controllers/cloudnativepg/
kubectl kustomize gitops/base/services/careerhub/postgresql/
```

두 명령 모두 유효한 YAML을 출력하면 성공. base에는 `OVERRIDE_REQUIRED` 값이 남아 있으므로 실제 배포는 overlay를 통해서만 가능.
