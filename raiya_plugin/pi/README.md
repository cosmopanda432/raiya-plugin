# pi で raiya-plugin の hook を使う (橋渡しの拡張)

pi には Claude Code の hook もサブエージェントも無い。サブエージェントは外部パッケージ
[pi-subagents](https://github.com/nicobailon/pi-subagents) (npm `pi-subagents`。0.88.0 で確認) が与える。
この拡張を pi-subagents の子に読ませ、子の出来事を Claude Code の hook の stdin の形に組んで
`python3 -B -m raiya_plugin.hooks …` に渡し、hook の出力を pi に戻す。判定は hook (Python) だけがする。

## 対応

| 子の出来事 (pi) | hook | stdin の主な項目 | hook の出力の扱い |
|---|---|---|---|
| `session_start` | `subagent_start` | `agent_type` = 役の名、`agent_id` = 子の session id、`session_id`、`cwd`、`source` = 開始の理由 | `additionalContext` を、その後の最初の run の `before_agent_start` で custom message (`raiya-plugin-start`) として子の文脈に入れる |
| `tool_call` (`bash`) | `pre_tool_use` | `tool_name: "Bash"`、`tool_input: {command, description?, timeout?}` | `permissionDecision` が `deny` (と `ask`) なら tool_call を block し、理由を子に返す |
| `tool_call` (`edit`・`write`) | `pre_tool_use` | `tool_name: "Edit"`/`"Write"`、`tool_input: {file_path}` = pi の `path` (相対なら hook が `cwd` から解く) | 同上 (規則のリポへの書き込みを止める。対治設計書 B17) |
| `agent_before_settle` (outcome が completed のとき) | `subagent_stop` | `last_assistant_message` = 最後の assistant の text、`stop_hook_active` = 直前の settle で差し戻したか | `decision: "block"` なら理由を custom message (`raiya-plugin-stop`) で足して 1 回続けさせる (`continue: true`) |
| `tool_result` (`bash`、`isError` が偽) | `post_tool_use` | `tool_name: "Bash"`、`tool_input: {command, description?}`、`tool_response: {interrupted: false}` | 順序照合 (対治設計書 B15) の記録。規則のリポに書いたものがあれば `additionalContext` が返り、結果の本文 (`content`) の末尾に text として足す (`structuredContent` は残す。対治設計書 B19) |
| `tool_result` (`bash`、`isError` が真) | `post_tool_use_failure` | 終了コード N (≠ 0) が分かれば `error: "Exit code N"` に本文 (4000 字まで) を改行で続けたもの、分からなければ (中断・時間切れ) 本文のまま。`is_interrupt: false` | 同上 |
| `tool_result` (`edit`・`write`、`isError` が偽) | `post_tool_use` | `tool_name: "Edit"`/`"Write"`、`tool_input: {file_path}` = pi の `path` | 同上 |

- 終了コードは pi 1.5.2 の bash が付ける `structuredContent.exit_code` から取り、無ければ本文末尾の `Command exited with code N` から取る。
  どちらも無い失敗は hook の側で成否不明に数える (差し戻さない側)。pi の役での順序照合は x52 (2026-11-14) の実機で観た。規則のリポに書いたものを返す形 (B19) も x53 (同日) の実機で観た。
- 役の名は入口のファイルで決まる: `raiya_plugin/pi/roles/<役>.ts` (architect・builder・qa-reviewer・scope-planner・shipper)。
  役の agent 定義は `raiya_plugin/pi/agents/<役>.md` に同梱し、frontmatter の `extensions` に相対 path (`../roles/<役>.ts`) を書く。
  このファイルは組み立てた写しで、手で直さない。正本は `raiya_plugin/agents/` (本文 `body/<役>.md` + pi 用 frontmatter `pi/<役>.md`)。
  直したら `sh raiya_plugin/agents/assemble.sh pi` で組み立て直す (`tests/test_agents_assemble.py` が食い違いを見る)。
  frontmatter の `tools` は pi の道具名 (read・write・edit・bash・grep・find・ls) で、Claude Code 用 (`raiya_plugin/agents/claude/<役>.md`) と同じ権限にしてある。
  pi-subagents は `./` `../` で始まる path を定義ファイルの場所から解く。
  `extensions` を書くと foreground の子でも読まれ、background の子でも ambient の拡張の代わりにそれだけが読まれる。
- 役の子では `bash` を、`description` (その呼び出しの意図) を受けるものに差し替える (`bash-tool.ts`)。
  pi の組み込み bash は `command` と `timeout` しか受けず、hook の「description が無い Bash は拒否」を満たす書き場所が無いため。
  実行は組み込みの bash のまま。
- 主セッション用の入口 (`extension.ts`) は 2026-11-18 に消した。それまでは、shipper を `subagent` で起動した結果と、裏で起動した役の完了の知らせ
  (custom message `subagent-notify`。`context` の写しの本文の末尾) に、「拒否されても避ける手順を勧めない」の 1 文 (単体設計書 B21 の追補) を足していた
  (x56・x57 の実機で観た。群れ §8.4 の 1 文の時代の記述)。Claude Code では役が必ず裏で起動され、文は報告に付かないので hook の 1 文を消し、
  pi の経路も同じ理由で消した。同じ禁止は `raiya_plugin/skills/leader/SKILL.md` の失敗分岐に置く。橋渡しは役の子だけが読む。
  (同日、`core.ts` の `bindBridge` の主セッション用の分岐 (`role: null`。役の項目を付けずに hook に渡す) も消した。呼ぶ入口が無くなり、`role` は必ず役の名になった。)
- hook はリポの根 (`raiya_plugin/pi/` の 2 つ上) を cwd にして、`.venv/bin/python` (無ければ `python3`。`RAIYA_PLUGIN_HOOK_PYTHON` で上書き) で起こす。
  作業先は payload の `cwd` で渡す。状態は raiya-plugin の clone の `.state/` (単体 B31)。
- `RAIYA_PLUGIN_PI_LOG` を指定すると、hook の呼び出しごとに payload・終了コード・stdout・stderr を JSONL で残す。
- hook の失敗 (終了コード 1・時間切れ・JSON でない出力) では止めない。Claude Code の「exit 1 = 止めない誤り」に合わせた。

## 使い方 (同梱の agent 定義を読ませる)

`package.json` の `"pi": {"subagents": {"agents": ["./agents"]}}` で、`agents/` の 5 本を pi-subagents の package の定義として宣言している。
`agents/` の 5 本は `sh raiya_plugin/agents/assemble.sh pi` の出力で、リポに入れてある (clone した時点で揃っている)。
正本 (`raiya_plugin/agents/`) を直したときは、リポの根で次を実行して組み立て直す。`agents/` を手で直さない:

```bash
sh raiya_plugin/agents/assemble.sh pi
```

pi の settings (`~/.pi/agent/settings.json`、または `PI_CODING_AGENT_DIR` の下) の `packages` にこのディレクトリを足す:

```json
{
  "packages": ["<raiya-plugin の clone>/raiya_plugin/pi"]
}
```

- 主セッションには拡張を入れない (`package.json` の `pi.extensions` は空配列 `[]`)。橋渡しは役の子だけが読む。
  この欄を省くと pi はこのディレクトリ自体を拡張として読もうとし、`Cannot find module` で起動に失敗する (pi 1.5.2 で確認)。
- 同じ名前の定義がユーザー (`~/.pi/agent/agents/`) やプロジェクト (`.pi/agents/`) にあると、そちらが優先される。
- 定義は `async: false` (foreground)。background の子での動きは実機で未確認。
- pi-subagents 0.88.0 の発見処理 (`discoverAgents`) で、6 本 (当時。2026-11-18 から 5 本) が package の定義として見つかり、`extensions` が `roles/<役>.ts` の絶対 path に解けることを確かめた (2026-11-13)。

## Claude Code との違い (分かっている範囲)

- `SubagentHandback` に当たるツールは pi-subagents に無い。報告本文は常に `last_assistant_message` (子の最後の assistant の text) から取る。
- pi-subagents は子の最後の返答に `acceptance-report` の JSON 塊を求めることがある。hook はその塊ごと報告本文として読む。
- `agent_id` は子の pi session id。pi-subagents の run id とは別。子を resume すると同じ session id になり、hook の「再開」として扱われる。
- `session_id` は `PI_SUBAGENT_PARENT_SESSION` があればそれ、無ければ子自身の session id (foreground の子では後者)。
- `stop_hook_active` は橋渡しが自分で数える (直前の settle で差し戻したら true)。
- 文脈の圧縮後の再注入はしない (Claude Code 側でも未確認)。

## 試験

```bash
cd raiya_plugin/pi && node --test test/*.test.ts   # 偽の pi と実物の hook で対応を確かめる
```
