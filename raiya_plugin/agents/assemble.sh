#!/bin/sh
# 役の定義を正本から組み立てる: frontmatter (claude/ か pi/) + 本文 (body/) を cat で連結する。
#   sh raiya_plugin/agents/assemble.sh pi              → raiya_plugin/pi/agents/<役>.md (pi が読む。出力をリポに入れる)
#   sh raiya_plugin/agents/assemble.sh claude <出力先>  → <出力先>/<役>.md (例: ~/.claude/agents。リポには入れない)
#     claude のときは、リーダーが読むスキル (raiya_plugin/skills/leader/SKILL.md) も、出力先の隣の
#     skills/leader/SKILL.md に写す (例: ~/.claude/agents → ~/.claude/skills/leader/SKILL.md)。pi には出さない。
#     写すとき、スキルの <clone> をこの clone の絶対 path に置き換える (報告の置き場 <clone>/.state。単体 B31)。
# 手で直すのは正本 (body/・claude/・pi/) だけ。直したら組み立て直す。出力先には出力だけを置き、手で直さない
# (2 か所を手で直すと食い違う。tests/test_agents_assemble.py が食い違いを見る)。
set -eu
here=$(cd "$(dirname "$0")" && pwd)
case "${1:-}" in
	pi) target=$here/pi; out=${2:-$here/../pi/agents} ;;
	claude) target=$here/claude; out=${2:?出力先のディレクトリを指定する (例: ~/.claude/agents)} ;;
	*) echo "usage: $0 {pi|claude} [出力先]" >&2; exit 2 ;;
esac
mkdir -p "$out"
# 出力先の同名ファイルは黙って上書きする (写しは取らない。要るなら先に退避する)。
for fm in "$target"/*.md; do
	role=$(basename "$fm")
	cat "$fm" "$here/body/$role" > "$out/$role"
	echo "wrote $out/$role"
done
# リーダーのスキル (群れ設計書 §4.4)。役の定義と違い、組み立てずに <clone> だけを置き換えて写す。同名ファイルは上書きする。
if [ "$1" = claude ]; then
	skill=$(dirname "$out")/skills/leader
	mkdir -p "$skill"
	clone=$(cd "$here/../.." && pwd -P)
	# sed の置き換え側で意味を持つ \ & | を逃がす。
	esc=$(printf '%s\n' "$clone" | sed 's/[\\&|]/\\&/g')
	sed "s|<clone>|$esc|g" "$here/../skills/leader/SKILL.md" > "$skill/SKILL.md"
	echo "wrote $skill/SKILL.md"
fi
# 正本に無い .md は消さず、知らせるだけにする (利用者が置いた別の定義かもしれない)。
for f in "$out"/*.md; do
	[ -e "$f" ] || continue
	[ -e "$target/$(basename "$f")" ] || echo "note: 正本に無い定義が出力先に残っている (消していない): $f" >&2
done
