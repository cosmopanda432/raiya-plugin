"""役の定義の正本 (raiya_plugin/agents/) と、cat で組み立てた定義が食い違わないか。

正本は 1 本: body/<役>.md (本文)。frontmatter だけが Claude Code 用 (claude/<役>.md) と
pi 用 (pi/<役>.md) で分かれる。道具の名前が違うため (Claude Code = Read・Bash、pi = read・bash)。
組み立ては `cat <frontmatter> body/<役>.md` (raiya_plugin/agents/assemble.sh)。
pi はリポ内の raiya_plugin/pi/agents/<役>.md を読むので、その写しが組み立ての結果と同じかを見る。
"""
import os
import re
import subprocess
import tempfile
from unittest import mock
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "raiya_plugin" / "agents"
PI_OUT = REPO / "raiya_plugin" / "pi" / "agents"
ROLES = ["architect", "builder", "qa-reviewer", "scope-planner", "shipper"]
FM_RE = re.compile(r"\A---\n(.*?)\n---\n\Z", re.S)

# 群れ設計書 §5: tools 行が権限の実体。pi 側は Claude Code 側と同じ権限を pi の道具名で書く。
# Glob → find・ls。WebSearch・WebFetch に当たる組み込みの道具は pi に無い。
CLAUDE_TO_PI = {
    "Read": {"read"},
    "Write": {"write"},
    "Edit": {"edit"},
    "Bash": {"bash"},
    "Grep": {"grep"},
    "Glob": {"find", "ls"},
    "WebSearch": set(),
    "WebFetch": set(),
}


def fields(path):
    m = FM_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError(f"frontmatter だけのファイルでない (--- で始まり --- で終わる): {path}")
    out = {}
    for line in m.group(1).split("\n"):
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def tools(value):
    return {t.strip() for t in value.split(",") if t.strip()}


def section(body, heading):
    """見出し `## <heading>` の下の行 (次の `## ` の手前まで)。"""
    out, on = [], False
    for line in body.split("\n"):
        if line.startswith("## "):
            on = line[3:].strip() == heading
        elif on:
            out.append(line)
    return out


STEP_RE = re.compile(r"\d+\. (.*)")
QUALIFIER_RE = re.compile(r" \([^()]*\)\Z")
POINTER_RE = re.compile(r"「(.+?)」の確認で観る")
ANY_STEP = "どの段でも"


def name_of(text):
    """`名前: 説明` の名前。`: ` が無いか、名前か説明が空なら None。"""
    name, sep, rest = text.partition(": ")
    return name.strip() if sep and name.strip() and rest.strip() else None


def step_check_problems(body):
    """単体設計書 B22 (1-11・1-12): 段に名前があり、どの段にも同じ名前の確認の行があるか。形式だけを見る。

    見るのは行頭の `N. ` (段) と行頭の `- ` (確認) だけ。字下げした行 (段の中の箇条書き・表) は見ない。
    """
    problems, steps = [], []
    for line in section(body, "順序付き手順"):
        m = STEP_RE.match(line)
        if not m:
            continue
        name = name_of(m.group(1))
        if name is None:
            problems.append(f"名前の無い段: {line}")
        else:
            steps.append(name)
    checked, pointers = set(), {}
    for line in section(body, "各段の確認"):
        if not line.startswith("- "):
            continue
        name = name_of(line[2:])
        if name is None:
            problems.append(f"名前の無い確認: {line[:40]}")
            continue
        name = QUALIFIER_RE.sub("", name)
        if name == ANY_STEP:
            continue
        if name not in steps:
            problems.append(f"段に無い名前の確認: {name}")
            continue
        m = POINTER_RE.search(line)
        if m:
            pointers[name] = m.group(1)
        else:
            checked.add(name)
    for name, target in pointers.items():
        if target not in checked:
            problems.append(f"「{name}」が指す「{target}」は、自分の確認を持つ段でない")
    for name in steps:
        if name not in checked and name not in pointers:
            problems.append(f"確認の無い段: {name}")
    if not steps:
        problems.append("段が無い")
    return problems


ROLE_WORD_RE = re.compile(r"[a-z]+(?:-[a-z]+)+|[a-z]{4,}")


def handoff_problems(body):
    """1-14: 「渡す先」があり、そこに書いた役の名が 5 役のどれかか。"""
    lines = [l for l in section(body, "終了/引継ぎ") if "渡す先:" in l]
    if not lines:
        return ["「渡す先:」が無い"]
    problems = []
    for line in lines:
        for word in ROLE_WORD_RE.findall(line.split("渡す先:", 1)[1]):
            if word not in ROLES:
                problems.append(f"渡す先に、5 役に無い名前: {word}")
    return problems


class TestAgentSources(unittest.TestCase):
    def test_each_role_has_one_body_and_two_frontmatters(self):
        for sub in ("body", "claude", "pi"):
            names = sorted(p.stem for p in (SRC / sub).glob("*.md"))
            self.assertEqual(names, sorted(ROLES), sub)

    def test_body_has_no_frontmatter(self):
        for role in ROLES:
            text = (SRC / "body" / f"{role}.md").read_text(encoding="utf-8")
            self.assertFalse(text.startswith("---"), role)
            self.assertTrue(text.strip(), role)

    def test_body_names_the_three_report_fields(self):
        # 単体設計書 裁定 1・3 / B3: 全役の定義に、報告の 3 欄 (hook の検査と同じ欄名) を書く。
        # hook の注入だけに頼ると、注入を読み落とした役が初回に差し戻される。
        from raiya_plugin import report

        for role in ROLES:
            body = (SRC / "body" / f"{role}.md").read_text(encoding="utf-8")
            tail = body.split("## 終了/引継ぎ", 1)[-1]
            for name, _ in report.FIELDS:
                self.assertIn(name + ":", tail, f"{role}: 終了/引継ぎに「{name}:」が無い")

    def test_every_step_has_a_named_check(self):
        # 単体設計書 B22 (1-11・1-12)。
        for role in ROLES:
            body = (SRC / "body" / f"{role}.md").read_text(encoding="utf-8")
            self.assertEqual(step_check_problems(body), [], role)

    def test_step_check_problems_are_found(self):
        body = (
            "## 順序付き手順\n\n1. 読む: 依頼文。\n   - 字下げ: 見ない。\n2. 名前が無い。\n3. 書く: 本文。\n4. 直す: 本文。\n5. 送る: 本文。\n"
            "\n## 各段の確認\n\n- 読む (長いとき): 言える。\n- 名前の無い行。\n- 余り: 段に無い。\n"
            "- どの段でも (数): 出所がある。\n- 書く: 「読む」の確認で観る。\n- 直す: 「書く」の確認で観る。\n"
        )
        self.assertEqual(step_check_problems(body), [
            "名前の無い段: 2. 名前が無い。",
            "名前の無い確認: - 名前の無い行。",
            "段に無い名前の確認: 余り",
            "「直す」が指す「書く」は、自分の確認を持つ段でない",
            "確認の無い段: 送る",
        ])
        self.assertEqual(step_check_problems("## 順序付き手順\n\n本文だけ。\n"), ["段が無い"])
        # `: ` のあとが空の行は、名前だけで中身が無い。確認のある段に数えない。
        empty = "## 順序付き手順\n\n1. 読む: 依頼文。\n2. 書く: \n\n## 各段の確認\n\n- 読む:  \n- 書く: 書いてある。\n"
        self.assertEqual(step_check_problems(empty), [
            "名前の無い段: 2. 書く: ",
            "名前の無い確認: - 読む:  ",
            "段に無い名前の確認: 書く",
            "確認の無い段: 読む",
        ])

    def test_handoff_names_real_roles(self):
        # 1-14: 次に誰へ渡すかを書く。役の名を書くなら、実在する 5 役のどれか。
        for role in ROLES:
            body = (SRC / "body" / f"{role}.md").read_text(encoding="utf-8")
            self.assertEqual(handoff_problems(body), [], role)

    def test_handoff_problems_are_found(self):
        self.assertEqual(handoff_problems("## 終了/引継ぎ\n\n- 終わり: 済んだ。\n"), ["「渡す先:」が無い"])
        self.assertEqual(
            handoff_problems("## 終了/引継ぎ\n\n- 完了。渡す先: builder と qa-reviewr、tester へ。\n"),
            ["渡す先に、5 役に無い名前: qa-reviewr", "渡す先に、5 役に無い名前: tester"],
        )

    def test_frontmatter_names_match_role(self):
        for role in ROLES:
            for sub in ("claude", "pi"):
                f = fields(SRC / sub / f"{role}.md")
                self.assertEqual(f.get("name"), role, f"{sub}/{role}")
                self.assertTrue(f.get("description"), f"{sub}/{role}")
                self.assertTrue(f.get("tools"), f"{sub}/{role}: tools が無い")

    def test_claude_frontmatter_has_model(self):
        for role in ROLES:
            self.assertTrue(fields(SRC / "claude" / f"{role}.md").get("model"), role)

    def test_pi_tools_are_claude_tools_in_pi_names(self):
        for role in ROLES:
            claude = tools(fields(SRC / "claude" / f"{role}.md")["tools"])
            expected = set().union(*(CLAUDE_TO_PI[t] for t in claude))
            self.assertEqual(tools(fields(SRC / "pi" / f"{role}.md")["tools"]), expected, role)

    def test_no_absolute_paths(self):
        # OSS で公開するので、定義に手元の絶対 path やホームを書かない (assemble.sh の使い方の例は除く)
        for path in SRC.rglob("*.md"):
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"/home/|~/", str(path))

    def test_pi_copy_is_cat_of_frontmatter_and_body(self):
        for role in ROLES:
            want = (SRC / "pi" / f"{role}.md").read_bytes() + (SRC / "body" / f"{role}.md").read_bytes()
            self.assertEqual((PI_OUT / f"{role}.md").read_bytes(), want, f"{role}: assemble.sh pi を実行し直す")

    def test_assemble_script_writes_claude_definitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "agents")
            subprocess.run(["sh", str(SRC / "assemble.sh"), "claude", out], check=True)
            for role in ROLES:
                want = (SRC / "claude" / f"{role}.md").read_bytes() + (SRC / "body" / f"{role}.md").read_bytes()
                self.assertEqual((Path(out) / f"{role}.md").read_bytes(), want, role)
            self.assertEqual(sorted(os.listdir(out)), sorted(f"{r}.md" for r in ROLES))

    def test_assemble_script_copies_leader_skill_next_to_claude_definitions(self):
        """群れ設計書 §4.4: claude のときだけ、出力先の隣の skills/leader/ にリーダーのスキルを写す。"""
        skill = SRC.parent / "skills" / "leader" / "SKILL.md"
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["sh", str(SRC / "assemble.sh"), "claude", str(Path(tmp) / "agents")], check=True)
            self.assertEqual(sorted(os.listdir(tmp)), ["agents", "skills"])
            # 単体 B31: 写すとき <clone> をこの clone の絶対 path に置き換える。置き換えたあとの置き場は hook の既定と同じ。
            clone = str(SRC.parent.parent.resolve())
            want = skill.read_text(encoding="utf-8").replace("<clone>", clone)
            got = (Path(tmp) / "skills" / "leader" / "SKILL.md").read_text(encoding="utf-8")
            self.assertEqual(got, want)
            self.assertNotIn("<clone>", got)
            self.assertIn(f"{clone}/.state/reports/<agentId>.md", got)
            from raiya_plugin import hooks
            # RAIYA_PLUGIN_STATE_DIR が無い状態で呼ぶ。with を抜けると patch.dict が env を元に戻す。
            with mock.patch.dict(os.environ):
                os.environ.pop("RAIYA_PLUGIN_STATE_DIR", None)
                self.assertEqual(str(hooks.state_dir()), f"{clone}/.state")

    def test_assemble_script_pi_does_not_copy_leader_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["sh", str(SRC / "assemble.sh"), "pi", str(Path(tmp) / "agents")], check=True)
            self.assertEqual(os.listdir(tmp), ["agents"])


if __name__ == "__main__":
    unittest.main()
