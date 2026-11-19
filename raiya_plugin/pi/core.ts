// raiya-plugin の hook (raiya_plugin/hooks.py) を pi の拡張から呼ぶ橋渡しの本体。
//
// pi には Claude Code の hook もサブエージェントも無い。pi-subagents の子セッションに
// この拡張を読ませ、子の出来事を Claude Code の hook の stdin の形に組んで hook に渡し、
// hook の出力を pi の側に戻す。判定はすべて hook (Python) がする。ここは結線だけ。
//
//   子の session_start          → subagent_start   (返った additionalContext を最初の run の前に注入)
//   子の bash の tool_call      → pre_tool_use (tool_name "Bash")   (deny は block にする)
//   子の edit・write の tool_call → pre_tool_use (tool_name "Edit"/"Write"、path を file_path に。deny は block)
//   子の agent_before_settle    → subagent_stop    (block は理由を子に渡して 1 回続けさせる)
//   子の bash の tool_result    → post_tool_use / post_tool_use_failure (順序を記す。additionalContext が返れば結果の本文の末尾に足す)
//   子の edit・write の tool_result (成功) → post_tool_use (tool_name "Edit"/"Write"、path を file_path に)
//   (2026-11-18: 主セッションで subagent の結果・完了の知らせに「拒否されても避ける手順を勧めない」を足す経路 (単体 B21 の追補) は消した。
//    役は必ず裏で起動され、文は報告に付かない。禁止はスキル leader の失敗分岐に置く。
//    同日、主セッション (role なし) 用の分岐も消した。入口 extension.ts を消して呼ぶ所が無くなり、role は必ず役の名になった)
//
// pi に依存する部分 (bash ツールの差し替え) は bash-tool.ts に分け、ここは node だけで試せる形にする。
import { spawn } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const ROLES = ["architect", "builder", "qa-reviewer", "scope-planner", "shipper"] as const;
export type HookEvent = "subagent_start" | "pre_tool_use" | "subagent_stop" | "post_tool_use" | "post_tool_use_failure";
export type HookResult = { code: number | null; stdout: string; stderr: string; out: any; error?: string };
export type HookRunner = (event: HookEvent, payload: Record<string, unknown>) => Promise<HookResult>;

// このファイルは <リポ>/raiya_plugin/pi/core.ts。2 つ上がリポの根。
export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const HOOK_TIMEOUT_MS = Number(process.env.RAIYA_PLUGIN_HOOK_TIMEOUT_MS ?? 30_000);
export const INJECT_TYPE = "raiya-plugin-start";
export const SENDBACK_TYPE = "raiya-plugin-stop";

export function hookPython(env: NodeJS.ProcessEnv = process.env): string {
	if (env.RAIYA_PLUGIN_HOOK_PYTHON) return env.RAIYA_PLUGIN_HOOK_PYTHON;
	const venv = join(REPO_ROOT, ".venv", "bin", "python");
	return existsSync(venv) ? venv : "python3";
}

/** `python3 -m raiya_plugin.hooks <event>` に payload を stdin で渡す。失敗しても投げない (hook の失敗で作業を止めない)。 */
export const runHook: HookRunner = (event, payload) =>
	new Promise((done) => {
		const env = { ...process.env, PYTHONPATH: [REPO_ROOT, process.env.PYTHONPATH].filter(Boolean).join(":") };
		let stdout = "";
		let stderr = "";
		let settled = false;
		const finish = (r: Omit<HookResult, "out">) => {
			if (settled) return;
			settled = true;
			let out: any = null;
			if (r.code === 0 && r.stdout.trim()) {
				try {
					out = JSON.parse(r.stdout);
				} catch (e) {
					r.error = `stdout が JSON でない: ${(e as Error).message}`;
				}
			}
			done({ ...r, out });
		};
		let child;
		try {
			// cwd はこのリポに固定する。`python -m` は cwd を sys.path の先頭に置くので、作業先のリポ (raiya-plugin の写しなど) の
			// raiya_plugin を読んでしまわないように。hook は作業先を payload の cwd で受ける。
			// -B: 規則のリポに __pycache__ を書かない (対治設計書 B18)。
			child = spawn(hookPython(), ["-B", "-m", "raiya_plugin.hooks", event], { cwd: REPO_ROOT, env, stdio: ["pipe", "pipe", "pipe"] });
		} catch (e) {
			finish({ code: null, stdout, stderr, error: (e as Error).message });
			return;
		}
		const timer = setTimeout(() => {
			child.kill("SIGKILL");
			finish({ code: null, stdout, stderr, error: `timeout ${HOOK_TIMEOUT_MS}ms` });
		}, HOOK_TIMEOUT_MS);
		child.stdout.on("data", (d: Buffer) => (stdout += d.toString("utf8")));
		child.stderr.on("data", (d: Buffer) => (stderr += d.toString("utf8")));
		child.on("error", (e: Error) => {
			clearTimeout(timer);
			finish({ code: null, stdout, stderr, error: e.message });
		});
		child.on("close", (code: number | null) => {
			clearTimeout(timer);
			finish({ code, stdout, stderr });
		});
		child.stdin.on("error", () => {});
		child.stdin.end(JSON.stringify(payload));
	});

/** RAIYA_PLUGIN_PI_LOG があれば、hook の呼び出しを 1 行ずつ JSONL で残す (検証用)。 */
export function logCall(row: Record<string, unknown>, env: NodeJS.ProcessEnv = process.env): void {
	const path = env.RAIYA_PLUGIN_PI_LOG;
	if (!path) return;
	try {
		mkdirSync(dirname(path), { recursive: true });
		appendFileSync(path, JSON.stringify({ ts: new Date().toISOString(), pid: process.pid, ...row }) + "\n");
	} catch {
		// 記録に失敗しても作業は止めない
	}
}

const ERROR_TEXT_MAX = 4000;

function textOf(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((p: any) => p && p.type === "text" && typeof p.text === "string")
		.map((p: any) => p.text)
		.join("\n");
}

/** 境界の文脈から、最後の assistant の本文 (text 部分だけ) を取る。Claude Code の last_assistant_message に当たる。 */
export function lastAssistantText(messages: unknown): string {
	if (!Array.isArray(messages)) return "";
	for (let i = messages.length - 1; i >= 0; i--) {
		const m: any = messages[i];
		if (m && m.role === "assistant") {
			const t = textOf(m.content);
			if (t.trim()) return t;
		}
	}
	return "";
}

/**
 * bash の tool_result から終了コードを取る。pi 1.5.2 の bash は終了した実行に structuredContent.exit_code を付け、
 * 非 0 なら本文の末尾に "Command exited with code N" を足す。中断・時間切れ・終了コード無しは null (成否不明)。
 */
export function bashExitCode(event: any): number | null {
	const code = event?.structuredContent?.exit_code;
	if (typeof code === "number" && Number.isInteger(code)) return code;
	if (!event?.isError) return null;
	const m = /\nCommand exited with code (\d+)\s*$/.exec("\n" + textOf(event?.content));
	return m ? Number(m[1]) : null;
}

/** pi の tool_result を Claude Code の PostToolUse / PostToolUseFailure の形に組む。対象外は null。 */
export function postToolPayload(event: any): { hook: HookEvent; fields: Record<string, unknown> } | null {
	const input = event?.input ?? {};
	const name = event?.toolName;
	if (name === "edit" || name === "write") {
		if (event.isError || typeof input.path !== "string") return null;
		return { hook: "post_tool_use", fields: { tool_name: name === "edit" ? "Edit" : "Write", tool_input: { file_path: input.path } } };
	}
	if (name !== "bash") return null;
	const toolInput: Record<string, unknown> = { command: input.command };
	if (input.description !== undefined) toolInput.description = input.description;
	const common = { tool_name: "Bash", tool_input: toolInput, tool_use_id: event.toolCallId ?? null };
	if (!event.isError) return { hook: "post_tool_use", fields: { ...common, tool_response: { interrupted: false } } };
	const code = bashExitCode(event);
	// 終了コードが分かる失敗だけを Claude Code の形 (1 行目が "Exit code N"、続けて本文) にする。他 (中断・時間切れ) は本文のまま渡し、hook は成否不明に数える。
	// 本文を付けるのは、hook が「テストを流す道具が見つからない」形 (No module named pytest など) を見分けるため (x61 S9)。長さは ERROR_TEXT_MAX まで。
	const text = textOf(event.content);
	const error = code !== null && code !== 0 ? `Exit code ${code}${text ? "\n" + text.slice(0, ERROR_TEXT_MAX) : ""}` : text || "error";
	return { hook: "post_tool_use_failure", fields: { ...common, error, is_interrupt: false } };
}

export type PiLike = {
	on(event: string, handler: (event: any, ctx: any) => unknown): unknown;
	registerTool?(tool: any): void;
};

export type BridgeOptions = {
	/** 役の名 (agent_type)。 */
	role: string;
	run?: HookRunner;
	/** bash を description 付きのものに差し替える (bash-tool.ts)。 */
	bashTool?: (pi: PiLike) => void;
	/** 子の環境 (PI_SUBAGENT_PARENT_SESSION を読む)。既定は process.env。 */
	env?: NodeJS.ProcessEnv;
};

export function bindBridge(pi: PiLike, opts: BridgeOptions): void {
	const role = opts.role;
	const run = opts.run ?? runHook;
	const env = opts.env ?? process.env;
	// 親の session id (pi-subagents が子に立てる) があればそれを session_id にする。無ければ子自身の id。
	const parentSession = env.PI_SUBAGENT_PARENT_SESSION || null;
	let agentId: string | null = null;
	let sessionId: string | null = null;
	let started: Promise<string | null> | null = null;
	let injected = false;
	let continued = false;

	if (opts.bashTool) opts.bashTool(pi);

	const base = (ctx: any): Record<string, unknown> => {
		return { session_id: sessionId, cwd: ctx?.cwd ?? process.cwd(), agent_type: role, agent_id: agentId };
	};
	const call = async (event: HookEvent, payload: Record<string, unknown>, extra: Record<string, unknown> = {}) => {
		const t0 = Date.now();
		const r = await run(event, payload);
		logCall({ hook: event, ms: Date.now() - t0, role, agent_id: agentId, session_id: sessionId, payload, code: r.code, stdout: r.stdout, stderr: r.stderr, error: r.error, ...extra });
		return r;
	};

	pi.on("session_start", async (event: any, ctx: any) => {
		const own = ctx?.sessionManager?.getSessionId?.() ?? null;
		agentId = own ? String(own) : null;
		sessionId = parentSession ?? agentId;
		injected = false;
		continued = false;
		started = (async () => {
			const r = await call("subagent_start", { ...base(ctx), hook_event_name: "SubagentStart", source: event?.reason ?? null });
			const ctxText = r.out?.hookSpecificOutput?.additionalContext;
			return typeof ctxText === "string" && ctxText ? ctxText : null;
		})();
		await started;
	});

	pi.on("before_agent_start", async () => {
		if (injected || !started) return undefined;
		injected = true;
		const text = await started;
		if (!text) return undefined;
		return { message: { customType: INJECT_TYPE, content: text, display: true } };
	});

	pi.on("tool_call", async (event: any, ctx: any) => {
		const name = event?.toolName;
		const input = event?.input ?? {};
		let toolName: string;
		let toolInput: Record<string, unknown>;
		if (name === "bash") {
			toolName = "Bash";
			toolInput = { command: input.command };
			if (input.description !== undefined) toolInput.description = input.description;
			if (input.timeout !== undefined) toolInput.timeout = input.timeout;
		} else if (name === "edit" || name === "write") {
			// 書き先の照合 (対治設計書 B17) のため。
			toolName = name === "edit" ? "Edit" : "Write";
			toolInput = { file_path: input.path };
		} else {
			return undefined;
		}
		const r = await call("pre_tool_use", { ...base(ctx), hook_event_name: "PreToolUse", tool_name: toolName, tool_input: toolInput, tool_use_id: event.toolCallId ?? null });
		const hso = r.out?.hookSpecificOutput;
		if (hso && (hso.permissionDecision === "deny" || hso.permissionDecision === "ask")) {
			return { block: true, reason: String(hso.permissionDecisionReason ?? "raiya-plugin: 拒否") };
		}
		return undefined;
	});

	// 順序照合 (対治設計書 B15) の記録。順序が要なので hook の終わりを待つ。
	// bash のあとで hook が additionalContext (規則のリポに書いたもの。B19) を返せば、結果の本文の末尾に足して子に見せる。
	pi.on("tool_result", async (event: any, ctx: any) => {
		const p = postToolPayload(event);
		if (!p) return undefined;
		const hookEventName = p.hook === "post_tool_use" ? "PostToolUse" : "PostToolUseFailure";
		const r = await call(p.hook, { ...base(ctx), hook_event_name: hookEventName, ...p.fields });
		const note = r.out?.hookSpecificOutput?.additionalContext;
		if (typeof note !== "string" || !note) return undefined;
		const changed: Record<string, unknown> = { content: [...(Array.isArray(event.content) ? event.content : []), { type: "text", text: note }] };
		// pi は content だけを替えると structuredContent を落とす。終了コードを残すために一緒に返す。
		if (event.structuredContent !== undefined) changed.structuredContent = event.structuredContent;
		return changed;
	});

	// 検証用の記録だけ (hook は呼ばない): run の終わり方を RAIYA_PLUGIN_PI_LOG に残す。
	pi.on("agent_settled", (event: any) => {
		logCall({ lifecycle: "agent_settled", role, agent_id: agentId, aborted: event?.aborted ?? null });
	});

	pi.on("agent_before_settle", async (event: any, ctx: any) => {
		if (event?.outcome !== "completed") {
			logCall({ lifecycle: "agent_before_settle", role, agent_id: agentId, outcome: event?.outcome ?? null, skipped: true });
			return undefined;
		}
		const message = lastAssistantText(event?.context?.contextMessages);
		const r = await call("subagent_stop", {
			...base(ctx),
			hook_event_name: "SubagentStop",
			stop_hook_active: continued,
			last_assistant_message: message,
		});
		const out = r.out;
		// event.context.canContinue は足す前の文脈 (最後が assistant なので false になる) の見積もり。
		// 差し戻しの custom message を足した後の文脈で pi が続けられるかを決めるので、ここでは見ない。
		if (out && out.decision === "block") {
			continued = true;
			const reason = String(out.reason ?? "");
			logCall({ lifecycle: "send_back", role, agent_id: agentId, reason });
			return {
				entries: [...(event.entries ?? []), { type: "custom_message", customType: SENDBACK_TYPE, content: reason, display: true }],
				continue: true,
			};
		}
		continued = false;
		return undefined;
	});
}
