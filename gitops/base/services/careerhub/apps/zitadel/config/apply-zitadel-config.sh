#!/usr/bin/env bash
# 구성 Job entrypoint — 역할 분담:
#   - 이 쉘: 재실행 최적화(checksum 무변경 skip)와 state ConfigMap 기록. kubectl 담당.
#   - apply_zitadel_config.py: Zitadel API와의 수렴 로직 전부. 헤더 주석 참조.
set -euo pipefail

: "${ZITADEL_URL:?in-cluster Zitadel URL (예: http://zitadel:8080)}"
: "${ZITADEL_EXTERNAL_DOMAIN:?인스턴스 판별용 Host 헤더 값 (= ExternalDomain)}"
CONFIG_FILE=${CONFIG_FILE:-/config/zitadel-config.yaml}
STATE_CONFIGMAP=${STATE_CONFIGMAP:-zitadel-config-state}
PY_SCRIPT=$(dirname "$(readlink -f "$0")")/apply_zitadel_config.py

log() { echo "[apply-zitadel-config] $*"; }

# python·PyYAML은 alpine/k8s의 공표 도구 목록 밖(awscli v1의 의존)이다 —
# 이미지 태그 인상으로 사라졌다면 조용한 오작동 전에 여기서 명시적으로 죽는다.
python3 -c "import yaml" 2>/dev/null \
  || { log "ERROR: 이미지에 python3/PyYAML이 없다 — alpine/k8s 태그 인상 시 재확인 필요(config-job.yaml 주석)"; exit 1; }

# 재실행 최적화: 선언+수렴 로직의 checksum이 지난 적용과 같으면 skip.
DESIRED_SUM=$(cat "$CONFIG_FILE" "$PY_SCRIPT" "$0" | sha256sum | cut -d' ' -f1)
CURRENT_SUM=$(kubectl get configmap "$STATE_CONFIGMAP" \
  -o jsonpath='{.data.checksum}' 2>/dev/null || true)
if [[ "$DESIRED_SUM" == "$CURRENT_SUM" && "${FORCE_APPLY:-false}" != "true" ]]; then
  log "선언 무변경(checksum 일치) — 적용 생략"
  exit 0
fi

python3 "$PY_SCRIPT"

kubectl create configmap "$STATE_CONFIGMAP" \
  --from-literal=checksum="$DESIRED_SUM" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
log "적용 완료 — checksum ${DESIRED_SUM:0:12} 기록"
