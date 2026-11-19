"""仮のものの印 (単体設計書 8-4・§10.2)。純関数。

印の語は原典に無く工学で決めたもの (既定)。
"""
import re
from collections import Counter

# path は NUL まで (改行を含みうる)、行の本文は LF まで。
_RECORD_RE = re.compile(r"([^\0]*)\0(\d+)\0([^\n]*)(?:\n|$)")

# `git grep -F -i` に渡す粗い語。当たった行を has_mark で絞る。
GREP_WORDS = ("TODO", "FIXME", "XXX", "stub", "仮置き")

# 語の境は ASCII の英数字と _ だけで見る (Python の \b は日本語の字も語の一部に数える)。
_L = r"(?<![A-Za-z0-9_])"
_R = r"(?![A-Za-z0-9_])"
_UPPER_RE = re.compile(_L + r"(?:TODO|FIXME|XXX)" + _R)
_ANYCASE_RE = re.compile(_L + r"stubs?" + _R + r"|仮置き", re.IGNORECASE)


def has_mark(line):
    """行に印があるか。TODO・FIXME・XXX は大文字のみ、stub は大小を問わない。"""
    return bool(_UPPER_RE.search(line) or _ANYCASE_RE.search(line))


def parse_grep(output):
    """`git grep -n -z` の出力 (path NUL 行番号 NUL 行 LF) から、印のある行を (path, 行番号, 行) で返す。"""
    return [
        (m.group(1), int(m.group(2)), m.group(3).strip())
        for m in _RECORD_RE.finditer(output)
        if has_mark(m.group(3))
    ]


def new_marks(baseline, current):
    """起動後に増えた印を返す。

    baseline は起動時の印の行の本文の列、current は (path, 行番号, 行) の列。
    印の行の総数が増えていなければ空 (元からある印の行を直した・動かしただけでは数えない)。
    増えていれば、本文が baseline に無い行を、増えた数まで返す。
    """
    grown = len(current) - len(baseline)
    if grown <= 0:
        return []
    remaining = Counter(baseline)
    fresh = []
    for path, lineno, text in current:
        if remaining[text] > 0:
            remaining[text] -= 1
        else:
            fresh.append((path, lineno, text))
    return fresh[:grown]
