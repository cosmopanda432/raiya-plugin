"""規則のリポを名指す呼び出しを見分ける (単体 5-6・5-7・§6.1、G18)。純関数。

原典が言うのは「与えられていないものを取らない」「共に住止しない」まで。
何を規則の場所とし、どう照合するかは工学の既定 (対治設計書 B17)。
見えないもの: $HOME 以外の変数・コマンド置換・cd を重ねて辿る形。
これらは前もっては見えないので、Bash のあとで規則のリポの git status を取り直して見つける (B19。parse_status・changed)。
"""
import os
import re
import shlex

# 語の区切り。空白・引用符・= : ; | & ( ) < > ` 。
_SPLIT_RE = re.compile(r"[\s'\"=:;|&()<>`]+")
_HOME_RE = re.compile(r"^(?:~|\$HOME|\$\{HOME\})(?=/|$)")


def _real(path):
    return os.path.realpath(path)


def is_inside(path, root):
    """path (実体) が root (実体) そのものかその下か。"""
    p = _real(path)
    r = _real(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def bash_names(command, root, home, cwd=None):
    """Bash のコマンドが root を名指していれば、その語を返す。無ければ None。

    `/` で始まらない語は cwd から解く (cwd が無ければ見ない)。
    """
    if not isinstance(command, str):
        return None
    for word in _SPLIT_RE.split(command):
        if not word:
            continue
        if home:
            word = _HOME_RE.sub(lambda _: home, word)
        if word.startswith("/"):
            path = word
        elif cwd and not word.startswith("~"):
            path = os.path.join(cwd, word)
        else:
            continue
        if is_inside(path, root):
            return word
    return None


# 読むだけのコマンド (対治設計書 B17)。一覧は工学の既定。
_READ_ONLY = {"cat", "head", "tail", "wc", "grep", "ls", "stat"}
_FIND_WRITES = {"-exec", "-execdir", "-ok", "-okdir", "-delete",
                "-fprint", "-fprint0", "-fprintf", "-fls"}
_GIT_READ = {"log", "show", "rev-parse", "ls-files", "blame", "grep"}
_COMPOUND = (";", "&", "|", ">", "$(", "<(", "`", "\n")
# git の読むだけのサブコマンドでも、別のプログラムを起こすか書く語。
_GIT_RUNS = ("--output", "--ext-diff", "-O", "--open-files-in-pager")


def read_only(command):
    """1 つの単純なコマンドで、読むだけのものか。"""
    if not isinstance(command, str) or any(c in command for c in _COMPOUND):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False  # 引用符が閉じていない
    if not words:
        return False
    head, rest = words[0], words[1:]
    if head in _READ_ONLY:
        return True
    if head == "find":
        return not any(w in _FIND_WRITES for w in rest)
    if head != "git":
        return False
    i = 0
    while i < len(rest):
        if rest[i] == "-C" and i + 1 < len(rest):
            i += 2
        elif rest[i] == "--no-pager":
            i += 1
        else:
            break
    if i >= len(rest) or rest[i] not in _GIT_READ:
        return False
    return not any(w.startswith(_GIT_RUNS) for w in rest[i + 1:])


def file_names(path, root, cwd):
    """Edit・Write の書き先 (相対なら cwd から解く) が root の中か。"""
    if not isinstance(path, str) or not path:
        return False
    if not os.path.isabs(path):
        if not cwd:
            return False
        path = os.path.join(cwd, path)
    return is_inside(path, root)


def parse_status(raw):
    """`git status --porcelain -z` の出力 (bytes) を {path: 状態の 2 字} にする (対治設計書 B19)。"""
    out = {}
    parts = raw.split(b"\0") if isinstance(raw, bytes) else []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        code = entry[:2].decode("ascii", errors="replace")
        out[entry[3:].decode("utf-8", errors="replace")] = code
        if "R" in code or "C" in code:
            i += 1  # 名前を変えた・写したものは、元の path が次の 1 件に付く
    return out


def changed(before, after):
    """2 つの写し ({path: 値}) で、増えた・消えた・値の変わった path を並べて返す。"""
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
