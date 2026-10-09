import io
import json

import pytest

from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import PullRequest
from eigenaugen.src.mcp_server import github_tools
from eigenaugen.src.mcp_server.diffs import file_diff, section_paths, split_sections
from eigenaugen.src.mcp_server.github_tools import GitHubTools, ToolName
from eigenaugen.src.mcp_server.protocol import (
    SUPPORTED_PROTOCOL_VERSIONS,
    ErrorCode,
    Server,
    Tool,
)

DIFF = """\
diff --git a/app.py b/app.py
index 1..2 100644
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-x = 1
+x = 2
diff --git a/my dir/a b.txt b/my dir/a b.txt
new file mode 100644
--- /dev/null
+++ b/my dir/a b.txt
@@ -0,0 +1 @@
+hi
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""


def test_split_sections():
    sections = split_sections(DIFF)
    assert len(sections) == 4
    assert all(s.startswith("diff --git ") for s in sections)
    assert "".join(sections) == DIFF


def test_section_paths():
    sections = split_sections(DIFF)
    assert section_paths(sections[0]) == {"app.py"}
    assert section_paths(sections[1]) == {"my dir/a b.txt"}
    assert section_paths(sections[2]) == {"old.py", "new.py"}
    assert section_paths(sections[3]) == {"img.png"}


def test_file_diff_selects_section():
    assert file_diff(DIFF, "new.py").startswith("diff --git a/old.py b/new.py")
    assert "+hi" in file_diff(DIFF, "my dir/a b.txt")


def test_file_diff_unknown_path_lists_changes():
    with pytest.raises(EigenaugenError, match="app.py"):
        file_diff(DIFF, "nope.py")


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


PR = PullRequest.from_url("https://github.com/o/r/pull/1", "a" * 40, "b" * 40)


def test_github_tools_cache_diff_and_page(monkeypatch):
    calls = []
    monkeypatch.setattr(
        github_tools.github, "diff", lambda pr: calls.append(pr) or DIFF
    )
    tools = GitHubTools(PR)
    assert tools.get_diff({"path": "app.py"}).endswith("+x = 2\n")
    first = tools.get_diff({"limit": 3})
    assert first.startswith("[lines 1-3 of ")
    assert len(calls) == 1


@pytest.mark.parametrize("args", [{"offset": "1"}, {"limit": True}, {"path": ""}])
def test_github_tools_validate_args(monkeypatch, args):
    monkeypatch.setattr(github_tools.github, "diff", lambda pr: DIFF)
    with pytest.raises(EigenaugenError):
        GitHubTools(PR).get_diff(args)


def test_github_tools_base_file_requires_path():
    with pytest.raises(EigenaugenError):
        GitHubTools(PR).get_base_file({})


def test_tool_names_match_enum():
    assert {t.name for t in GitHubTools(PR).tools()} == set(ToolName)
