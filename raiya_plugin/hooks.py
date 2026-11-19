"""Claude Code の hook 入口 (単体設計書 §10.2 の形式検査と、規則の届け方)。

    python3 -m raiya_plugin.hooks subagent_start   # SubagentStart: 規則の所在と報告の 3 欄を注入する
    python3 -m raiya_plugin.hooks pre_tool_use     # PreToolUse(Bash|Edit|Write|SubagentHandback)。shipper の、テストが落ちたままの git commit も止める
    python3 -m raiya_plugin.hooks subagent_stop    # SubagentStop
    python3 -m raiya_plugin.hooks post_tool_use          # PostToolUse(Bash|Edit|Write): 順序を記す。Bash のあとは規則のリポに書いたものを返す
    python3 -m raiya_plugin.hooks post_tool_use_failure  # PostToolUseFailure(Bash): 同上

stdin の JSON だけで決める。LLM は呼ばない。
対象は役 (ROLES) の中で発火した呼び出しだけ = stdin に agent_type (役の名) と agent_id の両方があるもの。
それ以外 (主セッション・役でない agent・項目の無い呼び出し) には何も出さない。例外は無い。
(役を起動したツール Agent の PostToolUse で、起動した側に 1 文を足す処理は 2026-11-18 に消した。単体 B21 の追補・B30。
役は必ず裏で起動され、文は報告に付かない。リーダーへの禁止はスキル leader の失敗分岐に置く。)
状態はこのパッケージの隣の .state/ に置く (RAIYA_PLUGIN_STATE_DIR はテストが置き場を移すためだけに読む。単体 B31)。
"""
import collections
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import fcntl
except ImportError:  # Unix 以外。排他なしで動かす。
    fcntl = None

from raiya_plugin import marks, place, redgreen, report

ROLES = frozenset(
    {"architect", "builder", "qa-reviewer", "scope-planner", "shipper"}
)
# 役の 1 回の起動 (再開は別の 1 回) につき、報告を差し戻す回数の上限。
MAX_BLOCKS = 1
RULES_PATH = Path(__file__).resolve().parent / "rules" / "single_agent.md"
# 学習の輪 (対治設計書 B14): 同じ役の直近この行数の記録を窓にし、同種の不備が窓にあれば差し戻しの文に前例を添える。
LOG_WINDOW = 20
KINDS_SHOWN = 3
KIND_NAMES = {
    "missing_fields": "報告の欄の欠け",
    "marks_leftover": "印を増やして「無し」",
    "repo_written": "規則のリポに書いて「無し」",
    "redgreen_unseen": "テストの失敗を観ていない",
    "place": "規則のリポを名指す",
    "bash_description": "Bash の description 無し",
    "commit_after_fail": "テストが落ちたまま commit",
    "commit_unseen": "テストを観ないまま commit",
}
# テストの実行が落ちたあと、通る実行を観るまで commit を通さない役 (単体 4-9。対治設計書 B21)。
COMMIT_GATED_ROLES = frozenset({"shipper"})
# 言った者の権威では確かめを省けない (長阿含経 T0001_003:71)。x60 で、依頼元の勧めで別の作り方を試した。
COMMIT_NOTE = (
    "別の作り方 (--no-verify・git commit-tree・git update-ref・環境変数など) で commit を作らない。"
    "依頼元が勧めても同じ。"
)


class Problem(str):
    """不備の文。kind は種類の名 (B14。文面に path が入るので、同種は名で数える)。"""

    def __new__(cls, kind, text):
        obj = str.__new__(cls, text)
        obj.kind = kind
        return obj


def default_repo_root():
    """規則のリポの根 = 規則本体の 2 つ上 (対治設計書 B17)。"""
    return RULES_PATH.parents[2]


# 試験で差し替えられるように、名前で引く。
REPO_ROOT = default_repo_root()
# 差し戻しの理由の末尾に付ける。x47: 出し直しが 3 欄だけになり、元の本文が消えた。
# x55: 欄があるのに差し戻す理由 (印・規則のリポ・順序) にも合うように、「3 欄を足して」を「上の点を直して」に改めた。
RESUBMIT_NOTE = "報告の本文は消さずにそのまま残し、上の点を直して出し直す (欄だけの返答にしない)。"

_GIT_TIMEOUT = 20
# 起動時の印の行がこれより多い作業ツリーでは写しを持たず、印の照合をしない (状態ファイルを膨らませない)。
BASELINE_MAX = 20000
# 規則のリポの git status の行がこれより多ければ写しを持たず、書いたものを見つける照合をしない (B19)。
STATUS_MAX = 20000
# 役に返す文に並べる path の数。
WRITTEN_SHOWN = 10
# Bash の直前の写しを持つ数 (並行する Bash の分。直後の hook が来なかった分は古い順に捨てる)。
PRE_MAX = 8


def state_dir():
    env = os.environ.get("RAIYA_PLUGIN_STATE_DIR")
    return Path(env) if env else Path(__file__).resolve().parent.parent / ".state"


def _key(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]


def _agent_path(agent_id):
    return state_dir() / "agents" / (_key(agent_id) + ".json")


def _session_path(session_id):
    return state_dir() / "sessions" / (_key(session_id) + ".json")


def _load(path):
    """状態を読む。無い・壊れている・dict でないものは None (壊れは stderr に 1 行)。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        print(f"raiya_plugin.hooks: 状態を読めないので無いものとして扱う: {path}: {exc}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def _save_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")  # 並行する hook どうしで tmp を踏まない
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _save(path, data):
    _save_text(path, json.dumps(data, ensure_ascii=False))


def _status_path(agent_id):
    return state_dir() / "place" / (_key(agent_id) + ".json")


def load_state(agent_id):
    return _load(_agent_path(agent_id))


def save_state(agent_id, state):
    _save(_agent_path(agent_id), state)


@contextlib.contextmanager
def _locked(agent_id):
    """役の状態の 読む → 書く を排他する (PostToolUse の hook は並行して走りうる)。"""
    if fcntl is None:
        yield
        return
    path = state_dir() / "locks" / (_key(agent_id) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _set(agent_id, **fields):
    """状態の一部の項目だけを書き換える。読んでから検査している間に並行の hook が足した項目を消さない。"""
    with _locked(agent_id):
        state = load_state(agent_id) or {"baseline": None}
        state.update(fields)
        save_state(agent_id, state)


def _log_path():
    return state_dir() / "log.jsonl"


def log_event(payload, event, decision, problems):
    """差し戻し・不備のまま通した・不備なく通した事実を .state/log.jsonl に 1 行足す (単体 7-10: 事実だけ)。"""
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": int(time.time()),
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "event": event,
        "decision": decision,
        "problems": [str(p) for p in problems],
        "kinds": [p.kind for p in problems if isinstance(p, Problem)],
    }
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:  # 記録できなくても、差し戻し・通過の判定は出す。
        print(f"raiya_plugin.hooks log_event: 記録を書けない: {exc}", file=sys.stderr)


# 報告の置き場の名前に使える agent_id (単体 B31)。path を組めるものだけ。
_REPORT_NAME = re.compile(r"[A-Za-z0-9_-]+")


def write_report(agent_id, text):
    """通した報告の全文を <state>/reports/<agent_id>.md に書く (単体 B31)。

    名前は agent_id そのまま。英数字・_・- 以外を含めば書かない。本文が空・空白だけでも書かない。書けなくても判定は変えず、stderr に 1 行。
    """
    if not isinstance(agent_id, str) or not _REPORT_NAME.fullmatch(agent_id):
        return
    if not text.strip():  # 空・空白だけの本文は置かない
        return
    try:
        _save_text(state_dir() / "reports" / f"{agent_id}.md", text)
    except OSError as exc:
        print(f"raiya_plugin.hooks write_report: 報告を書けない: {exc}", file=sys.stderr)


def _recent_rows(agent_type):
    """同じ役の直近 LOG_WINDOW 行 (単体 7-11: 役ごと。7-13: 窓から外れれば消える)。"""
    rows = []
    with _log_path().open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("agent_type") == agent_type:
                rows.append(row)
    return rows[-LOG_WINDOW:]


def precedent(payload, problems):
    """差し戻しの文に添える前例の 1 文 (単体 7-15・7-16。対治設計書 B14)。窓に同種が無ければ None。

    数える単位は起動 (agent_id)。同じ起動の中の呼び直しは数えず (単体 7-12: 一度きりは規則にしない)、
    今回の行を書く前に呼ぶ (窓は今回の行を除く)。ログが読めなければ None。
    """
    kinds = [p.kind for p in problems if isinstance(p, Problem)]
    agent_type = payload.get("agent_type")
    if not kinds or not isinstance(agent_type, str):
        return None
    try:
        rows = _recent_rows(agent_type)
    except (OSError, ValueError):
        return None
    launches = collections.defaultdict(set)  # 種類 → その不備を出した起動 (agent_id) の集合
    for row in rows:
        seen = row.get("kinds")
        launch = row.get("agent_id")
        if not isinstance(seen, list) or not isinstance(launch, str) or launch == payload.get("agent_id"):
            continue
        for k in seen:
            if isinstance(k, str):
                launches[k].add(launch)
    counts = {k: len(v) for k, v in launches.items()}
    hits = [(k, counts[k]) for k in dict.fromkeys(kinds) if counts.get(k)]
    if not hits:
        return None
    hits.sort(key=lambda kv: -kv[1])
    shown = "・".join(f"{KIND_NAMES.get(k, k)} ({n + 1} 回目)" for k, n in hits[:KINDS_SHOWN])
    return f"この役 ({agent_type}) は同じ不備を繰り返している: {shown}。"


def _with_precedent(payload, problems):
    """問題の文の列に、前例の 1 文があれば足して返す。"""
    note = precedent(payload, problems)
    return list(problems) + ([note] if note else [])


def collect_marks(cwd):
    """作業ツリー全体 (追跡中 + 未追跡。ignore されたものは除く) の印を (path, 行番号, 行) で返す。

    git で読めなければ None。commit の有無に依らない (役が途中で commit しても同じ結果)。
    """
    if not cwd:
        return None
    # 利用者の git 設定 (色・列番号) で出力の形が変わらないように固定する。
    args = ["git", "-c", "color.grep=never", "-c", "grep.column=false", "-c", "grep.lineNumber=true"]
    args += ["grep", "--no-color", "-n", "-z", "-I", "--untracked", "-F", "-i", "--full-name"]
    for word in marks.GREP_WORDS:
        args += ["-e", word]
    args += ["--", ":/"]
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):  # 1 = 当たり無し
        return None
    return marks.parse_grep(proc.stdout.decode("utf-8", errors="replace"))


def collect_repo_status(root):
    """規則のリポの git status (未追跡・ignore されたものも 1 本ずつ) を {path: [状態, [mtime_ns, size]]} で返す。

    読めなければ None (対治設計書 B19)。index を書き直さない (--no-optional-locks)。
    状態の置き場がリポの中にあれば除く (hook 自身が書く場所)。
    """
    root = os.path.realpath(str(root))
    if not os.path.exists(os.path.join(root, ".git")):
        return None  # 根が git のリポでない (外側のリポの status を読まない)
    args = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", root]
    args += ["status", "--porcelain", "-z", "--ignored", "-uall"]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=_GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    own = None
    if place.is_inside(state_dir(), root):
        own = os.path.relpath(os.path.realpath(str(state_dir())), root)
    snap = {}
    for path, code in place.parse_status(proc.stdout).items():
        if own is not None and (own == "." or path == own or path.startswith(own + "/")):
            continue
        try:
            st = os.lstat(os.path.join(root, path))
            stat = [st.st_mtime_ns, st.st_size]
        except OSError:
            stat = None
        snap[path] = [code, stat]
    return snap


def _snapshot_repo(agent_id, start_cwd):
    """役の起動時に、規則のリポの status を写す。リポの中で起動した役・読めないリポは写さない (照合しない)。"""
    snap = None
    if isinstance(start_cwd, str) and start_cwd and not place.is_inside(start_cwd, REPO_ROOT):
        snap = collect_repo_status(REPO_ROOT)
        if snap is not None and len(snap) > STATUS_MAX:
            snap = None
    # start = 起動時の写し (報告のときに比べる)。pre = Bash の直前の写し (tool_use_id ごと)。
    # wrote = この役の Bash の間に増えた・変わった path。再開は別の 1 回なので、どれも取り直す。
    _save(_status_path(agent_id), {"start": snap, "pre": {}, "wrote": []})


def _bash_key(payload):
    key = payload.get("tool_use_id")
    return key if isinstance(key, str) and key else ""


def _checked_place(agent_id):
    """場所を照合する役なら写しのファイルの中身、そうでなければ None (起動を見ていない・リポの中で起動した・読めない)。"""
    saved = _load(_status_path(agent_id))
    return saved if saved and isinstance(saved.get("start"), dict) else None


def _before_bash(payload, agent_id):
    """役の Bash の直前に、規則のリポの status を写す (対治設計書 B19)。

    直後の写しと比べるのはこれ。その Bash が走っていた間の変化だけを、この役のものとする
    (Claude Code の実機で、同時に動く別の役が書いたものを取り違えたため)。
    """
    if place.read_only(_tool_input(payload).get("command")):
        return
    with _locked(agent_id):
        saved = _checked_place(agent_id)
        if saved is None:
            return
        snap = collect_repo_status(REPO_ROOT)
        if snap is None:
            return
        pre = saved.get("pre") if isinstance(saved.get("pre"), dict) else {}
        pre[_bash_key(payload)] = snap
        while len(pre) > PRE_MAX:  # 直後の hook が来なかった Bash の写しを溜めない
            pre.pop(next(iter(pre)))
        saved["pre"] = pre
        _save(_status_path(agent_id), saved)


def _written(payload, event_name):
    """役の Bash の間に、規則のリポに増えた・変わった path を役に返す (単体 5-6・§6.1。対治設計書 B19)。"""
    agent_id = _role(payload)
    if agent_id is None or payload.get("tool_name") != "Bash":
        return None
    if place.read_only(_tool_input(payload).get("command")):
        return None  # 読むだけのコマンドは前後とも写さない
    with _locked(agent_id):
        saved = _checked_place(agent_id)
        pre = saved.get("pre") if saved else None
        before = pre.pop(_bash_key(payload), None) if isinstance(pre, dict) else None
        if not isinstance(before, dict):  # 直前の写しが無い Bash は照合しない
            return None
        after = collect_repo_status(REPO_ROOT)
        paths = place.changed(before, after) if after is not None else []
        wrote = saved.get("wrote") if isinstance(saved.get("wrote"), list) else []
        saved["wrote"] = sorted(set(wrote) | set(paths))[:STATUS_MAX]
        _save(_status_path(agent_id), saved)
    if not paths:
        return None
    log_event(payload, "place", "wrote", paths)
    more = f" (他 {len(paths) - WRITTEN_SHOWN} 件)" if len(paths) > WRITTEN_SHOWN else ""
    text = (
        # hook が観たのは「増えた・変わった」まで。誰が書いたかは言わない。
        # x53 L8: 「自分が書いたものでなければ、そう書く」の 1 文が、自分の書き込みを否定する余地になったので外した。
        f"この役の Bash のあとで、規則のリポ ({REPO_ROOT}) に増えた・変わった path がある: "
        + ", ".join(paths[:WRITTEN_SHOWN]) + more
        + "。このリポは読むだけの場所。消さずに、この path を報告の「残した仮のもの」に書く。"
    )
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}


def _remaining_written(agent_id):
    """この役の Bash の間に増えた・変わった path のうち、起動時の写しと今とで違ったままのもの。

    照合しない役・読めないときは空。他の役や利用者が別の時間に書いたものは入らない。
    """
    saved = _checked_place(agent_id) if agent_id else None
    wrote = saved.get("wrote") if saved else None
    if not isinstance(wrote, list) or not wrote:
        return []
    now = collect_repo_status(REPO_ROOT)
    if now is None:
        return []
    start = saved["start"]
    return [p for p in wrote if isinstance(p, str) and start.get(p) != now.get(p)]


def injection_text():
    names = " / ".join(name + ":" for name, _ in report.FIELDS)
    return (
        "[raiya-plugin 単体の所作] 作業の前に " + str(RULES_PATH) + " を読み、従うこと。\n"
        "このファイルは読むだけ。作業はいまの作業ディレクトリで行い、"
        "規則ファイルのあるディレクトリでコマンドを打たない。\n"
        "最終報告の末尾に次の 3 欄を、この欄名のまま 1 行ずつ書くこと (無ければ「無し」): "
        + names
        + "\n残した仮のもの = 自分が置いた stub・仮置き・一時ファイル・TODO。消したものは書かない。\n"
        "Bash には description (その呼び出しの意図) を付けること。"
    )


def check_report(text, state, cwd, agent_id=None):
    """報告の形式の不備を文の列で返す。空なら通す。"""
    problems = []
    missing = report.missing_fields(text)
    if missing:
        problems.append(Problem(
            "missing_fields",
            "報告に欄が無い: " + "・".join(missing) + "。欄名のまま書く (無ければ「無し」)。",
        ))
    leftover = report.parse_fields(text).get("残した仮のもの")
    # 起動時の印の写し (baseline) が無ければ、どれが起動後に足された印か分からないので見ない。
    baseline = state.get("baseline") if state else None
    if leftover is not None and report.is_none(leftover) and isinstance(baseline, list):
        current = collect_marks(cwd)
        if current is not None:
            fresh = marks.new_marks(baseline, current)
            if fresh:
                shown = "; ".join(f"{p}:{n} {t[:60]}" for p, n, t in fresh[:5])
                more = f" (他 {len(fresh) - 5} 件)" if len(fresh) > 5 else ""
                problems.append(Problem(
                    "marks_leftover",
                    "「残した仮のもの: 無し」だが、起動後に印の行が増えている: "
                    + shown
                    + more
                    + "。消すか、欄に書く。",
                ))
    # 単体 5-6・8-4: 規則のリポに増えた・変わったものが残っているのに「無し」(対治 B19。x54 L10)。
    if leftover is not None and report.is_none(leftover):
        remaining = _remaining_written(agent_id)
        if remaining:
            more = f" (他 {len(remaining) - WRITTEN_SHOWN} 件)" if len(remaining) > WRITTEN_SHOWN else ""
            problems.append(Problem(
                "repo_written",
                f"「残した仮のもの: 無し」だが、起動後に規則のリポ ({REPO_ROOT}) に増えた・変わった path が残っている: "
                + ", ".join(remaining[:WRITTEN_SHOWN])
                + more
                + "。消さずに、この path を「残した仮のもの」に書く。",
            ))
    # 単体 4-14・4-15: 自分が書いたテストに、失敗する実行を観ないまま通る実行がある (順序だけ。対治 B15)。
    unseen = redgreen.unseen_red(state.get("redgreen")) if state else []
    if unseen:
        more = f" (他 {len(unseen) - 3} 件)" if len(unseen) > 3 else ""
        problems.append(Problem(
            "redgreen_unseen",
            "書いたテストに、失敗する実行を観ないまま通る実行がある: "
            + "; ".join(unseen[:3])
            + more
            + "。実装の無い状態で落ちるのを観るか、観ていないことを「省略」に書く。",
        ))
    return problems


def _role(payload):
    """役の中で発火した呼び出しなら agent_id を返す。そうでなければ None。"""
    agent_id = payload.get("agent_id")
    if payload.get("agent_type") in ROLES and isinstance(agent_id, str) and agent_id:
        return agent_id
    return None


def _blocks(state):
    n = state.get("blocks", 0)
    return n if isinstance(n, int) else 0


def subagent_start(payload):
    agent_id = _role(payload)
    if agent_id is None:
        return None
    try:
        state = load_state(agent_id)
        if state is None or "baseline" not in state:
            # 印の写しは最初の起動だけ。再開で写し直すと、自分の足した印が baseline に入る。
            current = collect_marks(payload.get("cwd"))
            keep = current is not None and len(current) <= BASELINE_MAX
            state = {"baseline": [t for _, _, t in current] if keep else None}
            # 起動時の作業ディレクトリ。場所の照合 (B17) はこれで決める。再開で書き換えない。
            state["start_cwd"] = payload.get("cwd")
        state["handback"] = False
        state["blocks"] = 0
        state.pop("redgreen", None)  # 再開は別の 1 回 (B9)。前の起動の順序を持ち越さない。
        state.pop("failed_tests", None)  # 同じく、前の起動で落ちた実行を持ち越さない (B21)。
        state.pop("test_passed", None)
        save_state(agent_id, state)
        # 再開のたびに写し直す。止まっている間に他が書いたものを、この役のせいにしない (B19)。
        _snapshot_repo(agent_id, state.get("start_cwd"))
    except OSError as exc:  # 状態を書けなくても、規則の注入は出す。
        print(f"raiya_plugin.hooks subagent_start: 状態を書けない: {exc}", file=sys.stderr)
    return {
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": injection_text(),
        }
    }


def _deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _place_problem(payload, tool, tool_input):
    """規則のリポを名指していれば理由の文を返す (単体 5-6・5-7・§6.1。対治設計書 B17)。"""
    root = REPO_ROOT
    cwd = payload.get("cwd")
    state = load_state(payload.get("agent_id")) or {}
    start = state.get("start_cwd") if isinstance(state.get("start_cwd"), str) else cwd
    if isinstance(start, str) and start and place.is_inside(start, root):
        return None  # raiya-plugin 自身を作業する役
    if tool == "Bash":
        command = tool_input.get("command")
        if place.read_only(command):
            return None  # 読むだけのコマンドは通す (B17。x51)
        hit = place.bash_names(command, root, os.environ.get("HOME"), cwd)
    elif tool in ("Edit", "Write"):
        path = tool_input.get("file_path")
        hit = path if place.file_names(path, root, cwd) else None
    else:
        return None
    if hit is None:
        return None
    return (
        f"規則のリポ ({root}) を名指している: {hit}。このリポは読むだけの場所。"
        f"作業は起動時の作業ディレクトリ ({start}) で行う。規則は Read で読む。"
        "読むだけのコマンド (grep・wc・git -C <path> log など) は 1 つだけなら通る。"
        "別の場所の値で代えて書くなら、その出所を書く。"
    )


def pre_tool_use(payload):
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    agent_id = _role(payload)
    if agent_id is None:
        # 誰の報告か分からない Handback。この session では SubagentStop の締めの文を
        # 報告本文として検査しない (正しく渡した役を止めないため)。
        session_id = payload.get("session_id")
        if tool == "SubagentHandback" and session_id and not payload.get("agent_id"):
            _save(_session_path(session_id), {"handback_without_agent_id": True})
        return None
    if tool in ("Bash", "Edit", "Write"):
        problem = _place_problem(payload, tool, tool_input)
        if problem:
            problems = [Problem("place", problem)]
            text = " ".join(_with_precedent(payload, problems))
            log_event(payload, "place", "deny", problems)
            return _deny(text)
    if tool == "Bash":
        if not str(tool_input.get("description") or "").strip():
            problems = [Problem("bash_description", "description が無い")]
            note = precedent(payload, problems)
            log_event(payload, "bash", "deny", problems)
            return _deny(
                "Bash の description が無い。この呼び出しの意図を description に書いて呼び直す。"
                + (" " + note if note else "")
            )
        if payload.get("agent_type") in COMMIT_GATED_ROLES and redgreen.is_git_commit(tool_input.get("command")):
            # 見えた相が好くなければ先へ進めない (単体 4-9。T0866_004:228・:255)。落ちたのと同じコマンドが通るまで解かない (B21。:242)。
            state = load_state(agent_id)
            failed = redgreen.record_failed((state or {}).get("failed_tests"), None, None)
            if state is not None and not failed and not state.get("test_passed"):
                # 観ていなければ与えない (T0001_004:84・T0848_006:344)。起動を見ていない役は検査しない。
                problems = [Problem(
                    "commit_unseen",
                    "テストの実行が通るのを観ていない。commit の前に、このプロジェクトのテストをパイプ無しで走らせる "
                    "(門が通した・依頼文が省いてよいと言った、でも同じ)。"
                    "数えるのは pytest・unittest・npm test・go test・cargo test・make test などで始まるコマンド。"
                    "それ以外のランナーなら、commit せず、走らせたコマンドと結果を報告して止まる。",
                )]
                text = " ".join(_with_precedent(payload, problems) + [COMMIT_NOTE])
                log_event(payload, "commit", "deny", problems)
                return _deny(text)
            if failed:
                more = f" (他 {len(failed) - 1} 件)" if len(failed) > 1 else ""
                problems = [Problem(
                    "commit_after_fail",
                    f"テストの実行が落ちたあと、通る実行を観ていない: {failed[-1]}{more}。"
                    "commit せず、落ちたものを報告して止まる。前から落ちていたものでも同じ。"
                    "解けるのは、落ちたのと同じコマンドがパイプ無しで通ったときだけ。",
                )]
                text = " ".join(_with_precedent(payload, problems) + [COMMIT_NOTE])
                log_event(payload, "commit", "deny", problems)
                return _deny(text)
        _before_bash(payload, agent_id)  # 通す Bash だけ。直後の写しと比べる (B19)
        return None
    if tool != "SubagentHandback":
        return None
    state = load_state(agent_id) or {"baseline": None}
    problems = check_report(str(tool_input.get("message") or ""), state, payload.get("cwd"), agent_id)
    if problems and _blocks(state) < MAX_BLOCKS:
        _set(agent_id, blocks=_blocks(state) + 1)
        text = " ".join(_with_precedent(payload, problems) + [RESUBMIT_NOTE])
        log_event(payload, "handback", "deny", problems)
        return _deny(text)
    _set(agent_id, handback=True)  # 通した報告だけを「渡した」と数える
    write_report(agent_id, str(tool_input.get("message") or ""))  # 通した報告だけ置く (B31)
    # 通した事実も書く。不備の行だけだと、直った役の古い不備が窓に残り続ける (単体 7-13。B14)。
    log_event(payload, "handback", "passed_with_problems" if problems else "passed", problems)
    return None


def subagent_stop(payload):
    agent_id = _role(payload)
    if agent_id is None:
        return None
    state = load_state(agent_id)
    if state is None:  # 起動を見ていない役は、報告を渡したかどうかも上限も分からないので見ない。
        return None
    # 報告を SubagentHandback で渡した役の last_assistant_message は報告本文ではないので見ない。
    if state.get("handback"):
        return None
    session_id = payload.get("session_id")
    if session_id and _load(_session_path(session_id)):
        return None
    problems = check_report(
        str(payload.get("last_assistant_message") or ""), state, payload.get("cwd"), agent_id
    )
    if not problems:
        log_event(payload, "stop", "passed", [])  # 窓を動かす (単体 7-13。B14)
        return None
    if payload.get("stop_hook_active") or _blocks(state) >= MAX_BLOCKS:
        log_event(payload, "stop", "passed_with_problems", problems)
        return None
    _set(agent_id, blocks=_blocks(state) + 1)
    text = " ".join(_with_precedent(payload, problems) + [RESUBMIT_NOTE])
    log_event(payload, "stop", "block", problems)
    return {"decision": "block", "reason": text}


def _record(payload, update, ran=None):
    """役の状態の redgreen を update で書き換える。何も出さない (役を止めない)。

    ran はテストの実行の成否 (テストの実行でなければ None)。落ちたままのコマンドを failed_tests に残す (B21)。
    """
    agent_id = _role(payload)
    if agent_id is None:
        return None
    with _locked(agent_id):
        state = load_state(agent_id)
        if state is None:  # 起動を見ていない役は記さない (subagent_stop が検査しない役を作らない)。
            return None
        if update is not None:
            state["redgreen"] = update(state.get("redgreen"))
        if ran == redgreen.PASS:
            state["test_passed"] = True  # 通る実行を 1 つは観た (B21)
        failed = redgreen.record_failed(state.get("failed_tests"), _tool_input(payload).get("command"), ran)
        if failed:
            state["failed_tests"] = failed
        else:
            state.pop("failed_tests", None)
        save_state(agent_id, state)
    return None


def _tool_input(payload):
    tool_input = payload.get("tool_input")
    return tool_input if isinstance(tool_input, dict) else {}


def _record_success(payload):
    """成功した呼び出し。テストのファイルを書いたこと・テストの実行が通ったことを順番に記す。"""
    tool = payload.get("tool_name")
    tool_input = _tool_input(payload)
    if tool in ("Edit", "Write"):
        path = tool_input.get("file_path")
        if redgreen.is_test_file(path):
            return _record(payload, lambda s: redgreen.record_write(s, path))
        return None
    if tool != "Bash":
        return None
    kind = redgreen.classify_command(tool_input.get("command"))
    wide = redgreen.classify_command(tool_input.get("command"), wide=True)  # 門 (B21) は Python の外のランナーも数える
    response = payload.get("tool_response")
    # 中断された実行と、裏で走らせた実行 (結果が出る前に返る) は成否不明。
    unknown = (isinstance(response, dict) and response.get("interrupted")) or tool_input.get("run_in_background")
    ran = None
    if wide in (redgreen.PLAIN, redgreen.MASKED):
        ran = redgreen.PASS if wide == redgreen.PLAIN and not unknown else redgreen.MASKED
    if kind in (None, redgreen.LOOSE):  # 通ったコマンドがテストに触れただけでは、順序 (B15) の実行に数えない
        return _record(payload, None, ran=ran) if ran else None
    outcome = redgreen.PASS if kind == redgreen.PLAIN and not unknown else redgreen.MASKED
    return _record(payload, lambda s: redgreen.record_run(s, outcome), ran=ran)


def _record_failure(payload):
    """失敗した呼び出し。テストの実行が非 0 で終わったことを順番に記す。"""
    if payload.get("tool_name") != "Bash":
        return None
    kind = redgreen.classify_command(_tool_input(payload).get("command"))
    failed = redgreen.is_failed_run(payload.get("error"), payload.get("is_interrupt"))
    if kind == redgreen.PLAIN and failed:
        outcome = redgreen.FAIL
    elif kind is not None:
        # 時間切れ・中断・テストの実行と決められないコマンドの失敗。テストが落ちたのかもしれない。
        outcome = redgreen.FAILED_UNKNOWN
    else:
        # テストに触れない失敗 (tox・make check など、こちらの知らないランナーかもしれない)。
        # まだ実行を観ていないテストだけを「不備と言わない」側へ倒す。
        outcome = redgreen.MASKED
    # 落ちた実行 (B21) に数えるのは、テストの実行の失敗だけ。git stash pop の失敗などでは変えない。
    wide = redgreen.classify_command(_tool_input(payload).get("command"), wide=True)
    ran = None
    if wide in (redgreen.PLAIN, redgreen.MASKED):
        ran = redgreen.FAIL if wide == redgreen.PLAIN and failed else redgreen.FAILED_UNKNOWN
    return _record(payload, lambda s: redgreen.record_run(s, outcome), ran=ran)


def post_tool_use(payload):
    _record_success(payload)
    return _written(payload, "PostToolUse")


def post_tool_use_failure(payload):
    _record_failure(payload)
    return _written(payload, "PostToolUseFailure")


HANDLERS = {
    "subagent_start": subagent_start,
    "pre_tool_use": pre_tool_use,
    "subagent_stop": subagent_stop,
    "post_tool_use": post_tool_use,
    "post_tool_use_failure": post_tool_use_failure,
}


def main(argv=None, stdin=None, stdout=None):
    argv = sys.argv[1:] if argv is None else argv
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if len(argv) != 1 or argv[0] not in HANDLERS:
        print("usage: python3 -m raiya_plugin.hooks {" + "|".join(HANDLERS) + "}", file=sys.stderr)
        return 1
    try:
        payload = json.load(stdin)
        if not isinstance(payload, dict):
            raise ValueError("stdin is not a JSON object")
        out = HANDLERS[argv[0]](payload)
    except Exception as exc:  # hook の失敗で作業を止めない。exit 1 = 止めない誤りとして表示される。
        print(f"raiya_plugin.hooks {argv[0]}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if out is not None:
        json.dump(out, stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
