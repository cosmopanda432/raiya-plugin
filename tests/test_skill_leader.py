"""リーダー用のスキル (raiya_plugin/skills/leader/SKILL.md) の形式を機械で見る。中身が正しいかは見ない。

群れ設計書 §4.4。見るのは次だけ:
- frontmatter に name と description があり、description が 5 役を名指ししている。
- 役の定義と同じ 6 欄があり、段ごとに同じ名前の確認がある (単体 B16・B22)。
- 失敗分岐に、役が hook に拒否されたときの 1 文 (単体 B21 の追補) を持つ。同じ文を hook は足さない (2026-11-18 に hook から消した。群れ §8.4 の 1 文も同日に消した。単体 B30)。
リーダーが読んだか・従ったかは見ない (hook は主セッションの所作を検査しない)。
"""
import re
import unittest
from pathlib import Path

from tests.test_agents_assemble import ROLES, section, step_check_problems

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "raiya_plugin" / "skills" / "leader" / "SKILL.md"
HOOKS = REPO / "raiya_plugin" / "hooks.py"
HEADINGS = ["入口条件", "前提", "順序付き手順", "各段の確認", "失敗分岐", "終了/引継ぎ"]
FM_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
# スキルの失敗分岐の文。hook は足さない (2026-11-18 に hook の処理を消した。リーダーへの禁止はここだけが持つ)。
SHARED = [
    "避ける手順 (--no-verify・git commit-tree・環境変数など) を勧めない。自分でも行わない。",
    "落ちたテストを直す役に戻すか、利用者に尋ねる。",
]


def split(text):
    """(frontmatter の {項目: 値}, 本文)。frontmatter が無ければ (None, text)。"""
    m = FM_RE.match(text)
    if not m:
        return None, text
    fm = {}
    for line in m.group(1).split("\n"):
        key, sep, value = line.partition(": ")
        if sep:
            fm[key.strip()] = value.strip()
    return fm, m.group(2)


def heading_problems(body):
    found = [l[3:].strip() for l in body.split("\n") if l.startswith("## ")]
    return [] if found == HEADINGS else [f"見出しが 6 欄の並びでない: {found}"]


class LeaderSkillFormat(unittest.TestCase):
    def setUp(self):
        self.fm, self.body = split(SKILL.read_text(encoding="utf-8"))

    def test_frontmatter_names_the_skill_and_the_roles(self):
        self.assertIsNotNone(self.fm, "frontmatter が無い")
        self.assertEqual(self.fm.get("name"), "leader")
        missing = [r for r in ROLES if r not in self.fm.get("description", "")]
        self.assertEqual(missing, [], "description が名指ししていない役")

    def test_six_headings_in_order(self):
        self.assertEqual(heading_problems(self.body), [])

    def test_no_empty_section(self):
        empty = [h for h in HEADINGS if not any(l.strip() for l in section(self.body, h))]
        self.assertEqual(empty, [])

    def test_every_step_has_a_named_check(self):
        self.assertEqual(step_check_problems(self.body), [])

    def test_failure_branch_keeps_the_no_workaround_sentences(self):
        failure = "\n".join(section(self.body, "失敗分岐"))
        for s in SHARED:
            self.assertIn(s, failure)

    def test_hook_no_longer_says_them(self):
        hooks = HOOKS.read_text(encoding="utf-8")
        for s in SHARED:
            self.assertNotIn(s, hooks, "hook が起動した側に足す処理は消した (2026-11-18)")

    def test_no_absolute_paths(self):
        self.assertNotIn("/home/", SKILL.read_text(encoding="utf-8"))


class CheckerCatchesFaults(unittest.TestCase):
    def test_split_without_frontmatter(self):
        self.assertEqual(split("本文")[0], None)

    def test_split_reads_fields(self):
        fm, body = split("---\nname: x\ndescription: a: b\n---\n本文\n")
        self.assertEqual((fm, body), ({"name": "x", "description": "a: b"}, "本文\n"))

    def test_heading_missing(self):
        body = "\n".join(f"## {h}" for h in HEADINGS[:-1])
        self.assertEqual(len(heading_problems(body)), 1)

    def test_heading_out_of_order(self):
        body = "\n".join(f"## {h}" for h in reversed(HEADINGS))
        self.assertEqual(len(heading_problems(body)), 1)


if __name__ == "__main__":
    unittest.main()
