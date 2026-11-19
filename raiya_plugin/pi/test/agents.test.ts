// 同梱の agent 定義 (agents/<役>.md) の形を確かめる。pi-subagents は読み込まず、ファイルだけを見る。
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const PI_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const AGENTS_DIR = join(PI_DIR, "agents");
const ROLES = ["architect", "builder", "qa-reviewer", "scope-planner", "shipper"];

function frontmatter(text: string): Record<string, string> {
	const m = /^---\n([\s\S]*?)\n---\n/.exec(text);
	assert.ok(m, "frontmatter が無い");
	const out: Record<string, string> = {};
	for (const line of m[1].split("\n")) {
		const i = line.indexOf(":");
		if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim();
	}
	return out;
}

test("package.json: pi-subagents に agents/ を同梱の定義として宣言する", () => {
	const pkg = JSON.parse(readFileSync(join(PI_DIR, "package.json"), "utf8"));
	assert.deepEqual(pkg.pi?.subagents?.agents, ["./agents"]);
	// 主セッションに拡張を自動で入れない (役の子だけが橋渡しを読む)。
	// 欄を省くと pi はこのディレクトリ自体を拡張として読もうとし、起動に失敗する (x47)。空配列で明示する。
	assert.deepEqual(pkg.pi?.extensions, []);
});

test("agents/: 役 5 本がそろい、roles/ の入口と 1 対 1", () => {
	const files = readdirSync(AGENTS_DIR).filter((f) => f.endsWith(".md")).sort();
	assert.deepEqual(files, ROLES.map((r) => r + ".md").sort());
	const entries = readdirSync(join(PI_DIR, "roles")).filter((f) => f.endsWith(".ts")).sort();
	assert.deepEqual(entries, ROLES.map((r) => r + ".ts").sort());
});

for (const role of ROLES) {
	test(`agents/${role}.md: name が役の名、extensions は相対 path で roles/${role}.ts を指す`, () => {
		const text = readFileSync(join(AGENTS_DIR, role + ".md"), "utf8");
		const fm = frontmatter(text);
		assert.equal(fm.name, role);
		assert.ok(fm.description && fm.description.length > 0, "description が空");
		const ext = fm.extensions;
		assert.ok(ext && ext.startsWith("../"), `extensions は ../ で始まる相対 path にする: ${ext}`);
		assert.ok(!isAbsolute(ext));
		assert.equal(resolve(AGENTS_DIR, ext), join(PI_DIR, "roles", role + ".ts"));
		// OSS で公開するので、手元の絶対 path やホームを書かない
		assert.doesNotMatch(text, /\/home\/|~\//);
		assert.ok(text.replace(/^---\n[\s\S]*?\n---\n/, "").trim().length > 0, "本文が空");
	});
}

// 単体設計書 §2.1・B6: 役の手順は 6 欄を見出しとして、この順に書く。埋まらない欄は消さず「無し」と書く。
// 見るのは形式だけ (見出しの有無・順序・中身が空でないこと)。中身が手順として正しいかは見ない (§10.2)。
const SIX = ["入口条件", "前提", "順序付き手順", "各段の確認", "失敗分岐", "終了/引継ぎ"];

for (const role of ROLES) {
	test(`agents/${role}.md: 6 欄の見出しがこの順にあり、どの欄も空でない`, () => {
		const body = readFileSync(join(AGENTS_DIR, role + ".md"), "utf8").replace(/^---\n[\s\S]*?\n---\n/, "");
		const heads = [...body.matchAll(/^## (.+)$/gm)].map((m) => m[1].trim());
		assert.deepEqual(heads, SIX);
		const parts = body.split(/^## .+$/m).slice(1);
		parts.forEach((part, i) => assert.ok(part.trim().length > 0, `${SIX[i]} が空 (無ければ「無し」と書く)`));
	});
}
