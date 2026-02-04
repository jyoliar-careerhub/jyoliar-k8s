# jyoliar-k8s

## ⚠️ 필수 행동 규칙

**작업 실패/오류 발생 시:**
1. 원인 파악까지만 진행
2. **절대 임의로 조치하지 않음** (cleanup, rollback, delete, 재시도 등 금지)
3. 상황을 보고하고 사용자 지시 대기

이 규칙을 어기면 안 됩니다.

---

Kubernetes 리소스 관리를 위한 GitOps 프로젝트

## 세션 시작 시 환경 선택

Claude는 세션 시작 시 반드시 작업 환경을 확인해야 합니다:

1. `.kube/` 디렉토리의 `*.config` 파일 목록을 확인
2. config 파일이 **2개 이상**인 경우: 사용자에게 어떤 환경에서 작업할지 질문
3. config 파일이 **1개만** 존재하는 경우: 해당 환경으로 자동 선택 (질문 생략)

선택된 환경은 해당 세션 동안 다음과 같이 사용됩니다:

- kubeconfig: `.kube/{환경명}.config`
- platform overlay: `platform/overlays/{환경명}/`
- gitops overlay: `gitops/overlays/{환경명}/`

## 프로젝트 구조

```
.
├── .kube/                      # kubeconfig 파일 저장소 (gitignore 대상)
│   └── {환경명}.config         # 예: aws-ec2.config
├── gitops/                     # ArgoCD로 관리되는 K8s 리소스
│   ├── base/                   # 공통 리소스 정의
│   └── overlays/               # 환경별 오버레이
│       └── {환경명}/           # kubeconfig 이름과 매핑
└── platform/                   # ArgoCD 설치 전 필요한 리소스 (Ceph 등)
    ├── base/                   # 공통 리소스 정의
    │   └── rook/               # Rook-Ceph 관련 리소스
    └── overlays/               # 환경별 오버레이
        ├── aws-ec2/
        └── baremetal/
```

## 주요 컴포넌트

### platform/

- ArgoCD 설치 이전에 배포되어야 하는 인프라 리소스
- Rook-Ceph 스토리지 클러스터 등

### gitops/

- ArgoCD Application으로 관리되는 애플리케이션 리소스
- Kustomize base/overlays 패턴 사용

**중요**: ArgoCD Application을 구성할 때, `base/` 경로의 리소스를 직접 참조하지 마십시오. 항상 `base/` 경로의 리소스를 참조하는 `overlays/` 경로를 참조하십시오.

## 클러스터 접근

특정 환경의 클러스터에 접근할 때:

```bash
kubectl <동사> [리소스] [-n <namespace>] --kubeconfig .kube/{환경명}.config
```

**중요**: `kubectl` 바로 뒤에 동사(get, apply, delete 등)를 먼저 위치시키고, 옵션들(`-n`, `--kubeconfig` 등)은 뒤에 배치합니다.

```bash
# 예시
kubectl get nodes --kubeconfig .kube/{환경명}.config
kubectl get pods -n kube-system --kubeconfig .kube/{환경명}.config
kubectl apply -f manifest.yaml -n default --kubeconfig .kube/{환경명}.config
```

## 관련 프로젝트 (jyoliar-k8s-ansible)

디버깅이나 클러스터 구성 확인 시 `jyoliar-k8s-ansible` 프로젝트를 참조합니다:

- **노드 SSH 접근**: `jyoliar-k8s-ansible` 프로젝트의 inventory 파일에서 서버별 SSH 접근 정보 확인
- **쿠버네티스 구성 확인**: `jyoliar-k8s-ansible` 프로젝트의 CLAUDE.md 및 관련 파일 참조

## Git Commit 보안 가이드라인

**중요**: Git commit 전에 반드시 다음 사항을 확인해야 합니다:

- Secret, API Key, Password 등 민감한 정보가 코드에 하드코딩되어 있지 않은지 확인
- Kubernetes Secret 리소스에 실제 값이 아닌 placeholder나 ExternalSecret 참조를 사용하는지 확인
- 환경 변수나 설정 파일에 민감한 정보가 노출되지 않았는지 확인
