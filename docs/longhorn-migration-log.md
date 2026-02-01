# Rook-Ceph → Longhorn 마이그레이션 로그

## 작업 일시

2026-02-01

## 작업 개요

Rook-Ceph 스토리지 클러스터를 제거하고 Longhorn으로 교체

## 변경 사항

### 이전 구성 (Rook-Ceph)

| 항목 | 값 |
|------|-----|
| 스토리지 | Rook-Ceph v1.18.9 |
| 스토리지 노드 | 3대 (104, 105, 106) |
| 디스크 접근 | 원시 디스크 직접 사용 |
| 디스크 | nvme1n1 (104, 105), nvme0n1 (106) |

### 현재 구성 (Longhorn)

| 항목 | 값 |
|------|-----|
| 스토리지 | Longhorn v1.7.2 |
| 스토리지 노드 | 2대 (104, 105) |
| 디스크 접근 | 파일시스템 경로 (/var/lib/longhorn) |
| Replica 수 | 2 |
| 디스크 용량 | ~98GB per node |

## 완료된 작업

### 1. Rook-Ceph 삭제

```bash
# CephCluster 삭제
kubectl delete cephcluster rook-ceph -n rook-ceph

# Finalizer 제거 (강제 삭제)
kubectl patch cephcluster rook-ceph -n rook-ceph --type merge -p '{"metadata":{"finalizers":[]}}'

# 네임스페이스 삭제
kubectl delete namespace rook-ceph

# Kustomize로 CRD 및 ClusterRole 삭제
kubectl delete -k platform/base/rook
```

### 2. Longhorn Helm Chart 구성

```
platform/base/longhorn/
├── Chart.yaml      # Longhorn 1.7.2 dependency
├── Chart.lock      # 버전 고정
├── values.yaml     # 설정 (replica: 2, createDefaultDiskLabeledNodes: true)
├── .gitignore      # charts/ 제외
└── templates/
    └── .gitkeep
```

### 3. Longhorn 설치

```bash
helm dependency update platform/base/longhorn
helm install longhorn platform/base/longhorn -n longhorn-system --create-namespace
```

### 4. 스토리지 노드 설정

```bash
# 스토리지 노드 레이블
kubectl label node ip-10-0-25-104 node.longhorn.io/create-default-disk=true
kubectl label node ip-10-0-25-105 node.longhorn.io/create-default-disk=true

# 106 노드 스케줄링 비활성화
kubectl patch nodes.longhorn.io ip-10-0-25-106 -n longhorn-system \
  --type merge -p '{"spec":{"allowScheduling":false}}'
```

### 5. 테스트 완료

- PVC 생성 → Pod A에서 파일 쓰기 → Pod B에서 파일 읽기 ✓

## 현재 상태

### Longhorn 노드

| 노드 | 스케줄링 | 용량 | 레이블 |
|------|----------|------|--------|
| ip-10-0-25-104 | true | ~98GB | O |
| ip-10-0-25-105 | true | ~98GB | O |
| ip-10-0-25-106 | false | - | X |

### StorageClass

```
NAME                 PROVISIONER          RECLAIMPOLICY   DEFAULT
longhorn             driver.longhorn.io   Delete          true
longhorn-static      driver.longhorn.io   Delete          false
```

## 남은 작업

### Ansible에서 처리 필요

1. **nfs-common 패키지 설치** (RWX 지원용)
   - 현재 노드에서 `Missing packages: [nfs-common]` 경고 발생

2. **스토리지 노드 레이블 자동화**
   - `node.longhorn.io/create-default-disk=true` 레이블을 스토리지 노드에 자동 추가

3. **디스크 마운트 자동화** (신규 클러스터 시)
   - `/var/lib/longhorn` 경로에 데이터 디스크 마운트
   - `/etc/fstab` 등록

## 참고

- [Longhorn 설치 가이드](./longhorn-installation-guide.md)
- [Ansible 작업 요청서](./ansible-longhorn-tasks.md)
