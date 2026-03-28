# CloudNativePG 도입 검토

**작성일**: 2026-03-28 16:31 (KST)
**최종 수정일**: 2026-03-28 16:31 (KST)
**대상 서비스**: CareerHub (향후 PostgreSQL이 필요한 서비스)

---

## 1. 개요

| 항목 | 내용 |
|------|------|
| 공식 사이트 | https://cloudnative-pg.io |
| GitHub | https://github.com/cloudnative-pg/cloudnative-pg |
| Helm Chart | https://cloudnative-pg.github.io/charts |
| 최신 안정 버전 | v1.28.1 (2026-02-05) |
| 라이선스 | Apache License 2.0 |
| CNCF 상태 | Sandbox (2025-01 승인), Incubation 신청 중 |

CloudNativePG는 Kubernetes에서 PostgreSQL 클러스터의 전체 생명주기를 관리하는 오퍼레이터입니다. EDB(EnterpriseDB)가 개발했으며, Primary/Standby 아키텍처 기반의 고가용성 PostgreSQL을 선언적으로 관리합니다.

### 핵심 특징

- **Kubernetes 네이티브**: Patroni, repmgr 등 외부 HA 도구 없이 Kubernetes API와 직접 통합
- **StatefulSet 미사용**: PVC를 직접 관리하여 더 세밀한 제어 가능
- **자동 Failover/Switchover**: Primary 장애 시 자동으로 Standby를 승격
- **내장 백업**: S3 호환 오브젝트 스토리지로 WAL 아카이빙 및 PITR 지원
- **내장 모니터링**: Prometheus 메트릭 엔드포인트 기본 제공
- **멀티 아키텍처**: amd64, arm64 이미지 지원

## 2. 아키텍처

```
┌─────────────────────────────────────┐
│         cnpg-system namespace       │
│  ┌─────────────────────────────┐    │
│  │  cnpg-controller-manager    │    │
│  │  (Operator Deployment)      │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
            │ watches
            ▼
┌─────────────────────────────────────┐
│      application namespace          │
│                                     │
│  ┌──────────┐  streaming   ┌──────────┐
│  │ Primary  │ ──repl────▶ │ Standby  │
│  │ (rw)     │              │ (ro)     │
│  └──────────┘              └──────────┘
│       │                         │
│  ┌──────────┐            ┌──────────┐
│  │ PVC      │            │ PVC      │
│  │ (PGDATA) │            │ (PGDATA) │
│  └──────────┘            └──────────┘
│                                     │
│  ┌──────────────────────────────┐   │
│  │ Pooler (PgBouncer)           │   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
            │ WAL archiving
            ▼
      ┌──────────┐
      │ S3 Bucket│
      └──────────┘
```

각 PostgreSQL Pod 내부에는 **Instance Manager**가 사이드카로 동작하며, 오퍼레이터의 지시에 따라 인스턴스 생명주기를 관리합니다.

## 3. 주요 CRD

모든 CRD는 API 그룹 `postgresql.cnpg.io/v1`에 속합니다.

| CRD | 설명 |
|-----|------|
| **Cluster** | PostgreSQL 클러스터 정의 (Primary + Replica, 스토리지, 리소스, PostgreSQL 설정) |
| **Pooler** | PgBouncer 커넥션 풀링 설정 |
| **Backup** | 일회성 백업 요청 |
| **ScheduledBackup** | cron 스케줄 기반 정기 백업 |
| **Database** | PostgreSQL 데이터베이스 선언적 관리 |
| **ClusterImageCatalog** | 클러스터 범위 PostgreSQL 컨테이너 이미지 카탈로그 |
| **ImageCatalog** | 네임스페이스 범위 이미지 카탈로그 |

## 4. 고가용성

### Failover 프로세스

1. **Phase 1 (Shutdown)**: Primary 장애 감지 → Controller가 Primary 종료 표시 → Replica의 WAL receiver가 정상 중지
2. **Phase 2 (Promotion)**: Replica 중 리더 선출 → 선택된 인스턴스를 Primary로 승격 → 이전 Primary는 Replica로 재시작

### Quorum 기반 Failover (v1.28 stable)

동기 복제 환경에서 Dynamo 일관성 모델 적용: `R + W > N` 조건을 충족해야 승격 진행. 조건 미충족 시 데이터 손실 방지를 위해 대기.

### 설정 옵션

| 설정 | 설명 | 기본값 |
|------|------|--------|
| `spec.failoverDelay` | Failover 시작 전 대기 시간 | 0초 |
| `spec.postgresql.synchronous.failoverQuorum` | Quorum 기반 Failover 활성화 | false |
| `spec.minSyncReplicas` | 최소 동기 복제본 수 | 0 |
| `spec.maxSyncReplicas` | 최대 동기 복제본 수 | 0 |

## 5. 백업 및 복구

### 백업 방식

| 방식 | 설명 | 권장 여부 |
|------|------|-----------|
| **Plugin (Barman Cloud)** | v1.26+ 권장. 오브젝트 스토리지(S3/GCS/Azure)로 백업 | 권장 |
| **Volume Snapshots** | Kubernetes CSI 스냅샷 활용. Hot/Cold 백업 지원 | 보조 수단 |
| **Legacy Barman Object Store** | v1.26부터 deprecated | 비권장 |

### WAL 아카이빙

- Primary에서 오브젝트 스토리지로 지속적 WAL 전송
- 기본 `archive_timeout`: 5분 (최소 5분 간격 아카이빙)
- RPO: 최대 5분
- **중요**: WAL 아카이빙만으로는 복구 불가. 반드시 물리적 base backup과 함께 구성해야 함

### PITR (Point-in-Time Recovery)

Base backup + WAL 재생으로 특정 시점 복구 가능. Cluster 스펙의 recovery bootstrap 모드로 구성.

### ScheduledBackup 예시

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata:
  name: daily-backup
spec:
  schedule: "0 0 0 * * *"   # 매일 자정
  backupOwnerReference: self
  cluster:
    name: my-cluster
  method: plugin
```

## 6. 모니터링

### 내장 Prometheus 메트릭

| 대상 | 포트 | 엔드포인트 |
|------|------|-----------|
| PostgreSQL 인스턴스 | 9187 | `/metrics` |
| Operator | 8080 | `/metrics` |

### 기본 메트릭 항목

- WAL 파일 통계 (수, 크기, 세그먼트)
- 아카이브 상태
- 동기 복제 상태
- 백업 타임스탬프 및 복구 지점
- 클러스터 복제 모드

### 프로젝트 통합 방법

현재 프로젝트에서 prometheus-operator를 사용 중이므로, PodMonitor를 추가하면 자동 수집 가능:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: cnpg-cluster
spec:
  selector:
    matchLabels:
      cnpg.io/cluster: my-cluster
  podMetricsEndpoints:
    - port: metrics
```

Grafana 대시보드: https://grafana.com/grafana/dashboards/20417-cloudnativepg/

## 7. 스토리지 고려사항

### PVC 구성

| 항목 | 설명 |
|------|------|
| `spec.storage` | PGDATA 볼륨 (필수) |
| `spec.walStorage` | WAL 전용 볼륨 (선택, 프로덕션 권장) |

### WAL 별도 볼륨의 장점

- PGDATA와 WAL 간 I/O 분리
- PGDATA 볼륨 고갈이 WAL에 영향을 주지 않음
- 각각 다른 StorageClass 적용 가능
- **주의**: 생성 후 WAL 스토리지 제거 불가

### 프로젝트 적용 시

현재 Longhorn을 기본 StorageClass로 사용 중. CloudNativePG는 모든 StorageClass와 호환됩니다. Longhorn의 `allowVolumeExpansion`이 활성화되어 있으므로 온라인 볼륨 확장도 가능합니다.

**권장**: 프로덕션 배포 전 `fio`(스토리지 벤치마크) + `pgbench`(PostgreSQL 벤치마크)로 성능 검증.

## 8. 리소스 요구사항

### 최소/권장 사양

| 항목 | 최소값 | 프로덕션 권장 |
|------|--------|-------------|
| CPU request | 50m | 요구사항에 따라 설정 (request = limit) |
| CPU limit | 100m | 요구사항에 따라 설정 (request = limit) |
| Memory request | 32Mi | 요구사항에 따라 설정 (request = limit) |
| Memory limit | 128Mi | 요구사항에 따라 설정 (request = limit) |

### 프로덕션 권장사항

- **Guaranteed QoS**: request와 limit을 동일하게 설정
- `shared_buffers`는 Pod 메모리의 약 25%로 설정
- Guaranteed QoS 시 CloudNativePG가 자동으로 `PG_OOM_ADJUST_VALUE=0` 설정 (OOM Kill 방어)
- `nodeSelector` 또는 전용 노드 풀 사용 권장

## 9. 버전 호환성

### 지원 매트릭스 (2026-03 기준)

| Operator 버전 | 릴리스일 | EOL | Kubernetes | PostgreSQL |
|--------------|---------|-----|------------|------------|
| **1.28.x** (latest) | 2025-12-09 | ~2026-06 | 1.32, 1.33, 1.34 | 14 - 18 |
| **1.27.x** | 2025-08-12 | 2026-03-09 | 1.31, 1.32, 1.33 | 13 - 18 |

### 릴리스 주기

- 마이너 버전: 약 2~3개월 간격
- 1.29.0 RC1: 2026-03-13 릴리스 (Extension 관리 기능 추가)

## 10. 대안 비교

| 항목 | CloudNativePG | Zalando Postgres Operator | CrunchyData PGO |
|------|---------------|--------------------------|------------------|
| **CNCF** | Sandbox | 없음 | 없음 |
| **HA 메커니즘** | Kubernetes API 직접 통합 | Patroni + Spilo | Patroni |
| **외부 의존성** | 없음 | Patroni, Spilo | Patroni, pgBackRest |
| **백업 도구** | Barman Cloud + Volume Snapshots | WAL-E / WAL-G | pgBackRest |
| **커넥션 풀링** | PgBouncer (Pooler CRD) | 외부 구성 필요 | PgBouncer (통합) |
| **StatefulSet** | 미사용 (PVC 직접 관리) | 사용 | 사용 |
| **설계 철학** | Kubernetes 네이티브 | Zalando 내부 패턴 기반 | 에코시스템 통합 |
| **상용 지원** | EDB | 없음 | Crunchy Data |
| **GitHub Stars** | ~4,300+ | ~4,000+ | ~3,700+ |

### CloudNativePG 선택 근거

- 외부 HA 도구(Patroni) 없이 Kubernetes와 직접 통합 → 운영 복잡도 감소
- CNCF 프로젝트로서의 거버넌스 및 커뮤니티 성장세
- 멀티 아키텍처(arm64) 지원 — 현재 프로젝트의 arm64 노드 환경과 호환
- 활발한 개발 (2025년 880+ 커밋, 132M+ 다운로드)

## 11. 알려진 제한사항

### Major Version 업그레이드

- **전체 클러스터 다운타임 필요** (Primary + 모든 Replica 중단)
- 업그레이드 후 Replica는 `pg_basebackup`으로 재생성 (대규모 DB에서 시간 소요)
- 동일 OS 배포판 간 업그레이드만 지원 (예: bookworm → bookworm)

### 아키텍처 제한

- Multi-master / Active-Active 미지원 (Primary/Standby 전용)
- Cluster 리소스는 단일 네임스페이스에 종속
- 생성 후 WAL 스토리지(`spec.walStorage`) 제거 불가

### 논리 복제 제한

- DDL (CREATE TABLE, ALTER TABLE) 복제 안됨
- Sequence 복제 안됨
- Large Object (LOB) 제외

## 12. 프로젝트 적용 계획

현재 프로젝트 구조를 기반으로 한 CloudNativePG 도입 방안입니다.

### 디렉토리 구조 (안)

```
gitops/base/
├── controllers/
│   └── cloudnativepg/          # Operator 설치 (ArgoCD Application + Helm)
│       ├── kustomization.yaml
│       └── application.yaml    # Helm chart를 source로 참조하는 ArgoCD App
└── services/
    └── {서비스명}/
        └── postgresql/          # PostgreSQL Cluster 인스턴스
            ├── kustomization.yaml
            ├── cluster.yaml     # Cluster CRD
            ├── pooler.yaml      # PgBouncer Pooler (Failover 투명성을 위해 필수)
            └── scheduled-backup.yaml  # 정기 백업 (선택)
```

### Operator 설치 방식

prometheus-operator와 동일한 패턴으로, **ArgoCD Application에서 Helm chart를 직접 참조**합니다.

```yaml
# application.yaml
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

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  - application.yaml
```

### Cluster 리소스 예시 (base)

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: OVERRIDE_REQUIRED
spec:
  instances: OVERRIDE_REQUIRED
  imageName: ghcr.io/cloudnative-pg/postgresql:17

  postgresql:
    parameters:
      shared_buffers: "256MB"
      effective_cache_size: "768MB"

  storage:
    size: OVERRIDE_REQUIRED
    storageClass: longhorn

  walStorage:
    size: OVERRIDE_REQUIRED
    storageClass: longhorn

  resources:
    requests:
      cpu: OVERRIDE_REQUIRED
      memory: OVERRIDE_REQUIRED
    limits:
      cpu: OVERRIDE_REQUIRED
      memory: OVERRIDE_REQUIRED

  affinity:
    nodeSelector:
      kubernetes.io/arch: arm64
    enablePodAntiAffinity: true
    topologyKey: kubernetes.io/hostname

  monitoring:
    enablePodMonitor: true
```

### ApplicationSet 배포 순서

기존 CareerHub 배포와 동일한 패턴으로, phase 기반 순서 제어:

1. **Phase 1**: CloudNativePG Operator (controllers)
2. **Phase 2**: PostgreSQL Cluster + Secrets (services)
3. **Phase 3**: Application Deployments (services/apps)

### Secret 관리

CloudNativePG는 Cluster 생성 시 아래 Secret을 **자동 생성**합니다:

| Secret 이름 | 내용 |
|-------------|------|
| `{cluster-name}-app` | 애플리케이션용 접속 정보 (user, password, host, port, dbname, uri 등) |
| `{cluster-name}-superuser` | superuser 접속 정보 (enableSuperuserAccess 활성화 시) |

애플리케이션은 이 자동 생성된 Secret을 직접 참조하면 되므로, **ExternalSecret이 불필요**합니다. 이는 비밀번호를 ExternalSecret으로 별도 관리하는 현재 MongoDB 패턴과 다른 점입니다.

```yaml
# 애플리케이션에서의 참조 예시
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: my-cluster-app
        key: uri
```

## 13. 참고 자료

- [CloudNativePG 공식 문서 v1.28](https://cloudnative-pg.io/docs/1.28/)
- [CloudNativePG Helm Charts](https://cloudnative-pg.github.io/charts)
- [CloudNativePG GitHub](https://github.com/cloudnative-pg/cloudnative-pg)
- [CloudNativePG Grafana Dashboard](https://grafana.com/grafana/dashboards/20417-cloudnativepg/)
- [CNCF CloudNativePG 페이지](https://www.cncf.io/projects/cloudnativepg/)
