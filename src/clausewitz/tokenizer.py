"""
Clausewitz engine format tokenizer.

Uses a compiled regex for C-speed scanning, which is required for large
EU4 save files (~50 MB uncompressed, 1.7 M lines).

Token types:
  SCALAR   — unquoted value: foo  yes  -1  1444.11.1  @var
  QUOTED   — "quoted string" (escape sequences resolved)
  OPEN     — {
  CLOSE    — }
  OPERATOR — =  ==  !=  <  <=  >  >=  ?=
  EOF      — sentinel emitted once at end
"""
from __future__ import annotations

import re
from enum import Enum, auto
from typing import Iterator, NamedTuple


class TokenType(Enum):
    SCALAR = auto()
    QUOTED = auto()
    OPEN = auto()
    CLOSE = auto()
    OPERATOR = auto()
    EOF = auto()


class Token(NamedTuple):
    type: TokenType
    value: str
    line: int  # 1-based


# One master pattern.  Groups (named for clarity):
#   skip    — whitespace, semicolons, comments → discard
#   open    — {
#   close   — }
#   quoted  — "..." with possible \\ / \" escapes
#   op      — multi- or single-char operator
#   scalar  — anything else that isn't a boundary char
_MASTER = re.compile(
    r'(?P<skip>[ \t\r\n;]+|#[^\n]*)'
    r'|(?P<open>\{)'
    r'|(?P<close>\})'
    r'|(?P<quoted>"(?:[^"\\]|\\.)*")'
    r'|(?P<op>[!<>?]=|==|[=<>])'
    r'|(?P<scalar>[^\s{}=!<>?"#;]+)',
    re.DOTALL,
)

# Resolve backslash escapes inside quoted strings
_ESCAPE = re.compile(r'\\(.)')
_ESCAPE_MAP = {'"': '"', '\\': '\\'}


def _unescape(s: str) -> str:
    return _ESCAPE.sub(lambda m: _ESCAPE_MAP.get(m.group(1), m.group(1)), s)


def tokenize(text: str) -> Iterator[Token]:
    """Yield Tokens from a Clausewitz-format string."""
    if text.startswith("\ufeff"):
        text = text[1:]

    line = 1
    for m in _MASTER.finditer(text):
        kind = m.lastgroup

        if kind == "skip":
            line += m.group().count("\n")
            continue

        if kind == "open":
            yield Token(TokenType.OPEN, "{", line)

        elif kind == "close":
            yield Token(TokenType.CLOSE, "}", line)

        elif kind == "quoted":
            raw = m.group()[1:-1]          # strip outer quotes
            line += raw.count("\n")
            yield Token(TokenType.QUOTED, _unescape(raw), line)

        elif kind == "op":
            yield Token(TokenType.OPERATOR, m.group(), line)

        elif kind == "scalar":
            yield Token(TokenType.SCALAR, m.group(), line)

    yield Token(TokenType.EOF, "", line)
