import pytest

from eigenaugen.src import stacks
from eigenaugen.src.errors import EigenaugenError
from eigenaugen.src.github import Resolved
from eigenaugen.src.stacks import Relation, StackSource


def node(number: int, base: str, head: str, state="OPEN", fork=False, repo="o/r"):
    return {
        "url": f"https://github.com/{repo}/pull/{number}",
        "baseRefOid": f"{number:040x}",
        "headRefOid": f"{number + 1000:040x}",
        "changedFiles": 1,
        "additions": 1,
        "deletions": 0,
        "state": state,
        "baseRefName": base,
        "headRefName": head,
        "isCrossRepository": fork,
    }


def pull(number: int, base: str, head: str, **kw) -> Resolved:
    return Resolved.from_json(node(number, base, head, **kw))


class FakeGraphQL:
    """Answers the three stack queries from tables; records branch lookups."""

    def __init__(self, native=None, by_head=None, by_base=None, fail=False):
        self.native = native
        self.by_head = by_head or {}
        self.by_base = by_base or {}
        self.fail = fail
        self.calls = []

    def __call__(self, host, query, **variables):
        self.calls.append((query, variables))
        if query == stacks.GITHUB_STACK_QUERY:
            if self.fail:
                raise EigenaugenError("Field 'stack' doesn't exist")
            return {"repository": {"pullRequest": {"stack": self.native}}}
        branch = variables["branch"]
        if query == stacks.BELOW_QUERY:
            return {
                "repository": {
                    "defaultBranchRef": {"name": "main"},
                    "pullRequests": {"nodes": self.by_head.get(branch, [])},
                }
            }
        return {"repository": {"pullRequests": {"nodes": self.by_base.get(branch, [])}}}


def use(monkeypatch, fake: FakeGraphQL) -> FakeGraphQL:
    monkeypatch.setattr(stacks.github, "graphql", fake)
    monkeypatch.setattr(stacks.log, "warn", lambda message: None)
    return fake


def positions(stack):
    return [(m.pull.pr.number, m.position, m.relation) for m in stack.members]


def test_github_stack_keeps_open_members_in_order(monkeypatch):
    native = {
        "number": 577,
        "entries": {
            "totalCount": 4,
            "nodes": [
                {"position": 3, "pullRequest": node(13, "b", "c")},
                {"position": 1, "pullRequest": node(11, "main", "a", state="MERGED")},
                {"position": 2, "pullRequest": node(12, "a", "b")},
                {"position": 4, "pullRequest": node(14, "c", "d")},
            ],
        },
    }
    use(monkeypatch, FakeGraphQL(native=native))
    stack = stacks.discover(pull(12, "a", "b"))
    assert (stack.source, stack.number, stack.anchor) == (
        StackSource.GITHUB,
        577,
        "o/r#12",
    )
    assert positions(stack) == [
        (12, 2, Relation.THIS),
        (13, 3, Relation.ABOVE),
        (14, 4, Relation.ABOVE),
    ]
    assert stack.describe() == "GitHub stack #577, 3 PRs"


def test_branch_chain_when_no_github_stack(monkeypatch):
    fake = use(
        monkeypatch,
        FakeGraphQL(
            by_head={
                "feat-1": [node(10, "main", "feat-1")],
                # A fork's branch of the same name can never be a base.
                "feat-2": [
                    node(99, "main", "feat-2", fork=True),
                    node(11, "feat-1", "feat-2"),
                ],
            },
            by_base={
                "feat-3": [
                    node(13, "feat-3", "feat-4a"),
                    node(14, "feat-3", "feat-4b"),
                ],
                "feat-4a": [node(15, "feat-4a", "feat-5", fork=True)],
            },
        ),
    )
    stack = stacks.discover(pull(12, "feat-2", "feat-3"))
    assert stack.source == StackSource.BRANCHES
    assert positions(stack) == [
        (10, 1, Relation.BELOW),
        (11, 2, Relation.BELOW),
        (12, 3, Relation.THIS),
        (13, 4, Relation.ABOVE),
        (14, 4, Relation.ABOVE),
        (15, 5, Relation.ABOVE),
    ]
    # Nothing can be stacked on a fork's branch: no lookup for #15.
    looked_up = [v["branch"] for q, v in fake.calls if q == stacks.ABOVE_QUERY]
    assert "feat-5" not in looked_up


def test_failed_github_lookup_falls_back_to_branches(monkeypatch):
    use(monkeypatch, FakeGraphQL(fail=True, by_base={"b": [node(13, "b", "c")]}))
    stack = stacks.discover(pull(12, "main", "b"))
    assert stack.source == StackSource.BRANCHES
    assert positions(stack) == [(12, 1, Relation.THIS), (13, 2, Relation.ABOVE)]


def test_lone_pr_has_no_stack(monkeypatch):
    use(monkeypatch, FakeGraphQL())
    assert stacks.discover(pull(12, "main", "b")) is None


def test_ambiguous_parent_stops_the_chain(monkeypatch):
    two = [node(10, "main", "a"), node(11, "release", "a")]
    use(monkeypatch, FakeGraphQL(by_head={"a": two}))
    assert stacks.discover(pull(12, "a", "b")) is None


def test_explicit_stack_validates(monkeypatch):
    a, b, c = pull(1, "main", "a"), pull(2, "a", "b"), pull(3, "b", "c")
    stack = stacks.explicit(b, [a, b, c])
    assert positions(stack) == [
        (1, 1, Relation.BELOW),
        (2, 2, Relation.THIS),
        (3, 3, Relation.ABOVE),
    ]
    with pytest.raises(EigenaugenError, match="must list the reviewed PR"):
        stacks.explicit(b, [a, c])
    with pytest.raises(EigenaugenError, match="twice"):
        stacks.explicit(b, [a, b, b])
    other = pull(4, "main", "x", repo="o/other")
    with pytest.raises(EigenaugenError, match="--related"):
        stacks.explicit(b, [b, other])
