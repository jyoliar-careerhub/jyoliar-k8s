#!/usr/bin/env python3
"""Zitadel 내부 구성의 수렴 로직 — 선언(YAML)을 관리 API에 적용한다.

역할 분담(entrypoint인 apply-zitadel-config.sh 참조):
  - 쉘: checksum 기반 무변경 skip과 state ConfigMap 기록(kubectl).
  - 이 스크립트: Zitadel API와의 수렴 전부 — kubectl을 모른다.

수렴 방식은 keycloak-config-cli 모델(ops-workflow#55 코멘트 지시)을 따른다:
리소스가 없으면 생성, 있으면 전체 갱신(PUT/update) — Zitadel update API가
무변경을 no-op으로 처리하므로 필드 단위 diff를 두지 않는다.

IdP는 목록 순회이고 API 경로가 선언의 type에서 나온다 — 새 브로커(예: Naver
generic_oidc)는 선언에 항목을 더하면 등록된다. 제공자 전용 분기가 없다.

선언 속 {"$env": "NAME"}은 Job env 값으로 치환된다 — 시크릿(IdP client secret
등)이 git에 실리지 않게 하는 배선이다.

의존: PyYAML — alpine/k8s 이미지에 awscli v1의 의존으로 실재한다(공표 도구
목록 밖이므로 이미지 태그 인상 시 존재를 재확인할 것. entrypoint가 fail-fast
검사를 한다).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import yaml


def log(msg):
    print(f"[apply-zitadel-config] {msg}", flush=True)


def fail(msg):
    log(f"ERROR: {msg}")
    sys.exit(1)


class ZitadelApi:
    """서비스 직결 호출 — Host로 인스턴스를 판별시키고, ExternalSecure=true
    환경에서 TLS 종단 프록시 뒤인 것처럼 보이게 한다."""

    def __init__(self, base_url, external_domain, pat):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
            "Host": external_domain,
            "X-Forwarded-Proto": "https",
        }

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=self.headers
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} → HTTP {e.code}: {detail}") from e
        return json.loads(payload) if payload else {}


def resolve_env(node):
    """{"$env": "NAME"}을 env 값으로 치환 — 미주입은 즉시 실패."""
    if isinstance(node, dict):
        if set(node.keys()) == {"$env"}:
            name = node["$env"]
            if name not in os.environ:
                fail(f"Job env 미주입: {name}")
            return os.environ[name]
        return {k: resolve_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_env(v) for v in node]
    return node


def wait_ready(api, attempts=60, interval=5):
    """PostSync hook는 Zitadel 본체의 ready와 동기화되지 않는다 — 직접 기다린다."""
    for i in range(attempts):
        try:
            api.call("GET", "/debug/healthz")
            log("Zitadel 응답 확인")
            return
        except (RuntimeError, urllib.error.URLError):
            if i == attempts - 1:
                fail(f"Zitadel이 {attempts * interval}초 내에 응답하지 않는다")
            time.sleep(interval)


def ensure_idps(api, idps):
    for idp in idps:
        name, idp_type = idp["name"], idp["type"]
        payload = {"name": name, **idp["config"]}

        found = api.call(
            "POST", "/admin/v1/idps/templates/_search",
            {"queries": [{"idpNameQuery": {"name": name}}]},
        )
        results = found.get("result") or []
        idp_id = results[0]["id"] if results else None

        if idp_id is None:
            idp_id = api.call("POST", f"/admin/v1/idps/{idp_type}", payload)["id"]
            log(f"IdP 생성: {name} ({idp_type}) → {idp_id}")
        else:
            api.call("PUT", f"/admin/v1/idps/{idp_type}/{idp_id}", payload)
            log(f"IdP 갱신: {name} ({idp_type}) → {idp_id}")

        # 기본(default) 로그인 정책에 활성화 — 없으면 로그인 화면에 안 나온다
        if idp.get("addToLoginPolicy"):
            linked = api.call("POST", "/admin/v1/policies/login/idps/_search", {})
            linked_ids = {l["idpId"] for l in linked.get("result") or []}
            if idp_id not in linked_ids:
                api.call("POST", "/admin/v1/policies/login/idps", {"idpId": idp_id})
                log(f"로그인 정책에 IdP 활성화: {name}")


def ensure_project(api, project):
    pname = project["name"]
    found = api.call(
        "POST", "/management/v1/projects/_search",
        {"queries": [{"nameQuery": {"name": pname, "method": "TEXT_QUERY_METHOD_EQUALS"}}]},
    )
    results = found.get("result") or []
    pid = results[0]["id"] if results else None
    if pid is None:
        pid = api.call("POST", "/management/v1/projects", {"name": pname})["id"]
        log(f"프로젝트 생성: {pname} → {pid}")

    for app in project.get("apps") or []:
        aname, oidc = app["name"], app["oidc"]
        found = api.call(
            "POST", f"/management/v1/projects/{pid}/apps/_search",
            {"queries": [{"nameQuery": {"name": aname, "method": "TEXT_QUERY_METHOD_EQUALS"}}]},
        )
        results = found.get("result") or []
        if not results:
            app_id = api.call(
                "POST", f"/management/v1/projects/{pid}/apps/oidc",
                {"name": aname, **oidc},
            )["appId"]
            log(f"OIDC 앱 생성: {aname} → {app_id}")
        else:
            app_id = results[0]["id"]
            api.call("PUT", f"/management/v1/projects/{pid}/apps/{app_id}/oidc_config", oidc)
            log(f"OIDC 앱 구성 갱신: {aname}")


def ensure_actions(api, actions):
    target_ids = {}
    for tgt in actions.get("targets") or []:
        tname = tgt["name"]
        found = api.call(
            "POST", "/v2/actions/targets/search",
            {"filters": [{"targetNameFilter": {
                "targetName": tname, "method": "TEXT_FILTER_METHOD_EQUALS"}}]},
        )
        results = found.get("targets") or []
        if not results:
            tid = api.call("POST", "/v2/actions/targets", tgt)["id"]
            log(f"Actions target 생성: {tname} → {tid}")
        else:
            tid = results[0]["id"]
            api.call("POST", f"/v2/actions/targets/{tid}", tgt)
            log(f"Actions target 갱신: {tname} → {tid}")
        target_ids[tname] = tid

    executions = actions.get("executions") or []
    if not executions:
        return
    # 선언된 function이 실제 존재하는지 먼저 검증 — 오타가 조용히 no-op이
    # 되는 것을 막고, 실패 시 사용 가능 목록을 함께 보인다.
    available = set(api.call("GET", "/v2/actions/executions/functions").get("functions") or [])
    for exe in executions:
        fn = exe["function"]
        if fn not in available:
            fail(f"미존재 function '{fn}' — 사용 가능: {sorted(available)}")
        try:
            tids = [target_ids[t] for t in exe["targets"]]
        except KeyError as e:
            fail(f"미선언 target 참조: {e.args[0]}")
        api.call(
            "PUT", "/v2/actions/executions",
            {"condition": {"function": {"name": fn}}, "targets": tids},
        )
        log(f"Execution 설정: function={fn}")


def main():
    config_file = os.environ.get("CONFIG_FILE", "/config/zitadel-config.yaml")
    pat_file = os.environ.get("PAT_FILE", "/secrets/pat/pat")
    with open(pat_file) as f:
        pat = f.read().strip()
    with open(config_file) as f:
        cfg = resolve_env(yaml.safe_load(f))

    api = ZitadelApi(
        os.environ["ZITADEL_URL"], os.environ["ZITADEL_EXTERNAL_DOMAIN"], pat
    )
    wait_ready(api)
    ensure_idps(api, cfg.get("idps") or [])
    if cfg.get("project"):
        ensure_project(api, cfg["project"])
    ensure_actions(api, cfg.get("actions") or {})
    log("수렴 완료")


if __name__ == "__main__":
    main()
