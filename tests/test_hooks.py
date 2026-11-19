import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from raiya_plugin import hooks

GOOD = "直した。\n\n範囲外: 無し\n省略: 無し\n残した仮のもの: 無し\n"
BAD = "直した。"
OLD = "x = 1  # TODO 前からある\n"


def run(sub, payload):
    """main を通して (exit code, stdout の JSON か None) を返す。"""
    out = io.StringIO()
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    code = hooks.main([sub], stdin=stdin, stdout=out)
    return code, (json.loads(out.getvalue()) if out.getvalue() else None)


class HookCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state = self.tmp / "state"
        self.git_env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "HOME": str(self.tmp),
        }
        patcher = mock.patch.dict(os.environ, {"RAIYA_PLUGIN_STATE_DIR": str(self.state), **self.git_env})
        patcher.start()
        self.addCleanup(patcher.stop)
        # 規則のリポは、在りもしない場所に見立てる (試験が実リポの git status を読まないように)。
        patcher = mock.patch.object(hooks, "REPO_ROOT", self.tmp / "no-rules-repo")
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, repo, *args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    def git_repo(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        self.git(repo, "init", "-q")
        (repo / "a.py").write_text(OLD, encoding="utf-8")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-q", "-m", "init")
        return repo

    def start(self, agent_id="a1", cwd=None, agent_type="builder"):
        return run("subagent_start", {"agent_type": agent_type, "agent_id": agent_id,
                                      "session_id": "s1", "cwd": str(cwd or self.tmp)})

    def handback(self, message, agent_id="a1", cwd=None, agent_type="builder"):
        return run("pre_tool_use", {
            "agent_type": agent_type, "agent_id": agent_id, "session_id": "s1",
            "cwd": str(cwd or self.tmp),
            "tool_name": "SubagentHandback", "tool_input": {"message": message},
        })

    def stop(self, message, agent_id="a1", cwd=None, agent_type="builder", active=False):
        return run("subagent_stop", {
            "agent_type": agent_type, "agent_id": agent_id, "session_id": "s1",
            "cwd": str(cwd or self.tmp), "stop_hook_active": active,
            "last_assistant_message": message,
        })

    def log(self):
        path = self.state / "log.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]

    def deny_reason(self, result):
        code, out = result
        self.assertEqual(code, 0)
        self.assertIsNotNone(out, "deny を期待したが通った")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        return out["hookSpecificOutput"]["permissionDecisionReason"]


class SubagentStartTest(HookCase):
    def test_role_gets_pointer_and_field_names(self):
        code, out = self.start()
        self.assertEqual(code, 0)
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SubagentStart")
        ctx = hso["additionalContext"]
        self.assertIn(str(hooks.RULES_PATH), ctx)
        for name in ("範囲外:", "省略:", "残した仮のもの:"):
            self.assertIn(name, ctx)

    def test_rules_path_is_marked_read_only_not_a_workplace(self):
        # x45 life5: 規則の絶対パスを見た builder がそこへ cd して書いた。読むだけの場所と明記する。
        ctx = hooks.injection_text()
        self.assertIn("読むだけ", ctx)
        self.assertIn("作業はいまの作業ディレクトリで行い", ctx)
        self.assertIn("規則ファイルのあるディレクトリでコマンドを打たない", ctx)

    def test_resubmit_note_keeps_body(self):
        self.assertIn("本文", hooks.RESUBMIT_NOTE)
        # x55: 欄はあるのに差し戻す理由 (印・規則のリポ・順序) にも合う言い方にする。
        self.assertIn("上の点を直して出し直す", hooks.RESUBMIT_NOTE)
        self.assertIn("欄だけの返答にしない", hooks.RESUBMIT_NOTE)
        self.assertNotIn("3 欄を足して", hooks.RESUBMIT_NOTE)

    def test_rules_file_exists_and_path_is_absolute(self):
        self.assertTrue(hooks.RULES_PATH.is_absolute())
        self.assertTrue(hooks.RULES_PATH.is_file())

    def test_non_role_and_missing_fields_get_nothing(self):
        self.assertEqual(run("subagent_start", {"agent_type": "Explore", "agent_id": "a1"}), (0, None))
        self.assertEqual(run("subagent_start", {"agent_id": "a1"}), (0, None))
        self.assertEqual(run("subagent_start", {"agent_type": "builder"}), (0, None))
        self.assertEqual(run("subagent_start", {"agent_type": "builder", "agent_id": 7}), (0, None))
        self.assertFalse(self.state.exists())

    def test_agent_ids_do_not_escape_or_collide(self):
        self.start("../../x")
        self.start("a/b")
        self.start("a_b")
        files = [p for p in self.state.rglob("*") if p.is_file()]
        self.assertEqual(len(files), 6)  # 役の状態と、規則のリポの status の写し (B19)
        self.assertEqual({p.parent.name for p in files}, {"agents", "place"})

    def test_corrupt_state_still_injects_rules(self):
        for junk in ("{oops", "[]", "null"):
            self.start("a1")
            path = next((self.state / "agents").iterdir())
            path.write_text(junk, encoding="utf-8")
            with mock.patch("sys.stderr", new=io.StringIO()):
                code, out = self.start("a1")
                self.assertEqual(code, 0, junk)
                self.assertIn("additionalContext", out["hookSpecificOutput"])
                path.write_text(junk, encoding="utf-8")
                self.assertEqual(self.stop(BAD), (0, None), junk)
                path.write_text(junk, encoding="utf-8")
                self.assertEqual(self.handback(GOOD), (0, None), junk)
            path.unlink()


class BashDescriptionTest(HookCase):
    def payload(self, **tool_input):
        return {"agent_type": "builder", "agent_id": "a1", "tool_name": "Bash", "tool_input": tool_input}

    def test_missing_or_blank_description_is_denied(self):
        for ti in ({"command": "ls"}, {"command": "ls", "description": "  "}):
            reason = self.deny_reason(run("pre_tool_use", self.payload(**ti)))
            self.assertIn("description", reason)

    def test_description_passes(self):
        self.assertEqual(run("pre_tool_use", self.payload(command="ls", description="一覧")), (0, None))

    def test_main_session_and_non_role_pass(self):
        for drop, put in (("agent_type", None), ("agent_id", None), (None, ("agent_type", "general-purpose"))):
            p = self.payload(command="ls")
            if drop:
                del p[drop]
            if put:
                p[put[0]] = put[1]
            self.assertEqual(run("pre_tool_use", p), (0, None))

    def test_other_tools_and_odd_tool_input_pass(self):
        p = {"agent_type": "builder", "agent_id": "a1", "tool_name": "Read", "tool_input": {}}
        self.assertEqual(run("pre_tool_use", p), (0, None))
        p = {"agent_type": "builder", "agent_id": "a1", "tool_name": "Read", "tool_input": None}
        self.assertEqual(run("pre_tool_use", p), (0, None))


class HandbackTest(HookCase):
    def test_good_report_passes_and_logs_passed_row(self):
        # 通した事実も書く。不備の行だけだと古い不備が窓に残り続ける (単体 7-13。B14)。
        self.assertEqual(self.handback(GOOD), (0, None))
        rows = self.log()
        self.assertEqual([(r["decision"], r["kinds"], r["problems"]) for r in rows], [("passed", [], [])])

    def test_missing_fields_denied_once_then_let_through_and_logged(self):
        reason = self.deny_reason(self.handback(BAD))
        for name in ("範囲外", "省略", "残した仮のもの"):
            self.assertIn(name, reason)
        self.assertEqual(self.handback(BAD), (0, None))
        self.assertEqual([r["decision"] for r in self.log()], ["deny", "passed_with_problems"])
        self.assertEqual(self.log()[0]["agent_id"], "a1")

    def test_deny_reason_says_keep_body_and_resubmit_whole_report(self):
        # x47: 差し戻し後の出し直しが 3 欄だけになり、元の本文が消えた。
        reason = self.deny_reason(self.handback(BAD))
        self.assertIn(hooks.RESUBMIT_NOTE, reason)

    def test_block_count_is_per_agent(self):
        self.handback(BAD, agent_id="a1")
        self.deny_reason(self.handback(BAD, agent_id="a2"))

    def test_without_agent_id_passes(self):
        code, out = run("pre_tool_use", {
            "agent_type": "builder", "session_id": "s1",
            "tool_name": "SubagentHandback", "tool_input": {"message": BAD},
        })
        self.assertEqual((code, out), (0, None))


class MarksTest(HookCase):
    def test_old_mark_is_not_counted(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        self.assertEqual(self.handback(GOOD, cwd=repo), (0, None))

    def test_undeclared_new_marks_in_tracked_and_untracked_files(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "y = 2  # FIXME 足した\n", encoding="utf-8")
        (repo / "新規.py").write_text("def f():\n    pass  # stub\n", encoding="utf-8")
        reason = self.deny_reason(self.handback(GOOD, cwd=repo))
        self.assertIn("a.py:2", reason)
        self.assertIn("新規.py:2", reason)
        self.assertNotIn("前からある", reason)

    def test_trailing_text_after_fields_still_checks_marks(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "# FIXME 足した\n", encoding="utf-8")
        self.deny_reason(self.handback(GOOD + "\n以上です。\n", cwd=repo))

    def test_marks_committed_during_the_run_are_detected(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "# FIXME 足した\n", encoding="utf-8")
        self.git(repo, "commit", "-q", "-am", "w")
        self.assertIn("a.py:2", self.deny_reason(self.handback(GOOD, cwd=repo)))

    def test_edited_or_moved_old_mark_is_not_new(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text("x = 2  # TODO 前からある\n", encoding="utf-8")
        self.assertEqual(self.handback(GOOD, cwd=repo), (0, None))
        self.start("a2", cwd=repo)
        os.rename(repo / "a.py", repo / "b.py")
        self.assertEqual(self.handback(GOOD, agent_id="a2", cwd=repo), (0, None))

    def test_subdir_cwd_sees_the_whole_repo_with_root_relative_paths(self):
        repo = self.git_repo()
        (repo / "sub").mkdir()
        self.start(cwd=repo / "sub")
        (repo / "sub" / "n.py").write_text("# stub\n", encoding="utf-8")
        (repo / "a.py").write_text(OLD + "# FIXME n\n", encoding="utf-8")
        reason = self.deny_reason(self.handback(GOOD, cwd=repo / "sub"))
        self.assertIn("a.py:2", reason)
        self.assertIn("sub/n.py:1", reason)

    def test_ignored_files_are_not_scanned(self):
        repo = self.git_repo()
        (repo / ".gitignore").write_text(".state/\n", encoding="utf-8")
        self.start(cwd=repo)
        (repo / ".state").mkdir()
        (repo / ".state" / "x.txt").write_text("TODO\n", encoding="utf-8")
        self.assertEqual(self.handback(GOOD, cwd=repo), (0, None))

    def test_user_git_color_and_column_config_do_not_hide_marks(self):
        repo = self.git_repo()
        cfg = self.tmp / "gitconfig"
        cfg.write_text("[color]\n\tgrep = always\n\tui = always\n[grep]\n\tcolumn = true\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(cfg)}):
            self.start(cwd=repo)
            (repo / "a.py").write_text(OLD + "# FIXME 足した\n", encoding="utf-8")
            self.assertIn("a.py:2", self.deny_reason(self.handback(GOOD, cwd=repo)))

    def test_trailing_text_without_a_blank_line_still_checks_marks(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "# FIXME 足した\n", encoding="utf-8")
        self.deny_reason(self.handback(GOOD + "以上です。\n", cwd=repo))

    def test_too_many_marks_at_start_disables_the_mark_check(self):
        repo = self.git_repo()
        with mock.patch.object(hooks, "BASELINE_MAX", 0):
            self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "# FIXME 足した\n", encoding="utf-8")
        self.assertEqual(self.handback(GOOD, cwd=repo), (0, None))
        state = json.loads(next((self.state / "agents").iterdir()).read_text(encoding="utf-8"))
        self.assertIsNone(state["baseline"])

    def test_second_start_does_not_resnapshot_the_baseline(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "y = 2  # FIXME 足した\n", encoding="utf-8")
        self.start(cwd=repo)  # 再開
        self.assertIn("a.py:2", self.deny_reason(self.handback(GOOD, cwd=repo)))

    def test_declared_mark_passes(self):
        repo = self.git_repo()
        self.start(cwd=repo)
        (repo / "a.py").write_text(OLD + "y = 2  # FIXME 足した\n", encoding="utf-8")
        declared = GOOD.replace("残した仮のもの: 無し", "残した仮のもの: a.py:2 の FIXME")
        self.assertEqual(self.handback(declared, cwd=repo), (0, None))

    def test_marks_not_checked_without_baseline_or_outside_git(self):
        repo = self.git_repo()
        (repo / "a.py").write_text("# FIXME 足した\n# FIXME もう一つ\n", encoding="utf-8")
        self.assertEqual(self.handback(GOOD, cwd=repo), (0, None))  # SubagentStart を見ていない
        self.start("a3")  # git の外
        (self.tmp / "b.py").write_text("# FIXME\n", encoding="utf-8")
        self.assertEqual(self.handback(GOOD, agent_id="a3"), (0, None))

    def test_repo_without_commits(self):
        repo = self.tmp / "fresh"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.start(cwd=repo)
        (repo / "n.py").write_text("# FIXME\n", encoding="utf-8")
        self.assertIn("n.py:1", self.deny_reason(self.handback(GOOD, cwd=repo)))


class SubagentStopTest(HookCase):
    def test_good_report_passes(self):
        self.start()
        self.assertEqual(self.stop(GOOD), (0, None))

    def test_missing_fields_block_with_reason(self):
        self.start()
        code, out = self.stop(BAD)
        self.assertEqual(code, 0)
        self.assertEqual(out["decision"], "block")
        self.assertIn("範囲外", out["reason"])
        self.assertIn(hooks.RESUBMIT_NOTE, out["reason"])

    def test_second_stop_passes_and_is_logged(self):
        self.start()
        self.assertEqual(self.stop(BAD)[1]["decision"], "block")
        self.assertEqual(self.stop(BAD, active=True), (0, None))
        self.assertEqual([r["decision"] for r in self.log()], ["block", "passed_with_problems"])

    def test_block_budget_holds_even_if_stop_hook_active_is_not_set(self):
        self.start()
        self.assertEqual(self.stop(BAD)[1]["decision"], "block")
        self.assertEqual(self.stop(BAD), (0, None))

    def test_without_a_seen_start_never_blocks(self):
        self.assertEqual(self.stop(BAD), (0, None))
        self.assertEqual(self.stop(BAD), (0, None))

    def test_after_handback_the_closing_text_is_not_checked(self):
        self.start()
        self.handback(GOOD)
        self.assertEqual(self.stop("以上です。"), (0, None))

    def test_denied_handback_does_not_count_as_handed_back(self):
        self.start()
        self.deny_reason(self.handback(BAD))
        self.assertEqual(self.stop(BAD), (0, None))  # 上限は使い切っているので通すが、記録は残る
        self.assertEqual([r["decision"] for r in self.log()], ["deny", "passed_with_problems"])

    def test_handback_without_agent_id_disables_stop_check_for_the_session(self):
        self.start()
        run("pre_tool_use", {"session_id": "s1", "tool_name": "SubagentHandback",
                             "tool_input": {"message": GOOD}})
        self.assertEqual(self.stop("以上です。"), (0, None))

    def test_resume_gets_a_fresh_budget(self):
        self.start()
        self.handback(GOOD)
        self.start()  # 再開
        self.assertEqual(self.stop(BAD)[1]["decision"], "block")

    def test_non_role_passes(self):
        self.start()
        self.assertEqual(self.stop(BAD, agent_type=""), (0, None))


class MainTest(HookCase):
    def test_bad_json_is_nonblocking_error(self):
        with mock.patch("sys.stderr", new=io.StringIO()) as err:
            self.assertEqual(run("pre_tool_use", "{not json"), (1, None))
            self.assertIn("pre_tool_use", err.getvalue())

    def test_non_object_json_is_nonblocking_error(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(run("subagent_stop", "[]"), (1, None))

    def test_unknown_subcommand(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(hooks.main(["nope"], stdin=io.StringIO("{}"), stdout=io.StringIO()), 1)

class RedGreenTest(HookCase):
    """4-14・4-15: 自分が書いたテストについて、失敗する実行を観てから通る実行を観たか。"""

    def post(self, sub, tool, tool_input, agent_id="a1", agent_type="builder", **extra):
        return run(sub, {"agent_type": agent_type, "agent_id": agent_id, "session_id": "s1",
                         "cwd": str(self.tmp), "tool_name": tool, "tool_input": tool_input, **extra})

    def write(self, path, **kw):
        return self.post("post_tool_use", "Write", {"file_path": path, "content": "x"}, **kw)

    def passed(self, cmd="python3 -m pytest", **kw):
        return self.post("post_tool_use", "Bash", {"command": cmd, "description": "テスト"},
                         tool_response={"stdout": "ok", "stderr": "", "interrupted": False}, **kw)

    def failed(self, cmd="python3 -m pytest", **kw):
        return self.post("post_tool_use_failure", "Bash", {"command": cmd, "description": "テスト"},
                         error="Exit code 1\nFAILED", is_interrupt=False, **kw)

    def test_observers_emit_nothing(self):
        self.start()
        for result in (self.write("/r/tests/test_a.py"), self.failed(), self.passed()):
            self.assertEqual(result, (0, None))

    def test_red_then_green_passes_report(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.failed()
        self.post("post_tool_use", "Edit", {"file_path": "/r/a.py"})
        self.passed()
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_green_without_red_is_sent_back_once(self):
        self.start()
        self.post("post_tool_use", "Edit", {"file_path": "/r/a.py"})
        self.write("/r/tests/test_a.py")
        self.passed()
        reason = self.deny_reason(self.handback(GOOD))
        self.assertIn("/r/tests/test_a.py", reason)
        self.assertIn("失敗する実行", reason)
        self.assertEqual(self.handback(GOOD), (0, None))  # B9: 2 回目は通す
        self.assertEqual([r["decision"] for r in self.log()], ["deny", "passed_with_problems"])

    def test_declaring_it_in_the_omitted_field_does_not_clear_it(self):
        # B15: 「省略」に書いても、観ていない失敗は観たことにならない (T0676_005:97・T1558_029:74)。
        # 通すが段を下げて記す (B9。T0866_004:255)。
        self.start()
        self.write("/r/tests/test_a.py")
        self.passed()
        self.deny_reason(self.handback(GOOD))
        declared = GOOD.replace("省略: 無し", "省略: テストが実装の無い状態で落ちるのを観ていない。")
        self.assertEqual(self.handback(declared), (0, None))
        self.assertEqual([(r["decision"], r["kinds"]) for r in self.log()],
                         [("deny", ["redgreen_unseen"]), ("passed_with_problems", ["redgreen_unseen"])])

    def test_stop_path_also_checks(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.passed()
        code, out = self.stop(GOOD)
        self.assertEqual(out["decision"], "block")

    def test_interrupted_pass_is_not_green(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.post("post_tool_use", "Bash", {"command": "pytest", "description": "t"},
                  tool_response={"stdout": "", "stderr": "", "interrupted": True})
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_background_run_is_not_green(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.post("post_tool_use", "Bash", {"command": "pytest", "description": "t", "run_in_background": True},
                  tool_response={"stdout": "", "stderr": "", "interrupted": False})
        self.passed()
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_non_role_and_main_session_are_ignored(self):
        self.assertEqual(self.write("/r/tests/test_a.py", agent_type="Explore"), (0, None))
        self.assertEqual(run("post_tool_use", {"session_id": "s1", "tool_name": "Bash",
                                               "tool_input": {"command": "pytest"}}), (0, None))
        self.assertFalse((self.state / "agents").exists())

    def test_agents_do_not_share_order(self):
        self.start("a1")
        self.start("a2")
        self.write("/r/tests/test_a.py", agent_id="a1")
        self.passed(agent_id="a2")
        self.assertEqual(self.handback(GOOD, agent_id="a1"), (0, None))

    def test_missing_fields_fail_open(self):
        self.start()
        for payload in ({"tool_name": "Write"}, {"tool_name": "Bash", "tool_input": "x"},
                        {"tool_name": "Bash", "tool_input": {"command": 3}}, {}):
            self.assertEqual(run("post_tool_use", {"agent_type": "builder", "agent_id": "a1", **payload}), (0, None))
            self.assertEqual(
                run("post_tool_use_failure", {"agent_type": "builder", "agent_id": "a1", **payload}), (0, None))
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_timeout_and_interrupt_fail_open(self):
        for i, extra in enumerate(({"error": "Command timed out", "is_interrupt": False},
                                   {"error": "Exit code 1\nx", "is_interrupt": True})):
            aid = f"t{i}"
            self.start(aid)
            self.write("/r/tests/test_a.py", agent_id=aid)
            self.post("post_tool_use_failure", "Bash", {"command": "pytest"}, agent_id=aid, **extra)
            self.passed(agent_id=aid)
            self.assertEqual(self.handback(GOOD, agent_id=aid), (0, None))

    def test_unrecognized_failed_test_command_fails_open(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.failed(cmd="make test")
        self.passed()
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_red_via_runner_without_test_word_fails_open(self):
        for i, cmd in enumerate(("tox -e py39", "make check", "./scripts/check.sh")):
            aid = f"x{i}"
            self.start(aid)
            self.write("/r/tests/test_a.py", agent_id=aid)
            self.failed(cmd=cmd, agent_id=aid)
            self.passed(agent_id=aid)
            self.assertEqual(self.handback(GOOD, agent_id=aid), (0, None), cmd)

    def test_weak_unknowns_do_not_clear_unseen(self):
        for i, step in enumerate((lambda a: self.failed(cmd="grep -n foo x.py", agent_id=a),
                                  lambda a: self.passed(cmd="pytest -q | tail -3", agent_id=a))):
            aid = f"w{i}"
            self.start(aid)
            self.write("/r/tests/test_a.py", agent_id=aid)
            self.passed(agent_id=aid)
            step(aid)
            self.deny_reason(self.handback(GOOD, agent_id=aid))

    def test_set_keeps_concurrently_added_fields(self):
        self.start()
        stale = hooks.load_state("a1")
        self.write("/r/tests/test_a.py")
        with mock.patch.object(hooks, "load_state", side_effect=[stale, hooks.load_state("a1")]):
            self.deny_reason(self.handback(BAD))
        self.assertIn("/r/tests/test_a.py", hooks.load_state("a1")["redgreen"]["tests"])

    def test_successful_mention_of_tests_is_not_a_run(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.passed(cmd="which pytest")
        self.passed(cmd="cat tests/test_a.py")
        self.failed()
        self.passed()
        self.assertEqual(self.handback(GOOD), (0, None))
        self.start("b1")
        self.write("/r/tests/test_a.py", agent_id="b1")
        self.passed(cmd="cat tests/test_a.py", agent_id="b1")
        self.passed(agent_id="b1")
        self.deny_reason(self.handback(GOOD, agent_id="b1"))

    def test_red_seen_after_send_back_resolves(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.passed()
        self.deny_reason(self.handback(GOOD))
        self.failed()
        self.passed()
        self.assertEqual(self.handback(GOOD), (0, None))
        self.assertEqual([r["decision"] for r in self.log()], ["deny", "passed"])

    def test_resume_starts_a_fresh_order(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.passed()
        self.deny_reason(self.handback(GOOD))
        self.assertEqual(self.handback(GOOD), (0, None))
        self.start()  # 再開は別の 1 回 (B9)。前の起動の不備を持ち越さない。
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_corrupt_redgreen_in_state_file(self):
        for i, bad in enumerate(([], "x", {"tests": "x"}, {"tests": {"/r/tests/test_a.py": 3}})):
            aid = f"c{i}"
            self.start(aid)
            state = hooks.load_state(aid)
            state["redgreen"] = bad
            hooks.save_state(aid, state)
            self.assertEqual(self.passed(agent_id=aid), (0, None))
            self.assertEqual(self.handback(GOOD, agent_id=aid), (0, None))

    def test_role_without_start_is_not_recorded(self):
        self.assertEqual(self.write("/r/tests/test_a.py"), (0, None))
        self.assertIsNone(hooks.load_state("a1"))

    def test_concurrent_records_are_not_lost(self):
        import sys
        self.start()
        payload = json.dumps({"agent_type": "builder", "agent_id": "a1", "tool_name": "Write",
                              "tool_input": {"file_path": "/r/tests/test_%d.py"}})
        root = str(Path(hooks.__file__).resolve().parent.parent)
        env = {**os.environ, "PYTHONPATH": root}
        # -B: 親の -B は子に伝わらない。付けないと実リポの raiya_plugin/__pycache__ を書く (x53)。
        procs = [subprocess.Popen([sys.executable, "-B", "-m", "raiya_plugin.hooks", "post_tool_use"],
                                  stdin=subprocess.PIPE, env=env, cwd=root) for _ in range(20)]
        for i, proc in enumerate(procs):
            proc.stdin.write((payload % i).encode())
            proc.stdin.close()
        for proc in procs:
            self.assertEqual(proc.wait(), 0)
        self.assertEqual(len(hooks.load_state("a1")["redgreen"]["tests"]), 20)



class CommitGateTest(HookCase):
    """単体 4-9・対治設計書 B21: shipper は、テストの実行が落ちたあと通る実行を観るまで commit できない。"""

    post = RedGreenTest.post
    write = RedGreenTest.write
    passed = RedGreenTest.passed
    failed = RedGreenTest.failed

    def commit(self, cmd="git commit -m x", agent_id="a1", agent_type="shipper"):
        return run("pre_tool_use", {"agent_type": agent_type, "agent_id": agent_id, "session_id": "s1",
                                    "cwd": str(self.tmp), "tool_name": "Bash",
                                    "tool_input": {"command": cmd, "description": "commit する"}})

    def test_commit_after_failed_run_is_denied(self):
        self.start(agent_type="shipper")
        self.failed("python3 -B -m unittest discover", agent_type="shipper")
        reason = self.deny_reason(self.commit())
        self.assertIn("落ち", reason)
        self.assertIn("止まる", reason)
        self.assertEqual([(r["event"], r["decision"], r["kinds"]) for r in self.log()],
                         [("commit", "deny", ["commit_after_fail"])])

    def test_denied_every_time_until_a_passing_run(self):
        self.start(agent_type="shipper")
        self.failed(agent_type="shipper")
        self.deny_reason(self.commit())
        self.deny_reason(self.commit("git add a && git commit -m x"))
        self.passed(agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_unrelated_failure_does_not_clear_it(self):
        # x58 S1: 落ちたあと git stash pop が失敗し、テストは通らないまま commit した。
        self.start(agent_type="shipper")
        self.failed(agent_type="shipper")
        self.post("post_tool_use_failure", "Bash", {"command": "git stash pop", "description": "戻す"},
                  agent_type="shipper", error="Exit code 1", is_interrupt=False)
        self.post("post_tool_use", "Bash", {"command": "git status", "description": "見る"},
                  agent_type="shipper", tool_response={"interrupted": False})
        self.deny_reason(self.commit())

    def test_passing_a_different_test_does_not_clear_it(self):
        # x59 S4: 落ちたテストと別のモジュールを流して通ったあと、commit が通った。
        self.start(agent_type="shipper")
        self.failed("python3 -B -m unittest tests.test_broken", agent_type="shipper")
        self.passed("python3 -B -m unittest tests.test_other", agent_type="shipper")
        reason = self.deny_reason(self.commit())
        self.assertIn("python3 -B -m unittest tests.test_broken", reason)
        self.assertIn("同じコマンド", reason)
        self.passed("python3 -B -m unittest tests.test_broken 2>&1", agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_every_failed_command_must_pass(self):
        self.start(agent_type="shipper")
        self.failed("pytest a", agent_type="shipper")
        self.failed("pytest b", agent_type="shipper")
        self.passed("pytest a", agent_type="shipper")
        self.assertIn("pytest b", self.deny_reason(self.commit()))

    def test_commit_without_any_observed_passing_run_is_denied(self):
        # x59 S2・S8: 依頼文どおりテストを流さず commit した。観ていなければ与えない (T0001_004:84・T0848_006:344)。
        self.start(agent_type="shipper")
        reason = self.deny_reason(self.commit())
        self.assertIn("通るのを観ていない", reason)
        self.assertIn("npm test", reason)
        self.assertEqual([(r["event"], r["decision"], r["kinds"]) for r in self.log()],
                         [("commit", "deny", ["commit_unseen"])])
        self.passed(agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_other_runners_count(self):
        self.start(agent_type="shipper")
        self.passed("cd raiya_plugin/pi && npm test", agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))
        self.failed("go test ./...", agent_type="shipper")
        self.assertIn("go test ./...", self.deny_reason(self.commit()))
        self.passed("go test ./...", agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_other_runners_do_not_enter_the_order_check(self):
        self.start()
        self.write("/r/tests/test_a.py")
        self.passed("npm test")
        self.assertEqual(self.handback(GOOD), (0, None))

    def test_commit_after_passing_run_passes(self):
        self.start(agent_type="shipper")
        self.passed(agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_unknown_outcome_after_failure_is_still_denied(self):
        # x59 S5・S7: 落ちたあとパイプつきで流し直しても、通る実行を観たことにならない。
        self.start(agent_type="shipper")
        self.failed(agent_type="shipper")
        self.passed("python3 -m pytest | tail -3", agent_type="shipper")
        self.post("post_tool_use_failure", "Bash", {"command": "pytest | tail", "description": "t"},
                  agent_type="shipper", error="Exit code 1", is_interrupt=False)
        reason = self.deny_reason(self.commit())
        self.assertIn("パイプ", reason)
        self.assertIn("前から落ちていたものでも同じ", reason)
        self.passed(agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_unknown_outcome_alone_is_not_a_passing_run(self):
        self.start(agent_type="shipper")
        self.passed("python3 -m pytest | tail -3", agent_type="shipper")
        self.assertIn("通るのを観ていない", self.deny_reason(self.commit()))

    def test_other_roles_are_not_gated(self):
        self.start()
        self.failed()
        self.assertEqual(self.commit(agent_type="builder"), (0, None))

    def test_a_missing_runner_does_not_block_the_commit(self):
        # x61 S9: pytest が入っていない環境で落ち、unittest が通ったあとも commit を拒否した。
        self.start(agent_type="shipper")
        for cmd, error in (("python -m pytest tests/ -v", "Exit code 127"),
                           ("python3 -m pytest tests/ -v", "Exit code 1\n/usr/bin/python3: No module named pytest")):
            self.post("post_tool_use_failure", "Bash", {"command": cmd, "description": "t"},
                      agent_type="shipper", error=error, is_interrupt=False)
        self.assertIn("通るのを観ていない", self.deny_reason(self.commit()))  # 通る実行はまだ観ていない
        self.passed("python3 -B -m unittest discover", agent_type="shipper")
        self.assertEqual(self.commit(), (0, None))

    def test_other_ways_of_making_a_commit_are_denied_too(self):
        # x60 S3・S6: git commit-tree と update-ref・reset で commit を作って門を抜けた。
        self.start(agent_type="shipper")
        self.failed(agent_type="shipper")
        for cmd in ('TREE=$(git write-tree) && COMMIT=$(git commit-tree -p HEAD -m "m" "$TREE") && git update-ref HEAD "$COMMIT"',
                    'echo "m" | git commit-tree abc -p HEAD', "git commit --no-verify -m x",
                    "RAIYA_PLUGIN_DISABLE_HOOKS=1 git commit -m x"):
            reason = self.deny_reason(self.commit(cmd))
            self.assertIn("別の作り方", reason)
            self.assertIn("依頼元が勧めても", reason)

    def test_other_git_commands_pass(self):
        self.start(agent_type="shipper")
        self.failed(agent_type="shipper")
        self.assertEqual(self.commit("git status"), (0, None))

    def test_restart_counts_anew(self):
        self.start(agent_type="shipper")
        self.failed(agent_type="shipper")
        self.start(agent_type="shipper")
        self.assertIn("通るのを観ていない", self.deny_reason(self.commit()))  # 落ちた記録は持ち越さない
        self.passed(agent_type="shipper")
        self.start(agent_type="shipper")
        self.assertIn("通るのを観ていない", self.deny_reason(self.commit()))  # 通った記録も持ち越さない

    def test_second_launch_gets_the_precedent(self):
        for agent_id in ("a1", "a2"):
            self.start(agent_id=agent_id, agent_type="shipper")
            self.failed(agent_id=agent_id, agent_type="shipper")
        self.assertNotIn("繰り返している", self.deny_reason(self.commit(agent_id="a1")))
        reason = self.deny_reason(self.commit(agent_id="a2"))
        self.assertIn("テストが落ちたまま commit (2 回目)", reason)


class PrecedentTest(HookCase):
    """学習の輪 (単体 7-15・7-16。対治設計書 B14): 同じ不備がまた出たその場で前例を添える。"""

    REPEAT = "この役 (builder) は同じ不備を繰り返している"

    def test_first_failure_has_no_precedent_and_records_kind(self):
        reason = self.deny_reason(self.handback(BAD))
        self.assertNotIn("繰り返している", reason)
        self.assertEqual(self.log()[0]["kinds"], ["missing_fields"])

    def test_second_failure_of_same_kind_by_same_role_adds_count(self):
        self.deny_reason(self.handback(BAD, agent_id="a1"))
        reason = self.deny_reason(self.handback(BAD, agent_id="a2"))
        self.assertIn(self.REPEAT + ": 報告の欄の欠け (2 回目)。", reason)
        # 前例の 1 文は出し直しの指示より前に置く。
        self.assertLess(reason.index("2 回目"), reason.index(hooks.RESUBMIT_NOTE))

    def test_other_role_failures_do_not_count(self):
        self.deny_reason(self.handback(BAD, agent_id="q1", agent_type="qa-reviewer"))
        reason = self.deny_reason(self.handback(BAD, agent_id="a1"))
        self.assertNotIn("繰り返している", reason)

    def test_different_kind_does_not_count(self):
        self.deny_reason(self.handback(BAD, agent_id="a1"))
        p = {"agent_type": "builder", "agent_id": "a2", "session_id": "s1", "cwd": str(self.tmp),
             "tool_name": "Bash", "tool_input": {"command": "ls"}}
        reason = self.deny_reason(run("pre_tool_use", p))
        self.assertNotIn("繰り返している", reason)
        self.assertEqual(self.log()[-1]["kinds"], ["bash_description"])

    def test_bash_description_repeat_adds_precedent(self):
        p = {"agent_type": "builder", "agent_id": "a1", "session_id": "s1", "cwd": str(self.tmp),
             "tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.deny_reason(run("pre_tool_use", p))
        reason = self.deny_reason(run("pre_tool_use", {**p, "agent_id": "a2"}))
        self.assertIn(self.REPEAT + ": Bash の description 無し (2 回目)。", reason)

    def test_same_launch_retry_is_not_a_repeat(self):
        # 数える単位は起動。同じ起動の中の呼び直しは一度きり (単体 7-12)。
        p = {"agent_type": "builder", "agent_id": "a1", "session_id": "s1", "cwd": str(self.tmp),
             "tool_name": "Bash", "tool_input": {"command": "ls"}}
        self.deny_reason(run("pre_tool_use", p))
        self.deny_reason(run("pre_tool_use", p))
        reason = self.deny_reason(run("pre_tool_use", p))
        self.assertNotIn("繰り返している", reason)
        reason = self.deny_reason(run("pre_tool_use", {**p, "agent_id": "a2"}))
        self.assertIn("(2 回目)", reason)  # 3 行あっても起動は 1 つ

    def test_place_deny_adds_precedent(self):
        root = self.tmp / "no-rules-repo"
        root.mkdir()
        work = self.tmp / "work"
        work.mkdir()

        def write(agent_id):
            return run("pre_tool_use", {"agent_type": "builder", "agent_id": agent_id, "session_id": "s1",
                                        "cwd": str(work), "tool_name": "Write",
                                        "tool_input": {"file_path": str(root / "x.txt")}})
        self.deny_reason(write("a1"))
        self.assertEqual(self.log()[-1]["kinds"], ["place"])
        reason = self.deny_reason(write("a2"))
        self.assertIn(self.REPEAT + ": 規則のリポを名指す (2 回目)。", reason)

    def test_clean_stop_logs_passed(self):
        self.start(agent_id="a1")
        self.assertEqual(self.stop(GOOD, agent_id="a1"), (0, None))
        self.assertEqual([(r["event"], r["decision"]) for r in self.log()], [("stop", "passed")])

    def test_undecodable_bytes_in_log_do_not_break_the_deny(self):
        self.deny_reason(self.handback(BAD, agent_id="a1"))
        with (self.state / "log.jsonl").open("ab") as f:
            f.write(b"\xff\xfe not json\n")
        reason = self.deny_reason(self.handback(BAD, agent_id="a2"))
        self.assertIn("(2 回目)", reason)

    def test_passed_rows_push_old_failure_out_of_window(self):
        self.deny_reason(self.handback(BAD, agent_id="a0"))
        for i in range(hooks.LOG_WINDOW):
            self.assertEqual(self.handback(GOOD, agent_id=f"g{i}"), (0, None))
        reason = self.deny_reason(self.handback(BAD, agent_id="a1"))
        self.assertNotIn("繰り返している", reason)

    def test_failure_inside_window_still_counts(self):
        self.deny_reason(self.handback(BAD, agent_id="a0"))
        for i in range(hooks.LOG_WINDOW - 1):
            self.assertEqual(self.handback(GOOD, agent_id=f"g{i}"), (0, None))
        reason = self.deny_reason(self.handback(BAD, agent_id="a1"))
        self.assertIn("(2 回目)", reason)

    def test_stop_block_adds_precedent(self):
        self.start(agent_id="a1")
        self.assertEqual(self.stop(BAD, agent_id="a1")[1]["decision"], "block")
        self.start(agent_id="a2")
        _, out = self.stop(BAD, agent_id="a2")
        self.assertIn(self.REPEAT + ": 報告の欄の欠け (2 回目)。", out["reason"])

    def test_old_rows_without_kinds_count_in_window_but_not_as_same_kind(self):
        self.state.mkdir(parents=True, exist_ok=True)
        old = {"ts": 0, "agent_type": "builder", "event": "handback", "decision": "deny", "problems": ["報告に欄が無い"]}
        (self.state / "log.jsonl").write_text(json.dumps(old, ensure_ascii=False) + "\n", encoding="utf-8")
        reason = self.deny_reason(self.handback(BAD))
        self.assertNotIn("繰り返している", reason)

    def test_unreadable_log_line_is_skipped(self):
        self.deny_reason(self.handback(BAD, agent_id="a1"))
        with (self.state / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write("{broken\n")
        reason = self.deny_reason(self.handback(BAD, agent_id="a2"))
        self.assertIn("(2 回目)", reason)

    def test_at_most_three_kinds_shown_most_frequent_first(self):
        rows = []
        for i, kinds in enumerate((["missing_fields"], ["missing_fields"], ["bash_description"],
                                   ["redgreen_unseen"], ["redgreen_unseen"], ["redgreen_unseen"], ["marks_leftover"])):
            rows.append({"ts": 0, "agent_type": "builder", "agent_id": f"L{i}", "event": "x", "decision": "deny",
                         "problems": [], "kinds": kinds})
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "log.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        problems = [hooks.Problem(k, k) for k in
                    ("missing_fields", "bash_description", "redgreen_unseen", "marks_leftover")]
        note = hooks.precedent({"agent_type": "builder", "agent_id": "now"}, problems)
        self.assertEqual(
            note,
            "この役 (builder) は同じ不備を繰り返している: テストの失敗を観ていない (4 回目)・"
            "報告の欄の欠け (3 回目)・Bash の description 無し (2 回目)。",
        )

if __name__ == "__main__":
    unittest.main()


class PlaceTest(HookCase):
    """単体 5-6・5-7・§6.1 (G18)。規則のリポを名指す Bash・Edit・Write を拒否する (対治設計書 B17)。"""

    def setUp(self):
        super().setUp()
        # HOME は HookCase が self.tmp にしている。規則のリポを ~/repos/raiya-plugin に見立てる。
        self.root = self.tmp / "repos" / "raiya-plugin"
        (self.root / "raiya_plugin" / "rules").mkdir(parents=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        patcher = mock.patch.object(hooks, "REPO_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bash(self, command, cwd=None, agent_id="a1"):
        return run("pre_tool_use", {"agent_type": "qa-reviewer", "agent_id": agent_id, "session_id": "s1",
                                    "cwd": str(cwd or self.work), "tool_name": "Bash",
                                    "tool_input": {"command": command, "description": "試す"}})

    def write(self, tool, path, cwd=None):
        return run("pre_tool_use", {"agent_type": "builder", "agent_id": "a1", "session_id": "s1",
                                    "cwd": str(cwd or self.work), "tool_name": tool,
                                    "tool_input": {"file_path": str(path)}})

    def test_x50_cd_with_tilde_is_denied(self):
        self.start(cwd=self.work, agent_type="qa-reviewer")
        reason = self.deny_reason(self.bash("cd ~/repos/raiya-plugin && python3 -m unittest discover"))
        self.assertIn(str(self.root), reason)
        self.assertIn("Read", reason)
        self.assertEqual([(r["event"], r["decision"]) for r in self.log()], [("place", "deny")])

    def test_home_variable_absolute_and_symlink_forms_are_denied(self):
        self.start(cwd=self.work, agent_type="qa-reviewer")
        link = self.tmp / "link"
        link.symlink_to(self.root)
        for cmd in ("cd $HOME/repos/raiya-plugin", "touch ${HOME}/repos/raiya-plugin/raiya_plugin/x",
                    f"cd {self.root}", f"cp a.txt '{self.root}/raiya_plugin/rules/single_agent.md'",
                    f"python3 -m pytest --rootdir={self.root}", f"cd {link}/raiya_plugin", "cd ~/repos/raiya-plugin/"):
            self.deny_reason(self.bash(cmd))

    def test_other_paths_pass(self):
        self.start(cwd=self.work, agent_type="qa-reviewer")
        for cmd in ("python3 -m unittest discover", "cd ~/repos/raiya-plugin-copy", "ls ~/repos",
                    f"cd {self.work}", "cd ../raiya-plugin", "echo raiya-plugin"):
            self.assertEqual(self.bash(cmd), (0, None), cmd)

    def test_x51_relative_paths_are_resolved_from_cwd(self):
        # x51: 規則のリポを指す相対 path への書き込みが通った。
        self.start(cwd=self.work, agent_type="builder")
        sub = self.work / "a" / "b"
        sub.mkdir(parents=True)
        for cmd in ("echo x > ../../../repos/raiya-plugin/notes/b_rel.txt", "cd ../../../repos/raiya-plugin && make"):
            self.deny_reason(self.bash(cmd, cwd=sub))
        self.deny_reason(self.bash("touch repos/raiya-plugin/x", cwd=self.tmp))
        self.assertEqual(self.bash("touch ../../x", cwd=sub), (0, None))

    def test_x51_read_only_commands_pass(self):
        # x51: grep -c・git -C … log まで止まり、builder が clone の値で代えて書いた。
        self.start(cwd=self.work, agent_type="builder")
        for cmd in (f"grep -c def {self.root}/raiya_plugin/hooks.py", f"git -C {self.root} log --oneline -3",
                    "wc -l ~/repos/raiya-plugin/raiya_plugin/hooks.py", f"git --no-pager -C '{self.root}' rev-parse HEAD",
                    f"cat {self.root}/raiya_plugin/rules/single_agent.md", f"find {self.root} -name '*.py'"):
            self.assertEqual(self.bash(cmd), (0, None), cmd)
        for cmd in (f"grep -c def {self.root}/a.py > {self.root}/n.txt", f"git -C {self.root} status",
                    f"git -C {self.root} log --output={self.root}/o.txt", f"find {self.root} -delete",
                    f"cd {self.root} && grep -c def a.py", f"grep x {self.root}/a | sh",
                    f"cat $(echo {self.root})", f"FOO=1 grep x {self.root}/a", f"git -C {self.root} -c a=b log",
                    f"grep 'x {self.root}/a", f"cat <(touch {self.root}/x)",
                    f"git -C {self.root} grep -Ovi x", f"git -C {self.root} grep --open-files-in-pager=vi x",
                    f"git -C {self.root} log -p --ext-diff",
                    # x51 L2n: cd を前に置き、2>&1 と || を付けた形は通さない (理由の文で 1 つにするよう促す)
                    f"cd {self.work} && grep -E x {self.root}/a 2>&1 || echo y"):
            self.deny_reason(self.bash(cmd))

    def test_deny_reason_says_how_to_read_and_to_cite_substitutes(self):
        self.start(cwd=self.work, agent_type="builder")
        reason = self.deny_reason(self.bash("cd ~/repos/raiya-plugin && git log"))
        self.assertIn("git -C", reason)
        self.assertIn("出所", reason)

    def test_role_started_inside_the_repo_is_not_checked(self):
        # raiya-plugin 自身を作業する役。途中で cwd が変わっても、起動時の cwd で決める。
        self.start(cwd=self.root / "raiya_plugin", agent_type="qa-reviewer")
        self.assertEqual(self.bash("cd ~/repos/raiya-plugin && ls", cwd=self.work), (0, None))

    def test_without_a_seen_start_uses_the_call_cwd(self):
        self.deny_reason(self.bash("cd ~/repos/raiya-plugin"))
        self.assertEqual(self.bash("cd ~/repos/raiya-plugin", cwd=self.root, agent_id="a2"), (0, None))

    def test_edit_and_write_into_the_repo_are_denied(self):
        self.start(cwd=self.work)
        self.deny_reason(self.write("Write", self.root / "notes" / "count.txt"))
        self.deny_reason(self.write("Edit", "../repos/raiya-plugin/raiya_plugin/hooks.py"))
        self.assertEqual(self.write("Write", self.work / "notes.txt"), (0, None))
        self.assertEqual(self.write("Edit", "a.py"), (0, None))

    def test_read_and_non_roles_pass(self):
        self.start(cwd=self.work)
        p = {"agent_type": "builder", "agent_id": "a1", "cwd": str(self.work), "tool_name": "Read",
             "tool_input": {"file_path": str(self.root / "raiya_plugin" / "rules" / "single_agent.md")}}
        self.assertEqual(run("pre_tool_use", p), (0, None))
        p = {"cwd": str(self.work), "tool_name": "Bash", "tool_input": {"command": "cd ~/repos/raiya-plugin"}}
        self.assertEqual(run("pre_tool_use", p), (0, None))

    def test_real_repo_root_is_the_clone_root(self):
        root = hooks.default_repo_root()
        self.assertTrue((root / "pyproject.toml").is_file())
        self.assertTrue((root / "raiya_plugin" / "hooks.py").is_file())


class WrittenTest(HookCase):
    """単体 5-6・§6.1 (G18)。役の Bash の間に規則のリポに増えた・変わった path を役に返す (対治設計書 B19)。"""

    CMD = "D=../rules; echo OK > ${D}repo/x"

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "rulesrepo"
        self.root.mkdir()
        self.git(self.root, "init", "-q")
        (self.root / ".gitignore").write_text("__pycache__/\n.state/\n", encoding="utf-8")
        (self.root / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.git(self.root, "add", ".")
        self.git(self.root, "commit", "-q", "-m", "init")
        self.work = self.tmp / "work"
        self.work.mkdir()
        patcher = mock.patch.object(hooks, "REPO_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def payload(self, command, agent_id, **extra):
        return {"agent_type": "builder", "agent_id": agent_id, "session_id": "s1", "cwd": str(self.work),
                "tool_name": "Bash", "tool_input": {"command": command, "description": "試す"}, **extra}

    def before(self, command=CMD, agent_id="a1", **extra):
        self.assertEqual(run("pre_tool_use", self.payload(command, agent_id, **extra)), (0, None))

    def after(self, command=CMD, sub="post_tool_use", agent_id="a1", **extra):
        return run(sub, self.payload(command, agent_id, **extra))

    def bash(self, write=None, **kwargs):
        """役の Bash 1 回: 直前の hook → (その間の書き込み) → 直後の hook。"""
        self.before(kwargs.get("command", self.CMD), kwargs.get("agent_id", "a1"))
        if write:
            write()
        return self.after(**kwargs)

    def put(self, name, text="1\n"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return lambda: path.write_text(text, encoding="utf-8")

    def context(self, result, event="PostToolUse"):
        code, out = result
        self.assertEqual(code, 0)
        self.assertIsNotNone(out, "規則のリポに増えた・変わった path がある、を期待したが何も出なかった")
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], event)
        return out["hookSpecificOutput"]["additionalContext"]

    def test_x52_new_untracked_ignored_and_modified_paths_are_returned_once(self):
        # x52 L7: 変数・コマンド置換・cd の重ね・環境変数で組んだ path は、前もっての照合では見えない。
        self.start(cwd=self.work)
        self.assertEqual(self.bash(), (0, None))  # 何も書いていない

        def write():
            self.put("notes/b_var.txt", "OK\n")()
            self.put("__pycache__/a.pyc", "x")()
            self.put("a.py", "x = 2\n")()

        text = self.context(self.bash(write))
        for name in ("notes/b_var.txt", "__pycache__/a.pyc", "a.py"):
            self.assertIn(name, text)
        self.assertIn(str(self.root), text)
        self.assertIn("消さずに", text)
        self.assertIn("「残した仮のもの」に書く", text)
        # x53 L8: 「自分が書いたものでなければ」の 1 文が、自分の書き込みを否定する余地になった。
        self.assertNotIn("自分が書いたものでなければ", text)
        self.assertNotIn("に書いた:", text)
        self.assertEqual([(r["event"], r["decision"]) for r in self.log()], [("place", "wrote")])
        self.assertEqual(self.bash(), (0, None))  # 同じ path を 2 度言わない

    def test_rewrite_and_removal_of_an_already_listed_path_are_returned(self):
        self.put("n.txt")()
        self.start(cwd=self.work)
        self.assertIn("n.txt", self.context(self.bash(self.put("n.txt", "1234\n"))))
        self.assertIn("n.txt", self.context(self.bash((self.root / "n.txt").unlink)))
        self.assertIn("a.py", self.context(self.bash((self.root / "a.py").unlink)))

    def test_failed_bash_is_checked_too(self):
        self.start(cwd=self.work)
        result = self.bash(self.put("x"), sub="post_tool_use_failure", error="Exit code 1")
        self.assertIn("x", self.context(result, "PostToolUseFailure"))

    def test_state_dir_inside_the_repo_is_not_reported(self):
        # 状態の既定の置き場は規則のリポの .state/。hook 自身の書き込みを役のせいにしない。
        with mock.patch.dict(os.environ, {"RAIYA_PLUGIN_STATE_DIR": str(self.root / ".state")}):
            self.start(cwd=self.work)
            self.assertEqual(self.bash(), (0, None))
            self.assertEqual(self.bash(), (0, None))

    def test_not_checked_cases(self):
        # 起動を見ていない役
        self.assertEqual(self.bash(self.put("x"), agent_id="a9"), (0, None))
        # 規則のリポの中で起動した役 (raiya-plugin 自身を作業する)
        self.start(agent_id="a2", cwd=self.root)
        self.assertEqual(self.bash(self.put("y"), agent_id="a2"), (0, None))
        # 読むだけのコマンドは前後とも写しを取らない
        self.start(agent_id="a3", cwd=self.work)
        self.assertEqual(self.bash(self.put("z"), command=f"wc -l {self.root}/a.py", agent_id="a3"), (0, None))
        # 直前の写しが無い Bash (PreToolUse を見ていない) は照合しない
        self.put("w")()
        self.assertEqual(self.after(agent_id="a3"), (0, None))
        # 主セッション・役でない agent
        p = {"cwd": str(self.work), "tool_name": "Bash", "tool_input": {"command": "true"}}
        self.assertEqual(run("post_tool_use", p), (0, None))

    def test_wsl_paths_written_outside_this_roles_bash_are_not_attributed(self):
        # Claude Code の実機 (2026-11-14): 別の役が書いた probe5.txt で、何も書いていない役の報告が差し戻された。
        self.start(agent_id="a1", cwd=self.work)
        self.start(agent_id="a2", cwd=self.work)
        self.assertIn("probe5.txt", self.context(self.bash(self.put("probe5.txt"), agent_id="a1")))
        self.assertEqual(self.handback(GOOD, agent_id="a2", cwd=self.work), (0, None))  # Bash を打っていない役
        self.start(agent_id="a3", cwd=self.work)
        self.put("later.txt")()  # どの役の Bash の間でもない書き込み
        self.assertEqual(self.bash(agent_id="a3"), (0, None))  # 別の時間に Bash を打った役
        self.assertEqual(self.handback(GOOD, agent_id="a3", cwd=self.work), (0, None))
        self.assertIn("probe5.txt", self.deny_reason(self.handback(GOOD, agent_id="a1", cwd=self.work)))

    def test_parallel_bash_calls_are_matched_by_tool_use_id(self):
        self.start(cwd=self.work)
        self.before(tool_use_id="t1")
        self.put("one.txt")()
        self.before(tool_use_id="t2")
        self.put("two.txt")()
        second = self.context(self.after(tool_use_id="t2"))
        self.assertIn("two.txt", second)
        self.assertNotIn("one.txt", second)
        first = self.context(self.after(tool_use_id="t1"))
        self.assertIn("one.txt", first)
        self.assertIn("two.txt", first)  # t1 の間に書かれたもの

    def test_resume_forgets_the_previous_run(self):
        self.start(cwd=self.work)
        self.context(self.bash(self.put("x")))
        self.start(cwd=self.work)  # 再開は別の 1 回。写しを取り直し、前の起動の記録を持ち越さない
        self.assertEqual(self.bash(), (0, None))
        self.assertEqual(self.handback(GOOD, cwd=self.work), (0, None))

    def test_rules_repo_that_git_cannot_read_is_not_checked(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        with mock.patch.object(hooks, "REPO_ROOT", plain):
            self.start(cwd=self.work)
            self.assertEqual(self.bash((plain / "x").touch), (0, None))

    def test_snapshot_does_not_rewrite_the_index_and_many_paths_are_capped(self):
        index = self.root / ".git" / "index"
        self.put("a.py", "x = 3\n")()
        os.utime(index, (1, 1))
        self.start(cwd=self.work)

        def write():
            for i in range(12):
                self.put(f"f{i:02}.txt")()

        text = self.context(self.bash(write))
        self.assertEqual(int(index.stat().st_mtime), 1)
        self.assertIn("f00.txt", text)
        self.assertIn("他 2 件", text)

    def test_redgreen_is_still_recorded(self):
        self.start(cwd=self.work)
        run("post_tool_use", {"agent_type": "builder", "agent_id": "a1", "cwd": str(self.work),
                              "tool_name": "Write", "tool_input": {"file_path": "tests/test_x.py"}})
        self.context(self.bash(self.put("x"), command="python3 -m pytest"))
        self.assertTrue(hooks.load_state("a1").get("redgreen"))

    # --- x54: 文は届いたのに「残した仮のもの: 無し」と書いた報告 ---

    def test_x54_report_saying_none_while_paths_remain_is_sent_back_once(self):
        self.start(cwd=self.work)
        self.context(self.bash(self.put("notes/progress.txt")))
        reason = self.deny_reason(self.handback(GOOD, cwd=self.work))
        self.assertIn("notes/progress.txt", reason)
        self.assertIn("残した仮のもの", reason)
        self.assertIn(str(self.root), reason)
        self.assertEqual([(r["event"], r["decision"]) for r in self.log()][-1], ("handback", "deny"))
        self.assertEqual(self.handback(GOOD, cwd=self.work), (0, None))  # 差し戻しは 1 回まで

    def test_report_listing_something_passes_and_stop_is_checked_too(self):
        self.start(cwd=self.work)
        self.context(self.bash(self.put("x.txt")))
        named = GOOD.replace("残した仮のもの: 無し", f"残した仮のもの: {self.root}/x.txt (規則のリポに書いた)")
        self.assertEqual(self.handback(named, cwd=self.work), (0, None))
        self.start(agent_id="a2", cwd=self.work)
        self.context(self.bash(self.put("y.txt"), agent_id="a2"))
        code, out = self.stop(GOOD, agent_id="a2", cwd=self.work)
        self.assertEqual(out["decision"], "block")
        self.assertIn("y.txt", out["reason"])
        self.assertNotIn("x.txt", out["reason"])  # 別の役が書いたものは言わない

    def test_paths_put_back_before_the_report_are_not_reported(self):
        self.start(cwd=self.work)
        self.context(self.bash(self.put("x.txt")))
        self.context(self.bash((self.root / "x.txt").unlink))  # 消したことも、その場では返る
        self.assertEqual(self.handback(GOOD, cwd=self.work), (0, None))  # 起動時と同じに戻っていれば言わない

    def test_report_is_not_checked_for_roles_inside_the_repo_or_unreadable_repo(self):
        self.start(cwd=self.root)
        self.assertEqual(self.bash(self.put("x.txt")), (0, None))
        self.assertEqual(self.handback(GOOD, cwd=self.root), (0, None))
        self.start(agent_id="a2", cwd=self.work)
        self.context(self.bash(self.put("y.txt"), agent_id="a2"))
        with mock.patch.object(hooks, "collect_repo_status", return_value=None):
            self.assertEqual(self.handback(GOOD, agent_id="a2", cwd=self.work), (0, None))


class AgentLaunchTest(HookCase):
    """役を起動したツール (Agent・Task) の PostToolUse で、hook は何も足さない。

    単体 B21 の 1 文 (shipper を起動した側に「拒否されても避ける手順を勧めない」) は 2026-11-18 に消した (利用者の裁定。
    Claude Code では役が必ず裏で起動され、文は起動の直後に届き、報告に付かない。禁止はスキル leader の失敗分岐に置く)。
    群れ §8.4 の「報告の数を確かめる」1 文も同日に消した (単体 B30)。commit の門 (対治設計書 B21) は別のもので、残る。
    """

    def agent(self, tool_input, tool="Agent", sub="post_tool_use", **extra):
        return run(sub, {"session_id": "s1", "cwd": str(self.tmp), "tool_name": tool,
                         "tool_input": tool_input, **extra})

    def test_launching_any_role_adds_nothing_to_the_launcher(self):
        # x60: 門に拒否された shipper が尋ねると、リーダーが回避を勧めた。その禁止はスキルの失敗分岐にあり、hook は言わない。
        for tool in ("Agent", "Task"):
            for role in ("shipper", "architect", "scope-planner", "sheet-reader", "builder", "qa-reviewer", "Explore", "", None, 7):
                self.assertEqual(self.agent({"subagent_type": role, "prompt": "p"}, tool=tool), (0, None), (tool, role))
        self.assertEqual(self.agent({}), (0, None))
        self.assertEqual(self.agent({"subagent_types": ["builder", "shipper", "architect"]}), (0, None))
        self.assertEqual(self.agent({"subagent_type": "shipper"}, sub="post_tool_use_failure", error="x"), (0, None))
        self.assertFalse(self.state.exists())  # 状態も log も書かない

    def test_a_role_that_launches_shipper_gets_nothing_too(self):
        self.start()
        self.assertEqual(self.agent({"subagent_type": "shipper"}, agent_type="builder", agent_id="a1"), (0, None))

    def test_the_hook_has_no_relay_note(self):
        self.assertFalse(hasattr(hooks, "_relay_note"))
        self.assertFalse(hasattr(hooks, "AGENT_TOOLS"))

    def test_every_role_definition_has_bash(self):
        # B30: 役はどれも数える道具 (bash) を持つ。持たない役を足すなら、報告の数をどう確かめるかを先に決める (群れ §8.4)。
        root = Path(hooks.__file__).resolve().parent / "agents"
        for kind, word in (("claude", "Bash"), ("pi", "bash")):
            without = set()
            for path in (root / kind).glob("*.md"):
                tools = next(l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("tools:"))
                if word not in [t.strip() for t in tools.split(":", 1)[1].split(",")]:
                    without.add(path.stem)
            self.assertEqual(without, set(), kind)
        self.assertFalse(hasattr(hooks, "NO_BASH_ROLES"))


class ReportFileTest(HookCase):
    """B31: 通した報告の全文を <state>/reports/<agent_id>.md に置く。"""

    def report_path(self, agent_id="a1"):
        return self.state / "reports" / f"{agent_id}.md"

    def test_passed_report_is_written_whole(self):
        long_body = "行\n" * 20000 + GOOD
        self.assertEqual(self.handback(long_body), (0, None))
        self.assertEqual(self.report_path().read_text(encoding="utf-8"), long_body)

    def test_denied_report_is_not_written(self):
        self.deny_reason(self.handback(BAD))
        self.assertFalse(self.report_path().exists())

    def test_report_passed_with_problems_at_the_limit_is_written(self):
        self.deny_reason(self.handback(BAD))
        self.assertEqual(self.handback(BAD), (0, None))
        self.assertEqual(self.report_path().read_text(encoding="utf-8"), BAD)

    def test_second_passed_report_overwrites_the_first(self):
        self.handback(GOOD)
        second = "二回目。\n\n範囲外: 無し\n省略: 無し\n残した仮のもの: 無し\n"
        self.assertEqual(self.handback(second), (0, None))
        self.assertEqual(self.report_path().read_text(encoding="utf-8"), second)

    def test_agent_id_with_path_characters_is_not_written_and_still_passes(self):
        for bad_id in ("../x", "a/b", "..", "a b", "a.b", "a\n", ""):
            with self.subTest(agent_id=bad_id):
                self.assertEqual(self.handback(GOOD, agent_id=bad_id), (0, None))
        self.assertFalse((self.state / "reports").exists())
        self.assertFalse((self.state / "x.md").exists())

    def test_allowed_characters_are_used_as_the_name(self):
        self.handback(GOOD, agent_id="Ab_9-z")
        self.assertEqual(self.report_path("Ab_9-z").read_text(encoding="utf-8"), GOOD)

    def test_unwritable_place_does_not_change_the_decision(self):
        self.state.mkdir(parents=True)
        (self.state / "reports").write_text("file", encoding="utf-8")  # 場所をファイルで塞ぐ
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(self.handback(GOOD), (0, None))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertEqual([r["decision"] for r in self.log()], ["passed"])

    def test_failed_replace_leaves_no_tmp(self):
        (self.state / "reports" / "a1.md").mkdir(parents=True)  # 置き換え先をディレクトリにして os.replace を失敗させる
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(self.handback(GOOD), (0, None))
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertEqual(list((self.state / "reports").glob("*.tmp")), [])

    def test_empty_or_blank_body_is_not_written(self):
        for i, body in enumerate(("", "   ", "\n\t \n")):
            agent_id = f"e{i}"  # 差し戻しの回数は agent_id ごとなので、本文ごとに分けて同じ道を通す
            with self.subTest(body=body):
                self.deny_reason(self.handback(body, agent_id=agent_id))  # 1 回目は差し戻し、2 回目は上限で通す
                self.assertEqual(self.handback(body, agent_id=agent_id), (0, None))
                self.assertFalse(self.report_path(agent_id).exists())
