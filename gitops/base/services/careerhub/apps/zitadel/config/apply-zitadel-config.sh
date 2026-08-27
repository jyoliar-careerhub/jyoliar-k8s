#!/usr/bin/env bash
# 구성 Job entrypoint — 역할 분담:
#   - 이 쉘: 재실행 최적화(checksum 무변경 skip)·state ConfigMap 기록·앱 자격증명
#     Secret 반영. kubectl 담당.
#   - apply_zitadel_config.py: Zitadel API와의 수렴 로직 전부. 헤더 주석 참조.
# 회귀 스모크: tests/smoke_test.py — 이 파일이나 py를 고치면 돌려라(tests/README.md).
set -euo pipefail

: "${ZITADEL_URL:?in-cluster Zitadel URL (예: http://zitadel:8080)}"
: "${ZITADEL_EXTERNAL_DOMAIN:?인스턴스 판별용 Host 헤더 값 (= ExternalDomain)}"
CONFIG_FILE=${CONFIG_FILE:-/config/zitadel-config.yaml}
STATE_CONFIGMAP=${STATE_CONFIGMAP:-zitadel-config-state}
PY_SCRIPT=$(dirname "$(readlink -f "$0")")/apply_zitadel_config.py
# 앱 자격증명의 쉘↔파이썬 인계 파일 — 형식은 파이썬 헤더 주석 참조.
export CREDS_STATE_FILE=${CREDS_STATE_FILE:-/tmp/zitadel-app-creds-state.json}
export CREDS_OUT_FILE=${CREDS_OUT_FILE:-/tmp/zitadel-app-creds-out.json}

log() { echo "[apply-zitadel-config] $*"; }

# Job 산출물(자격증명 Secret·state CM)의 소유 표식 — ArgoCD 비추적 생성물이라
# namespace를 뒤진 사람이 정체를 읽을 수 있게 남긴다. 값은 실존 Job 이름이다.
MANAGED_META='{"labels":{"app.kubernetes.io/managed-by":"zitadel-config-apply"},"annotations":{"careerhub.jyo-liar.com/provenance":"zitadel-config-apply Job 산출물 — 선언: zitadel-config ConfigMap · 스크립트: gitops/base/services/careerhub/apps/zitadel/config/"}}'

# python·PyYAML은 alpine/k8s의 공표 도구 목록 밖(awscli v1의 의존)이다 —
# 이미지 태그 인상으로 사라졌다면 조용한 오작동 전에 여기서 명시적으로 죽는다.
python3 -c "import yaml" 2>/dev/null \
  || { log "ERROR: 이미지에 python3/PyYAML이 없다 — alpine/k8s 태그 인상 시 재확인 필요(config-job.yaml 주석)"; exit 1; }

# 선언된 자격증명 Secret과 각각이 보유해야 할 키 — "<이름>\t<키,키>" 한 줄씩.
DECLARED_CREDS=$(python3 "$PY_SCRIPT" --list-credentials-secrets)

# 기존 자격증명 Secret의 보유 키 목록 → 파이썬의 재발급 판단 재료이자,
# 아래 재실행 최적화의 재수렴 조건.
STATE_JSON='{}'
INCOMPLETE_CREDS=""
while IFS=$'\t' read -r name want_keys; do
  [[ -z "$name" ]] && continue
  if SECRET_JSON=$(kubectl get secret "$name" -o json 2>/dev/null); then
    KEYS=$(jq -c '.data // {} | keys' <<<"$SECRET_JSON")
    STATE_JSON=$(jq -c --arg n "$name" --argjson k "$KEYS" '. + {($n): $k}' <<<"$STATE_JSON")
    LACKS=$(jq -rn --argjson have "$KEYS" --arg want "$want_keys" \
      '($want | split(",")) - $have | join(",")')
    [[ -n "$LACKS" ]] && INCOMPLETE_CREDS="$INCOMPLETE_CREDS $name(키 없음: $LACKS)"
  else
    INCOMPLETE_CREDS="$INCOMPLETE_CREDS $name(부재)"
  fi
done <<<"$DECLARED_CREDS"
printf '%s' "$STATE_JSON" >"$CREDS_STATE_FILE"

# 재실행 최적화: 선언+수렴 로직의 checksum이 지난 적용과 같으면 skip — 단,
# 선언된 자격증명 Secret이 없거나 선언이 요구하는 키를 갖추지 못했으면 재수렴한다.
# 키까지 보는 이유: secret 인증 앱은 Secret이 있어도 client_secret 키만 빠질 수
# 있다(회전 삭제 · public→confidential 전환 직후). 존재만 보면 그 상태가
# checksum 일치에 묻혀 "무변경"으로 조용히 넘어간다 — 앱은 confidential인데
# 소비자가 쓸 secret은 없는 채로.
DESIRED_SUM=$(cat "$CONFIG_FILE" "$PY_SCRIPT" "$0" | sha256sum | cut -d' ' -f1)
CURRENT_SUM=$(kubectl get configmap "$STATE_CONFIGMAP" \
  -o jsonpath='{.data.checksum}' 2>/dev/null || true)
if [[ "$DESIRED_SUM" == "$CURRENT_SUM" && -z "$INCOMPLETE_CREDS" \
      && "${FORCE_APPLY:-false}" != "true" ]]; then
  log "선언 무변경(checksum 일치) — 적용 생략"
  exit 0
fi
[[ -n "$INCOMPLETE_CREDS" ]] && log "자격증명 Secret 미충족:$INCOMPLETE_CREDS — 수렴 실행"

python3 "$PY_SCRIPT"

# 파이썬이 내보낸 앱 자격증명 → k8s Secret 반영.
# 있으면 stringData 병합 patch(미출력 키 보존 — 기존 client_secret이 지워지지
# 않는다), 없으면 생성. 소비자는 이 Secret을 참조한다(선언의 credentialsSecret).
if [[ -s "$CREDS_OUT_FILE" ]]; then
  for name in $(jq -r 'keys[]' "$CREDS_OUT_FILE"); do
    DATA=$(jq -c --arg n "$name" '.[$n]' "$CREDS_OUT_FILE")
    if kubectl get secret "$name" >/dev/null 2>&1; then
      kubectl patch secret "$name" \
        -p "$(jq -nc --argjson d "$DATA" --argjson m "$MANAGED_META" \
              '{metadata: $m, stringData: $d}')" >/dev/null
      log "자격증명 Secret 갱신: $name"
    else
      jq -nc --arg n "$name" --argjson d "$DATA" --argjson m "$MANAGED_META" \
        '{apiVersion: "v1", kind: "Secret", metadata: ({name: $n} + $m), type: "Opaque", stringData: $d}' \
        | kubectl apply -f - >/dev/null
      log "자격증명 Secret 생성: $name"
    fi
  done
fi

jq -nc --arg n "$STATE_CONFIGMAP" --arg sum "$DESIRED_SUM" --argjson m "$MANAGED_META" \
  '{apiVersion: "v1", kind: "ConfigMap", metadata: ({name: $n} + $m), data: {checksum: $sum}}' \
  | kubectl apply -f - >/dev/null
log "적용 완료 — checksum ${DESIRED_SUM:0:12} 기록"
