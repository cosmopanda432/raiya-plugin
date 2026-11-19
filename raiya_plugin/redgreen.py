"""自分が書いたテストについて、失敗する実行を観てから通る実行を観たか (単体設計書 4-14・4-15)。純関数。

見るのは順序だけ。失敗が正しい理由で起きたかは見ない (単体 §10.2)。
何をテストの実行・テストのファイルと数えるかは原典に無く工学で決めたもの (対治設計書 B15)。
分からないものは数えない側へ倒す (不備と言わない)。
"""
import re

PLAIN = "plain"    # 終了コードがテストの成否を表す実行
MASKED = "masked"  # テストは走ったが、終了コードが別のもの (パイプ・|| ・; ・& ・改行) で決まる実行
LOOSE = "loose"    # テストに触れているが、テストの実行と決められないコマンド (失敗したときだけ成否不明に数える)
PASS = "pass"
FAIL = "fail"
FAILED_UNKNOWN = "failed_unknown"  # テストに触れたコマンドが失敗したが、テストの失敗と決められない

OPEN = "open"      # 書いたあと、まだ実行を観ていない
OK = "ok"          # 失敗する実行 (か成否不明の実行) を観た
UNSEEN = "unseen"  # 失敗する実行を観ないまま、通る実行を観た

_REDIRECT_RE = re.compile(r"\d*>>?&?\d*|&>>?")
_MASK_RE = re.compile(r"[|;&\n]")
_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*$")
_PYTHON_RE = re.compile(r"(?:.*/)?python[0-9.]*$")
_PYTEST_RE = re.compile(r"(?:.*/)?(?:pytest|py\.test)$")
_NOT_A_RUN = frozenset({"--version", "--collect-only", "--co", "--help", "-h"})
_LOOSE_RE = re.compile(r"test", re.IGNORECASE)
_TEST_FILE_RE = re.compile(r"(?:^|[/\\])(?:test_[^/\\]*|[^/\\]*_test)\.py$")
_EXIT_RE = re.compile(r"Exit code (\d+)\s*(?:\n|$)")
# テストを流す道具が見つからずに終わった形 (x61 S9)。テストは走っていないので、落ちたテストに数えない。
_NO_RUNNER_CODES = frozenset({126, 127})  # shell: 実行できない・コマンドが無い
_NO_RUNNER_RE = re.compile(
    r"No module named ['\"]?(?:pytest|unittest|tox|nox|coverage)\b|: command not found", re.IGNORECASE)


def _skip_prefix(words):
    """前置き (環境変数の代入・env・nohup・uv run・poetry run・timeout [-opt] N) を、順序を問わず読み飛ばす。"""
    i = 0
    while i < len(words):
        if _ENV_RE.match(words[i]) or words[i] in ("env", "nohup"):
            i += 1
        elif words[i:i + 2] in (["uv", "run"], ["poetry", "run"]):
            i += 2
        elif words[i] == "timeout":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-s", "-k") else 1
            i += 1  # 時間
        else:
            break
    return words[i:]


def _is_runner(words):
    """語の列が、前置きのあとテストランナーで始まるコマンドか。"""
    rest = _skip_prefix(words)
    if not rest or _NOT_A_RUN.intersection(rest):
        return False
    if _PYTEST_RE.match(rest[0]):
        return True
    if _PYTHON_RE.match(rest[0]):
        args = rest[1:]
    elif rest[:2] == ["coverage", "run"]:
        args = rest[2:]
    else:
        return False
    while args and args[0].startswith("-") and args[0] != "-m":
        args = args[1:]
    return args[:2] in (["-m", "pytest"], ["-m", "unittest"])


# commit の門 (対治設計書 B21) だけが数える、Python の外のランナー。順序照合 (B15) には入れない。
_WIDE_ALONE = frozenset({"jest", "vitest", "mocha", "tox", "nox", "ctest", "rspec", "phpunit"})
_WIDE_SUB = {
    "npm": {"test", "t"}, "pnpm": {"test", "t"}, "yarn": {"test"}, "bun": {"test"}, "deno": {"test"},
    "go": {"test"}, "cargo": {"test"}, "make": {"test", "check"}, "gradle": {"test"}, "gradlew": {"test"},
    "mvn": {"test"}, "dotnet": {"test"}, "rake": {"test"},
}
_WIDE_SCRIPT = frozenset({"npm", "pnpm", "yarn", "bun"})  # `<名> run test`・`<名> run test:<何か>` も数える
_WIDE_WRAP = frozenset({"npx", "pnpx", "bunx"})


def _is_wide_runner(words):
    """語の列が、前置きのあと、門が数えるランナー (Python のものを含む) で始まるコマンドか。"""
    if _is_runner(words):
        return True
    rest = _skip_prefix(words)
    if not rest or _NOT_A_RUN.intersection(rest):
        return False
    name = rest[0].rsplit("/", 1)[-1]
    args = [w for w in rest[1:] if not w.startswith("-")]
    if name in _WIDE_ALONE:
        return True
    if name in _WIDE_WRAP:
        return _is_wide_runner(args)
    if name == "bundle" and args[:1] == ["exec"]:
        return _is_wide_runner(args[1:])
    if name == "node":
        return "--test" in rest
    if not args or name not in _WIDE_SUB:
        return False
    if args[0] in _WIDE_SUB[name]:
        return True
    return (name in _WIDE_SCRIPT and args[0] in ("run", "run-script") and len(args) > 1
            and (args[1] == "test" or args[1].startswith("test:")))


def classify_command(command, wide=False):
    """Bash のコマンドを PLAIN・MASKED・LOOSE に分ける。テストに触れていなければ None。

    wide: commit の門の数え方 (B21)。Python の外のランナー (npm test・go test・make test など) も実行に数える。
    """
    if not isinstance(command, str) or not (wide or _LOOSE_RE.search(command)):
        return None
    plain = _REDIRECT_RE.sub(" ", command).replace("&&", "\0")
    masked = bool(_MASK_RE.search(plain))
    segments = re.split(r"[\0|;&\n]", plain)
    is_runner = _is_wide_runner if wide else _is_runner
    if not any(is_runner(seg.split()) for seg in segments):
        return LOOSE if _LOOSE_RE.search(command) else None
    return MASKED if masked else PLAIN


def is_test_file(path):
    return isinstance(path, str) and bool(_TEST_FILE_RE.search(path))


def is_failed_run(error, is_interrupt):
    """PostToolUseFailure の error が「走って非 0 で終わった」形か。中断・時間切れは失敗に数えない。

    道具が見つからずに終わった形 (Exit code 126・127、ランナーの `No module named`、`command not found`) も
    失敗に数えない (成否不明。対治設計書 B15・単体設計書 B21)。
    """
    if is_interrupt or not isinstance(error, str):
        return False
    m = _EXIT_RE.match(error)
    if not m or int(m.group(1)) == 0:
        return False
    return int(m.group(1)) not in _NO_RUNNER_CODES and not _NO_RUNNER_RE.search(error)


def _tests(state):
    tests = state.get("tests") if isinstance(state, dict) else None
    if not isinstance(tests, dict):
        return {}
    return {p: v for p, v in tests.items() if v in (OPEN, OK, UNSEEN)}


def record_write(state, path):
    """テストのファイルを書いたことを残す。同じファイルは最初の 1 回だけ。"""
    tests = _tests(state)
    tests.setdefault(path, OPEN)
    return {"tests": tests}


def record_run(state, outcome):
    """実行の成否を、書いてあるテストのファイルに当てる。

    PASS: まだ実行を観ていないもの → UNSEEN。
    FAIL・FAILED_UNKNOWN: どれも OK (UNSEEN も、後から落ちるのを観れば解ける)。
    MASKED (成否不明。失敗を観たとは言えない): まだ実行を観ていないものだけ OK。UNSEEN は解かない。
    件数に依らないので、実行を重ねても判定は変わらない。
    """
    tests = _tests(state)
    for path, status in tests.items():
        if outcome == PASS:
            if status == OPEN:
                tests[path] = UNSEEN
        elif outcome in (FAIL, FAILED_UNKNOWN) or (outcome == MASKED and status == OPEN):
            tests[path] = OK
    return {"tests": tests}


# commit を作る・枝に付ける git の下位コマンド (対治設計書 B21)。x60 で commit-tree と update-ref が門を抜けた。
_COMMIT_SUBCOMMANDS = frozenset({
    "commit", "commit-tree", "update-ref", "cherry-pick", "revert", "am", "merge", "rebase", "fast-import",
})


def is_git_commit(command):
    """Bash のコマンドに、commit を作る git の呼び出しがあるか (対治設計書 B21)。

    `&&`・`;`・`|`・改行で区切った各部の頭を見る。`$(`・バッククォート・`(` でも区切って、もう一度見る
    (値に `$(date)` を持つ環境変数の代入を前置きとして読むために、区切らない形も見る)。
    """
    if not isinstance(command, str):
        return False
    plain = _REDIRECT_RE.sub(" ", command).replace("&&", "\0")
    segments = re.split(r"[\0|;&\n]", plain) + re.split(r"[\0|;&\n]", re.sub(r"\$\(|`|\(", "\0", plain))
    for seg in segments:
        words = _skip_prefix(seg.split())
        if not words or words[0] != "git":
            continue
        i = 1
        while i < len(words) and words[i].startswith("-"):  # git 自身のオプション (-C <path>・-c <k=v> など)
            i += 2 if words[i] in ("-C", "-c") else 1
        if words[i:i + 1] and words[i] in _COMMIT_SUBCOMMANDS:
            return True
    return False


FAILED_MAX = 20  # 覚えておく、落ちたままのコマンドの数の上限


def normalize_run(command):
    """テストの実行を見分ける鍵。リダイレクトと空白の違いを除いたコマンドの字。"""
    if not isinstance(command, str):
        return ""
    return " ".join(_REDIRECT_RE.sub(" ", command).split())


def record_failed(failed, command, outcome):
    """落ちたあと、同じコマンドが通るのをまだ観ていないテストの実行の列 (対治設計書 B21)。

    FAIL: そのコマンドを足す。PASS: 同じコマンドだけを外す (別のテストが通っても、落ちたものが通った証にならない)。
    成否不明の実行 (MASKED・FAILED_UNKNOWN) とテストの実行でない呼び出し (None) では変えない
    (x59: パイプつきで流し直すと門が開いた)。
    """
    runs = [c for c in failed if isinstance(c, str)] if isinstance(failed, list) else []
    key = normalize_run(command)
    if outcome == FAIL and key and key not in runs:
        runs.append(key)
    elif outcome == PASS:
        runs = [c for c in runs if c != key]
    return runs[-FAILED_MAX:]


def unseen_red(state):
    """失敗する実行を観ないまま、通る実行を観たテストのファイル。"""
    return sorted(p for p, v in _tests(state).items() if v == UNSEEN)
