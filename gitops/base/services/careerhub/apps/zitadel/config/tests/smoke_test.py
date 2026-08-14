#!/usr/bin/env python3
"""구성 Job 스크립트의 스모크 테스트 — 시나리오·실행법은 README.md 참조.

mock Zitadel API 서버와 kubectl shim(임시 디렉터리에 생성)으로 entrypoint 쉘을
통째로 실행한다. 러너는 표준 라이브러리만 쓴다 — 피시험 스크립트가 bash·jq·
PyYAML을 요구하는 것은 그대로다(Job 실이미지 alpine/k8s와 동일 조건).
"""
import hashlib
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TESTS_DIR: Path = Path(__file__).resolve().parent
CONFIG_DIR: Path = TESTS_DIR.parent
ENTRYPOINT: Path = CONFIG_DIR / "apply-zitadel-config.sh"
PY_SCRIPT: Path = CONFIG_DIR / "apply_zitadel_config.py"

SECRET_NAME = "zitadel-app-careerhub-web"

# kubectl shim — 임시 state 디렉터리(KSTATE)가 가짜 클러스터 상태다.
#   get: state 파일이 있으면 성공(-o json은 내용, jsonpath는 checksum), 없으면 exit 1
#   patch: 호출을 calls.log에 기록만 한다
#   apply: stdin이 JSON Secret이면 state에 기록, 그 외(state CM yaml)는 소비만
KUBECTL_SHIM = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["KSTATE"]
args = sys.argv[1:]
verb = args[0] if args else ""
def log_call(line):
    with open(os.path.join(state, "calls.log"), "a") as f:
        f.write(line + "\\n")
if verb == "get":
    path = os.path.join(state, f"{args[1]}-{args[2]}.json")
    if not os.path.exists(path):
        sys.exit(1)
    rest = " ".join(args[3:])
    if "jsonpath" in rest:
        with open(path) as f:
            sys.stdout.write(json.load(f).get("data", {}).get("checksum", ""))
    elif "-o json" in rest:
        with open(path) as f:
            sys.stdout.write(f.read())
elif verb == "patch":
    log_call(f"patch {args[1]} {args[2]} {args[4]}")
elif verb == "create":
    sys.stdout.write("{}")
elif verb == "apply":
    stdin = sys.stdin.read()
    try:
        doc = json.loads(stdin)
    except json.JSONDecodeError:
        doc = None
    if isinstance(doc, dict) and doc.get("kind") == "Secret":
        name = doc["metadata"]["name"]
        with open(os.path.join(state, f"secret-{name}.json"), "w") as f:
            f.write(stdin)
        log_call(f"apply secret {name}")
    sys.stdout.write("applied\\n")
"""


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    status: int
    body: dict[str, Any]


NO_CHANGES = Route(
    "PUT", "/management/v1/projects/proj-1/apps/app-1/oidc_config", 400,
    # 실측(#29): v1 update API는 무변경 갱신을 400 gRPC code 9로 거부한다
    {"code": 9, "message": "No changes (COMMAND-1m88i)"},
)

CREATE_ROUTES = [
    Route("POST", "/admin/v1/idps/templates/_search", 200, {"result": []}),
    Route("POST", "/admin/v1/idps/google", 200, {"id": "idp-1"}),
    Route("POST", "/admin/v1/policies/login/idps/_search", 200, {"result": []}),
    Route("POST", "/admin/v1/policies/login/idps", 200, {}),
    Route("POST", "/management/v1/projects/_search", 200, {"result": []}),
    Route("POST", "/management/v1/projects", 200, {"id": "proj-1"}),
    Route("POST", "/management/v1/projects/proj-1/apps/_search", 200, {"result": []}),
    Route("POST", "/management/v1/projects/proj-1/apps/oidc", 200,
          {"appId": "app-1", "clientId": "cid-123"}),
    Route("POST", "/v2/actions/targets/search", 200, {"targets": []}),
    Route("POST", "/v2/actions/targets", 200, {"id": "tgt-1"}),
    Route("GET", "/v2/actions/executions/functions", 200,
          {"functions": ["preaccesstoken", "preuserinfo", "presamlresponse"]}),
    Route("PUT", "/v2/actions/executions", 200, {}),
]

UPDATE_ROUTES = [
    Route("POST", "/admin/v1/idps/templates/_search", 200, {"result": [{"id": "idp-1"}]}),
    Route("PUT", "/admin/v1/idps/google/idp-1", 200, {}),
    Route("POST", "/admin/v1/policies/login/idps/_search", 200, {"result": [{"idpId": "idp-1"}]}),
    Route("POST", "/management/v1/projects/_search", 200, {"result": [{"id": "proj-1"}]}),
    Route("POST", "/management/v1/projects/proj-1/apps/_search", 200,
          {"result": [{"id": "app-1", "oidcConfig": {"clientId": "cid-123"}}]}),
    NO_CHANGES,
    Route("POST", "/v2/actions/targets/search", 200, {"targets": [{"id": "tgt-1"}]}),
    Route("POST", "/v2/actions/targets/tgt-1", 200, {}),
    Route("GET", "/v2/actions/executions/functions", 200,
          {"functions": ["preaccesstoken", "preuserinfo"]}),
    Route("PUT", "/v2/actions/executions", 200, {}),
]

REGEN_ROUTES = UPDATE_ROUTES + [
    Route("POST",
          "/management/v1/projects/proj-1/apps/app-1/oidc_config/_generate_client_secret",
          200, {"clientSecret": "new-sec-456"}),
]

Call = tuple[str, str, Any]  # (method | "MISS" | "HDR", path, body)


def make_handler(routes: list[Route], calls: list[Call]) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _handle(self, method: str) -> None:
            if method == "GET" and self.path == "/debug/healthz":
                self._reply(200, b"ok", "text/plain")  # 실측(#28): 200 + 플레인 텍스트
                return
            body = None
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                body = json.loads(self.rfile.read(n))
            for header, want in (("Authorization", "Bearer test-pat-token"),
                                 ("Host", "id.example.test"),
                                 ("X-Forwarded-Proto", "https")):
                if self.headers.get(header) != want:
                    calls.append(("HDR", f"{header}={self.headers.get(header)}", None))
            for route in routes:
                if route.method == method and route.path == self.path:
                    calls.append((method, self.path, body))
                    self._reply(route.status, json.dumps(route.body).encode())
                    return
            calls.append(("MISS", f"{method} {self.path}", body))
            self._reply(404, b"{}")

        def do_GET(self) -> None: self._handle("GET")
        def do_POST(self) -> None: self._handle("POST")
        def do_PUT(self) -> None: self._handle("PUT")
        def log_message(self, *args: Any) -> None: pass

    return Handler


@dataclass(frozen=True)
class RunResult:
    proc: subprocess.CompletedProcess
    calls: list[Call]
    state: Path

    def calls_log(self) -> str:
        log = self.state / "calls.log"
        return log.read_text() if log.exists() else ""

    def api_calls(self, path_fragment: str) -> list[Call]:
        return [c for c in self.calls if path_fragment in c[1]]

    def check_clean(self, name: str) -> None:
        bad = [c for c in self.calls if c[0] in ("MISS", "HDR")]
        assert not bad, f"[{name}] 미매칭/헤더 위반: {bad}"
        assert self.proc.returncode == 0, (
            f"[{name}] exit {self.proc.returncode}\n{self.proc.stdout}\n{self.proc.stderr}")


def run_script(config: str, routes: list[Route] | None,
               seeds: dict[str, dict[str, Any]] | None = None) -> RunResult:
    """entrypoint 쉘을 임시 환경에서 실행한다. routes=None이면 서버 없이(닫힌 포트)."""
    tmp = Path(tempfile.mkdtemp(prefix="zitadel-smoke-"))
    state = tmp / "state"
    state.mkdir()
    bindir = tmp / "bin"
    bindir.mkdir()
    shim = bindir / "kubectl"
    shim.write_text(KUBECTL_SHIM)
    shim.chmod(0o755)
    (tmp / "pat").write_text("test-pat-token\n")
    for fname, doc in (seeds or {}).items():
        (state / fname).write_text(json.dumps(doc))

    calls: list[Call] = []
    srv: http.server.HTTPServer | None = None
    port = 9  # discard — skip 경로는 API를 대면 안 된다
    if routes is not None:
        srv = http.server.HTTPServer(("127.0.0.1", 0), make_handler(routes, calls))
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ,
               ZITADEL_URL=f"http://127.0.0.1:{port}",
               ZITADEL_EXTERNAL_DOMAIN="id.example.test",
               CONFIG_FILE=str(TESTS_DIR / config),
               PAT_FILE=str(tmp / "pat"),
               IDP_GOOGLE_CLIENT_ID="cid",
               IDP_GOOGLE_CLIENT_SECRET="sec",
               KSTATE=str(state),
               CREDS_STATE_FILE=str(tmp / "creds-state.json"),
               CREDS_OUT_FILE=str(tmp / "creds-out.json"),
               PATH=f"{bindir}:{os.environ['PATH']}")
    proc = subprocess.run(["bash", str(ENTRYPOINT)], env=env,
                          capture_output=True, text=True, timeout=60)
    if srv is not None:
        srv.shutdown()
    return RunResult(proc=proc, calls=calls, state=state)


def checksum(config: str) -> str:
    """entrypoint와 같은 방식의 선언+py+sh checksum."""
    h = hashlib.sha256()
    for p in (TESTS_DIR / config, PY_SCRIPT, ENTRYPOINT):
        h.update(p.read_bytes())
    return h.hexdigest()


EXISTING_SECRET = {"kind": "Secret", "metadata": {"name": SECRET_NAME},
                   "data": {"client_id": "Y2lkLTEyMw=="}}


def scenario_create() -> None:
    r = run_script("fixture-config.yaml", CREATE_ROUTES)
    r.check_clean("생성")
    sec = json.loads((r.state / f"secret-{SECRET_NAME}.json").read_text())
    assert sec["stringData"] == {"client_id": "cid-123"}, sec
    assert f"apply secret {SECRET_NAME}" in r.calls_log(), r.calls_log()
    assert not r.api_calls("_generate_client_secret")
    tgt = r.api_calls("/v2/actions/targets")[1]  # [0]은 search
    assert tgt[2]["restCall"]["interruptOnError"] is True, "fail-closed 미반영"
    exe = [c[2]["targets"] for c in r.api_calls("/v2/actions/executions") if c[0] == "PUT"]
    assert exe == [["tgt-1"], ["tgt-1"]], f"target id 매핑: {exe}"


def scenario_update() -> None:
    r = run_script("fixture-config.yaml", UPDATE_ROUTES,
                   seeds={f"secret-{SECRET_NAME}.json": EXISTING_SECRET})
    r.check_clean("갱신")
    want = f'patch secret {SECRET_NAME} {{"stringData":{{"client_id":"cid-123"}}}}'
    assert want in r.calls_log(), r.calls_log()
    assert not r.api_calls("_generate_client_secret"), "NONE 앱에 재발급 호출"


def scenario_regen() -> None:
    r = run_script("fixture-config-basic.yaml", REGEN_ROUTES,
                   seeds={f"secret-{SECRET_NAME}.json": EXISTING_SECRET})
    r.check_clean("재발급")
    assert len(r.api_calls("_generate_client_secret")) == 1
    want = (f'patch secret {SECRET_NAME} '
            '{"stringData":{"client_id":"cid-123","client_secret":"new-sec-456"}}')
    assert want in r.calls_log(), r.calls_log()


def scenario_skip_and_rotate() -> None:
    state_cm = {"data": {"checksum": checksum("fixture-config.yaml")}}
    # checksum 일치 + Secret 존재 → API를 대지 않고 생략 (서버 없음이 곧 검증)
    r = run_script("fixture-config.yaml", None,
                   seeds={"configmap-zitadel-config-state.json": state_cm,
                          f"secret-{SECRET_NAME}.json": EXISTING_SECRET})
    assert r.proc.returncode == 0 and "적용 생략" in r.proc.stdout, (
        f"skip 실패: {r.proc.returncode}\n{r.proc.stdout}\n{r.proc.stderr}")
    # checksum 일치 + Secret 부재(회전 삭제) → 재수렴해 재생성
    r = run_script("fixture-config.yaml", UPDATE_ROUTES,
                   seeds={"configmap-zitadel-config-state.json": state_cm})
    r.check_clean("회전")
    assert "자격증명 Secret 부재" in r.proc.stdout, r.proc.stdout
    assert f"apply secret {SECRET_NAME}" in r.calls_log(), r.calls_log()


def main() -> None:
    scenarios = [("생성 경로", scenario_create), ("갱신 경로", scenario_update),
                 ("재발급 경로", scenario_regen), ("skip·회전 가드", scenario_skip_and_rotate)]
    for name, fn in scenarios:
        fn()
        print(f"ok - {name}")
    print(f"SMOKE_OK ({len(scenarios)} scenarios)")


if __name__ == "__main__":
    main()
