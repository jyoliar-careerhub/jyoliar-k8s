#!/usr/bin/env bash
# Zitadel 내부 구성의 멱등 적용 — 선언 파일(zitadel-config.yaml)이 원하는 상태다.
#
# 모델은 keycloak-config-cli(ops-workflow#55 코멘트 지시): 원하는 상태는 git의
# 선언 파일에 살고, 이 스크립트는 그것을 관리 API로 수렴시킨다.
#   - 재실행 최적화: 선언+스크립트의 checksum을 state ConfigMap에 기억해
#     무변경이면 skip (FORCE_APPLY=true로 무시 가능).
#   - 수렴 방식: 리소스가 없으면 생성, 있으면 전체 갱신(PUT/update) — Zitadel
#     update API가 무변경을 no-op으로 처리하므로 필드 단위 diff를 두지 않는다.
#   - IdP는 목록 순회이고 API 경로가 선언의 type에서 나온다 — 새 브로커(예:
#     Naver generic_oidc)는 선언에 항목을 더하면 등록된다. 스크립트에 제공자
#     전용 분기가 없다.
#
# 자격증명: chart FirstInstance가 부트스트랩한 machine user의 PAT(k8s Secret
# iam-admin-pat, 마운트) — 손제작 자격증명 금지(#55 Requirements).
# 선언 속 {"$env":"NAME"}은 적용 시점에 Job env 값으로 치환된다 — 시크릿
# (IdP client secret 등)이 git에 실리지 않게 하는 배선이다.
set -euo pipefail

: "${ZITADEL_URL:?in-cluster Zitadel URL (예: http://zitadel:8080)}"
: "${ZITADEL_EXTERNAL_DOMAIN:?인스턴스 판별용 Host 헤더 값 (= ExternalDomain)}"
CONFIG_FILE=${CONFIG_FILE:-/config/zitadel-config.yaml}
PAT_FILE=${PAT_FILE:-/secrets/pat/pat}
STATE_CONFIGMAP=${STATE_CONFIGMAP:-zitadel-config-state}

PAT=$(<"$PAT_FILE")
SCRIPT_PATH=$(readlink -f "$0")

log() { echo "[apply-zitadel-config] $*"; }
fail() { log "ERROR: $*"; exit 1; }

# 관리 API 호출 — 서비스 직결이므로 Host로 인스턴스를 판별시키고,
# ExternalSecure=true 환경에서 TLS 종단 프록시 뒤인 것처럼 보이게 한다.
zapi() { # METHOD PATH [BODY]
  local method=$1 path=$2 body=${3:-}
  local args=(-sS --fail-with-body -X "$method" "${ZITADEL_URL}${path}"
    -H "Authorization: Bearer ${PAT}"
    -H "Content-Type: application/json"
    -H "Host: ${ZITADEL_EXTERNAL_DOMAIN}"
    -H "X-Forwarded-Proto: https")
  [[ -n "$body" ]] && args+=(--data "$body")
  curl "${args[@]}"
}

# ---------------------------------------------------------------- 0. checksum
DESIRED_SUM=$(cat "$CONFIG_FILE" "$SCRIPT_PATH" | sha256sum | cut -d' ' -f1)
CURRENT_SUM=$(kubectl get configmap "$STATE_CONFIGMAP" \
  -o jsonpath='{.data.checksum}' 2>/dev/null || true)
if [[ "$DESIRED_SUM" == "$CURRENT_SUM" && "${FORCE_APPLY:-false}" != "true" ]]; then
  log "선언 무변경(checksum 일치) — 적용 생략"
  exit 0
fi

# 선언을 JSON으로 — 이후 전부 jq로 다룬다. {"$env":"X"}를 env 값으로 치환.
CFG=$(yq -o=json '.' "$CONFIG_FILE" | jq '
  walk(if type == "object" and has("$env")
       then (env[."$env"] // error("Job env 미주입: " + ."$env"))
       else . end)')

# ------------------------------------------------------------- 1. 기동 대기
# PostSync hook는 Zitadel 본체의 ready와 동기화되지 않는다 — 직접 기다린다.
for i in $(seq 1 60); do
  if zapi GET /debug/healthz >/dev/null 2>&1; then break; fi
  [[ $i -eq 60 ]] && fail "Zitadel이 5분 내에 응답하지 않는다"
  sleep 5
done
log "Zitadel 응답 확인"

# ---------------------------------------------------------------- 2. IdP 목록
# 선언의 type이 admin API 경로가 된다: /admin/v1/idps/<type>
while IFS= read -r idp_b64; do
  idp=$(base64 -d <<<"$idp_b64")
  name=$(jq -r '.name' <<<"$idp")
  type=$(jq -r '.type' <<<"$idp")
  payload=$(jq '{name: .name} + .config' <<<"$idp")

  found=$(zapi POST /admin/v1/idps/templates/_search \
    "$(jq -n --arg n "$name" '{queries: [{idpNameQuery: {name: $n}}]}')")
  id=$(jq -r '.result[0].id // empty' <<<"$found")

  if [[ -z "$id" ]]; then
    id=$(zapi POST "/admin/v1/idps/${type}" "$payload" | jq -r '.id')
    log "IdP 생성: ${name} (${type}) → ${id}"
  else
    zapi PUT "/admin/v1/idps/${type}/${id}" "$payload" >/dev/null
    log "IdP 갱신: ${name} (${type}) → ${id}"
  fi
  # 기본(default) 로그인 정책에 활성화 — 없으면 로그인 화면에 안 나온다
  if [[ $(jq -r '.addToLoginPolicy // false' <<<"$idp") == "true" ]]; then
    linked=$(zapi POST /admin/v1/policies/login/idps/_search '{}')
    if ! jq -e --arg id "$id" '.result[]? | select(.idpId == $id)' \
        >/dev/null <<<"$linked"; then
      zapi POST /admin/v1/policies/login/idps \
        "$(jq -n --arg id "$id" '{idpId: $id}')" >/dev/null
      log "로그인 정책에 IdP 활성화: ${name}"
    fi
  fi
done < <(jq -r '.idps[]? | @base64' <<<"$CFG")

# ------------------------------------------------------- 3. 프로젝트·OIDC 앱
pname=$(jq -r '.project.name // empty' <<<"$CFG")
if [[ -n "$pname" ]]; then
  found=$(zapi POST /management/v1/projects/_search "$(jq -n --arg n "$pname" \
    '{queries: [{nameQuery: {name: $n, method: "TEXT_QUERY_METHOD_EQUALS"}}]}')")
  pid=$(jq -r '.result[0].id // empty' <<<"$found")
  if [[ -z "$pid" ]]; then
    pid=$(zapi POST /management/v1/projects \
      "$(jq -n --arg n "$pname" '{name: $n}')" | jq -r '.id')
    log "프로젝트 생성: ${pname} → ${pid}"
  fi

  while IFS= read -r app_b64; do
    app=$(base64 -d <<<"$app_b64")
    aname=$(jq -r '.name' <<<"$app")
    oidc=$(jq '.oidc' <<<"$app")

    found=$(zapi POST "/management/v1/projects/${pid}/apps/_search" \
      "$(jq -n --arg n "$aname" \
        '{queries: [{nameQuery: {name: $n, method: "TEXT_QUERY_METHOD_EQUALS"}}]}')")
    appid=$(jq -r '.result[0].id // empty' <<<"$found")

    if [[ -z "$appid" ]]; then
      appid=$(zapi POST "/management/v1/projects/${pid}/apps/oidc" \
        "$(jq --arg n "$aname" '{name: $n} + .' <<<"$oidc")" | jq -r '.appId')
      log "OIDC 앱 생성: ${aname} → ${appid}"
    else
      zapi PUT "/management/v1/projects/${pid}/apps/${appid}/oidc_config" \
        "$oidc" >/dev/null
      log "OIDC 앱 구성 갱신: ${aname}"
    fi
  done < <(jq -r '.project.apps[]? | @base64' <<<"$CFG")
fi

# ------------------------------------------------- 4. Actions target·execution
TARGET_MAP='{}' # 선언 name → Zitadel target id
while IFS= read -r tgt_b64; do
  tgt=$(base64 -d <<<"$tgt_b64")
  tname=$(jq -r '.name' <<<"$tgt")

  found=$(zapi POST /v2/actions/targets/search "$(jq -n --arg n "$tname" \
    '{filters: [{targetNameFilter: {targetName: $n, method: "TEXT_FILTER_METHOD_EQUALS"}}]}')")
  tid=$(jq -r '.targets[0].id // empty' <<<"$found")

  if [[ -z "$tid" ]]; then
    tid=$(zapi POST /v2/actions/targets "$tgt" | jq -r '.id')
    log "Actions target 생성: ${tname} → ${tid}"
  else
    zapi POST "/v2/actions/targets/${tid}" "$tgt" >/dev/null
    log "Actions target 갱신: ${tname} → ${tid}"
  fi
  TARGET_MAP=$(jq --arg k "$tname" --arg v "$tid" '. + {($k): $v}' <<<"$TARGET_MAP")
done < <(jq -r '.actions.targets[]? | @base64' <<<"$CFG")

# 선언된 function이 실제 존재하는지 먼저 검증 — 오타가 조용히 no-op이 되는
# 것을 막는다 (SetExecution은 미존재 조건을 400으로 거부하지만, 목록을 함께
# 보여 주는 쪽이 진단에 낫다).
available_fns=$(zapi GET /v2/actions/executions/functions)
while IFS= read -r exe_b64; do
  exe=$(base64 -d <<<"$exe_b64")
  fn=$(jq -r '.function' <<<"$exe")
  jq -e --arg fn "$fn" '.functions[]? | select(. == $fn)' \
    >/dev/null <<<"$available_fns" \
    || fail "미존재 function '${fn}' — 사용 가능: $(jq -c '.functions' <<<"$available_fns")"

  tids=$(jq -c --argjson m "$TARGET_MAP" \
    '.targets | map($m[.] // error("미선언 target 참조: " + .))' <<<"$exe")
  zapi PUT /v2/actions/executions "$(jq -n --arg fn "$fn" --argjson t "$tids" \
    '{condition: {function: {name: $fn}}, targets: $t}')" >/dev/null
  log "Execution 설정: function=${fn}"
done < <(jq -r '.actions.executions[]? | @base64' <<<"$CFG")

# ------------------------------------------------------------- 5. state 기록
kubectl create configmap "$STATE_CONFIGMAP" \
  --from-literal=checksum="$DESIRED_SUM" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
log "적용 완료 — checksum ${DESIRED_SUM:0:12} 기록"
