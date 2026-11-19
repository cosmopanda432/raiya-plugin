// 役の子セッション用の入口を作る。roles/<役>.ts がこれを呼ぶ。
import type { PiLike } from "./core.ts";
import { bindBridge } from "./core.ts";
import { registerDescribedBash } from "./bash-tool.ts";

export function roleExtension(role: string) {
	return function (pi: PiLike) {
		// raiya の子として起こされた pi (RAIYA_HOOK_ACTIVE=1) では何もしない。raiya-pi と同じ扱い。
		if (process.env.RAIYA_HOOK_ACTIVE === "1") return;
		bindBridge(pi, { role, bashTool: registerDescribedBash });
	};
}
