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

## 클러스터 초기 구축

새 클러스터를 구축할 때 아래 스크립트들을 **순차적으로** 실행해야 합니다:

```bash
# 1. Platform 리소스 배포 (Longhorn, ArgoCD, External Secrets 등)
./platform/overlays/deploy.sh {환경명}

# 2. AWS Credentials Secret 생성 (External Secrets용)
./platform/overlays/create-aws-credentials.sh \
    -a <AWS_ACCESS_KEY_ID> \
    -s <AWS_SECRET_ACCESS_KEY> \
    -k .kube/{환경명}.config

# 3. GitOps 리소스 배포 (ArgoCD ApplicationSet 부트스트랩)
./gitops/overlays/deploy.sh {환경명}
```

**실행 순서가 중요합니다:**

- `platform/overlays/deploy.sh`: ArgoCD 및 External Secrets Operator 설치
- `platform/overlays/create-aws-credentials.sh`: ExternalSecret이 사용할 AWS 인증 정보를 argocd 네임스페이스에 생성
- `gitops/overlays/deploy.sh`: ArgoCD Application 부트스트랩 (aws-credentials 시크릿 존재 필수)

## Kustomize 사용 규칙

Kustomize 관련 작업 시 **반드시 overlays 경로를 사용**해야 합니다:

```bash
# 올바른 사용법
kubectl kustomize platform/overlays/{환경명}/
kubectl kustomize gitops/overlays/{환경명}/

# 잘못된 사용법 (금지)
kubectl kustomize platform/base/
kubectl kustomize gitops/base/
```

## Kustomize Patch 규칙

Patch는 **Strategic Merge Patch** 방식만 사용합니다. JSON patch (`- op: replace` 등)는 사용하지 않습니다.

- Patch 파일에 `apiVersion`, `kind`, `metadata`를 포함하여 대상 리소스를 명시
- `kustomization.yaml`에서는 `- path:` 만 사용 (`target` 지정 불필요)
- 기존 필드를 제거해야 할 경우 (예: `valueFrom` → `value`로 교체), 제거할 필드를 빈 값으로 지정

```yaml
# patch 파일 예시
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-deployment
spec:
  template:
    spec:
      containers:
        - name: my-container
          env:
            - name: MY_VAR
              value: "new-value"
              valueFrom: # 빈 값으로 지정하여 기존 valueFrom 제거
```

```yaml
# kustomization.yaml 예시
patches:
  - path: my-patch.yaml
```

## ArgoCD AppProject sourceRepos 허용 확인 (필수)

ArgoCD Application의 `spec.source.repoURL`로 **외부 Helm chart 저장소나 신규 Git 저장소를 추가/변경할 때**, 해당 Application이 속한 AppProject의 `spec.sourceRepos` 목록에 그 URL이 허용되어 있는지 **반드시 사전 확인**합니다.

허용되지 않으면 ArgoCD sync 시 다음과 같은 에러로 실패합니다:

```
application repo <URL> is not permitted in project '<project-name>'
```

### 작업 절차

1. 새 `repoURL` 추가/변경 전, 해당 AppProject 정의를 확인:
   ```bash
   # 예: controllers AppProject
   cat gitops/overlays/baremetal/controllers/controllers-appproj.yaml
   ```
2. `spec.sourceRepos`에 새 URL이 없으면 **AppProject 매니페스트에 URL 추가를 같은 PR/커밋에 포함**합니다. (별도 커밋으로 미루면 첫 sync에서 반드시 실패함)
3. Helm chart의 경우 `chart:` 필드와 무관하게 `repoURL` 단위로 허용되므로, 동일 레포의 다른 차트는 이미 허용돼 있을 수 있음 — 그래도 URL이 정확히 일치하는지 확인.

### 자주 놓치는 패턴

- 같은 차트의 **레포 이전** (예: `https://grafana.github.io/helm-charts` → `https://grafana-community.github.io/helm-charts`)도 새 URL이므로 신규 등록 필요
- OCI 레지스트리(`oci://...`)와 HTTPS 레포는 별개 항목으로 등록

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

## 문서 파일명 규칙

`docs/` 디렉토리의 문서 파일명은 `YYMMDDHHmm_제목.md` 형식으로 작성합니다. prefix는 문서 최초 생성 시각(KST)을 나타내며, 이를 통해 파일명 정렬만으로 생성 시간순 정렬이 가능합니다.

```
예시: 2603251254_refactoring-plan.md  (2026년 3월 25일 12시 54분 생성)
```

## 문서 작성 포맷

`docs/` 디렉토리에 계획서·분석서 등 문서를 작성할 때 아래 헤더를 반드시 포함합니다:

```markdown
# 문서 제목

**작성일**: YYYY-MM-DD HH:MM (KST)
**최종 수정일**: YYYY-MM-DD HH:MM (KST)
**대상 서비스**: 해당 문서가 다루는 서비스 목록 (쉼표 구분)

---
```

- 날짜와 시간은 한국 표준시(KST, UTC+9) 기준으로 기록합니다.
- 문서를 수정할 때마다 **최종 수정일**을 갱신합니다.
