#!/usr/bin/env python3
"""Tests for difftour.py — run: python3 difftour_test.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "difftour.py")
sys.path.insert(0, HERE)
import difftour  # noqa: E402

OUT = tempfile.mkdtemp()  # run directories go here, not to ~/.cache


# --- builders ----------------------------------------------------------------------------

def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], cwd=repo, check=True,
                   capture_output=True)


def write(repo, path, data):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode())


def repo(files=None):
    path = tempfile.mkdtemp()
    git(path, "init", "-q", "-b", "main")
    for name, data in (files or {}).items():
        write(path, name, data)
    if files:
        git(path, "add", "-A")
        git(path, "commit", "-qm", "init")
    return path


def run(cwd, *args):
    proc = subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, capture_output=True,
                          text=True)
    return proc.returncode, proc.stdout, proc.stderr


def collect(cwd, *args):
    code, out, err = run(cwd, "collect", *args, "--out-root", OUT)
    assert code == 0, err
    assert out.startswith("Run directory: "), out
    return out.splitlines()[0].split(": ", 1)[1], out


def index(run_dir):
    with open(os.path.join(run_dir, "hunks.json"), encoding="utf-8") as fh:
        return json.load(fh)["units"]


def by_path(run_dir):
    return {u["path"]: u for u in index(run_dir)}


def build(run_dir, notes):
    with open(os.path.join(run_dir, "notes.json"), "w", encoding="utf-8") as fh:
        json.dump(notes, fh, ensure_ascii=False)
    return run(run_dir, "build", run_dir, "--all")


def triage(run_dir, name, **data):
    data.setdefault("units_seen", len(index(run_dir)))
    with open(os.path.join(run_dir, f"triage-{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def page(run_dir):
    with open(os.path.join(run_dir, "index.html"), encoding="utf-8") as fh:
        return fh.read()


def every_kind(hostile):
    """A repository whose working tree holds one change of every kind the parser knows."""
    path = repo({
        "mod.txt": "a\nb\nc\n\nd\n", "del.txt": "x\n", "old name.txt": "same\nline2\nline3\n",
        "bin.dat": b"\x00\x01\x02", "s.sh": "#!/bin/sh\necho hi\n", "nonl.txt": "no newline",
        "package-lock.json": "{}\n", ".gitignore": "ignored.log\n", "sub/keep.txt": "k\n",
    })
    write(path, "mod.txt", "a\nB\nc\n\nd\n")
    os.remove(os.path.join(path, "del.txt"))
    git(path, "mv", "old name.txt", "новое имя.txt")
    write(path, "bin.dat", b"\x00\x09\x02")
    os.chmod(os.path.join(path, "s.sh"), 0o755)
    write(path, "nonl.txt", "no newline changed")
    write(path, "package-lock.json", '{"a": 1}\n')
    write(path, "staged.txt", "staged\n")
    git(path, "add", "staged.txt")
    write(path, "untracked dir/u.cs", "class U {}\n")
    write(path, "newbin.bin", b"\x00bin")
    write(path, "ignored.log", "x\n")
    if hostile:
        for key, value in (("diff.noprefix", "true"), ("diff.mnemonicPrefix", "true"),
                           ("color.ui", "always"), ("color.diff", "always"),
                           ("diff.suppressBlankEmpty", "true"), ("diff.external", "false"),
                           ("diff.relative", "true"), ("core.quotepath", "true")):
            git(path, "config", key, value)
    return path


# --- collect -----------------------------------------------------------------------------

class Collect(unittest.TestCase):

    def test_every_kind_of_change(self):
        for hostile in (False, True):
            with self.subTest(hostile=hostile):
                path = every_kind(hostile)
                cwd = os.path.join(path, "sub") if hostile else path  # diff.relative + subdir
                run_dir, out = collect(cwd)
                units = by_path(run_dir)
                self.assertEqual(units["mod.txt"]["status"], "M")
                self.assertEqual((units["mod.txt"]["added"], units["mod.txt"]["removed"]),
                                 (1, 1))
                self.assertEqual(units["del.txt"]["status"], "D")
                self.assertEqual(units["новое имя.txt"]["status"], "R")
                self.assertIn("renamed from old name.txt", units["новое имя.txt"]["facts"])
                self.assertIn("binary", units["bin.dat"]["facts"])
                self.assertIn("mode 100644 -> 100755", units["s.sh"]["facts"])
                self.assertEqual(units["staged.txt"]["status"], "A")
                self.assertEqual(units["untracked dir/u.cs"]["status"], "A")
                self.assertIn("binary", units["newbin.bin"]["facts"])
                self.assertTrue(units["package-lock.json"]["id"].startswith("n"))
                self.assertIn("noise", units["package-lock.json"]["facts"])
                self.assertNotIn("ignored.log", units)
                self.assertEqual(len(units), 10)
                meta, files, _ = difftour.load_snapshot(run_dir)
                nonl = next(f for f in files if f["new"] == "nonl.txt")
                self.assertIn(["\\", "No newline at end of file"], nonl["hunks"][0]["lines"])
                self.assertEqual(meta["root"], os.path.realpath(path))

    def test_index_does_not_change(self):
        path = every_kind(False)
        before = subprocess.run(["git", "diff", "--cached", "--name-status"], cwd=path,
                                capture_output=True, text=True).stdout
        collect(path)
        after = subprocess.run(["git", "diff", "--cached", "--name-status"], cwd=path,
                               capture_output=True, text=True).stdout
        self.assertEqual(before, after)

    def test_quoted_paths(self):
        path = repo({'q"uote.txt': "1\n"})
        write(path, 'q"uote.txt', "2\n")
        write(path, "tab\tname.txt", "t\n")
        run_dir, _ = collect(path)
        self.assertEqual(set(by_path(run_dir)), {'q"uote.txt', "tab\tname.txt"})

    def test_no_changes(self):
        path = repo({"a.txt": "a\n"})
        code, out, _ = run(path, "collect", "--out-root", OUT)
        self.assertEqual((code, out.strip()), (0, "No changes."))
        self.assertFalse(os.path.exists(os.path.join(OUT, os.path.basename(path))))

    def test_not_a_repository(self):
        code, _, err = run(tempfile.mkdtemp(), "collect", "--out-root", OUT)
        self.assertEqual(code, 2)
        self.assertIn("not inside a git working tree", err)

    def test_unknown_ref(self):
        code, _, err = run(repo({"a.txt": "a\n"}), "collect", "nope", "--out-root", OUT)
        self.assertEqual(code, 2)
        self.assertIn("not a commit: nope", err)

    def test_repository_without_commits(self):
        path = repo()
        write(path, "a.txt", "a\n")
        write(path, "b.txt", "b\n")
        git(path, "add", "b.txt")
        run_dir, out = collect(path)
        self.assertIn("Base: empty tree", out)
        self.assertEqual(set(by_path(run_dir)), {"a.txt", "b.txt"})

    def test_range_leaves_out_the_working_tree(self):
        path = repo({"a.txt": "1\n"})
        write(path, "a.txt", "2\n")
        git(path, "commit", "-qam", "two")
        write(path, "a.txt", "3\n")
        write(path, "u.txt", "u\n")
        run_dir, _ = collect(path, "HEAD~1..HEAD")
        units = index(run_dir)
        self.assertEqual([u["path"] for u in units], ["a.txt"])
        _, files, _ = difftour.load_snapshot(run_dir)
        self.assertIn(["+", "2"], files[0]["hunks"][0]["lines"])

    def test_project_noise_patterns(self):
        path = repo({".claude/diff-tour-noise": "# generated\ngenerated/\n*.g.cs\n"})
        for name in ("generated/x.cs", "src/a.g.cs", "src/c.cs"):
            write(path, name, "x\n")
        run_dir, _ = collect(path)
        units = by_path(run_dir)
        self.assertIn("noise", units["generated/x.cs"]["facts"])
        self.assertIn("noise", units["src/a.g.cs"]["facts"])
        self.assertNotIn("noise", units["src/c.cs"]["facts"])

    def test_noise_patterns_come_from_the_base(self):
        path = repo({"src/a.cs": "1\n"})
        write(path, ".claude/diff-tour-noise", "src/\n")  # added by the change itself
        write(path, "src/a.cs", "2\n")
        units = by_path(collect(path)[0])
        self.assertNotIn("noise", units["src/a.cs"]["facts"])
        self.assertEqual(units[".claude/diff-tour-noise"]["risk"],
                         ["path: .claude/diff-tour-noise"])

    def test_attributes_come_from_the_base(self):
        path = repo({"a.cs": "one\n"})
        write(path, ".gitattributes", "*.cs -diff\n")  # would show a.cs as "Binary files differ"
        write(path, "a.cs", "two\n")
        run_dir, _ = collect(path)
        _, files, _ = difftour.load_snapshot(run_dir)
        a = next(f for f in files if f["new"] == "a.cs")
        self.assertFalse(a["binary"])
        self.assertIn(["+", "two"], a["hunks"][0]["lines"])
        self.assertEqual(by_path(run_dir)[".gitattributes"]["risk"], ["path: .gitattributes"])

    def test_snapshot_records_where_to_read_context(self):
        path = repo({"a.txt": "1\n"})
        write(path, "a.txt", "2\n")
        git(path, "commit", "-qam", "two")
        write(path, "a.txt", "3\n")
        head, base = (subprocess.run(["git", "rev-parse", r], cwd=path, check=True, capture_output=True,
                                     text=True).stdout.strip() for r in ("HEAD", "HEAD~1"))
        for args, new, old in (((), None, head), (("HEAD~1..HEAD",), head, base),
                               (("HEAD~1..",), head, base)):
            with self.subTest(args=args):
                run_dir, _ = collect(path, *args)
                with open(os.path.join(run_dir, "hunks.json"), encoding="utf-8") as fh:
                    meta = json.load(fh)["meta"]
                # pinned: build and the reviewers read these commits after the branch moves on
                self.assertEqual(meta["new_rev"], new)
                self.assertEqual(meta["old_rev"], old)

    def test_nested_repository_is_reported_not_diffed(self):
        path = repo({"a.txt": "a\n"})
        nested = os.path.join(path, "nested")
        os.makedirs(nested)
        git(nested, "init", "-q")
        write(path, "nested/x.txt", "x\n")
        write(path, "a.txt", "b\n")
        run_dir, out = collect(path)
        self.assertIn("Skipped untracked nested repository: nested/", out)
        self.assertEqual(list(by_path(run_dir)), ["a.txt"])


class CollectRegressions(unittest.TestCase):

    def test_index_file_is_not_rewritten(self):
        path = repo({"a.txt": "a\n", "b.txt": "b\n"})
        write(path, "a.txt", "A\n")
        write(path, "new.txt", "n\n")                       # takes the throwaway-index path
        later = os.path.getmtime(os.path.join(path, "b.txt")) + 5
        os.utime(os.path.join(path, "b.txt"), (later, later))  # stat-dirty, same content
        index_file = os.path.join(path, ".git", "index")
        with open(index_file, "rb") as fh:
            before = fh.read()
        run_dir, _ = collect(path)
        code, _, err = build(run_dir, {"title": "t", "lede": "l", "notes": []})
        self.assertEqual(code, 0, err)
        with open(index_file, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_same_second_same_size_edit_is_not_lost(self):
        # Racy git: the index and the edited file share one mtime, and the edit keeps the size,
        # so only the index's own mtime tells git to look at the content. A copy of the index
        # with a fresh mtime made git trust the stale stat and drop a.txt from the diff.
        path = repo()
        git(path, "config", "core.trustctime", "false")
        a, then = os.path.join(path, "a.txt"), 1_700_000_000
        write(path, "a.txt", "1\n")
        os.utime(a, (then, then))
        git(path, "add", "a.txt")
        git(path, "commit", "-qm", "init")
        write(path, "a.txt", "2\n")
        os.utime(a, (then, then))
        os.utime(os.path.join(path, ".git", "index"), (then, then))
        write(path, "u.txt", "u\n")                         # takes the throwaway-index path
        run_dir, _ = collect(path)
        self.assertEqual(set(by_path(run_dir)), {"a.txt", "u.txt"})
        code, out, _ = build(run_dir, notes_with())         # check_drift takes the same path
        self.assertEqual(code, 0)
        self.assertNotIn("WARNING", out)

    def test_submodule_change_survives_diff_submodule_log(self):
        src = repo({"a.txt": "a\n"})
        path = repo({"x.txt": "x\n"})
        git(path, "-c", "protocol.file.allow=always", "submodule", "add", "-q", src, "sub")
        git(path, "commit", "-qm", "sub")
        write(os.path.join(path, "sub"), "a.txt", "b\n")
        git(os.path.join(path, "sub"), "commit", "-qam", "bump")
        git(path, "config", "diff.submodule", "log")
        run_dir, _ = collect(path)                            # a submodule-only change
        self.assertEqual(list(by_path(run_dir)), ["sub"])
        git(path, "config", "diff.submodule", "diff")
        write(path, "x.txt", "y\n")
        run_dir, _ = collect(path)
        self.assertEqual(set(by_path(run_dir)), {"sub", "x.txt"})

    def test_untracked_symlink_is_a_link(self):
        outside = tempfile.mkdtemp()
        write(outside, "null", "outside secret\n")
        path = repo({"a.txt": "a\n"})
        os.symlink(outside, os.path.join(path, "linkdir"))
        run_dir, _ = collect(path)
        self.assertEqual(list(by_path(run_dir)), ["linkdir"])
        _, files, _ = difftour.load_snapshot(run_dir)
        self.assertEqual(files[0]["new_mode"], "120000")
        self.assertIn(["+", outside], files[0]["hunks"][0]["lines"])
        with open(os.path.join(run_dir, "patch.diff"), encoding="utf-8") as fh:
            self.assertNotIn("outside secret", fh.read())

    def test_commit_after_collect_is_drift(self):
        path = three_changes()
        run_dir, _ = collect(path)
        git(path, "commit", "-qam", "all of it")
        _, out, _ = build(run_dir, NOTES)
        self.assertIn("WARNING: the working tree or HEAD changed", out)

    def test_sha256_repository_without_commits(self):
        path = tempfile.mkdtemp()
        git(path, "init", "-q", "--object-format=sha256", "-b", "main")
        write(path, "a.txt", "a\n")
        run_dir, out = collect(path)
        self.assertIn("Base: empty tree", out)
        self.assertEqual(list(by_path(run_dir)), ["a.txt"])

    def test_ref_is_compared_from_the_merge_base(self):
        path = repo({"a.txt": "a\n"})
        git(path, "checkout", "-q", "-b", "feature")
        write(path, "f.txt", "f\n")
        git(path, "add", "f.txt")
        git(path, "commit", "-qm", "feature work")
        git(path, "checkout", "-q", "main")
        write(path, "m.txt", "m\n")
        git(path, "add", "m.txt")
        git(path, "commit", "-qm", "main moved on")
        git(path, "checkout", "-q", "feature")
        write(path, "f.txt", "f\nwip\n")
        run_dir, out = collect(path, "main")
        self.assertIn("Base: merge-base of main and HEAD", out)
        self.assertEqual(list(by_path(run_dir)), ["f.txt"])  # not m.txt reversed
        build(run_dir, notes_with())
        self.assertIn("branch since the base + uncommitted", page(run_dir))

    def test_tree_range_for_a_caller(self):
        # What ticket's publish gate passes: the base commit .. the reviewed tree id.
        path = repo({"a.txt": "1\n"})
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True,
                              text=True).stdout.strip()
        write(path, "a.txt", "2\n")
        write(path, "n.txt", "n\n")
        git(path, "add", "-A")
        tree = subprocess.run(["git", "write-tree"], cwd=path, capture_output=True,
                              text=True).stdout.strip()
        write(path, "a.txt", "3\n")                         # later edits stay out of it
        run_dir, out = collect(path, f"{base}..{tree}")
        self.assertIn(f"Base: {base}..{tree}", out)
        self.assertEqual(set(by_path(run_dir)), {"a.txt", "n.txt"})
        _, files, _ = difftour.load_snapshot(run_dir)
        self.assertIn(["+", "2"], files[0]["hunks"][0]["lines"])
        code, out, err = build(run_dir, notes_with())
        self.assertEqual(code, 0, err)
        self.assertNotIn("WARNING", out)

    def test_head_base_is_uncommitted(self):
        run_dir, _ = collect(three_changes())
        build(run_dir, notes_with())
        self.assertIn("· uncommitted</span>", page(run_dir))


def feature_branch():
    """main with one commit, feature with two commits on top of it, nothing uncommitted."""
    path = repo({"a.txt": "a\n"})
    git(path, "checkout", "-q", "-b", "feature")
    for name in ("f1.txt", "f2.txt"):
        write(path, name, name + "\n")
        git(path, "add", name)
        git(path, "commit", "-qm", name)
    return path


class AfterCommit(unittest.TestCase):

    def test_falls_back_to_the_default_branch(self):
        run_dir, out = collect(feature_branch())
        self.assertIn("Base: main (", out)
        self.assertIn("Nothing uncommitted — showing the commits HEAD has beyond main", out)
        self.assertEqual(set(by_path(run_dir)), {"f1.txt", "f2.txt"})
        code, out, err = build(run_dir, notes_with())
        self.assertEqual(code, 0, err)
        self.assertNotIn("WARNING", out)

    def test_upstream_comes_first(self):
        path = feature_branch()
        git(path, "branch", "pushed", "HEAD~1")
        git(path, "branch", "-u", "pushed")
        run_dir, out = collect(path)
        self.assertIn("beyond pushed", out)
        self.assertEqual(list(by_path(run_dir)), ["f2.txt"])

    def test_up_to_date_upstream_falls_through(self):
        path = feature_branch()
        git(path, "branch", "pushed", "HEAD")
        git(path, "branch", "-u", "pushed")
        run_dir, out = collect(path)
        self.assertIn("beyond main", out)
        self.assertEqual(set(by_path(run_dir)), {"f1.txt", "f2.txt"})

    def test_uncommitted_work_wins(self):
        path = feature_branch()
        write(path, "a.txt", "b\n")
        run_dir, out = collect(path)
        self.assertNotIn("Nothing uncommitted", out)
        self.assertEqual(list(by_path(run_dir)), ["a.txt"])

    def test_unrelated_history_is_skipped(self):
        path = repo({"a.txt": "a\n"})
        git(path, "checkout", "-q", "--orphan", "pages")
        git(path, "rm", "-rqf", ".")
        write(path, "index.html", "x\n")
        git(path, "add", "index.html")
        git(path, "commit", "-qm", "pages")
        code, out, _ = run(path, "collect", "--out-root", OUT)
        lines = out.strip().splitlines()
        self.assertEqual((code, lines[0]), (0, "No changes."))
        self.assertEqual(len(lines), 2, out)
        self.assertIn("HEAD shares no history with main, so nothing is shown", lines[1])
        self.assertIn("pass a ref or range", lines[1])
        # an explicit ref still works on the orphan branch: main's file removed, index.html added
        code, out, err = run(path, "collect", "main", "--out-root", OUT)
        self.assertEqual(code, 0, err)
        self.assertNotIn("shares no history", out)

    def test_pushed_orphan_branch_still_says_why(self):
        # `git push -u origin lone`: the upstream is HEAD itself, main and origin/main unrelated
        path = repo({"a.txt": "a\n"})
        remote = tempfile.mkdtemp(prefix="difftour-remote-")
        self.addCleanup(shutil.rmtree, remote, True)
        git(remote, "init", "-q", "--bare")
        git(path, "remote", "add", "origin", remote)
        git(path, "push", "-q", "origin", "main")
        git(path, "checkout", "-q", "--orphan", "lone")
        git(path, "rm", "-rqf", ".")
        write(path, "index.html", "x\n")
        git(path, "add", "index.html")
        git(path, "commit", "-qm", "lone")
        git(path, "push", "-q", "-u", "origin", "lone")
        code, out, _ = run(path, "collect", "--out-root", OUT)
        lines = out.strip().splitlines()
        self.assertEqual((code, lines[0]), (0, "No changes."))
        self.assertEqual(len(lines), 2, out)
        self.assertIn("HEAD shares no history with main, origin/main", lines[1])

    def test_related_but_up_to_date_says_nothing_of_history(self):
        path = feature_branch()
        git(path, "checkout", "-q", "main")
        code, out, _ = run(path, "collect", "--out-root", OUT)
        self.assertEqual((code, out.strip()), (0, "No changes."))

    def test_explicit_ref_never_falls_back(self):
        path = feature_branch()
        code, out, _ = run(path, "collect", "HEAD", "--out-root", OUT)
        self.assertEqual((code, out.strip()), (0, "No changes."))


class RunDirectories(unittest.TestCase):

    def test_private_and_rotated(self):
        root = os.path.join(tempfile.mkdtemp(), "cache")
        path = repo({"a.txt": "a\n"})
        write(path, "a.txt", "b\n")
        repo_dir = os.path.join(root, os.path.basename(path))
        os.makedirs(repo_dir)
        for k in range(difftour.KEEP_RUNS + 3):              # older runs, oldest first
            old = os.path.join(repo_dir, f"20200101-0000{k:02d}")
            os.makedirs(old)
            with open(os.path.join(old, "hunks.json"), "w") as fh:
                fh.write("{}")
        os.makedirs(os.path.join(repo_dir, "keep-me"))       # not a run directory
        code, out, err = run(path, "collect", "--out-root", root)
        self.assertEqual(code, 0, err)
        run_dir = out.splitlines()[0].split(": ", 1)[1]
        self.assertEqual(os.stat(run_dir).st_mode & 0o777, 0o700)
        left = sorted(os.listdir(repo_dir))
        self.assertIn("keep-me", left)
        self.assertIn(os.path.basename(run_dir), left)
        runs = [d for d in left if d != "keep-me"]
        self.assertEqual(len(runs), difftour.KEEP_RUNS)
        self.assertNotIn("20200101-000000", runs)            # the oldest went first
        self.assertIn(f"20200101-0000{difftour.KEEP_RUNS + 2:02d}", runs)

    def test_out_root_runs_live_outside_the_default_cache(self):
        path = repo({"a.txt": "a\n"})
        write(path, "a.txt", "b\n")
        kept = os.path.join(tempfile.mkdtemp(), "ticket-run")
        code, out, err = run(path, "collect", "HEAD", "--out-root", kept)
        self.assertEqual(code, 0, err)
        page_dir = out.splitlines()[0].split(": ", 1)[1]
        self.assertTrue(page_dir.startswith(kept + os.sep))
        self.assertFalse(page_dir.startswith(difftour.OUT_ROOT + os.sep))
        cache = os.path.join(tempfile.mkdtemp(), "cache")
        for _ in range(difftour.KEEP_RUNS + 2):
            self.assertEqual(run(path, "collect", "--out-root", cache)[0], 0)
        self.assertTrue(os.path.isfile(os.path.join(page_dir, "hunks.json")))

    def test_new_directories_are_private(self):
        root = os.path.join(tempfile.mkdtemp(), "a", "cache")
        run_dir = difftour.new_run_dir(root, "repo")
        for d in (root, os.path.dirname(run_dir), run_dir):
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700, d)


class Noise(unittest.TestCase):

    def test_patterns(self):
        cases = [
            ("a/package-lock.json", ["package-lock.json"], True),
            ("gen_out/a.cs", ["gen*/"], True), ("x/gen_out/a.cs", ["gen*/"], True),
            ("gen_out.cs", ["gen*/"], False),
            ("Assets/Generated/a.cs", ["Assets/Gen*/"], True),
            ("Other/Assets/Generated/a.cs", ["Assets/Gen*/"], False),
            ("src/obj/a.cs", ["**/obj/"], True), ("src/obj/a.cs", ["/obj/"], False),
            ("obj/a.cs", ["/obj/"], True), ("a.g.cs", ["**/*.g.cs"], True),
            ("src/a.g.cs", ["src/*.g.cs"], True), ("lib/src/a.g.cs", ["src/*.g.cs"], False),
            ("x/gen/a.cs", ["**/gen/*.cs"], True), ("src/c.cs", ["generated/", "*.g.cs"], False),
        ]
        for path, patterns, expected in cases:
            with self.subTest(path=path, patterns=patterns):
                self.assertEqual(difftour.is_noise(path, patterns), expected)


class Risk(unittest.TestCase):

    def risk_of(self, run_dir):
        return {u["id"]: u["risk"] for u in index(run_dir)}

    def path_risk(self, run_dir):
        return {u["path"]: u["risk"] for u in index(run_dir)}

    def test_project_rules_are_read_from_the_base(self):
        r = repo({".claude/review-risk": "# money\npath src/pay/\nline \\bcharge\\(\n",
                  "src/pay/a.py": "1\n", "src/ui/b.py": "1\n", "src/ui/c.py": "1\n"})
        write(r, "src/pay/a.py", "2\n")
        write(r, "src/ui/b.py", "2\n")
        write(r, "src/ui/c.py", "charge(10)\n")
        run_dir, out = collect(r)
        risk = self.path_risk(run_dir)
        self.assertEqual(risk["src/pay/a.py"], ["path: src/pay/"])
        self.assertEqual(risk["src/ui/b.py"], [])
        self.assertEqual(risk["src/ui/c.py"], ["line: charge(10)"])
        self.assertIn("Risk rules (built-in + .claude/review-risk at the base): 2 units", out)

    def test_change_cannot_edit_its_own_rules(self):
        r = repo({".claude/review-risk": "path src/pay/\n", "src/pay/a.py": "1\n"})
        write(r, ".claude/review-risk", "path docs/\n")
        write(r, "src/pay/a.py", "2\n")
        self.assertEqual(self.path_risk(collect(r)[0])["src/pay/a.py"], ["path: src/pay/"])

    def test_rules_added_by_the_change_do_not_apply_yet(self):
        r = repo({"src/pay/a.py": "1\n"})
        write(r, ".claude/review-risk", "path src/pay/\n")
        write(r, "src/pay/a.py", "2\n")
        self.assertEqual(self.path_risk(collect(r)[0])["src/pay/a.py"], [])

    def test_file_moved_out_of_a_risky_path_stays_risky(self):
        body = "".join(f"line {i}\n" for i in range(20))
        r = repo({".claude/review-risk": "path src/pay/\n", "src/pay/a.py": body})
        git(r, "mv", "src/pay/a.py", "src/a.py")
        self.assertEqual(self.path_risk(collect(r)[0])["src/a.py"], ["path: src/pay/"])

    def test_range_reads_rules_from_its_left_side(self):
        r = repo({".claude/review-risk": "path src/pay/\n", "src/pay/a.py": "1\n"})
        write(r, ".claude/review-risk", "")
        write(r, "src/pay/a.py", "2\n")
        git(r, "commit", "-qam", "edit")
        for spec in ("HEAD~1..HEAD", "HEAD~1...HEAD"):
            with self.subTest(spec=spec):
                risk = self.path_risk(collect(r, spec)[0])
                self.assertEqual(risk["src/pay/a.py"], ["path: src/pay/"])

    def test_bad_rule_is_reported_not_fatal(self):
        r = repo({".claude/review-risk": "src/pay/\nline (\n", "a.py": "1\n"})
        write(r, "a.py", "2\n")
        _, out = collect(r)
        self.assertIn("not a valid rule, skipped: 'src/pay/'", out)
        self.assertIn("not a valid rule, skipped: 'line ('", out)

    def test_built_in_rules_are_per_hunk(self):
        far = "".join(f"x{i}\n" for i in range(40))
        r = repo({"Net.cs": "enum Kind { A }\n" + far + "int y = 1;\n",
                  "tool.js": "a()\n", "Game.asmdef": "{}\n"})
        write(r, "Net.cs", "enum Kind { A, B }\n" + far + "int y = 2;\n")
        write(r, "tool.js", "process.kill(pid)\n")
        write(r, "Game.asmdef", '{"name": "Game"}\n')
        risk = [(u["path"], u["risk"]) for u in index(collect(r)[0])]
        self.assertEqual([v for p, v in risk if p == "Net.cs"],
                         [[], []])  # B appended to a one-line enum; the far hunk is not raised
        self.assertEqual([v for p, v in risk if p == "tool.js"], [["line: process.kill(pid)"]])
        self.assertEqual([v for p, v in risk if p == "Game.asmdef"], [["path: *.asmdef"]])

    def test_collect_drops_the_skills_all(self):
        r = repo({"a.py": "1\n"})
        write(r, "a.py", "2\n")
        self.assertIn("1 files", collect(r, "all")[1])
        git(r, "commit", "-qam", "two")
        self.assertIn("Base: HEAD~1..HEAD", collect(r, "all", "HEAD~1..HEAD")[1])
        code, _, err = run(r, "collect", "a", "b", "--out-root", OUT)
        self.assertEqual(code, 2)
        self.assertIn("one ref or range", err)

    def test_parametrized_tests_are_counted(self):
        old = "test.each([1, 2])('a %i', (n) => {\n  expect(f(n)).toBe(1)\n})\n"
        r = repo({"src/a.test.ts": old + "it('b', () => {\n  expect(g()).toBe(1)\n})\n"})
        write(r, "src/a.test.ts", old)
        self.assertEqual(self.path_risk(collect(r)[0])["src/a.test.ts"],
                         ["test: tests −1 +0, asserts −1 +0"])

    def test_weakened_test_is_risky_a_new_test_is_not(self):
        old = "def test_a():\n    assert f() == 1\n\ndef test_b():\n    assert g()\n"
        r = repo({"tests/test_x.py": old})
        write(r, "tests/test_x.py", "def test_a():\n    assert f() == 1\n")
        write(r, "tests/test_new.py", "def test_c():\n    pass\n")
        risk = self.path_risk(collect(r)[0])
        self.assertEqual(risk["tests/test_x.py"], ["test: tests −1 +0, asserts −1 +0"])
        self.assertEqual(risk["tests/test_new.py"], [])

    def test_skipped_or_moved_test_is_risky(self):
        r = repo({"Tests/HubTests/ATests.swift": "@Test func a() {\n    #expect(x == 1)\n}\n",
                  "py/tests/test_b.py": "def test_b():\n    assert b()\n",
                  "src/c.spec.ts": "it('c', () => {\n  expect(c()).toBe(1)\n})\n"})
        write(r, "Tests/HubTests/ATests.swift",
              "@Test(.disabled) func a() {\n    #expect(x == 1)\n}\n")
        write(r, "py/tests/test_b.py", "@unittest.skip('flaky')\ndef test_b():\n    assert b()\n")
        git(r, "mv", "src/c.spec.ts", "src/c.ts")
        risk = self.path_risk(collect(r)[0])
        self.assertEqual(risk["py/tests/test_b.py"], ["test: @unittest.skip('flaky')"])
        self.assertEqual(risk["src/c.ts"], ["test: moved out of the tests: src/c.ts"])
        # @Test(.disabled) keeps the counts: no rule for it — the reviewers judge it
        self.assertEqual(risk["Tests/HubTests/ATests.swift"], [])

    def test_enum_members_raised_by_the_code_around_them(self):
        r = repo({"Kind.cs": "public enum Kind\n{\n    A = 0,\n    B = 1,\n}\n"})
        write(r, "Kind.cs", "public enum Kind\n{\n    A = 0,\n    X = 1,\n    B = 2,\n}\n")
        self.assertEqual(self.path_risk(collect(r)[0])["Kind.cs"], ["enum: public enum Kind"])

    def enum_risk(self, path, old, new):
        r = repo({path: old})
        write(r, path, new)
        return self.path_risk(collect(r)[0])[path]

    def test_enum_member_appended_is_not_raised(self):
        old = "public enum Kind\n{\n    A = 0,\n    B = 1,\n}\n"
        self.assertEqual(self.enum_risk("Kind.cs", old, old.replace("B = 1,\n", "B = 1,\n    C = 2,\n")), [])
        self.assertEqual(self.enum_risk("Kind.cs", old, old.replace("B = 1", "B = 3")),
                         ["enum: public enum Kind"])
        self.assertEqual(self.enum_risk("Kind.cs", old, old.replace("    B = 1,\n", "")),
                         ["enum: public enum Kind"])

    def test_enum_without_cases_or_imported_is_not_raised(self):
        ns = "enum Paths {\n    static let a = 1\n}\n"
        self.assertEqual(self.enum_risk("P.swift", ns, ns.replace("a = 1\n", "b = 0\n    static let a = 1\n")), [])
        self.assertEqual(self.enum_risk("a.py", "import os\n", "from enum import Enum\nimport os\n"), [])
        swift = "enum K {\n    case a\n    case b\n}\n"
        self.assertEqual(self.enum_risk("K.swift", swift, swift.replace("case a\n", "case a\n    case x\n")),
                         ["enum: enum K {"])

    def test_one_line_enum(self):
        old = "enum Kind { A, B }\n"
        self.assertEqual(self.enum_risk("K.cs", old, "enum Kind { A, X, B }\n"), ["enum: enum Kind { A, B }"])
        keys = "    enum CodingKeys: String, CodingKey { case a, b }\n"
        self.assertEqual(self.enum_risk("M.swift", keys, keys.replace("a, b", "x, a, b")), [])

    def test_enum_reorder_and_gaps(self):
        old = "public enum Kind\n{\n    A,\n    B,\n    C,\n}\n"
        raised = ["enum: public enum Kind"]
        self.assertEqual(self.enum_risk("K.cs", old, old.replace("A,\n    B,", "B,\n    A,")), raised)
        self.assertEqual(self.enum_risk("K.cs", old, old.replace("A,\n", "A,\n    X,\n\n")), raised)
        self.assertEqual(self.enum_risk("K.cs", old, old.replace("A,\n", "A,\n    X,\n    // legacy\n")), raised)
        self.assertEqual(self.enum_risk("K.cs", old, old.replace("C,\n", "C,\n\n    // new\n    D,\n")), [])

    def test_enum_declaration_and_member_lines(self):
        allman = "public enum Kind : byte\n{\n    A,\n}\n"
        self.assertEqual(self.enum_risk("K.cs", allman, allman.replace("byte", "int")),
                         ["enum: public enum Kind : byte"])
        many = "enum Dir\n{\n    North, East,\n    South,\n}\n"
        self.assertEqual(self.enum_risk("D.cs", many, many.replace("North, East", "North, NorthEast, East")),
                         ["enum: enum Dir"])
        java = "enum Color {\n    RED,\n    GREEN;\n\n    private int x;\n}\n"
        self.assertEqual(self.enum_risk("C.java", java, java.replace("GREEN;", "GREEN, BLUE;")), [])
        self.assertEqual(self.enum_risk("C.java", java, java.replace("GREEN;", "GREEN,\n    BLUE;")), [])
        swift = "enum K {\n    case a\n    case b\n\n    var label: String { \"\" }\n}\n"
        self.assertEqual(self.enum_risk("K.swift", swift, swift.replace("case b\n", "case b\n    case c\n")), [])

    def test_coding_keys_raised_only_on_removal(self):
        old = "enum CodingKeys: String, CodingKey {\n    case a\n    case b\n}\n"
        self.assertEqual(self.enum_risk("M.swift", old, old.replace("case a\n", "case a\n    case x\n")), [])
        self.assertEqual(self.enum_risk("M.swift", old, old.replace("    case a\n", "")),
                         ["enum: enum CodingKeys: String, CodingKey {"])

    def test_python_int_enum_insert_is_raised(self):
        old = "class Kind(IntEnum):\n    A = 1\n    B = 2\n\n\ndef f():\n    pass\n"
        self.assertEqual(self.enum_risk("k.py", old, old.replace("A = 1\n", "A = 1\n    X = 3\n")),
                         ["enum: class Kind(IntEnum):"])
        self.assertEqual(self.enum_risk("k.py", old, old.replace("B = 2\n", "B = 2\n    C = 3\n")), [])

    def test_memorypack_layout(self):
        old = ("[MemoryPackable]\npublic partial class Save\n{\n    [MemoryPackOrder(0)] public int A;\n"
               "    [MemoryPackOrder(1)] public int B;\n}\n")
        append = old.replace("int B;\n", "int B;\n    [MemoryPackOrder(2)] public int C;\n")
        reuse = old.replace("int B;\n", "int B;\n    [MemoryPackOrder(1)] public int C;\n")
        retype = old.replace("public int B", "public long B")
        comment = old.replace("{\n", "{\n    // the player's save\n")
        fresh = old + "\n[MemoryPackable(GenerateType.VersionTolerant)]\npublic partial class New\n{\n    public int X;\n}\n"
        self.assertEqual(self.enum_risk("Save.cs", old, append), [])
        self.assertEqual(self.enum_risk("Save.cs", old, comment), [])
        self.assertEqual(self.enum_risk("Save.cs", old, fresh), [])
        self.assertEqual(self.enum_risk("Save.cs", old, reuse),
                         ["memorypack: [MemoryPackOrder(1)] public int C;"])
        self.assertEqual(self.enum_risk("Save.cs", old, retype),
                         ["memorypack: [MemoryPackOrder(1)] public int B;"])

    def test_memorypack_field_inserted_before_others(self):
        old = "[MemoryPackable]\npublic partial class S\n{\n    public int A;\n    public int B;\n}\n"
        self.assertEqual(self.enum_risk("S.cs", old, old.replace("int A;\n", "int A;\n    public int X;\n")),
                         ["memorypack: public int X;"])
        self.assertEqual(self.enum_risk("S.cs", old, old.replace("int B;\n", "int B;\n    public int X;\n")), [])
        method = old.replace("int A;\n", "int A;\n    void F()\n    {\n    }\n")
        self.assertEqual(self.enum_risk("S.cs", method, method.replace("int A;\n", "int A;\n    public int X;\n")),
                         ["memorypack: public int X;"])

    def test_memorypack_packable_and_base_list(self):
        old = "[MemoryPackable]\npublic partial class S\n    : IBase\n{\n    public int A;\n    public int B;\n}\n"
        self.assertEqual(self.enum_risk("S.cs", old, old.replace("int A;\n", "int A;\n    public int X;\n")),
                         ["memorypack: public int X;"])
        self.assertEqual(len(self.enum_risk("S.cs", old, old.replace("int A;\n    public int B;",
                                                                     "int B;\n    public int A;"))), 1)
        self.assertEqual(self.enum_risk("S.cs", old, old.replace("[MemoryPackable]",
                                                                 "[MemoryPackable(GenerateType.VersionTolerant)]")),
                         ["memorypack: [MemoryPackable]"])

    def test_memorypack_union_tags_are_per_type(self):
        old = ("[MemoryPackable]\n[MemoryPackUnion(0, typeof(A))]\n[MemoryPackUnion(1, typeof(B))]\n"
               "public partial interface IShape\n{\n}\n")
        other = old + "\n[MemoryPackable]\n[MemoryPackUnion(0, typeof(C))]\npublic partial interface IOther\n{\n}\n"
        added = old.replace("typeof(B))]\n", "typeof(B))]\n[MemoryPackUnion(2, typeof(C))]\n")
        reused = old.replace("typeof(B))]\n", "typeof(B))]\n[MemoryPackUnion(1, typeof(C))]\n")
        self.assertEqual(self.enum_risk("U.cs", old, other), [])
        self.assertEqual(self.enum_risk("U.cs", old, added), [])
        self.assertEqual(self.enum_risk("U.cs", old, reused),
                         ["memorypack: [MemoryPackUnion(1, typeof(C))]"])

    def test_anonymous_c_enum(self):
        old = "typedef enum\n{\n    A,\n    B,\n} kind_t;\n"
        self.assertEqual(self.enum_risk("k.h", old, old.replace("A,\n", "A,\n    X,\n")),
                         ["enum: typedef enum"])
        self.assertEqual(self.enum_risk("k.h", old, old.replace("B,\n", "B,\n    C,\n")), [])

    def test_json_number_or_bool_retyped_is_not_reformatting(self):
        r = repo({"Packages/manifest.json": '{"testables": false, "port": 8080}\n'})
        write(r, "Packages/manifest.json", '{\n  "testables": 0,\n  "port": 8080.0\n}\n')
        u = by_path(collect(r)[0])["Packages/manifest.json"]
        self.assertNotIn("only reformatted", u["facts"])
        self.assertNotEqual(u["risk"], [])

    def test_json_only_reformatted_drops_the_path_rule(self):
        r = repo({"Game.asmdef": '{"name": "Game", "references": []}\n'})
        write(r, "Game.asmdef", '{\n    "name": "Game",\n    "references": []\n}\n')
        u = by_path(collect(r)[0])["Game.asmdef"]
        self.assertEqual(u["risk"], [])
        self.assertIn("only reformatted", u["facts"])

    def test_nested_manifest_and_unity_field(self):
        r = repo({"Client/Packages/manifest.json": "{}\n", "pkg/package.json": '{\n  "unity": "2022.3"\n}\n'})
        write(r, "Client/Packages/manifest.json", '{"a": 1}\n')
        write(r, "pkg/package.json", '{\n  "unity": "6000.0"\n}\n')
        risk = self.path_risk(collect(r)[0])
        self.assertEqual(risk["Client/Packages/manifest.json"], ["path: **/Packages/manifest.json"])
        self.assertEqual(risk["pkg/package.json"], ['line: "unity": "2022.3"'])

    def test_destructive_calls_and_secrets(self):
        cases = {
            "a.ts": 'await git(["clean", "-ffdxq"])\n',
            "b.py": "shutil.rmtree(work)\n",
            "c.js": "await rm(dir, {recursive: true})\n",
            "d.py": 'API_KEY = "abcd1234efgh5678"\n',
            "e.ts": 'run("git", ["push", "origin", "--force-with-lease"])\n',
        }
        r = repo({p: "x\n" for p in cases})
        for p, body in cases.items():
            write(r, p, body)
        risk = self.path_risk(collect(r)[0])
        for p in cases:
            with self.subTest(path=p):
                self.assertEqual(len(risk[p]), 1, risk[p])
        r = repo({"f.py": "x\n"})
        write(r, "f.py", 'token = os.environ["TOKEN"]\nform.remove(item)\ntokenizer = "bert-base-uncased"\n'
                         'API_KEY_ENV = "MYAPP_API_KEY_VAR"\nx = {"tokenUrl": "https://example.com/t"}\n')
        self.assertEqual(self.path_risk(collect(r)[0])["f.py"], [])

    def test_checks_switched_off_is_a_fact(self):
        top = "[Il2CppSetOption(Option.ArrayBoundsChecks, false)]\npublic unsafe class Buf\n{\n"
        far = "".join(f"    int x{i};\n" for i in range(30))
        r = repo({"Buf.cs": top + far + "    void W() { a[i] = 1; }\n}\n"})
        write(r, "Buf.cs", top + far + "    void W() { a[i + 1] = 1; }\n}\n")
        u = by_path(collect(r)[0])["Buf.cs"]
        self.assertIn("ArrayBoundsChecks, unsafe", u["facts"])

    def test_rewritten_assert_is_left_to_the_reviewers(self):
        r = repo({"Tests/ATests.cs": "Assert.That(x, Is.EqualTo(new[] { 1, 2 }));\n"})
        write(r, "Tests/ATests.cs", "Assert.That(x, Has.Length.EqualTo(2));\n")
        self.assertEqual(self.path_risk(collect(r)[0])["Tests/ATests.cs"], [])

    def test_noise_is_still_read_by_the_rules(self):
        r = repo({".claude/diff-tour-noise": "gen/\n", "gen/Net.cs": "int a;\n"})
        write(r, "gen/Net.cs", "process.kill(pid);\n")
        u = by_path(collect(r)[0])["gen/Net.cs"]
        self.assertEqual(u["id"], "n1")
        self.assertEqual(u["risk"], ["line: process.kill(pid);"])

    def test_build_reads_the_rules_again(self):
        r = repo({".claude/review-risk": "path src/pay/\n", "src/pay/a.py": "1\n"})
        write(r, "src/pay/a.py", "2\n")
        run_dir, _ = collect(r)
        with open(os.path.join(run_dir, "hunks.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        data["meta"]["risk"], data["meta"]["patterns"] = [], ["*"]  # edited after collect
        with open(os.path.join(run_dir, "hunks.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        _, _, units = difftour.load_snapshot(run_dir)
        self.assertEqual([(u["id"], u["risk"]) for u in units], [("h1", [("path", "src/pay/")])])

    def test_lost_base_stops_build(self):
        r = repo({"a.py": "1\n"})
        write(r, "a.py", "2\n")
        run_dir, _ = collect(r)
        shutil.rmtree(r)
        with self.assertRaises(difftour.Fail) as cm:
            difftour.load_snapshot(run_dir)
        self.assertIn("cannot re-read the noise and risk rules", str(cm.exception))


class Triage(unittest.TestCase):
    """Only the script folds: reviewers and rules can only raise a unit into the queue."""

    def setUp(self):
        r = repo({"a.cs": "one\n", "b.cs": "two\n", "c.cs": "three\n", "d.js": "x\n"})
        write(r, "a.cs", "ONE\n")
        write(r, "b.cs", "TWO\n")
        write(r, "c.cs", "THREE\n")
        write(r, "d.js", "fs.rm(dir)\n")
        self.run_dir, _ = collect(r)
        with open(os.path.join(self.run_dir, "notes.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "t", "lede": "l", "notes": [
                {"unit": "h1", "source": "session", "text": "a"},
                {"unit": "h2", "source": "session", "kind": "untested", "text": "b"},
                {"unit": "h4", "source": "session", "text": "d"}]}, fh)

    def build(self, *args):
        return run(self.run_dir, "build", self.run_dir, *args)

    def queue(self):
        return page(self.run_dir).split('<section class="queue">')[1].split("</section>")[0]

    def test_build_needs_triage_or_all(self):
        code, out, _ = self.build()
        self.assertEqual(code, 1)
        self.assertIn("no triage-*.json", out)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "index.html")))
        code, _, err = self.build("--all")
        self.assertEqual(code, 0, err)
        self.assertNotIn('class="queue"', page(self.run_dir))

    def test_queue_sections(self):
        triage(self.run_dir, "1", suspects=[{"unit": "h1", "text": "wrong `sign`"}], risks=[])
        triage(self.run_dir, "2", suspects=[{"unit": "h1", "text": "wrong `sign`"}],
               risks=[{"unit": "h1", "check": "callers"}])
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        # h1: suspect; h2: author's flag; h4: fs.rm; h3 has no note, and that alone folds
        self.assertIn("queue: 1 suspected · 2 worth a look · 1 folded (reviewers: 1, 2)", out)
        self.assertIn("  ! h1    a.cs  [1] wrong `sign`", out)
        q = self.queue()
        self.assertEqual(q.count('<li class="qi suspect">'), 1)
        self.assertEqual(q.count('<li class="qi risk">'), 2)
        self.assertIn("wrong <code>sign</code> <span class=\"by\">all reviewers</span>", q)
        self.assertIn("callers", q)
        self.assertIn("<code>fs.rm(dir)</code>", q)
        self.assertIn("author: Untested", q)
        self.assertIn("1 units have no author note", q)
        self.assertNotIn('data-unit="h3"', q)
        self.assertNotIn("data-unit=\"h1\"><td", q)  # code copies are not link targets
        self.assertNotIn('<tr class="hunk"', q)
        html = page(self.run_dir)
        self.assertIn('data-mode="queue" aria-pressed="true"', html)
        self.assertIn(difftour.QUEUE_JS, html)
        self.assertIn("<main>", html)  # the whole diff is still there

    def test_what_nobody_raised_is_folded(self):
        with open(os.path.join(self.run_dir, "notes.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "t", "lede": "l", "notes": [
                {"unit": u, "source": "session", "text": "x"} for u in ("h1", "h2", "h3", "h4")
            ]}, fh)
        triage(self.run_dir, "1", suspects=[], risks=[])
        triage(self.run_dir, "2", suspects=[], risks=[])
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertIn("queue: 0 suspected · 1 worth a look · 3 folded", out)
        self.assertIn("The other 3 units are folded", self.queue())

    def test_empty_queue_says_so(self):
        r = repo({"a.cs": "one\n"})
        write(r, "a.cs", "ONE\n")
        run_dir, _ = collect(r)
        with open(os.path.join(run_dir, "notes.json"), "w", encoding="utf-8") as fh:
            json.dump({"title": "t", "lede": "l", "lang": "ru",
                       "notes": [{"unit": "h1", "source": "session", "text": "x"}]}, fh)
        triage(run_dir, "1", suspects=[], risks=[])
        triage(run_dir, "2", suspects=[], risks=[])
        code, _, err = run(run_dir, "build", run_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("Смотреть нечего", page(run_dir))

    def test_partial_reading_is_flagged(self):
        triage(self.run_dir, "1", units_seen=2, suspects=[], risks=[])
        triage(self.run_dir, "2", suspects=[], risks=[])
        code, out, _ = self.build()
        self.assertEqual(code, 0)
        self.assertIn("WARNING: reviewer 1 read only 2 of 4 units", out)
        self.assertIn("reviewer 1 read only 2 of 4 units: what it did not read", self.queue())

    def test_one_reviewer_is_not_enough(self):
        triage(self.run_dir, "1", suspects=[], risks=[])
        code, out, _ = self.build()
        self.assertEqual(code, 1)
        self.assertIn("1 triage file(s) (triage-1.json) — the triage step runs 2 reviewers", out)

    def test_bad_triage_files(self):
        triage(self.run_dir, "1", suspects=[{"unit": "h9", "text": "x"}],
               risks=[{"unit": "h1", "check": " "}], extra=1)
        triage(self.run_dir, "2", units_seen="all")
        with open(os.path.join(self.run_dir, "triage-3.json"), "w") as fh:
            fh.write("{oops")
        triage(self.run_dir, "4", units_seen=99, suspects=[{"unit": ["h1"], "text": "x"}])
        with open(os.path.join(self.run_dir, "triage-5.json"), "w") as fh:
            fh.write("[" * 100000 + "]" * 100000)
        code, out, err = self.build()
        self.assertEqual(code, 1, err)
        for msg in ("triage-1.json: unknown key 'extra'",
                    "triage-1.json: suspects[0]: unit must be an id from index.txt",
                    "triage-1.json: risks[0]: check must be non-empty text",
                    "triage-2.json: units_seen must be",
                    "triage-3.json: not valid JSON",
                    "triage-4.json: units_seen must be the number of units the reviewer read "
                    "(0–4)",
                    "triage-4.json: suspects[0]: unit must be an id from index.txt",
                    "triage-5.json: not valid JSON"):
            self.assertIn(msg, out)

    def test_cleared_and_merged_reasons(self):
        why = {"unit": "h4", "why": "removes only its own temp dir"}
        triage(self.run_dir, "1", suspects=[], risks=[{"unit": "h3", "check": "callers?"}],
               cleared=[why])
        triage(self.run_dir, "2", suspects=[], risks=[{"unit": "h3", "check": "callers?"}],
               cleared=[why])
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        q = self.queue()
        self.assertIn('data-unit="h4"', q)  # cleared, but still in the queue
        self.assertIn("why safe</span><span>removes only its own temp dir "
                      '<span class="by">all reviewers</span>', q)
        self.assertEqual(q.count("callers?"), 1)
        self.assertIn("queue: 0 suspected · 3 worth a look · 1 folded", out)

    def test_cleared_only_for_rule_hits(self):
        triage(self.run_dir, "1", cleared=[{"unit": "h1", "why": "fine"}])
        triage(self.run_dir, "2")
        code, out, _ = self.build()
        self.assertEqual(code, 1)
        self.assertIn("triage-1.json: cleared[0]: h1 was not raised by a risk rule", out)

    def test_author_flag_once(self):
        with open(os.path.join(self.run_dir, "notes.json"), encoding="utf-8") as fh:
            notes = json.load(fh)
        notes["notes"].append({"unit": "h2", "after": "TWO", "source": "session",
                               "kind": "untested", "text": "again"})
        with open(os.path.join(self.run_dir, "notes.json"), "w", encoding="utf-8") as fh:
            json.dump(notes, fh)
        triage(self.run_dir, "1")
        triage(self.run_dir, "2")
        code, _, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.queue().count("author: Untested"), 1)

    def test_index_txt(self):
        with open(os.path.join(self.run_dir, "index.txt"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("old_rev: ", text)
        self.assertIn("new_rev: (working tree)", text)
        self.assertRegex(text, r"h4 +M  d\.js .*\[risk line: fs\.rm\(dir\)\]  \+1 -1  L\d+")

    def test_page_without_triage_matches_the_old_page(self):
        meta, files, units = difftour.load_snapshot(self.run_dir)
        with open(os.path.join(self.run_dir, "notes.json"), encoding="utf-8") as fh:
            notes = json.load(fh)
        old, _ = difftour.render(meta, files, units, notes, None, "x")
        self.assertNotIn(difftour.QUEUE_CSS, old)
        self.assertNotIn("data-mode", old)


# --- parser ------------------------------------------------------------------------------

class Parse(unittest.TestCase):

    def test_read_quoted(self):
        self.assertEqual(difftour.read_quoted('"a/\\321\\217 \\"x\\"\\t.txt" rest'),
                         ('a/я "x"\t.txt', " rest"))

    def test_git_line_with_b_slash_in_the_name(self):
        self.assertEqual(difftour.split_git_line("a/x b/y b/x b/y"), ("x b/y", "x b/y"))

    def test_removed_line_that_looks_like_a_header(self):
        text = ("diff --git a/f b/f\nindex 1..2 100644\n--- a/f\n+++ b/f\n"
                "@@ -1,2 +1 @@\n--- a/x\n keep\n")
        files = difftour.parse(text)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["hunks"][0]["lines"], [["-", "-- a/x"], [" ", "keep"]])


# --- notes.json --------------------------------------------------------------------------

SAMPLE = (
    "diff --git a/a.cs b/a.cs\nindex 1..2 100644\n--- a/a.cs\n+++ b/a.cs\n"
    "@@ -1,3 +1,3 @@\n keep\n-return old;\n+return fresh;\n tail\n"
    "@@ -10,2 +10,3 @@\n x = 1;\n-y = 2;\n+y = 3;\n+y = 4;\n"
    "diff --git a/b.bin b/b.bin\nindex 1..2 100644\nBinary files a/b.bin and b/b.bin differ\n"
    "diff --git a/package-lock.json b/package-lock.json\nindex 1..2 100644\n"
    "--- a/package-lock.json\n+++ b/package-lock.json\n@@ -1 +1 @@\n-{}\n+{\"a\": 1}\n"
)
FILES = difftour.parse(SAMPLE)
UNITS = difftour.make_units(FILES, difftour.DEFAULT_NOISE)  # h1 h2 (a.cs), h3 (b.bin), n1


def note(**over):
    n = {"unit": "h1", "text": "why", "source": "session"}
    n.update(over)
    return n


def notes_with(**over):
    n = {"title": "t", "lede": "l", "notes": []}
    n.update(over)
    return n


class Validate(unittest.TestCase):

    def problems(self, notes):
        return difftour.validate(notes, FILES, UNITS)

    def test_units(self):
        self.assertEqual([u["id"] for u in UNITS], ["h1", "h2", "h3", "n1"])

    def test_valid(self):
        self.assertEqual(self.problems(notes_with()), [])
        self.assertEqual(self.problems({
            "lang": "ru", "title": "t", "lede": "l",
            "link": {"url": "https://example.com/t/1", "label": "Task 1"},
            "panels": [{"title": "What", "text": "x"},
                       {"title": "Checks", "checks": [{"pill": "3/3", "text": "tests"},
                                                      {"pill": "no", "text": "c", "warn": True}]}],
            "files": [{"path": "a.cs", "role": "game code", "kind": "prod"},
                      {"path": "b.bin"}],
            "notes": [note(after="return fresh;"), note(unit="h2", after="y =", side="old"),
                      note(unit="n1", kind="stray", source="inferred")],
            "unexplained": {"h3": "что"}}), [])

    def test_uncovered_units_are_not_a_problem(self):
        self.assertEqual(self.problems(notes_with(notes=[note()])), [])

    def assertProblem(self, notes, fragment):
        found = self.problems(notes)
        self.assertTrue(any(fragment in p for p in found), f"{fragment!r} not in {found}")

    def test_each_problem(self):
        cases = [
            ({"lede": "l", "notes": []}, "title: required"),
            ({"title": "t", "notes": []}, "lede: required"),
            ({"title": "t", "lede": "l"}, "notes: required"),
            (notes_with(extra=1), "unknown top-level keys: extra"),
            (notes_with(link={"url": "javascript:alert(1)", "label": "x"}), "http(s) only"),
            (notes_with(link={"url": "https://x.y"}), "http(s) only"),
            (notes_with(panels=[{"title": "p", "text": "a", "checks": []}]),
             "exactly one of text, checks"),
            (notes_with(panels=[{"title": "p", "checks": [{"pill": "1", "text": "a",
                                                           "warn": "yes"}]}]),
             "checks[1]"),
            (notes_with(panels=[{"title": "p", "checks": []}]), "checks: a non-empty list"),
            (notes_with(files=[{"path": "nope.cs"}]), "'nope.cs' is not in the diff"),
            (notes_with(files=[{"path": "a.cs"}, {"path": "a.cs"}]), "listed twice"),
            (notes_with(files=[{"path": "a.cs", "kind": "core"}]), "kind: one of"),
            (notes_with(notes=[note(unit="h9")]), "unknown unit id 'h9'"),
            (notes_with(notes=[note(text="")]), "text: required"),
            (notes_with(notes=[note(source="guess")]), "source: one of"),
            (notes_with(notes=[note(kind="maybe")]), "kind: one of"),
            (notes_with(notes=[note(line=3)]), "unknown keys: line"),
            (notes_with(notes=[note(side="new")]), "only together with after"),
            (notes_with(notes=[note(after="absent")]), "no line of h1 contains 'absent'"),
            (notes_with(notes=[note(after="return")]), "2 lines of h1 contain 'return'"),
            (notes_with(notes=[note(unit="h2", after="y =")]), "3 lines of h2 contain"),
            (notes_with(notes=[note(unit="h3", after="x")]), "no line of h3 contains"),
            (notes_with(notes=[note(after="a\nb")]), "piece of one line"),
            (notes_with(notes=[note()], unexplained={"h1": "x"}), "h1 has notes"),
            (notes_with(unexplained={"h9": "x"}), "unexplained: unknown unit id 'h9'"),
            (notes_with(unexplained={"h1": 5}), "a string"),
            (notes_with(files=[{"path": ["a.cs"]}]), "files[1].path: a string"),
        ]
        for notes, fragment in cases:
            with self.subTest(fragment=fragment):
                self.assertProblem(notes, fragment)

    def test_side_hint_only_without_side(self):
        found = self.problems(notes_with(notes=[note(unit="h2", after="y =", side="new")]))
        self.assertEqual(len(found), 1)
        self.assertNotIn("add side", found[0])

    def test_side_disambiguates(self):
        self.assertEqual(self.problems(notes_with(notes=[note(after="return", side="new")])), [])
        self.assertEqual(difftour.anchor_matches(FILES, UNITS[0], "return", "old"), [(0, 1)])


# --- moved blocks ------------------------------------------------------------------------

def one_file(*hunks):
    text = "diff --git a/m.cs b/m.cs\nindex 1..2 100644\n--- a/m.cs\n+++ b/m.cs\n"
    for old_start, lines in hunks:
        olds = sum(1 for ln in lines if ln[0] in " -")
        news = sum(1 for ln in lines if ln[0] in " +")
        text += f"@@ -{old_start},{olds} +{old_start},{news} @@\n" + "".join(ln + "\n" for ln in lines)
    return difftour.parse(text)[0]


class Moved(unittest.TestCase):

    def test_block_moved_between_hunks(self):
        f = one_file((1, [" a", "-first();", "-second();", "-{", "-third();", " b"]),
                     (50, [" c", "+first();", "+second();", "+{", "+third();", " d"]))
        moved = difftour.moved_lines(0, f)
        self.assertEqual(moved, {(0, 0, i) for i in range(1, 5)} | {(0, 1, i) for i in range(1, 5)})

    def test_two_lines_are_a_coincidence(self):
        f = one_file((1, [" a", "-first();", "-second();", " b"]),
                     (50, [" c", "+first();", "+second();", " d"]))
        self.assertEqual(difftour.moved_lines(0, f), set())

    def test_reindented_block_is_not_moved(self):
        f = one_file((1, [" a", "-first();", "-second();", "-third();",
                          "+    first();", "+    second();", "+    third();", " b"]))
        self.assertEqual(difftour.moved_lines(0, f), set())

    def test_brace_style_method_moves_with_its_braces(self):
        body = ["void Fire()", "{", "    Shoot(target);", "    Recoil();", "}"]
        f = one_file((1, [" a"] + ["-" + x for x in body] + [" b"]),
                     (50, [" c"] + ["+" + x for x in body] + [" d"]))
        moved = difftour.moved_lines(0, f)
        self.assertEqual(moved, {(0, h, i) for h in (0, 1) for i in range(1, 6)})

    def test_braces_alone_are_chance(self):
        f = one_file((1, [" a", "-}", "-", "-return x;", " b"]),
                     (50, [" c", "+}", "+", "+return x;", " d"]))
        self.assertEqual(difftour.moved_lines(0, f), set())

    def test_changed_line_breaks_the_block(self):
        f = one_file((1, [" a", "-one();", "-two();", "-three();", " b"]),
                     (50, [" c", "+one();", "+two(changed);", "+three();", " d"]))
        moved = difftour.moved_lines(0, f)
        self.assertNotIn((0, 1, 2), moved)          # the edited line stays an addition
        self.assertEqual({k for k in moved if k[1] == 1}, set())  # 1 + 1 matched: no block


# --- build -------------------------------------------------------------------------------

def three_changes():
    path = repo({"a.cs": "one\n", "b.cs": "two\n", "c.cs": "three\n"})
    write(path, "a.cs", "ONE\n")
    write(path, "b.cs", "TWO\n")
    write(path, "c.cs", "THREE\n")
    return path


NOTES = {"lang": "ru", "title": "Правки", "lede": "Абзац",
         "notes": [{"unit": "h1", "after": "ONE", "text": "**Фикс.** верхний регистр",
                    "source": "session", "kind": "untested"}],
         "unexplained": {"h2": "поменял b"}}


GOLDEN_PATCH = """\
diff --git a/src/Npc.cs b/src/Npc.cs
index 1111111..2222222 100644
--- a/src/Npc.cs
+++ b/src/Npc.cs
@@ -1,6 +1,6 @@ class Npc
 void Tick()
 {
-    if (d < minDist)
+    if (!sp.InSafeZone && d < minDist)
         Aggro(sp);
     Debug.Log("<tick> & 'x'");
 }
@@ -20,5 +20,7 @@ void Fire()
 a();
-first();
-second();
-third();
+first();
+second();
+third();
+Debug.Log("x");\r
+tail\r
 b();
\\ No newline at end of file
diff --git a/tests/NpcTests.cs b/tests/NpcTests.cs
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/NpcTests.cs
@@ -0,0 +1,2 @@
+[Test] public void SafeZone() {}
+// mid\rline
diff --git a/old.txt b/old.txt
deleted file mode 100644
index 4444444..0000000
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-gone
diff --git a/img.png b/img.png
index 5555555..6666666 100644
Binary files a/img.png and b/img.png differ
diff --git a/a b.txt b/dir/c d.txt
similarity index 100%
rename from a b.txt
rename to dir/c d.txt
diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
diff --git a/package-lock.json b/package-lock.json
index 7777777..8888888 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-{}
+{"a": 1}
"""

GOLDEN_META = {
    "root": "/repo", "repo": "repo", "branch": "feature/safe-zone",
    "spec": {"mode": "worktree", "ref": "main", "sha": "a" * 40, "tip": "b" * 40,
             "head": "c" * 40},
    "patterns": difftour.DEFAULT_NOISE, "skipped": ["vendor/lib"], "sha256": "0" * 64,
    "collected": "2026-10-01T12:34:56+03:00",
}

GOLDEN_NOTES = {
    "lang": "ru", "title": "Моб **не агрится** в `safe zone`",
    "lede": "Что было <b>не так</b>\nи что делает `fix`.",
    "link": {"url": "https://example.com/t/1?a=1&b=2", "label": "Task <1>"},
    "panels": [{"title": "Что изменилось", "text": "1 файл, **2** теста"},
               {"title": "Проверки", "checks": [
                   {"pill": "6/6", "text": "тесты `green`"},
                   {"pill": "не сделано", "text": "коммит", "warn": True}]}],
    "files": [{"path": "tests/NpcTests.cs", "role": "тесты", "kind": "test"},
              {"path": "src/Npc.cs", "kind": "prod"}],
    "notes": [
        {"unit": "h1", "after": "InSafeZone", "side": "new", "source": "session",
         "text": "**Сам фикс.** `InSafeZone` first"},
        {"unit": "h1", "source": "inferred", "kind": "decision", "text": "top note"},
        {"unit": "h2", "after": "Debug.Log", "side": "new", "kind": "stray",
         "source": "session", "text": "debug leftover"},
        {"unit": "h3", "source": "session", "kind": "untested", "text": "new tests"},
        {"unit": "h5", "source": "inferred", "kind": "temporary", "text": "binary"},
        {"unit": "n1", "source": "inferred", "text": "lock"},
    ],
    "unexplained": {"h4": "deletes `old.txt`"},
}


class Build(unittest.TestCase):

    def test_page_and_report(self):
        run_dir, _ = collect(three_changes())
        code, out, err = build(run_dir, NOTES)
        self.assertEqual(code, 0, err)
        self.assertIn("notes: 1 · flags: 1 · inferred: 0 · unexplained: 2 (of 3 units)", out)
        self.assertIn("h2    b.cs  поменял b", out)
        self.assertIn("h3    c.cs  (no description)", out)
        html = page(run_dir)
        self.assertEqual(html.count('<details class="file loose"'), 2)
        self.assertEqual(html.count('<details class="file"'), 1)
        self.assertIn("Самопроверка", html)
        self.assertIn('<table class="diff unified"', html)
        self.assertIn('<table class="diff split"', html)
        self.assertIn("<b>Фикс.</b>", html)
        self.assertNotIn('<div class="banner">', html)
        self.assertIn(f'src="{difftour.HLJS}" integrity="{difftour.HLJS_SRI}" '
                      f'crossorigin="anonymous"', html)

    def test_anchored_note_follows_its_line(self):
        run_dir, _ = collect(three_changes())
        build(run_dir, NOTES)
        unified = page(run_dir).split('<table class="diff unified"')[1].split("</table>")[0]
        self.assertRegex(unified, r'>ONE</span></td></tr><tr class="note-row">.*Фикс')

    def test_files_order_and_roles(self):
        run_dir, _ = collect(three_changes())
        notes = dict(NOTES, files=[{"path": "c.cs", "role": "игровой код", "kind": "prod"}])
        build(run_dir, notes)
        html = page(run_dir)
        cards = html.split("<main>")[1]
        self.assertLess(cards.index("c.cs"), cards.index("a.cs"))
        self.assertIn('<span class="role prod">игровой код</span>', html)

    def test_english_labels_by_default(self):
        run_dir, _ = collect(three_changes())
        notes = dict(NOTES)
        del notes["lang"]
        build(run_dir, notes)
        self.assertIn("Self-check", page(run_dir))

    def test_invalid_notes(self):
        run_dir, _ = collect(three_changes())
        code, out, _ = build(run_dir, dict(NOTES, notes=[{"unit": "h1", "text": "x",
                                                          "source": "maybe"}]))
        self.assertEqual(code, 1)
        self.assertIn("1 problem(s)", out)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "index.html")))

    def test_not_json(self):
        run_dir, _ = collect(three_changes())
        with open(os.path.join(run_dir, "notes.json"), "w") as fh:
            fh.write("{oops")
        code, out, _ = run(run_dir, "build", run_dir)
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", out)

    def test_missing_notes(self):
        run_dir, _ = collect(three_changes())
        code, _, err = run(run_dir, "build", run_dir)
        self.assertEqual(code, 2)
        self.assertIn("no notes.json", err)

    def test_everything_is_escaped(self):
        path = repo({"x.html": "a\n"})
        write(path, "x.html", "<script>alert(1)</script>\n")
        run_dir, _ = collect(path)
        code, _, err = build(run_dir, {
            "title": "<b>t</b>", "lede": "<i>l</i>",
            "link": {"url": "https://x.y/?a=\"><b>", "label": "<u>x</u>"},
            "panels": [{"title": "<em>p</em>", "checks": [{"pill": "<s>1</s>",
                                                          "text": "<img src=y>"}]}],
            "notes": [note(after="alert", text="<img src=x onerror=alert(2)> `<br>`")]})
        self.assertEqual(code, 0, err)
        html = page(run_dir)
        for raw in ("<script>alert(1)", "<img src=x", "<img src=y", "<b>t</b>", "<i>l</i>",
                    "<u>x</u>", "<em>p</em>", "<s>1</s>", '"><b>'):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("<code>&lt;br&gt;</code>", html)

    def test_drift_is_reported(self):
        path = three_changes()
        run_dir, _ = collect(path)
        write(path, "a.cs", "changed again\n")
        code, out, _ = build(run_dir, NOTES)
        self.assertEqual(code, 0)
        self.assertIn("WARNING: the working tree or HEAD changed after collect", out)
        self.assertIn('<div class="banner">', page(run_dir))

    def test_tampered_snapshot(self):
        run_dir, _ = collect(three_changes())
        with open(os.path.join(run_dir, "patch.diff"), "ab") as fh:
            fh.write(b"+sneaky\n")
        code, _, err = build(run_dir, NOTES)
        self.assertEqual(code, 2)
        self.assertIn("modified after collect", err)

    def test_folding(self):
        path = repo({"small.txt": "s\n", "package-lock.json": "{}\n"})
        big = "".join(f"line {i}\n" for i in range(1600))
        write(path, "big.txt", big)
        write(path, "noted.txt", big)
        write(path, "small.txt", "S\n")
        write(path, "package-lock.json", '{"a": 1}\n')
        run_dir, _ = collect(path)
        ids = {p: u["id"] for p, u in by_path(run_dir).items()}
        build(run_dir, notes_with(notes=[note(unit=ids["noted.txt"], after="line 1500")]))
        html = page(run_dir)
        heads = {p: html.split(f">{p}</span>")[0].rsplit("<details", 1)[1]
                 for p in ("big.txt", "noted.txt", "small.txt", "package-lock.json")}
        self.assertNotIn(" open>", heads["big.txt"])            # big, nothing to read in it
        self.assertIn(" open>", heads["noted.txt"])             # big, but it carries a note
        self.assertNotIn(" open>", heads["package-lock.json"])  # noise
        self.assertIn(" open>", heads["small.txt"])

    def test_new_file_has_one_side(self):
        path = repo({"old.cs": "a\n"})
        write(path, "old.cs", "b\n")
        write(path, "new.cs", "c\n")
        run_dir, _ = collect(path)
        build(run_dir, notes_with())
        html = page(run_dir)
        card = html.split(">new.cs</span>")[1].split("</details>")[0]
        self.assertIn('<table class="diff unified solo"', card)
        self.assertNotIn('<table class="diff split"', card)
        self.assertIn('class="seg"', html)

    def test_no_view_switch_without_two_sided_files(self):
        path = repo({"keep.txt": "k\n"})
        write(path, "new.cs", "c\n")
        run_dir, _ = collect(path)
        build(run_dir, notes_with())
        html = page(run_dir)
        self.assertNotIn('class="seg"', html)
        self.assertNotIn('<table class="diff split"', html)

    def test_moved_block_is_marked(self):
        body = [f"line number {i}\n" for i in range(12)]
        path = repo({"m.cs": "".join(body)})
        write(path, "m.cs", "".join(body[:2] + body[6:] + body[2:6]))
        run_dir, _ = collect(path)
        build(run_dir, notes_with())
        html = page(run_dir)
        self.assertIn('<tr class="moved">', html)
        self.assertIn("c-moved", html)

    def test_huge_diff_renders_unified_only(self):
        run_dir, _ = collect(three_changes())
        meta, files, units = difftour.load_snapshot(run_dir)
        saved = difftour.SPLIT_MAX
        difftour.SPLIT_MAX = 0
        try:
            html, _ = difftour.render(meta, files, units, NOTES, None, run_dir)
        finally:
            difftour.SPLIT_MAX = saved
        self.assertNotIn('<table class="diff split"', html)
        self.assertNotIn('class="seg"', html)

    def test_side_new_anchors_the_duplicate_not_the_original(self):
        path = repo({"d.c": "a();\nfoo();\nb();\n"})
        write(path, "d.c", "a();\nfoo();\nfoo();\nb();\n")
        run_dir, _ = collect(path)
        code, out, err = build(run_dir, notes_with(notes=[
            note(after="foo();", side="new", kind="stray", text="STRAY")]))
        self.assertEqual(code, 0, out + err)
        unified = page(run_dir).split('<table class="diff unified"')[1].split("</table>")[0]
        self.assertRegex(unified, r'<tr class="add">(?:(?!</tr>).)*foo\(\);</span></td></tr>'
                                  r'<tr class="note-row">(?:(?!</tr>).)*STRAY')

    def test_mid_line_cr_is_visible(self):
        path = repo({"keep.txt": "k\n"})
        write(path, "cr.cs", b"alpha\rbeta\n")
        run_dir, _ = collect(path)
        build(run_dir, notes_with())
        self.assertIn("alpha␍beta", page(run_dir))

    def test_markup_and_labels(self):
        run_dir, _ = collect(three_changes())
        build(run_dir, dict(NOTES, title="Тур **по** `диффу`",
                            files=[{"path": "a.cs", "kind": "prod"}],
                            notes=[note(text="**`build` не доверяет.** дальше")]))
        html = page(run_dir)
        self.assertIn("<title>Тур по диффу</title>", html)
        self.assertIn("<b><code>build</code> не доверяет.</b>", html)
        self.assertIn('<span class="role prod">код</span>', html)   # kind alone gives a badge
        self.assertIn("table.solo ", difftour.PAGE_JS)                # links reach one-sided files

    def test_lone_surrogate_does_not_break_the_page(self):
        run_dir, _ = collect(three_changes())
        with open(os.path.join(run_dir, "notes.json"), "w", encoding="utf-8") as fh:
            fh.write('{"title": "cut \\ud83d", "lede": "l", "notes": []}')
        code, _, err = run(run_dir, "build", run_dir, "--all")
        self.assertEqual(code, 0, err)
        self.assertGreater(os.path.getsize(os.path.join(run_dir, "index.html")), 1000)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_highlight_split_keeps_spans_per_line(self):
        js = difftour.PAGE_JS
        start = js.index("function lines(")
        end = js.index("function paint(")
        probe = (js[start:end] + "\nconsole.log(JSON.stringify(lines("
                 "'<span class=\"c\">/* a\\nb */</span>\\nx/y <span class=\"k\">if</span>')));")
        out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(out.stdout), ['<span class="c">/* a</span>',
                                                  '<span class="c">b */</span>',
                                                  'x/y <span class="k">if</span>'])

    def test_page_matches_the_golden_render(self):
        """The page for a fixed input is byte-identical to testdata/golden-*.html. After an
        intended change to the page, regenerate: DIFFTOUR_REGEN_GOLDEN=1 python3 difftour_test.py"""
        for name, notes, drift in (("ru", GOLDEN_NOTES, "changed"),
                                   ("en", dict(GOLDEN_NOTES, lang="en"), "git status failed")):
            files = difftour.parse(GOLDEN_PATCH)
            units = difftour.make_units(files, GOLDEN_META["patterns"])
            self.assertEqual(difftour.validate(notes, files, units), [])
            html, _ = difftour.render(GOLDEN_META, files, units, notes, drift, "/runs/repo/x")
            path = os.path.join(HERE, "testdata", f"golden-{name}.html")
            if os.environ.get("DIFFTOUR_REGEN_GOLDEN"):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(html)
            with open(path, encoding="utf-8", newline="") as fh:
                self.assertEqual(html, fh.read(), path)

    def test_open_bypasses_the_open_on_path(self):
        calls = []
        real_run, real_platform = difftour.subprocess.run, difftour.sys.platform
        difftour.subprocess.run = lambda argv, **kw: calls.append(argv)
        difftour.sys.platform = "darwin"
        try:
            difftour.open_in_browser("/tmp/x/index.html")
        finally:
            difftour.subprocess.run, difftour.sys.platform = real_run, real_platform
        if os.path.exists("/usr/bin/open"):
            self.assertEqual(calls, [["/usr/bin/open", "/tmp/x/index.html"]])


if __name__ == "__main__":
    unittest.main()
