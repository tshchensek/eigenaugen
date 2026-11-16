import io
import json

import pytest

from eigenaugen.src.context import ContextPull, ReviewContext, Role
from eigenaugen.src.coverage import Coverage
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.mcp_server import github_tools
from eigenaugen.src.mcp_server.github_tools import GitHubTools, ToolName
from eigenaugen.src.mcp_server.protocol import (
    SUPPORTED_PROTOCOL_VERSIONS,
    ErrorCode,
    Server,
    Tool,
)


def echo_tool(fail: Exception | None = None) -> Tool:
    def handler(args):
        if fail:
            raise fail
        return f"echo {args.get('x')}"

    return Tool("echo", "Echo x.", {"type": "object"}, handler)


def request(server: Server, method: str, params=None, msg_id=1):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(json.dumps(message))


def test_initialize_echoes_supported_version():
    server = Server("s", "1", [])
    old = SUPPORTED_PROTOCOL_VERSIONS[-1]
    result = request(server, "initialize", {"protocolVersion": old})["result"]
    assert result["protocolVersion"] == old
    assert result["capabilities"] == {"tools": {}}


def test_initialize_offers_newest_for_unknown_version():
    server = Server("s", "1", [])
    result = request(server, "initialize", {"protocolVersion": "1999-01-01"})
    assert result["result"]["protocolVersion"] == SUPPORTED_PROTOCOL_VERSIONS[0]


def test_notifications_get_no_response():
    server = Server("s", "1", [])
    line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert server.handle(line) is None


def test_errors():
    server = Server("s", "1", [echo_tool()])
    assert server.handle("{bad")["error"]["code"] == ErrorCode.PARSE_ERROR
    assert server.handle("[]")["error"]["code"] == ErrorCode.INVALID_REQUEST
    unknown = request(server, "nope")["error"]["code"]
    assert unknown == ErrorCode.METHOD_NOT_FOUND
    missing = request(server, "tools/call", {"name": "missing"})
    assert missing["error"]["code"] == ErrorCode.INVALID_PARAMS


def test_tools_list_and_call():
    server = Server("s", "1", [echo_tool()])
    tools = request(server, "tools/list")["result"]["tools"]
    assert tools == [
        {"name": "echo", "description": "Echo x.", "inputSchema": {"type": "object"}}
    ]
    result = request(server, "tools/call", {"name": "echo", "arguments": {"x": 3}})
    assert result["result"] == {
        "content": [{"type": "text", "text": "echo 3"}],
        "isError": False,
    }


@pytest.mark.parametrize(
    "fail,text",
    [(EigenaugenError("gh failed"), "gh failed"), (KeyError("k"), "internal error")],
)
def test_tool_failures_become_error_results(fail, text):
    server = Server("s", "1", [echo_tool(fail)])
    result = request(server, "tools/call", {"name": "echo"})["result"]
    assert result["isError"] is True
    assert text in result["content"][0]["text"]


def test_serve_round_trip():
    server = Server("s", "1", [echo_tool()])
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
        "",
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]
    out = io.StringIO()
    server.serve(io.StringIO("\n".join(lines) + "\n"), out)
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [1, 2]


REVIEWED = "o/r#1"
OTHER = "o/r#2"
DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-x = 1
+x = 2
diff --git a/yarn.lock b/yarn.lock
--- a/yarn.lock
+++ b/yarn.lock
@@ -1 +1 @@
-a
+b
"""


def context_pull(tmp_path, key: str, role: Role) -> ContextPull:
    number = key.rpartition("#")[2]
    diff_file = tmp_path / f"{number}.diff"
    diff_file.write_text(DIFF)
    return ContextPull(
        key=key,
        url=f"https://github.com/o/r/pull/{number}",
        base_sha="a" * 40,
        head_sha="b" * 40,
        merge_base_sha="c" * 40,
        behind_by=0,
        role=role,
        state="OPEN",
        changed_files=2,
        additions=2,
        deletions=2,
        repo_dir=str(tmp_path / "repo"),
        diff_file=str(diff_file),
        skim={"yarn.lock": "lockfile"},
    )


@pytest.fixture
def context(tmp_path) -> ReviewContext:
    context = ReviewContext(
        reviewed=REVIEWED,
        pulls={
            REVIEWED: context_pull(tmp_path, REVIEWED, Role.REVIEWED),
            OTHER: context_pull(tmp_path, OTHER, Role.STACK),
        },
        stacks=(),
        coverage_file=str(tmp_path / "coverage.json"),
    )
    path = tmp_path / "context.json"
    context.save(path)
    return ReviewContext.load(path)


def test_context_round_trips(context, tmp_path):
    assert context.pull(None).key == REVIEWED
    assert context.pull(OTHER).role == Role.STACK
    with pytest.raises(
        EigenaugenError, match="unknown PR 'x/y#3'; known PRs: o/r#1, o/r#2"
    ):
        context.pull("x/y#3")


def test_get_diff_records_reviewed_pages_only(context):
    tools = GitHubTools(context)
    page = tools.get_diff({"chunk": 1})
    assert page.startswith("[o/r#1 diff chunk 1 of 2 (review), page 1 of 1]\n")
    assert "+x = 2" in page and "yarn.lock" not in page
    tools.get_diff({"pr": OTHER, "chunk": 2})
    tools.get_diff({"path": "yarn.lock"})
    recorded = Coverage.load(tools.coverage_file)
    assert recorded.chunk_pages == {(1, 1)}
    assert recorded.path_pages == {"yarn.lock": {1}}
    # A restarted server (resumed session) continues the record.
    assert GitHubTools(context).coverage == recorded


@pytest.mark.parametrize(
    "args,match",
    [
        ({}, "exactly one of"),
        ({"chunk": 1, "path": "app.py"}, "exactly one of"),
        ({"chunk": 3}, "no chunk 3"),
        ({"chunk": 1, "page": 2}, "pages are 1 to 1"),
        ({"chunk": "1"}, "must be an integer"),
        ({"page": True, "chunk": 1}, "must be an integer"),
        ({"path": ""}, "non-empty string"),
        ({"pr": "x/y#1", "chunk": 1}, "unknown PR"),
    ],
)
def test_get_diff_validates_args(context, args, match):
    with pytest.raises(EigenaugenError, match=match):
        GitHubTools(context).get_diff(args)


def test_get_diff_index(context):
    tools = GitHubTools(context)
    index = tools.get_diff_index({})
    assert index.startswith("[o/r#1 diff index, page 1 of 1]\no/r#1 diff: 2 files")
    assert "| lockfile | `.` | 1 | +1 -1 |" in index
    chunk = tools.get_diff_index({"pr": OTHER, "chunk": 2})
    assert "o/r#2 diff chunk 2 of 2 (skim)" in chunk


class FakeRepo:
    def __init__(self, path):
        self.path = path

    def file(self, sha, path):
        return f"{path} at {sha[0]}\n"


def test_get_file_sides(context, monkeypatch):
    monkeypatch.setattr(github_tools, "GitRepo", FakeRepo)
    github_tools._file_pages.cache_clear()
    tools = GitHubTools(context)
    assert tools.get_file({"path": "app.py"}).endswith("app.py at b\n")
    assert tools.get_file({"path": "app.py", "side": "base"}).endswith("at a\n")
    merge_base = tools.get_file({"pr": OTHER, "path": "a", "side": "merge-base"})
    assert merge_base.startswith("[`a` at o/r#2 merge-base cccccccccccc, page 1 of 1]")
    with pytest.raises(EigenaugenError, match="side must be one of"):
        tools.get_file({"path": "app.py", "side": "tip"})
    with pytest.raises(EigenaugenError, match="non-empty string"):
        tools.get_file({})


def test_get_pull_request_adds_merge_base_and_role(context, monkeypatch):
    monkeypatch.setattr(
        github_tools.github,
        "metadata",
        lambda pr: {"number": pr.number, "pinned": {"headRefOid": pr.head_sha}},
    )
    data = json.loads(GitHubTools(context).get_pull_request({"pr": OTHER}))
    assert data["number"] == 2
    assert data["pinned"]["mergeBaseOid"] == "c" * 40
    assert data["role"] == "stack"


def test_tool_names_match_enum(context):
    assert {t.name for t in GitHubTools(context).tools()} == set(ToolName)
