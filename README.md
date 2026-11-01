# 클러스터 안 — jyoliar-k8s

클러스터 안의 모든 것을 Argo CD 로 반영하는 GitOps 저장소다. 클러스터 자체는 onpremise-k8s 가 세우고, 클라우드 쪽 산출물은 jyoliar-platform-terraform 이 만든다.

## 디렉터리 구조

```
platform/                  # Argo CD 보다 먼저 있어야 하는 것 — helmfile · kustomize 로 직접 깐다
├── base/
└── overlays/
    ├── {env}/             # helmfile.yaml · kustomization.yaml · 차트별 values
    ├── deploy.sh          # 초기 구축 1단계
    └── create-tunnel-token.sh
gitops/                    # Argo CD 가 동기화하는 전부
├── base/
│   ├── bootstrap/         # argocd · cloudflare-tunnel · oidc-provider · external-secrets-resources
│   ├── controllers/       # 오퍼레이터 · 컨트롤러
│   ├── system/            # 컨트롤러의 설정 CR
│   └── services/          # careerhub · ai-agents
└── overlays/
    ├── {env}/             # root-appset.yaml 과 층별 ApplicationSet · AppProject · 패치
    └── deploy.sh          # 초기 구축 3단계
docs/                      # 검토 · 계획 · 기록 문서
```

`base/` 는 직접 적용하지 않는다 — 렌더도 참조도 항상 `platform/overlays/{env}/` · `gitops/overlays/{env}/` 를 거친다.

## 환경

- 환경명 하나가 양쪽 overlay 경로를 정한다 — `platform/overlays/{env}/` · `gitops/overlays/{env}/`. `overlays/` 아래 디렉터리 목록이 환경 목록이다.
- gitops overlay 안은 ApplicationSet 층이다. `root-appset.yaml` 이 네 층(bootstrap → controllers → system → services)을 이 순서로 동기화하고, 층마다 자기 ApplicationSet · AppProject 가 그 아래를 다시 나눈다. 어느 Application 이 어느 경로를 보나는 각 층의 `*-appset.yaml` 이, 허용 소스 저장소는 `*-appproj.yaml` 이 답한다.
- services 층은 서비스 묶음(careerhub prod · dev, ai-agents)마다 다시 ApplicationSet 과 AppProject 를 가진다.
- 초기 구축 스크립트는 로컬 경로 `.kube/{env}.config` 에서 kubeconfig 를 읽는다 — gitignore 대상이고, 평소 클러스터 접근 경로는 아니다.

## 반영 방법

**평소** — PR 이 유일한 길이다. `main` 에 머지되면 Argo CD 가 자동 sync(prune · selfHeal) 한다. 이미지 태그 올리기도 overlay kustomization 의 `images` 를 PR 로 바꾸는 일이다.

머지 전 렌더 확인은 overlay 경로로 한다.

```bash
kubectl kustomize gitops/overlays/{env}/<층>/<앱>
kubectl kustomize platform/overlays/{env}
```

새 Helm 저장소나 Git 저장소를 `repoURL` 로 더할 때는 그 층의 AppProject `sourceRepos` 에 같은 PR 로 함께 넣는다 — 빠지면 첫 sync 에서 거부된다.

**초기 구축** — 새 클러스터에 한 번. onpremise-k8s 가 넘긴 admin kubeconfig 를 `.kube/{env}.config` 에 둔 뒤 순서대로.

```bash
# 1단계 — External Secrets · kubelet-csr-approver · Longhorn · metrics-server 를 helmfile · kustomize 로
./platform/overlays/deploy.sh {env}

# 2단계 — Cloudflare 터널 자격증명(jyoliar-platform-terraform 출력)을 Secret 으로
./platform/overlays/create-tunnel-token.sh -f <credentials.json> -k .kube/{env}.config

# 3단계 — Argo CD · ClusterSecretStore · root ApplicationSet. 이후는 Argo CD 가 맡는다
./gitops/overlays/deploy.sh {env}
```

External Secrets 의 AWS 인증은 IRSA 다 — AWS 키를 Secret 으로 만드는 단계는 없다.

## 다른 저장소와의 경계

받는 것:

| 넘기는 저장소 | 무엇 | 그쪽의 자리 | 이 저장소의 자리 |
|---|---|---|---|
| onpremise-k8s | 클러스터와 admin kubeconfig | 구축 뒤 `/etc/kubernetes/admin.conf` | `.kube/{env}.config` — 초기 구축 스크립트가 요구 |
| onpremise-k8s | kube-vip 컨트롤 플레인 VIP · LoadBalancer IP 풀 | 인벤토리의 kube-vip 변수 | Gateway 의 LoadBalancer 주소 |
| onpremise-k8s | Longhorn 용 디스크 | `longhorn-prep.yaml` | [`platform/overlays/{env}/longhorn`](platform/overlays/baremetal/longhorn) |
| jyoliar-platform-terraform | Parameter Store 파라미터 | `oauth` · `alert` · `cloudflare` · `zitadel` 워크스페이스 | [`ClusterSecretStore/aws-parameter-store`](gitops/base/bootstrap/external-secrets-resources) 를 쓰는 ExternalSecret — 파라미터 경로는 overlay 의 ExternalSecret 패치 |
| jyoliar-platform-terraform | IRSA Role | `iam`(External Secrets · ECR pull) · `backup`(PostgreSQL 백업) | ServiceAccount 의 `eks.amazonaws.com/role-arn` annotation |
| jyoliar-platform-terraform | S3 백업 버킷 | `backup` | CloudNativePG `ObjectStore` 의 `destinationPath` — overlay 패치 |
| jyoliar-platform-terraform | ECR 저장소 | `ci` | overlay kustomization 의 `images` |
| jyoliar-platform-terraform | Cloudflare 터널 자격증명 | `cloudflare` 출력 `tunnel_credentials` | [`create-tunnel-token.sh`](platform/overlays/create-tunnel-token.sh) → [`cloudflare-tunnel`](gitops/base/bootstrap/cloudflare-tunnel) 의 Secret |
| jyoliar-platform-terraform | 합의 값 — OIDC 발급자 URL · IRSA 가 신뢰하는 ServiceAccount 좌표 | `iam` · `backup` 의 변수 기본값이 먼저 정한다 | [`cloudflare-tunnel`](gitops/base/bootstrap/cloudflare-tunnel) 의 ingress hostname · 해당 ServiceAccount 의 이름 · 네임스페이스 |

넘기는 것은 없다 — 클러스터 안이 가장 위 층이다. OIDC 발급자의 JWKS 를 [`oidc-provider`](gitops/base/bootstrap/oidc-provider) 가 서빙하고 [`cloudflare-tunnel`](gitops/base/bootstrap/cloudflare-tunnel) 이 밖으로 내지만, 그것을 읽는 쪽은 저장소가 아니라 AWS STS 다(IRSA 토큰 교환 시점).

Terraform 은 클러스터 안을 알지 못한다. 방향은 한쪽이다.
