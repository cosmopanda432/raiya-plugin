# raiya-plugin

> **β版**: 実機での動作確認を続けています。hook の挙動や役の定義は予告なく変わることがあります。

Claude Code・pi のサブエージェント(役)の運用を、設計書に沿って実装するリポジトリ。
記憶エンジン raiya の姉妹リポ。

※ pi 用は準備中。

## 設計書

- 単体エージェント 設計書 — サブエージェント 1 役の定義と所作 (知覚・判断・自己検証・行為・失敗の型・引継ぎ) の規則
- 単体エージェント 対治・発火条件 設計書 — 失敗の型ごとの対治と徴候、規則の発火条件、検査への渡し先
- 群れ運用 設計書 — 複数の役(サブエージェント)の運用規則 (S1〜S7: hooks・agent 定義・settings・文書。Python 約 800〜1200 行の見積もり)

※ 設計書は本リポに含めず、別の文書リポで管理している。

## 使い方

Python 3.9 以上 (標準ライブラリだけで動く)。以下の `<clone>` は本リポを clone した場所。

### Claude Code

1. 仮想環境を作り、本リポをそこに入れる。hook はこの仮想環境の python で動かす (`PYTHONPATH` は使わない。シェルの設定に `export PYTHONPATH=<clone>` を書くと、どの仮想環境からも `import raiya_plugin` が通ってしまう)。`-e` を付けて入れる (hook は自分のファイルの場所から clone の場所を知るため):

   ```bash
   cd <clone>   # <clone> = clone してできたディレクトリ (本リポの根。例: $HOME/raiya-plugin)
   python3 -m venv .venv
   .venv/bin/pip install -e .
   ```

2. 役の定義を組み立てて置く (同名のファイルは上書きされるので、要るなら先に退避する)。リーダーが読むスキルも、出力先の隣の `skills/leader/SKILL.md` (下の例では `~/.claude/skills/leader/SKILL.md`) に写される:

   ```bash
   cd <clone>   # <clone> = clone してできたディレクトリ (本リポの根。例: $HOME/raiya-plugin)
   sh raiya_plugin/agents/assemble.sh claude ~/.claude/agents
   ```

3. `~/.claude/settings.json` の `hooks` に次を足す。hook は役 (下の「役の定義」の 5 本) の中で発火したときだけ働き、主セッションや他の agent には何も出さない (例外は無い。2026-11-18 までは、shipper を起動した側に 1 文を足す例外があったが、消した。下の表の `post_tool_use`)。

   ```json
   {
     "hooks": {
       "SubagentStart": [
         {"hooks": [{"type": "command", "command": "<clone>/.venv/bin/python -B -m raiya_plugin.hooks subagent_start", "timeout": 30}]}
       ],
       "PreToolUse": [
         {"matcher": "Bash|Edit|Write|SubagentHandback", "hooks": [{"type": "command", "command": "<clone>/.venv/bin/python -B -m raiya_plugin.hooks pre_tool_use", "timeout": 30}]}
       ],
       "SubagentStop": [
         {"hooks": [{"type": "command", "command": "<clone>/.venv/bin/python -B -m raiya_plugin.hooks subagent_stop", "timeout": 30}]}
       ],
       "PostToolUse": [
         {"matcher": "Bash|Edit|Write", "hooks": [{"type": "command", "command": "<clone>/.venv/bin/python -B -m raiya_plugin.hooks post_tool_use", "timeout": 30}]}
       ],
       "PostToolUseFailure": [
         {"matcher": "Bash", "hooks": [{"type": "command", "command": "<clone>/.venv/bin/python -B -m raiya_plugin.hooks post_tool_use_failure", "timeout": 30}]}
       ]
     }
   }
   ```

   | hook | すること |
   |---|---|
   | `subagent_start` | 規則本体 (`raiya_plugin/rules/single_agent.md`) の所在と、報告の末尾に書く 3 欄 (`範囲外:`・`省略:`・`残した仮のもの:`) を役に伝える |
   | `pre_tool_use` | `description` の無い Bash を止める。規則のリポ (この clone) を名指す Bash・Edit・Write を止める (役がこの clone の外で起動したときだけ。`~`・`$HOME` を展開し、相対 path は呼び出しの cwd から解く。`grep`・`wc`・`git -C <path> log` などの読むだけのコマンドは 1 つだけなら通す。読むだけの形と相対 path は pi の実機 (x52) で確かめた。Claude Code の実機では Edit・Write と変数に書いた path の拒否を 2026-11-14 に観た)。shipper の `git commit` (と、commit を作るほかの git の呼び出し: `commit-tree`・`update-ref`・`cherry-pick`・`revert`・`am`・`merge`・`rebase`・`fast-import`) は、その起動で落ちたテストの実行があれば止める (落ちたのと同じコマンドが、パイプ無しで通るのを観るまで。別のテストが通っても、パイプつきで流し直しても解けない。前から落ちていたものでも同じ)。通る実行を 1 つも観ていない `git commit` も止める (数えるのは `pytest`・`unittest`・`npm test`・`pnpm test`・`yarn test`・`go test`・`cargo test`・`make test` などで始まる、パイプの無い実行。ほかのランナーのプロジェクトでは shipper は commit できず、結果を報告して止まる)。報告 (`SubagentHandback`) に不備があれば差し戻す (下) |
   | `subagent_stop` | 同じ報告の検査を、`SubagentHandback` を使わない役の最後の発言に当てる |
   | `post_tool_use`・`post_tool_use_failure` | テストを書いた・走らせた順序を記す。役の Bash のあとで、規則のリポ (この clone) に増えた・変わったファイルがあれば役に伝える (止めない。変数や `cd` の重ねで組んだ path は `pre_tool_use` では見えないため。役の Bash が走っていた間の変化だけを見る。ちょうど同じ時間に他が書いたものは拾う)。役を起動する `Agent` のあとで hook は何もしない (matcher に `Agent` は要らない。2026-11-18 まで: 起動したのが shipper なら、起動した側に「拒否されても避ける手順を勧めない・自分でも行わない」の 1 文を足していた (単体設計書 B21 の追補)。Claude Code では役は必ず裏 (background) で起動され、文は起動の直後に届いて報告に付かないので消した。同じ禁止はリーダーのスキルの失敗分岐 (`raiya_plugin/skills/leader/SKILL.md`) に置く)。登録しなければどれもしない (Claude Code の実機では 2026-11-14 に観た。そこで観た、同時に動く役の取り違えは、Bash の間だけを見る形に改めて狭めた。改めた形は実機で未確認。対治設計書 §7) |

   報告の不備は 4 つ: 3 欄が無い / `残した仮のもの: 無し` なのに起動後に TODO などの印の行が増えている / `残した仮のもの: 無し` なのに、起動後に規則のリポ (この clone) に増えた・変わったファイルが残っている / 書いたテストが落ちるのを観ないまま通している。差し戻しは役の 1 回の起動につき 1 回まで。差し戻し・拒否・通した事実は `<clone>/.state/log.jsonl` に 1 行ずつ残り、同じ役が直近 20 行の中で同じ種類の不備を出していれば、差し戻しの文に「同じ不備を繰り返している: 種類 (k 回目)」の 1 文を添える (単体 §8.1・対治 B14。Claude Code の実機で 2026-11-15 に観た)。状態は `<clone>/.state/` に置く (置き場は固定。`.gitignore` に入れてある)。役の報告の全文も `<clone>/.state/reports/<agentId>.md` に書き、リーダーのスキルはこの path で次の役へ渡してよい (単体 B31)。スキルの中の `<clone>` は手順 2 のコマンドが clone の絶対 path に置き換えるので、clone を動かしたら手順 2 をやり直す。

4. Claude Code を起動し直し、いつも通り主セッションに仕事を頼む。

   主セッションがリーダー (群れ設計書の「司令」) になる。依頼を読み、Agent ツールで役をサブエージェントとして起動し、役の報告を受けて次の役に渡す。
   どの役を使うかは、各役の frontmatter の `description` を手がかりに主セッションが選ぶ (例: 何を作るかが曖昧なら scope-planner、実装は builder、完了の前に qa-reviewer)。
   役を名指しして頼んでもよい (例:「builder でこの関数にテストを足して」)。

   リーダー用の定義ファイルは無いが、役を起動する前に読むスキルが 1 本ある (役の選び方、依頼文に入れるもの、報告を現物で確かめてから渡すこと)。正本は `raiya_plugin/skills/leader/SKILL.md` で、手順 2 のコマンドが `~/.claude/skills/leader/SKILL.md` に写す。
   スキルは主セッションが読むだけのもので、読んだか・従ったかを hook は検査しない。pi 用は準備中。

   お勧めの設定: `~/.claude/CLAUDE.md` (無ければ作る) に次の 1 行を足す。主セッションにスキルの一覧で渡るのは frontmatter の `description` だけで、本文を読むかどうかは主セッションが決める。`CLAUDE.md` は毎セッション本文ごと渡るので、ここに書くと読み落としが減る (これも読ませるだけで、守らせる力は無い):

   ```markdown
   - raiya-plugin の役 (architect・builder・qa-reviewer・scope-planner・shipper) をサブエージェントとして起動する前に、leader スキルを読む。
   ```

### pi

※ 準備中。

[pi-subagents](https://github.com/nicobailon/pi-subagents) の package として読ませる。手順は [raiya_plugin/pi/README.md](raiya_plugin/pi/README.md)。

### テスト

```bash
python3 -B -m unittest discover       # リポの根で。-B: リポに __pycache__ を書かない
(cd raiya_plugin/pi && npm test)         # pi の橋渡し (Node)
```

## 止めきれないもの

hook は、ツールの呼び出しを 1 つずつ字で見て止める。役がその字を避ければ通る。

- shipper の commit の門は、`git commit` と、commit を作るほかの git の呼び出しを見る。`.git` の中のファイルを直に書く形や、門が知らない作り方は止められない。実機の試験では、`git commit` を拒否された役が `git commit-tree` で commit を作った。その形は塞いだが、同じことは別の字で起こりうる。
- 門が数えるテストの実行は、決まったランナーで始まる、パイプの無いコマンドだけ。それ以外のランナーのプロジェクトでは、shipper は commit できずに止まる。テストを流す道具が見つからずに終わった実行 (Exit code 126・127、`No module named pytest`、`command not found`) は、落ちたテストに数えない。
- hook が見るのは役の呼び出しだけで、リーダー (主セッション) の呼び出しは見ない。実機の試験では、拒否された役が尋ねると、リーダーが門を避ける手順を勧めた回があった。禁止は、リーダーが読む手順 (`raiya_plugin/skills/leader/SKILL.md` の失敗分岐) にだけある。hook は何も足さない (2026-11-18 まで、shipper を起動した側に同じ趣旨の 1 文を足していたが、役は必ず裏で起動され文が報告に付かないので消した)。手順は読むだけのもので、守らせる力は無く、従うかどうかはリーダー次第で、リーダーが自分で commit することも止められない。
- 報告の検査は形 (欄の有無・呼び出しの順序) だけを見る。中身が正しいかは見ない。
- `-` で始まる名前のファイルやディレクトリ (例: Claude Code の記憶の置き場 `~/.claude/projects/-home-dev-…`) は、役が正常に処理できないことがある。`head`・`grep` などのコマンドは「`-` で始まる文字列はオプション」と読む決まりのため、`--` も `./` も付けずに渡すとパスがオプションと解釈されてエラーになる。実際の作業では、shipper が `head -5 "$f"` (`f` は `-home-dev-raiya-pi/…` のパス) と打ち、`head: invalid option -- 'h'` で読めなかった。hook はこれを直さない。

根本の対策は、このプロジェクトだけでは難しい。役が使える道具と権限を絞ること (サンドボックス、git の権限、保護されたブランチ、CI) は、hook の外で用意する必要がある。このプロジェクトの hook は、規則を届けて、よくある踏み外しをその場で止めるためのもので、悪意や回避を防ぐ仕組みではない。

## 役の定義

役 5 本 (architect・builder・qa-reviewer・scope-planner・shipper) の定義の正本は [raiya_plugin/agents/](raiya_plugin/agents/)。
本文 `body/<役>.md` が 1 本で、frontmatter だけが Claude Code 用 `claude/<役>.md` と pi 用 `pi/<役>.md` に分かれる (道具の名前が違うため)。

```bash
sh raiya_plugin/agents/assemble.sh pi                      # raiya_plugin/pi/agents/ に出力する (出力をリポに入れる)
sh raiya_plugin/agents/assemble.sh claude ~/.claude/agents  # Claude Code 用 (同名ファイルは上書きする。リーダーのスキルも ~/.claude/skills/leader/ に写す)
```

手で直すのは正本だけ。`raiya_plugin/pi/agents/` にはコマンドの出力だけを置き、手で直さない。正本を直したら `assemble.sh pi` を実行し、出力も一緒に commit する (`tests/test_agents_assemble.py` が食い違いを見る)。
リーダーのスキルも同じで、手で直すのは正本の `raiya_plugin/skills/leader/SKILL.md` だけ。`~/.claude/skills/leader/SKILL.md` は手で直さない。


## ライセンス

Apache-2.0 ([LICENSE](LICENSE))。役の定義の本文の一部は superpowers (MIT License) をもとにしている。出所と許諾の全文は [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
