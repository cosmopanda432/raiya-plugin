"""報告の 3 欄 (単体設計書 §10.1 B3) の形式検査。純関数。

検査するのは欄の存在と、欄が「無し」かどうかだけ。中身の真偽は見ない (§10.2)。
"""
import re

# (正規の欄名, 欄名の後ろに付いてよい言い足し)。それ以外の語が続く行は地の文として扱う。
FIELDS = (
    ("範囲外", r"(?:\s*[/／]\s*できなかったこと)?"),
    ("省略", r"(?:したもの)?"),
    ("残した仮のもの", r""),
)

# 行頭の飾り: 見出し・引用・箇条書き (-, *, +, ・, 1. 1))・太字・表の縦線。
_DECORATION = r"(?:[\s#>*\-_`+・|]|\d+[.)])*"
_NONE_RE = re.compile(r"(?:特に)?(?:無し|なし|無い|ない|none|n/a)", re.IGNORECASE)
_TRIM = " \t\r\n*_`-。.、,:：()（）「」|"


def _field_re(name, suffix):
    return re.compile(r"^" + _DECORATION + re.escape(name) + suffix + r"[*_`\s]*[:：](.*)$")


_FIELD_RES = tuple((name, _field_re(name, suffix)) for name, suffix in FIELDS)


def parse_fields(text):
    """報告文から 3 欄を取り出す。{欄名: 中身} を返す。無い欄はキーごと無い。

    中身 = 欄の行の「:」より後と、それに続く行。空行・見出し・``` の行・次の欄で終わる。
    同じ欄が複数あれば最後のものを採る。
    """
    found = {}
    current = None
    for line in (text or "").splitlines():
        for name, rx in _FIELD_RES:
            m = rx.match(line)
            if m:
                current = name
                found[name] = [m.group(1).lstrip("*_` ")]  # 「**欄名:** 中身」の閉じ記号
                break
        else:
            if current is None:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("```"):
                current = None
            else:
                found[current].append(line)
    return {name: "\n".join(parts).strip() for name, parts in found.items()}


def missing_fields(text):
    """無い欄の正規名を、FIELDS の順で返す。"""
    found = parse_fields(text)
    return [name for name, _ in FIELDS if name not in found]


def is_none(content):
    """欄の中身が「無し」(または空) か。中身の最初の行で決める (続く行は締めの文でありうる)。"""
    first = content.strip().split("\n", 1)[0]
    core = first.strip(_TRIM)
    return core == "" or _NONE_RE.fullmatch(core) is not None
