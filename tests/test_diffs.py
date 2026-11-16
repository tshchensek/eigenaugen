from eigenaugen.src.diffs import (
    LinePosition,
    is_binary,
    line_counts,
    primary_path,
    section_paths,
    split_sections,
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
+++ b/my dir/a b.txt\t
@@ -0,0 +1 @@
+hi
diff --git a/old.py b/new.py
similarity index 100%
rename from old.py
rename to new.py
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
"""


def test_split_sections():
    sections = split_sections(DIFF)
    assert len(sections) == 5
    assert all(s.startswith("diff --git ") for s in sections)
    assert "".join(sections) == DIFF


def test_section_paths():
    sections = split_sections(DIFF)
    assert section_paths(sections[0]) == {"app.py"}
    assert section_paths(sections[1]) == {"my dir/a b.txt"}
    assert section_paths(sections[2]) == {"old.py", "new.py"}
    assert section_paths(sections[3]) == {"img.png"}


def test_primary_path_is_new_path_or_deleted_path():
    paths = [primary_path(s) for s in split_sections(DIFF)]
    assert paths == ["app.py", "my dir/a b.txt", "new.py", "img.png", "gone.py"]


def test_primary_path_of_unparsable_header_uses_right_side():
    assert primary_path('diff --git "a/t\\tx" "b/t\\tx"\n') == '"b/t\\tx"'


def test_line_counts_ignore_headers():
    sections = split_sections(DIFF)
    assert line_counts(sections[0]) == (1, 1)
    assert line_counts(sections[4]) == (0, 2)
    assert line_counts(sections[3]) == (0, 0)


def test_is_binary():
    sections = split_sections(DIFF)
    assert [is_binary(s) for s in sections] == [False, False, False, True, False]


def test_line_position_tracks_hunks():
    position = LinePosition("a.py")
    assert position.note(mid_line=False) == "continues the header of `a.py`"
    for line in [
        "--- a/a.py\n",
        "@@ -10,3 +20,4 @@ def f():\n",
        " x\n",
        "-y\n",
        "+z\n",
    ]:
        position.advance(line)
    assert (position.old, position.new) == (12, 22)
    assert position.note(mid_line=False).endswith("next line is old 12, new 22")
    assert "long line" in position.note(mid_line=True)
