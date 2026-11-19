# Third-party notices

raiya-plugin 自体のライセンスは Apache-2.0 ([LICENSE](LICENSE))。次のファイルは、他の著作物をもとにした部分を含む。

## superpowers (MIT License)

- 出所: superpowers (Jesse Vincent)。
- 使い方: 役の手順の中身 (段の並び・確認・失敗時の扱い・報告の項目) を、superpowers のスキルと雛形をもとに
  日本語で書き直し、単体設計書 §2.1 の 6 欄に並べ替えた。逐語の写しではない。

本文の正本は `raiya_plugin/agents/body/<役>.md`。`raiya_plugin/pi/agents/<役>.md` (`raiya_plugin/agents/assemble.sh pi` の出力) と、利用者が `raiya_plugin/agents/assemble.sh claude` で組み立てる Claude Code 用の定義は、同じ本文に frontmatter を付けたもの。

| ファイル (本文の正本) | もとにした superpowers のファイル |
|---|---|
| `raiya_plugin/agents/body/architect.md` | skills/writing-plans/SKILL.md, skills/brainstorming/SKILL.md |
| `raiya_plugin/agents/body/builder.md` | skills/subagent-driven-development/implementer-prompt.md, skills/test-driven-development/writing-good-tests.md, skills/verification-before-completion/SKILL.md |
| `raiya_plugin/agents/body/qa-reviewer.md` | skills/subagent-driven-development/task-reviewer-prompt.md, skills/requesting-code-review/code-reviewer.md |
| `raiya_plugin/agents/body/scope-planner.md` | skills/brainstorming/SKILL.md |
| `raiya_plugin/agents/body/shipper.md` | skills/finishing-a-development-branch/SKILL.md |

```
MIT License

Copyright (c) 2025 Jesse Vincent

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
