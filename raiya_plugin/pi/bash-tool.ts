// 役の子の bash を、Claude Code の Bash と同じく description (その呼び出しの意図) を受けるものに差し替える。
// pi の組み込み bash の引数は command と timeout だけで、description を書く場所が無い。
// 実行は組み込みの bash そのまま (description は実行に使わない。hook が見るだけ)。
import { createBashToolDefinition } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import type { PiLike } from "./core.ts";

export function registerDescribedBash(pi: PiLike): void {
	const base = createBashToolDefinition(process.cwd());
	pi.registerTool?.({
		...base,
		parameters: Type.Object({
			command: Type.String({ description: "Shell command to execute" }),
			description: Type.Optional(Type.String({ description: "What this command is for, in one short sentence" })),
			timeout: Type.Optional(Type.Number({ description: "Timeout in seconds (optional, no default timeout)" })),
		}),
		execute: (id: string, params: any, signal: any, onUpdate: any, ctx: any) =>
			(base as any).execute(id, { command: params.command, timeout: params.timeout }, signal, onUpdate, ctx),
	});
}
