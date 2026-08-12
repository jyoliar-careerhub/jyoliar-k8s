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
from dataclasses import dataclass
from typing import Any, NoReturn

import yaml

# 관리 API가 주고받는 JSON 객체 — 임의 깊이의 트리라 값은 Any로 둔다
JsonObj = dict[str, Any]


def log(msg: str) -> None:
    print(f"[apply-zitadel-config] {msg}", flush=True)


def fail(msg: str) -> NoReturn:
    log(f"ERROR: {msg}")
    sys.exit(1)


def resolve_env(node: Any) -> Any:
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


# ---------------------------------------------------------------- 선언 모델
# 스크립트 로직이 읽는 구조(이름·타입·관계)만 dataclass로 고정한다.
# 제공자 config·앱 oidc·target 본문은 관리 API 요청 본문 그대로의 통과
# 페이로드(JsonObj)로 둔다 — 그 스키마의 소유자는 Zitadel이고, 필드를 여기
# 복제하면 새 제공자 타입 추가가 스크립트 수정에 막힌다(#55 Requirements의
# 확장 구조 요구).


@dataclass(frozen=True)
class IdpSpec:
    name: str
    type: str  # admin API 경로가 된다: /admin/v1/idps/<type>
    config: JsonObj  # Add/Update<Provider> 요청 본문(name 제외)
    add_to_login_policy: bool

    @classmethod
    def parse(cls, node: JsonObj) -> "IdpSpec":
        return cls(
            name=node["name"],
            type=node["type"],
            config=node["config"],
            add_to_login_policy=bool(node.get("addToLoginPolicy")),
        )


@dataclass(frozen=True)
class AppSpec:
    name: str
    oidc: JsonObj  # AddOIDCApp·UpdateOIDCAppConfig 요청 본문

    @classmethod
    def parse(cls, node: JsonObj) -> "AppSpec":
        return cls(name=node["name"], oidc=node["oidc"])


@dataclass(frozen=True)
class ProjectSpec:
    name: str
    apps: list[AppSpec]

    @classmethod
    def parse(cls, node: JsonObj) -> "ProjectSpec":
        return cls(
            name=node["name"],
            apps=[AppSpec.parse(a) for a in node.get("apps") or []],
        )


@dataclass(frozen=True)
class TargetSpec:
    name: str
    body: JsonObj  # CreateTarget·UpdateTarget 요청 본문(name 포함)

    @classmethod
    def parse(cls, node: JsonObj) -> "TargetSpec":
        return cls(name=node["name"], body=node)


@dataclass(frozen=True)
class ExecutionSpec:
    function: str
    targets: list[str]  # TargetSpec.name 참조

    @classmethod
    def parse(cls, node: JsonObj) -> "ExecutionSpec":
        return cls(function=node["function"], targets=list(node["targets"]))


@dataclass(frozen=True)
class ZitadelConfig:
    idps: list[IdpSpec]
    project: ProjectSpec | None
    targets: list[TargetSpec]
    executions: list[ExecutionSpec]

    @classmethod
    def load(cls, path: str) -> "ZitadelConfig":
        with open(path) as f:
            raw = resolve_env(yaml.safe_load(f))
        actions = raw.get("actions") or {}
        return cls(
            idps=[IdpSpec.parse(n) for n in raw.get("idps") or []],
            project=ProjectSpec.parse(raw["project"]) if raw.get("project") else None,
            targets=[TargetSpec.parse(n) for n in actions.get("targets") or []],
            executions=[ExecutionSpec.parse(n) for n in actions.get("executions") or []],
        )


# ------------------------------------------------------------------- 수렴
class ZitadelApi:
    """Zitadel 관리 API 클라이언트 + 선언 모델의 수렴 메서드.

    호출은 서비스 직결 — Host로 인스턴스를 판별시키고, ExternalSecure=true
    환경에서 TLS 종단 프록시 뒤인 것처럼 보이게 한다.
    """

    def __init__(self, base_url: str, external_domain: str, pat: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
            "Host": external_domain,
            "X-Forwarded-Proto": "https",
        }

    def _call(self, method: str, path: str, body: JsonObj | None = None) -> JsonObj:
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
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            # /debug/healthz 등 일부 표면은 200에 플레인 텍스트를 준다(실측:
            # 첫 배포에서 Job이 여기서 죽었다) — 소비하는 응답은 전부 JSON이므로
            # 비-JSON 200은 빈 객체로 취급한다.
            return {}

    def wait_ready(self, attempts: int = 60, interval: int = 5) -> None:
        """PostSync hook는 Zitadel 본체의 ready와 동기화되지 않는다 — 직접 기다린다."""
        for i in range(attempts):
            try:
                self._call("GET", "/debug/healthz")
                log("Zitadel 응답 확인")
                return
            except (RuntimeError, urllib.error.URLError):
                if i == attempts - 1:
                    fail(f"Zitadel이 {attempts * interval}초 내에 응답하지 않는다")
                time.sleep(interval)

    def ensure_idps(self, idps: list[IdpSpec]) -> None:
        for idp in idps:
            payload = {"name": idp.name, **idp.config}

            found = self._call(
                "POST", "/admin/v1/idps/templates/_search",
                {"queries": [{"idpNameQuery": {"name": idp.name}}]},
            )
            results = found.get("result") or []
            idp_id = results[0]["id"] if results else None

            if idp_id is None:
                idp_id = self._call("POST", f"/admin/v1/idps/{idp.type}", payload)["id"]
                log(f"IdP 생성: {idp.name} ({idp.type}) → {idp_id}")
            else:
                self._call("PUT", f"/admin/v1/idps/{idp.type}/{idp_id}", payload)
                log(f"IdP 갱신: {idp.name} ({idp.type}) → {idp_id}")

            # 기본(default) 로그인 정책에 활성화 — 없으면 로그인 화면에 안 나온다
            if idp.add_to_login_policy:
                linked = self._call("POST", "/admin/v1/policies/login/idps/_search", {})
                linked_ids = {l["idpId"] for l in linked.get("result") or []}
                if idp_id not in linked_ids:
                    self._call("POST", "/admin/v1/policies/login/idps", {"idpId": idp_id})
                    log(f"로그인 정책에 IdP 활성화: {idp.name}")

    def ensure_project(self, project: ProjectSpec) -> None:
        found = self._call(
            "POST", "/management/v1/projects/_search",
            {"queries": [{"nameQuery": {
                "name": project.name, "method": "TEXT_QUERY_METHOD_EQUALS"}}]},
        )
        results = found.get("result") or []
        pid = results[0]["id"] if results else None
        if pid is None:
            pid = self._call("POST", "/management/v1/projects", {"name": project.name})["id"]
            log(f"프로젝트 생성: {project.name} → {pid}")

        for app in project.apps:
            found = self._call(
                "POST", f"/management/v1/projects/{pid}/apps/_search",
                {"queries": [{"nameQuery": {
                    "name": app.name, "method": "TEXT_QUERY_METHOD_EQUALS"}}]},
            )
            results = found.get("result") or []
            if not results:
                app_id = self._call(
                    "POST", f"/management/v1/projects/{pid}/apps/oidc",
                    {"name": app.name, **app.oidc},
                )["appId"]
                log(f"OIDC 앱 생성: {app.name} → {app_id}")
            else:
                app_id = results[0]["id"]
                self._call(
                    "PUT", f"/management/v1/projects/{pid}/apps/{app_id}/oidc_config",
                    app.oidc,
                )
                log(f"OIDC 앱 구성 갱신: {app.name}")

    def ensure_targets(self, targets: list[TargetSpec]) -> dict[str, str]:
        """선언 name → Zitadel target id 맵을 돌려준다(execution 참조 해석용)."""
        target_ids: dict[str, str] = {}
        for tgt in targets:
            found = self._call(
                "POST", "/v2/actions/targets/search",
                {"filters": [{"targetNameFilter": {
                    "targetName": tgt.name, "method": "TEXT_FILTER_METHOD_EQUALS"}}]},
            )
            results = found.get("targets") or []
            if not results:
                tid = self._call("POST", "/v2/actions/targets", tgt.body)["id"]
                log(f"Actions target 생성: {tgt.name} → {tid}")
            else:
                tid = results[0]["id"]
                self._call("POST", f"/v2/actions/targets/{tid}", tgt.body)
                log(f"Actions target 갱신: {tgt.name} → {tid}")
            target_ids[tgt.name] = tid
        return target_ids

    def ensure_executions(
        self, executions: list[ExecutionSpec], target_ids: dict[str, str]
    ) -> None:
        if not executions:
            return
        # 선언된 function이 실제 존재하는지 먼저 검증 — 오타가 조용히 no-op이
        # 되는 것을 막고, 실패 시 사용 가능 목록을 함께 보인다.
        available = set(
            self._call("GET", "/v2/actions/executions/functions").get("functions") or []
        )
        for exe in executions:
            if exe.function not in available:
                fail(f"미존재 function '{exe.function}' — 사용 가능: {sorted(available)}")
            try:
                tids = [target_ids[t] for t in exe.targets]
            except KeyError as e:
                fail(f"미선언 target 참조: {e.args[0]}")
            self._call(
                "PUT", "/v2/actions/executions",
                {"condition": {"function": {"name": exe.function}}, "targets": tids},
            )
            log(f"Execution 설정: function={exe.function}")


def main() -> None:
    config_file = os.environ.get("CONFIG_FILE", "/config/zitadel-config.yaml")
    pat_file = os.environ.get("PAT_FILE", "/secrets/pat/pat")
    with open(pat_file) as f:
        pat = f.read().strip()
    cfg = ZitadelConfig.load(config_file)

    api = ZitadelApi(
        os.environ["ZITADEL_URL"], os.environ["ZITADEL_EXTERNAL_DOMAIN"], pat
    )
    api.wait_ready()
    api.ensure_idps(cfg.idps)
    if cfg.project:
        api.ensure_project(cfg.project)
    target_ids = api.ensure_targets(cfg.targets)
    api.ensure_executions(cfg.executions, target_ids)
    log("수렴 완료")


if __name__ == "__main__":
    main()
