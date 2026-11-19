import unittest

from raiya_plugin import redgreen as rg


class CommandTest(unittest.TestCase):
    def test_runner_commands(self):
        for cmd in ("pytest", "python3 -m pytest tests/test_a.py", "python -m unittest discover",
                    "cd /x && .venv/bin/pytest -q", "source .venv/bin/activate && python3 -m unittest",
                    "python3 -m pytest 2>&1", "pytest -q > out.txt 2>&1", "PYTHONPATH=. pytest",
                    "uv run pytest", "timeout 60 python -m pytest", "pip install -e . && pytest",
                    "pytest tests/install/test_a.py", 'pytest -k "not install"',
                    "timeout 60 uv run pytest", "timeout -s KILL 60 pytest", "env X=1 pytest",
                    "python3 -B -m pytest", "coverage run -m pytest", "nohup pytest"):
            self.assertEqual(rg.classify_command(cmd), rg.PLAIN, cmd)

    def test_not_a_test_run(self):
        for cmd in ("ls", "git status", "", "echo done"):
            self.assertIsNone(rg.classify_command(cmd), cmd)
        self.assertIsNone(rg.classify_command(None))
        self.assertIsNone(rg.classify_command(3))

    def test_mentions_tests_but_is_not_a_plain_run(self):
        for cmd in ("which pytest", "ls .venv/bin/pytest", "grep -n pytest pyproject.toml", "pip install pytest",
                    "pip show pytest", 'git commit -m "add pytest tests"', "pytest --version",
                    "python3 -m pytest --collect-only", "pytest --co", "cat tests/test_a.py", "make test",
                    "python3 tests/test_a.py", "bash -c 'python -m pytest'"):
            self.assertEqual(rg.classify_command(cmd), rg.LOOSE, cmd)

    def test_masked_when_exit_code_is_not_the_runners(self):
        for cmd in ("pytest | tail -5", "pytest || true", "pytest; echo done", "pytest &",
                    "pytest tests/test_a.py\necho done"):
            self.assertEqual(rg.classify_command(cmd), rg.MASKED, cmd)


class FileTest(unittest.TestCase):
    def test_test_files(self):
        self.assertTrue(rg.is_test_file("/r/tests/test_hooks.py"))
        self.assertTrue(rg.is_test_file("pkg/hooks_test.py"))
        for p in ("/r/raiya_plugin/hooks.py", "/r/tests/__init__.py", "/r/tests/conftest.py", "test_a.txt", None, ""):
            self.assertFalse(rg.is_test_file(p), p)


class FailureTest(unittest.TestCase):
    def test_nonzero_exit_is_fail(self):
        self.assertTrue(rg.is_failed_run("Exit code 1\nFAILED tests/test_a.py", False))

    def test_unknown_shapes_are_not_fail(self):
        self.assertFalse(rg.is_failed_run("Exit code 1\n...", True))  # 中断
        self.assertFalse(rg.is_failed_run("Command timed out", False))
        self.assertFalse(rg.is_failed_run("Exit code 0", False))
        self.assertFalse(rg.is_failed_run(None, False))

    def test_a_missing_runner_is_not_a_failed_test(self):
        # x61 S9: pytest の無い環境で python3 -m pytest が Exit code 1、python が無くて Exit code 127。
        # テストは走っていないので、落ちたテストに数えない (成否不明)。
        self.assertFalse(rg.is_failed_run("Exit code 127", False))
        self.assertFalse(rg.is_failed_run("Exit code 126\nPermission denied", False))
        self.assertFalse(rg.is_failed_run("Exit code 1\n/usr/bin/python3: No module named pytest", False))
        self.assertFalse(rg.is_failed_run("Exit code 1\nModuleNotFoundError: No module named 'tox'", False))
        self.assertFalse(rg.is_failed_run("Exit code 2\nbash: line 1: npm: command not found", False))
        # テストの中の import の誤りは、落ちたテスト。
        self.assertTrue(rg.is_failed_run("Exit code 1\nModuleNotFoundError: No module named 'mypkg'\nFAILED", False))
        self.assertTrue(rg.is_failed_run("Exit code 1", False))


class OrderTest(unittest.TestCase):
    def seq(self, *events):
        state = None
        for kind, arg in events:
            state = rg.record_write(state, arg) if kind == "w" else rg.record_run(state, arg)
        return rg.unseen_red(state)

    def test_red_then_green_is_fine(self):
        self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", rg.FAIL), ("r", rg.PASS)), [])

    def test_green_without_red_is_reported(self):
        self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", rg.PASS)), ["t/test_a.py"])

    def test_red_before_the_test_was_written_does_not_count(self):
        self.assertEqual(self.seq(("r", rg.FAIL), ("w", "t/test_a.py"), ("r", rg.PASS)), ["t/test_a.py"])

    def test_red_seen_afterwards_resolves(self):
        # 差し戻しの文が勧める手当て (後から落ちるのを観る) で解ける。
        self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", rg.PASS), ("r", rg.FAIL), ("r", rg.PASS)), [])

    def test_unknown_run_before_green_fails_open(self):
        for unknown in (rg.MASKED, rg.FAILED_UNKNOWN):
            self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", unknown), ("r", rg.PASS)), [])

    def test_weak_unknown_does_not_clear_unseen(self):
        # 通ったあとの「パイプつきの実行」や関係のない失敗では、観ていない RED は観たことにならない。
        self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", rg.PASS), ("r", rg.MASKED)), ["t/test_a.py"])
        self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", rg.PASS), ("r", rg.FAILED_UNKNOWN)), [])

    def test_never_run_is_not_reported(self):
        self.assertEqual(self.seq(("w", "t/test_a.py")), [])
        self.assertEqual(self.seq(("w", "t/test_a.py"), ("r", rg.FAIL)), [])

    def test_only_first_write_counts(self):
        self.assertEqual(
            self.seq(("w", "t/test_a.py"), ("r", rg.FAIL), ("r", rg.PASS), ("w", "t/test_a.py"), ("r", rg.PASS)), [])

    def test_each_file_judged_separately(self):
        self.assertEqual(
            self.seq(("w", "t/test_a.py"), ("r", rg.FAIL), ("r", rg.PASS), ("w", "t/test_b.py"), ("r", rg.PASS)),
            ["t/test_b.py"])

    def test_many_runs_do_not_drop_the_red(self):
        events = [("w", "t/test_a.py"), ("r", rg.FAIL)] + [("r", rg.PASS)] * 500
        self.assertEqual(self.seq(*events), [])

    def test_broken_state_is_empty(self):
        for bad in (None, {}, {"tests": []}, {"tests": {"t/test_a.py": 3}}, "x", []):
            self.assertEqual(rg.unseen_red(bad), [])
            self.assertEqual(rg.unseen_red(rg.record_run(bad, rg.FAIL)), [])


class CommitTest(unittest.TestCase):
    """対治設計書 B21: テストが落ちたままの commit を見分ける材料。"""

    def test_git_commit_commands(self):
        for cmd in ("git commit -m x", "git add a && git commit -m x", "git -C /r commit -m x",
                    "git -c user.name=t commit -m x", "cd /r && git commit --amend", "GIT_EDITOR=true git commit",
                    "git add a; git commit -m x",
                    # x60 S3・S6: git commit を拒否されたあと、別の作り方で commit を作った。
                    "git commit-tree abc -p HEAD -m x", 'echo "m" | git commit-tree abc -p HEAD',
                    'TREE=$(git write-tree) && COMMIT=$(git commit-tree -p HEAD -m "m" "$TREE") && echo "$COMMIT"',
                    "C=`git commit-tree abc -p HEAD -m x`", 'git update-ref HEAD "$COMMIT"',
                    "git cherry-pick abc", "git revert HEAD", "git am < a.patch", "git merge topic",
                    "git rebase main", "git fast-import < dump", "(cd /r && git commit -m x)",
                    'GIT_AUTHOR_DATE="$(date)" GIT_COMMITTER_DATE="$(date)" git commit -m x'):
            self.assertTrue(rg.is_git_commit(cmd), cmd)

    def test_other_commands_are_not_a_commit(self):
        for cmd in ("git status", "git log --grep commit", "git show commit", "echo git commit",
                    "git commit-graph write", "git stash", "", None, "grep -n 'git commit' README.md",
                    "git write-tree", "git rev-parse HEAD", "git worktree add /tmp/x HEAD", "git log --merges",
                    "git diff --stat", "git add README.md", "git branch -v", "git reset --hard abc"):
            self.assertFalse(rg.is_git_commit(cmd), cmd)

    def test_wide_runners_count_for_the_commit_gate(self):
        for cmd in ("npm test", "npm t", "npm run test", "npm run test:unit", "cd raiya_plugin/pi && npm test",
                    "pnpm test", "pnpm run test", "yarn test", "yarn run test", "bun test", "npx jest", "npx vitest run",
                    "jest", "vitest run", "mocha", "node --test", "deno test", "go test ./...", "cargo test",
                    "make test", "make check", "tox", "tox -e py312", "nox", "./gradlew test", "gradle test", "mvn test",
                    "mvn -q test", "dotnet test", "ctest", "rake test", "bundle exec rspec", "rspec", "phpunit",
                    "CI=1 npm test", "timeout 60 go test ./...", "pytest", "python3 -m unittest discover"):
            self.assertEqual(rg.classify_command(cmd, wide=True), rg.PLAIN, cmd)
        self.assertEqual(rg.classify_command("npm test 2>&1 | tail -5", wide=True), rg.MASKED)

    def test_wide_runners_do_not_change_the_order_check(self):
        # B15 (順序照合) は Python のランナーだけのまま。
        self.assertEqual(rg.classify_command("npm test"), rg.LOOSE)
        self.assertIsNone(rg.classify_command("make check"))
        self.assertIsNone(rg.classify_command("tox"))

    def test_wide_non_runners(self):
        for cmd in ("npm install", "npm run build", "npm run lint", "go build ./...", "cargo build", "make",
                    "make install", "git status", "echo npm test", "yarn", "node server.js", "mvn package",
                    "npm test --help", "ls tests", "cat test.txt", "", None):
            self.assertNotIn(rg.classify_command(cmd, wide=True), (rg.PLAIN, rg.MASKED), cmd)

    def test_failed_run_stays_until_the_same_command_passes(self):
        # x59 S4: 落ちたのと別のテストだけ流して通ると門が開いた。相は、その障が消えたことを示すもの (T0866_004:242)。
        failed = rg.record_failed(None, "pytest tests/test_a.py", rg.FAIL)
        self.assertEqual(failed, ["pytest tests/test_a.py"])
        self.assertEqual(rg.record_failed(failed, "pytest tests/test_b.py", rg.PASS), failed)
        self.assertEqual(rg.record_failed(failed, "pytest tests/test_a.py", rg.PASS), [])

    def test_failed_run_ignores_redirects_and_spacing(self):
        failed = rg.record_failed([], "cd /r &&  python3 -m unittest discover 2>&1", rg.FAIL)
        self.assertEqual(rg.record_failed(failed, "cd /r && python3 -m unittest discover", rg.PASS), [])

    def test_failed_run_is_not_cleared_by_unknown_outcomes(self):
        # x59 S3・S6・S7: 落ちたあとパイプつきで流し直すと門が開いた。成否不明は好い相ではない (T0866_004:255)。
        failed = rg.record_failed([], "pytest", rg.FAIL)
        for outcome in (rg.MASKED, rg.FAILED_UNKNOWN, None):
            self.assertEqual(rg.record_failed(failed, "pytest", outcome), ["pytest"])

    def test_unknown_outcome_alone_is_not_a_failed_run(self):
        for outcome in (rg.MASKED, rg.FAILED_UNKNOWN, rg.PASS, None):
            self.assertEqual(rg.record_failed(None, "pytest", outcome), [])

    def test_failed_runs_keep_each_command_once_and_tolerate_junk(self):
        failed = rg.record_failed("junk", "pytest a", rg.FAIL)
        failed = rg.record_failed(failed + [7], "pytest a", rg.FAIL)
        failed = rg.record_failed(failed, "pytest b", rg.FAIL)
        self.assertEqual(failed, ["pytest a", "pytest b"])
        self.assertEqual(rg.record_failed(failed, "pytest a", rg.PASS), ["pytest b"])


if __name__ == "__main__":
    unittest.main()
