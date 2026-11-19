// pi-subagents の agent 定義 (agents/<役>.md) の extensions がこのファイルを指すと、その子が役「qa-reviewer」になる。
import { roleExtension } from "../role.ts";

export default roleExtension("qa-reviewer");
