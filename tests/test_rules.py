"""規則本体 (raiya_plugin/rules/single_agent.md) の形式を機械で見る。中身が正しいかは見ない。

- 各節の末尾の確認 (1-12): 手順の節のどれにも、末尾に `- 確認:` の行が 1 つだけある。
  6 節 (型の表) と 7 節 (報告の形) は手順の節でなく、確認の行を持たない。
  設計書 B22・B24。
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "raiya_plugin" / "rules" / "single_agent.md"
STEP_SECTIONS = range(1, 6)  # 確認の行を持つ手順の節。
SECTION_RE = re.compile(r"## (\d+)\. ")


def sections(text):
    """{節の番号: その節の行}。見出しは `## N. 名前`。"""
    out, cur = {}, None
    for line in text.split("\n"):
        if line.startswith("## "):
            m = SECTION_RE.match(line)
            cur = int(m.group(1)) if m else None
            if cur is not None:
                out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return out


def check_problems(secs):
    """手順の節ごとの、確認の行の問題。"""
    problems = []
    for n in STEP_SECTIONS:
        if n not in secs:
            problems.append(f"{n} 節が無い")
            continue
        lines = [l for l in secs[n] if l.strip()]
        checks = [l for l in lines if l.startswith("- 確認:")]
        if len(checks) != 1:
            problems.append(f"{n} 節: 確認の行が {len(checks)} 本 (1 本であること)")
        elif lines[-1] is not checks[0]:
            problems.append(f"{n} 節: 確認の行が節の末尾にない")
    return problems


class RulesFormat(unittest.TestCase):
    def setUp(self):
        self.text = RULES.read_text(encoding="utf-8")

    def test_section_numbers_are_consecutive(self):
        self.assertEqual(sorted(sections(self.text)), list(range(1, len(sections(self.text)) + 1)))

    def test_each_step_section_ends_with_one_check(self):
        self.assertEqual(check_problems(sections(self.text)), [])


class CheckerCatchesFaults(unittest.TestCase):
    """検査自身が、壊れた規則を見逃さないか。"""

    def make(self, body):
        return sections("\n".join(f"## {n}. x\n{b}" for n, b in body.items()))

    def ok(self):
        return {n: "- a\n- 確認: ok" for n in STEP_SECTIONS}

    def test_ok_passes(self):
        self.assertEqual(check_problems(self.make(self.ok())), [])

    def test_missing_check(self):
        b = self.ok(); b[3] = "- a"
        self.assertEqual(len(check_problems(self.make(b))), 1)

    def test_two_checks(self):
        b = self.ok(); b[2] = "- 確認: 1\n- 確認: 2"
        self.assertEqual(len(check_problems(self.make(b))), 1)

    def test_check_not_last(self):
        b = self.ok(); b[4] = "- 確認: ok\n- a"
        self.assertEqual(len(check_problems(self.make(b))), 1)

    def test_missing_section(self):
        b = self.ok(); del b[4]
        self.assertEqual(check_problems(self.make(b)), ["4 節が無い"])


if __name__ == "__main__":
    unittest.main()
