// 橋渡しの試験。pi は偽物 (on で handler を集めるだけ)、hook は実物の raiya_plugin.hooks を呼ぶ。
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { bashExitCode, bindBridge, lastAssistantText, postToolPayload, REPO_ROOT, runHook, type HookRunner } from "../core.ts";

const GOOD = "直した。\n\n範囲外: 無し\n省略: 無し\n残した仮のもの: 無し\n";
const BAD = "直した。";

function fakePi() {
	const handlers: Record<string, (e: any, c: any) => any> = {};
	const tools: any[] = [];
	return {
		handlers,
		tools,
		on(event: string, h: (e: any, c: any) => any) {
			handlers[event] = h;
		},
		registerTool(t: any) {
			tools.push(t);
		},
	};
}

function ctx(cwd: string, sid = "child-1") {
	return { cwd, sessionManager: { getSessionId: () => sid } };
}

function settle(text: string) {
	return {
		type: "agent_before_settle",
		outcome: "completed",
		entries: [],
		continue: false,
		context: { canContinue: false, contextMessages: [{ role: "user", content: "do" }, { role: "assistant", content: [{ type: "text", text }] }] },
	};
}

function sandbox(t: any) {
	const dir = mkdtempSync(join(tmpdir(), "raiya-plugin-pi-"));
	const saved = { s: process.env.RAIYA_PLUGIN_STATE_DIR, l: process.env.RAIYA_PLUGIN_PI_LOG };
	process.env.RAIYA_PLUGIN_STATE_DIR = join(dir, "state");
	process.env.RAIYA_PLUGIN_PI_LOG = join(dir, "bridge.jsonl");
	t.after(() => {
		if (saved.s === undefined) delete process.env.RAIYA_PLUGIN_STATE_DIR;
		else process.env.RAIYA_PLUGIN_STATE_DIR = saved.s;
		if (saved.l === undefined) delete process.env.RAIYA_PLUGIN_PI_LOG;
		else process.env.RAIYA_PLUGIN_PI_LOG = saved.l;
		rmSync(dir, { recursive: true, force: true });
	});
	return dir;
}

test("役: session_start で規則の所在が返り、最初の run の前に 1 回だけ注入する", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "builder", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	const r1 = await pi.handlers.before_agent_start({}, ctx(dir));
	assert.equal(r1.message.customType, "raiya-plugin-start");
	assert.match(r1.message.content, /raiya_plugin\/rules\/single_agent\.md/);
	for (const name of ["範囲外:", "省略:", "残した仮のもの:"]) assert.ok(r1.message.content.includes(name));
	// x45 life5 の再発防止: 規則の所在は読むだけで、作業場所ではないと子に届く
	assert.ok(r1.message.content.includes("読むだけ"));
	assert.ok(r1.message.content.includes("規則ファイルのあるディレクトリでコマンドを打たない"));
	assert.equal(await pi.handlers.before_agent_start({}, ctx(dir)), undefined);
	assert.ok(existsSync(join(dir, "state", "agents")));
});

test("役: description の無い bash は block、付けたものは通す。bash 以外は hook を呼ばない", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "qa-reviewer", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	const denied = await pi.handlers.tool_call({ toolName: "bash", toolCallId: "t1", input: { command: "ls" } }, ctx(dir));
	assert.equal(denied.block, true);
	assert.match(denied.reason, /Bash の description が無い/);
	const ok = await pi.handlers.tool_call({ toolName: "bash", toolCallId: "t2", input: { command: "ls", description: "一覧" } }, ctx(dir));
	assert.equal(ok, undefined);
	const lines = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	assert.equal(await pi.handlers.tool_call({ toolName: "read", input: { path: "x" } }, ctx(dir)), undefined);
	const after = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n");
	assert.equal(after.length, lines.length);
	const pre = lines.filter((l) => l.hook === "pre_tool_use");
	assert.deepEqual(pre.map((l) => l.payload.tool_name), ["Bash", "Bash"]);
	assert.equal(pre[0].payload.agent_type, "qa-reviewer");
	assert.equal(pre[0].payload.agent_id, "child-1");
});

test("役: 欄の無い報告は 1 回だけ差し戻し (continue)、2 回目は通す。欄のある報告は通す", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "builder", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	const back = await pi.handlers.agent_before_settle(settle(BAD), ctx(dir));
	assert.equal(back.continue, true);
	assert.equal(back.entries.length, 1);
	assert.equal(back.entries[0].type, "custom_message");
	assert.match(back.entries[0].content, /報告に欄が無い: 範囲外・省略・残した仮のもの/);
	assert.equal(await pi.handlers.agent_before_settle(settle(BAD), ctx(dir)), undefined);
	const log = readFileSync(join(dir, "state", "log.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	assert.deepEqual(log.map((l) => [l.event, l.decision]), [["stop", "block"], ["stop", "passed_with_problems"]]);

	const pi2 = fakePi();
	bindBridge(pi2, { role: "builder", env: {} });
	await pi2.handlers.session_start({ reason: "startup" }, ctx(dir, "child-2"));
	assert.equal(await pi2.handlers.agent_before_settle(settle(GOOD), ctx(dir, "child-2")), undefined);
	assert.equal(await pi2.handlers.agent_before_settle({ ...settle(BAD), outcome: "aborted" }, ctx(dir, "child-2")), undefined);
});

// pi 1.5.2 の bash の tool_result の形 (dist/core/tools/bash.js): 終了した実行は structuredContent.exit_code を持ち、
// 非 0 は isError で本文の末尾に "Command exited with code N"。中断・時間切れは structuredContent 無しの isError。
function bashResult(command: string, exit: number | null, id = "b") {
	const text = exit === null ? "Command aborted" : exit === 0 ? "OK" : `FAILED\n\nCommand exited with code ${exit}`;
	return {
		type: "tool_result",
		toolName: "bash",
		toolCallId: id,
		input: { command, description: "テストを走らせる" },
		content: [{ type: "text", text }],
		...(exit === null ? {} : { structuredContent: { exit_code: exit } }),
		isError: exit !== 0,
	};
}
const writeResult = (path: string, isError = false) => ({ type: "tool_result", toolName: "write", toolCallId: "w", input: { path, content: "x" }, content: [], isError });

test("役: 書いたテストの通る実行だけを観た報告は 1 回差し戻す (順序照合 B15)", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "builder", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	assert.equal(await pi.handlers.tool_result(writeResult("tests/test_x.py"), ctx(dir)), undefined);
	assert.equal(await pi.handlers.tool_result(bashResult("python3 -m pytest", 0), ctx(dir)), undefined);
	const back = await pi.handlers.agent_before_settle(settle(GOOD), ctx(dir));
	assert.equal(back.continue, true);
	assert.match(back.entries[0].content, /失敗する実行を観ないまま通る実行がある: tests\/test_x\.py/);
	const lines = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	const post = lines.filter((l) => l.hook === "post_tool_use");
	assert.deepEqual(post.map((l) => [l.payload.hook_event_name, l.payload.tool_name, l.payload.tool_input.file_path ?? l.payload.tool_input.command]), [
		["PostToolUse", "Write", "tests/test_x.py"],
		["PostToolUse", "Bash", "python3 -m pytest"],
	]);
	assert.equal(post[1].payload.agent_type, "builder");
});

test("役: 書く → 落ちる実行 → 通る実行の順なら通す。失敗は Exit code N の形で渡す", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "builder", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	await pi.handlers.tool_result(writeResult("test_y.py"), ctx(dir));
	await pi.handlers.tool_result(bashResult("python3 -m unittest", 1, "b1"), ctx(dir));
	await pi.handlers.tool_result(bashResult("python3 -m unittest", 0, "b2"), ctx(dir));
	assert.equal(await pi.handlers.agent_before_settle(settle(GOOD), ctx(dir)), undefined);
	const lines = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	const fail = lines.filter((l) => l.hook === "post_tool_use_failure");
	assert.equal(fail.length, 1);
	assert.equal(fail[0].payload.hook_event_name, "PostToolUseFailure");
	// 1 行目が Exit code N、続けて本文 (hook が「道具が見つからない」形を見分ける。x61 S9)。
	assert.equal(fail[0].payload.error, "Exit code 1\nFAILED\n\nCommand exited with code 1");
	assert.equal(fail[0].payload.is_interrupt, false);
});

test("役: 中断した実行は成否不明 (差し戻さない)。失敗した write・read は記さない", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "builder", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	await pi.handlers.tool_result(writeResult("test_z.py", true), ctx(dir));
	await pi.handlers.tool_result({ type: "tool_result", toolName: "read", input: { path: "test_z.py" }, content: [], isError: false }, ctx(dir));
	await pi.handlers.tool_result(writeResult("test_z.py"), ctx(dir));
	await pi.handlers.tool_result(bashResult("python3 -m pytest", null), ctx(dir));
	await pi.handlers.tool_result(bashResult("python3 -m pytest", 0), ctx(dir));
	assert.equal(await pi.handlers.agent_before_settle(settle(GOOD), ctx(dir)), undefined);
	const lines = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	const posts = lines.filter((l) => typeof l.hook === "string" && l.hook.startsWith("post_"));
	assert.equal(posts.length, 3); // 成功した write 1 + bash 2
	assert.equal(posts[1].payload.error, "Command aborted");
});

test("bashExitCode: structuredContent が無くても本文末尾の終了コードを読む", () => {
	assert.equal(bashExitCode({ isError: true, content: [{ type: "text", text: "x\n\nCommand exited with code 2" }] }), 2);
	assert.equal(bashExitCode({ isError: true, content: [{ type: "text", text: "Command timed out after 5 seconds" }] }), null);
	assert.equal(bashExitCode({ isError: false, content: [{ type: "text", text: "Command exited with code 2" }] }), null);
	assert.deepEqual(postToolPayload({ toolName: "edit", input: { path: "a/test_b.py" }, isError: false })?.fields, { tool_name: "Edit", tool_input: { file_path: "a/test_b.py" } });
	assert.equal(postToolPayload({ toolName: "grep", input: {}, isError: false }), null);
});

test("役: 規則のリポを名指す bash・edit・write は block、作業先は通す (B17)", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "qa-reviewer", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	const cd = await pi.handlers.tool_call({ toolName: "bash", input: { command: `cd ${REPO_ROOT} && python3 -m unittest`, description: "試す" } }, ctx(dir));
	assert.equal(cd.block, true);
	assert.match(cd.reason, /規則のリポ .* を名指している/);
	const w = await pi.handlers.tool_call({ toolName: "write", input: { path: join(REPO_ROOT, "notes", "count.txt"), content: "1" } }, ctx(dir));
	assert.equal(w.block, true);
	assert.equal((await pi.handlers.tool_call({ toolName: "edit", input: { path: join(REPO_ROOT, "README.md"), edits: [] } }, ctx(dir))).block, true);
	assert.equal(await pi.handlers.tool_call({ toolName: "write", input: { path: "notes.txt", content: "1" } }, ctx(dir)), undefined);
	assert.equal(await pi.handlers.tool_call({ toolName: "read", input: { path: join(REPO_ROOT, "README.md") } }, ctx(dir)), undefined);
	const lines = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	const pre = lines.filter((l) => l.hook === "pre_tool_use");
	assert.deepEqual(pre.map((l) => l.payload.tool_name), ["Bash", "Write", "Edit", "Write"]);
	assert.equal(pre[1].payload.tool_input.file_path, join(REPO_ROOT, "notes", "count.txt"));
});

test("役: bindBridge は渡された bashTool を 1 回呼び、pi を渡す (description を書ける bash への差し替え)", () => {
	const pi = fakePi();
	const calls: unknown[] = [];
	bindBridge(pi, { role: "builder", bashTool: (p) => { calls.push(p); }, env: {} });
	assert.equal(calls.length, 1);
	assert.equal(calls[0], pi);
});

test("役でない名・hook の失敗は止めない", async (t) => {
	const dir = sandbox(t);
	const pi = fakePi();
	bindBridge(pi, { role: "worker", env: {} });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir));
	assert.equal(await pi.handlers.tool_call({ toolName: "bash", input: { command: "ls" } }, ctx(dir)), undefined);
	const broken: HookRunner = async () => ({ code: 1, stdout: "", stderr: "boom", out: null });
	const pi2 = fakePi();
	bindBridge(pi2, { role: "builder", run: broken, env: {} });
	await pi2.handlers.session_start({ reason: "startup" }, ctx(dir));
	assert.equal(await pi2.handlers.before_agent_start({}, ctx(dir)), undefined);
	assert.equal(await pi2.handlers.tool_call({ toolName: "bash", input: { command: "ls" } }, ctx(dir)), undefined);
	assert.equal(await pi2.handlers.agent_before_settle(settle(BAD), ctx(dir)), undefined);
});

test("session_id は PI_SUBAGENT_PARENT_SESSION があればそれ、agent_id は子の session id", async (t) => {
	const dir = sandbox(t);
	const seen: any[] = [];
	const rec: HookRunner = async (event, payload) => {
		seen.push(payload);
		return { code: 0, stdout: "", stderr: "", out: null };
	};
	const pi = fakePi();
	bindBridge(pi, { role: "builder", run: rec, env: { PI_SUBAGENT_PARENT_SESSION: "parent-9" } });
	await pi.handlers.session_start({ reason: "startup" }, ctx(dir, "kid"));
	assert.equal(seen[0].session_id, "parent-9");
	assert.equal(seen[0].agent_id, "kid");
	assert.equal(seen[0].agent_type, "builder");
});

test("lastAssistantText は最後の assistant の text 部分だけを取る", () => {
	assert.equal(lastAssistantText([{ role: "assistant", content: "a" }, { role: "assistant", content: [{ type: "toolCall" }] }]), "a");
	assert.equal(lastAssistantText([{ role: "user", content: "u" }]), "");
	assert.equal(lastAssistantText(null), "");
});

test("runHook: 実物の hook を呼んで JSON を読む", async (t) => {
	const dir = sandbox(t);
	const r = await runHook("subagent_start", { agent_type: "builder", agent_id: "x", session_id: "s", cwd: dir });
	assert.equal(r.code, 0);
	assert.equal(r.out.hookSpecificOutput.hookEventName, "SubagentStart");
});

test("runHook: 作業先に別の raiya_plugin があっても、このリポの hook を呼ぶ", async (t) => {
	const dir = sandbox(t);
	const fake = join(dir, "work");
	mkdirSync(join(fake, "raiya_plugin"), { recursive: true });
	writeFileSync(join(fake, "raiya_plugin", "__init__.py"), "");
	writeFileSync(join(fake, "raiya_plugin", "hooks.py"), "print('{\"fake\": 1}')\n");
	const prev = process.cwd();
	process.chdir(fake);
	t.after(() => process.chdir(prev));
	const r = await runHook("subagent_start", { agent_type: "builder", agent_id: "x", session_id: "s", cwd: fake });
	assert.equal(r.out?.fake, undefined);
	assert.match(r.out.hookSpecificOutput.additionalContext, new RegExp(REPO_ROOT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
});

test("runHook: hook の Python に -B を付ける (規則のリポに __pycache__ を書かない。B18)", async (t) => {
	const dir = sandbox(t);
	const py = join(dir, "py.sh");
	writeFileSync(py, "#!/bin/sh\nprintf '{\"args\": \"%s\"}' \"$*\"\n", { mode: 0o755 });
	const prev = process.env.RAIYA_PLUGIN_HOOK_PYTHON;
	process.env.RAIYA_PLUGIN_HOOK_PYTHON = py;
	t.after(() => (prev === undefined ? delete process.env.RAIYA_PLUGIN_HOOK_PYTHON : (process.env.RAIYA_PLUGIN_HOOK_PYTHON = prev)));
	const r = await runHook("subagent_start", {});
	assert.equal(r.out?.args, "-B -m raiya_plugin.hooks subagent_start");
});

test("REPO_ROOT: このリポの根 (raiya_plugin/pi の 2 つ上) を指す", () => {
	assert.ok(existsSync(join(REPO_ROOT, "pyproject.toml")));
	assert.ok(existsSync(join(REPO_ROOT, "raiya_plugin", "hooks.py")));
	assert.ok(existsSync(join(REPO_ROOT, "raiya_plugin", "pi", "core.ts")));
});

test("役: bash の tool_result で hook が additionalContext を返せば、結果の本文の末尾に足す (B19)", async () => {
	const pi = fakePi();
	const note = "規則のリポ (/r) に増えた・変わった path がある: notes/b_var.txt。";
	const run: HookRunner = async (event) => {
		const name = event === "post_tool_use" ? "PostToolUse" : "PostToolUseFailure";
		const out = event.startsWith("post_") ? { hookSpecificOutput: { hookEventName: name, additionalContext: note } } : null;
		return { code: 0, stdout: "", stderr: "", out };
	};
	bindBridge(pi, { role: "builder", env: {}, run });
	await pi.handlers.session_start({ reason: "startup" }, ctx("/w"));
	const ok = await pi.handlers.tool_result(bashResult("D=../r; echo OK > $D/notes/b_var.txt", 0), ctx("/w"));
	assert.deepEqual(ok.content, [{ type: "text", text: "OK" }, { type: "text", text: note }]);
	assert.deepEqual(ok.structuredContent, { exit_code: 0 }); // 本文を替えても終了コードを落とさない
	assert.equal("isError" in ok, false);
	const failed = await pi.handlers.tool_result(bashResult("false", 1), ctx("/w"));
	assert.equal(failed.content.at(-1).text, note);
	assert.deepEqual(failed.structuredContent, { exit_code: 1 });
	const aborted = await pi.handlers.tool_result(bashResult("sleep 99", null), ctx("/w"));
	assert.equal(aborted.content.at(-1).text, note);
	assert.equal("structuredContent" in aborted, false);
});

test("役の子は、subagent の結果と完了の知らせに hook の文を足さない (単体 B21 の 1 文は 2026-11-18 に消した)", async (t) => {
	const dir = sandbox(t);
	for (const role of ["builder", "shipper"]) {
		const pi = fakePi();
		bindBridge(pi, { role, env: {} });
		await pi.handlers.session_start({ reason: "startup" }, ctx(dir, "main"));
		const result = { type: "tool_result", toolName: "subagent", toolCallId: "s", input: { agent: "shipper", task: "commit する" }, content: [{ type: "text", text: "報告" }], isError: false };
		assert.equal(await pi.handlers.tool_result(result, ctx(dir, "main")), undefined, role);
		// 裏で起動した役の完了の知らせ (pi-subagents 0.88.0 の custom message) を書き換える context の入口も無い。
		assert.equal(pi.handlers.context, undefined, role);
	}
	const lines = readFileSync(join(dir, "bridge.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
	assert.equal(lines.filter((l) => l.hook === "post_tool_use").length, 0); // subagent の結果では hook を呼ばない
	assert.equal(existsSync(join(dir, "state", "log.jsonl")), false);
});
