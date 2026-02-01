# Longhorn 설치 가이드

Longhorn은 Kubernetes를 위한 경량 분산 블록 스토리지 시스템입니다.

## 개요

| 항목 | 내용 |
|------|------|
| 공식 사이트 | https://longhorn.io |
| GitHub | https://github.com/longhorn/longhorn |
| Helm Chart | https://github.com/longhorn/charts |
| 설치 버전 | v1.7.2 |

### Ceph vs Longhorn 비교

| 항목 | Rook-Ceph | Longhorn |
|------|-----------|----------|
| 복잡도 | 높음 | 낮음 |
| 리소스 사용량 | 높음 (MON, MGR, OSD 등) | 낮음 |
| 최소 노드 수 | 3개 권장 | 1개 (HA: 2개 이상) |
| 디스크 접근 방식 | 원시 디스크 직접 사용 | 파일시스템 경로 사용 |
| 디스크 준비 | 마운트 불필요 (OSD가 관리) | 사전 마운트 필요 |
| 블록 스토리지 | RBD | 자체 구현 |
| 파일 스토리지 | CephFS | NFS (RWX) |
| 오브젝트 스토리지 | RGW (S3 호환) | 미지원 |
| UI | Ceph Dashboard | Longhorn UI |

## 노드 요구사항

### 필수 패키지

각 노드에 다음 패키지가 설치되어 있어야 합니다.

**Debian/Ubuntu:**
```bash
sudo apt-get update
sudo apt-get install -y open-iscsi nfs-common
sudo systemctl enable iscsid --now
```

**RHEL/CentOS/Amazon Linux:**
```bash
sudo yum install -y iscsi-initiator-utils nfs-utils
sudo systemctl enable iscsid --now
```

### 시스템 요구사항

- Kubernetes >= v1.25
- 컨테이너 런타임: Docker v1.13+, containerd v1.3.7+
- 파일시스템: ext4 또는 XFS
- 필수 유틸리티: `bash`, `curl`, `findmnt`, `grep`, `awk`, `blkid`, `lsblk`
- Mount propagation 활성화

### 스토리지 노드 디스크 준비

Longhorn은 파일시스템 경로를 사용하므로 **디스크가 미리 마운트**되어 있어야 합니다.

```bash
# 스토리지 노드에서 실행
sudo mkfs.ext4 /dev/nvme1n1
sudo mkdir -p /var/lib/longhorn
sudo mount /dev/nvme1n1 /var/lib/longhorn
echo '/dev/nvme1n1 /var/lib/longhorn ext4 defaults 0 0' | sudo tee -a /etc/fstab
```

## 프로젝트 구조

이 프로젝트에서는 Helm dependency를 사용하여 Longhorn을 관리합니다.

```
platform/base/longhorn/
├── Chart.yaml      # Longhorn chart를 dependency로 참조
├── Chart.lock      # 버전 고정 (git에 포함)
├── values.yaml     # 설정 오버라이드
├── .gitignore      # charts/ 폴더 제외
├── charts/         # 다운로드된 chart (git 제외)
└── templates/
    └── .gitkeep
```

### Chart.yaml

```yaml
apiVersion: v2
name: longhorn-wrapper
description: Longhorn distributed storage for Kubernetes
type: application
version: 0.1.0
appVersion: "1.7.2"

dependencies:
  - name: longhorn
    version: 1.7.2
    repository: https://charts.longhorn.io
```

### values.yaml

```yaml
longhorn:
  persistence:
    defaultClass: true
    defaultClassReplicaCount: 2
    defaultFsType: ext4
    reclaimPolicy: Delete

  defaultSettings:
    defaultReplicaCount: 2
    defaultDataPath: /var/lib/longhorn
    defaultDataLocality: best-effort
    storageOverProvisioningPercentage: 200
    storageMinimalAvailablePercentage: 15
    replicaAutoBalance: best-effort
    upgradeChecker: false
    nodeDownPodDeletionPolicy: delete-both-statefulset-and-deployment-pod
    # 레이블이 있는 노드에만 기본 디스크 생성
    createDefaultDiskLabeledNodes: true
```

## 설치 방법

### 1. 스토리지 노드 레이블 지정

스토리지로 사용할 노드에 레이블을 추가합니다 (Ansible에서 처리 권장):

```bash
kubectl label node <storage-node-1> node.longhorn.io/create-default-disk=true
kubectl label node <storage-node-2> node.longhorn.io/create-default-disk=true
```

### 2. Helm dependency 다운로드

```bash
cd platform/base/longhorn
helm dependency update
```

### 3. 설치

```bash
helm install longhorn . -n longhorn-system --create-namespace
```

### 4. 설치 확인

```bash
# Pod 상태 확인
kubectl get pods -n longhorn-system

# StorageClass 확인
kubectl get storageclass

# 노드별 스토리지 상태 확인
kubectl get nodes.longhorn.io -n longhorn-system
```

## 스토리지 노드 관리

### 레이블 기반 스토리지 노드 지정

| 설정 | 위치 | 역할 |
|------|------|------|
| `node.longhorn.io/create-default-disk=true` | Kubernetes 노드 레이블 | 스토리지 노드 지정 |
| `createDefaultDiskLabeledNodes: true` | Helm values | 레이블 있는 노드만 스토리지로 사용 |

**중요**: 노드 레이블은 Ansible에서 관리합니다 (인프라 레이어).

### 노드 스케줄링 수동 제어

```bash
# 스케줄링 비활성화
kubectl patch nodes.longhorn.io <node-name> -n longhorn-system \
  --type merge -p '{"spec":{"allowScheduling":false}}'

# 스케줄링 활성화
kubectl patch nodes.longhorn.io <node-name> -n longhorn-system \
  --type merge -p '{"spec":{"allowScheduling":true}}'
```

## 주요 설정 옵션

### Data Locality 모드

| 모드 | 설명 |
|------|------|
| `disabled` | 로컬 복제본 보장 안함 (기본값) |
| `best-effort` | 가능하면 로컬 복제본 유지, 실패 시 다른 노드 사용 |
| `strict-local` | 단일 로컬 복제본 강제, 높은 I/O 성능, HA 없음 |

### Replica Auto Balance 모드

| 모드 | 설명 |
|------|------|
| `disabled` | 자동 밸런싱 비활성화 |
| `least-effort` | 새 복제본만 밸런싱 |
| `best-effort` | 기존 복제본도 재배치 (권장) |

## PVC 사용 예시

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-pvc
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: longhorn
  resources:
    requests:
      storage: 10Gi
---
apiVersion: v1
kind: Pod
metadata:
  name: my-pod
spec:
  containers:
    - name: app
      image: nginx
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: my-pvc
```

## Longhorn UI 접근

```bash
kubectl port-forward -n longhorn-system svc/longhorn-frontend 8080:80
# 브라우저에서 http://localhost:8080 접근
```

## 업그레이드

```bash
cd platform/base/longhorn
helm dependency update  # 새 버전 다운로드
helm upgrade longhorn . -n longhorn-system
```

## 삭제

```bash
helm uninstall longhorn -n longhorn-system
kubectl delete namespace longhorn-system
```

## 참고 자료

- [Longhorn 공식 문서](https://longhorn.io/docs/)
- [Longhorn Helm Chart](https://github.com/longhorn/charts)
- [설치 요구사항](https://longhorn.io/docs/1.7.2/deploy/install/)
