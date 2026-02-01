# Ansible 작업 요청서: Longhorn 스토리지 지원

## 개요

Kubernetes 클러스터에서 Longhorn 분산 스토리지를 사용하기 위한 노드 준비 작업입니다.

## 대상 노드

| 역할 | 노드 | 작업 |
|------|------|------|
| 스토리지 노드 | 워커 노드 중 일부 (예: worker-1, worker-2) | 전체 작업 |
| 비스토리지 노드 | 나머지 워커 노드 | 패키지 설치만 |
| 컨트롤 플레인 | 마스터 노드 | 패키지 설치만 |

## 필수 작업

### 1. 패키지 설치 (모든 노드)

Longhorn이 볼륨을 마운트하기 위해 호스트 OS에 필요한 패키지입니다.

**Debian/Ubuntu:**
```yaml
- name: Install Longhorn prerequisites
  apt:
    name:
      - open-iscsi
      - nfs-common
    state: present
    update_cache: yes

- name: Enable and start iscsid
  systemd:
    name: iscsid
    enabled: yes
    state: started
```

**RHEL/CentOS/Amazon Linux:**
```yaml
- name: Install Longhorn prerequisites
  yum:
    name:
      - iscsi-initiator-utils
      - nfs-utils
    state: present

- name: Enable and start iscsid
  systemd:
    name: iscsid
    enabled: yes
    state: started
```

### 2. 디스크 마운트 (스토리지 노드만)

스토리지 노드에 데이터 디스크를 `/var/lib/longhorn`에 마운트합니다.

```yaml
- name: Create filesystem on data disk
  filesystem:
    fstype: ext4
    dev: "{{ longhorn_disk }}"  # 예: /dev/nvme1n1
  when: "'storage' in group_names"

- name: Create Longhorn data directory
  file:
    path: /var/lib/longhorn
    state: directory
    mode: '0755'
  when: "'storage' in group_names"

- name: Mount data disk for Longhorn
  mount:
    path: /var/lib/longhorn
    src: "{{ longhorn_disk }}"
    fstype: ext4
    opts: defaults
    state: mounted
  when: "'storage' in group_names"
```

### 3. 노드 레이블 (스토리지 노드만)

Longhorn이 스토리지 노드를 인식하도록 레이블을 추가합니다.

```yaml
- name: Label storage nodes for Longhorn
  command: >
    kubectl label node {{ inventory_hostname }}
    node.longhorn.io/create-default-disk=true
    --overwrite
  delegate_to: "{{ groups['masters'][0] }}"
  when: "'storage' in group_names"
```

**또는** kubelet 설정에서 노드 레이블 지정:

```yaml
# kubelet 추가 인자
kubelet_extra_args:
  node-labels: "node.longhorn.io/create-default-disk=true"
```

## 변수 정의

```yaml
# group_vars/storage.yml 또는 host_vars
longhorn_disk: /dev/nvme1n1  # 스토리지 디스크 디바이스
```

## 인벤토리 예시

```ini
[masters]
master-1 ansible_host=10.0.25.101

[workers]
worker-1 ansible_host=10.0.25.104
worker-2 ansible_host=10.0.25.105
worker-3 ansible_host=10.0.25.106

[storage]
worker-1
worker-2

[k8s_cluster:children]
masters
workers
```

## 검증

작업 완료 후 확인 사항:

```bash
# 패키지 설치 확인
systemctl status iscsid
dpkg -l | grep -E "open-iscsi|nfs-common"  # Debian/Ubuntu

# 디스크 마운트 확인 (스토리지 노드)
df -h /var/lib/longhorn
cat /etc/fstab | grep longhorn

# 노드 레이블 확인
kubectl get nodes --show-labels | grep longhorn
```

## 참고 사항

1. **디스크 초기화 주의**: `filesystem` 모듈은 기존 파일시스템이 있으면 스킵합니다. 강제 포맷이 필요하면 `force: yes` 옵션 사용

2. **Ceph에서 마이그레이션 시**: 기존 Ceph OSD가 사용하던 디스크는 `wipefs -a /dev/nvme1n1`로 초기화 필요

3. **레이블 관리**: 노드 레이블은 kubelet 설정으로 관리하면 노드 재생성 시에도 유지됩니다

## 관련 문서

- [Longhorn 설치 가이드](./longhorn-installation-guide.md)
- [마이그레이션 로그](./longhorn-migration-log.md)
- [Longhorn 공식 요구사항](https://longhorn.io/docs/1.7.2/deploy/install/)
