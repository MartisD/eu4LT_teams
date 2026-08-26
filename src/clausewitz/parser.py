"""
Clausewitz engine format parser.

Builds a ClausewitzNode tree from tokenized input.  Duplicate keys are
accumulated into a list.  A node can hold both keyed entries (object) and
positional values (array) simultaneously to match the mixed-content blocks
found in EU4 game files and save files.
"""
from __future__ import annotations

from typing import Any, Iterator

from clausewitz.tokenizer import Token, TokenType, tokenize


class ClausewitzNode:
    """
    A {} block.  May contain key-value pairs, positional array values, or both.
    Duplicate keys are stored as a list under the same key.
    """

    __slots__ = ("_data", "_array")

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._array: list[Any] = []

    # ── write helpers (used only by parser) ──────────────────────────────────

    def _set(self, key: str, value: Any) -> None:
        if key in self._data:
            existing = self._data[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                self._data[key] = [existing, value]
        else:
            self._data[key] = value

    def _push(self, value: Any) -> None:
        self._array.append(value)

    # ── read API ─────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """First value for key, or default."""
        v = self._data.get(key, default)
        return v[0] if isinstance(v, list) else v

    def get_list(self, key: str) -> list:
        """All values for key as a list (never raises)."""
        v = self._data.get(key)
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

    def get_node(self, key: str) -> "ClausewitzNode | None":
        v = self.get(key)
        return v if isinstance(v, ClausewitzNode) else None

    def get_str(self, key: str, default: str = "") -> str:
        v = self.get(key, default)
        if isinstance(v, ClausewitzNode):
            return default
        return str(v) if v is not None else default

    def get_float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get_str(key))
        except (ValueError, TypeError):
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get_str(key))
        except (ValueError, TypeError):
            return default

    @property
    def array(self) -> list:
        """Positional (keyless) values — the 'array' part of a mixed node."""
        return self._array

    def keys(self):
        return self._data.keys()

    def items(self):
        """Yields (key, first_value) for every distinct key."""
        for k, v in self._data.items():
            yield k, (v[0] if isinstance(v, list) else v)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str) -> Any:
        v = self._data[key]
        return v[0] if isinstance(v, list) else v

    def __repr__(self) -> str:
        keys = list(self._data.keys())[:4]
        arr_len = len(self._array)
        return f"ClausewitzNode(keys={keys!r}, array_len={arr_len})"


# ── internal parser ───────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, tokens: Iterator[Token]) -> None:
        self._iter = tokens
        self._peeked: Token | None = None

    def _peek(self) -> Token:
        if self._peeked is None:
            self._peeked = next(self._iter)
        return self._peeked

    def _consume(self) -> Token:
        if self._peeked is not None:
            t = self._peeked
            self._peeked = None
            return t
        return next(self._iter)

    def parse_document(self) -> ClausewitzNode:
        node = ClausewitzNode()
        while True:
            t = self._peek()
            if t.type in (TokenType.EOF, TokenType.CLOSE):
                break
            self._parse_item_into(node)
        return node

    def _parse_item_into(self, node: ClausewitzNode) -> None:
        t = self._consume()

        if t.type == TokenType.OPEN:
            # Anonymous nested block — treat as array element
            inner = self._parse_block()
            node._push(inner)
            return

        if t.type == TokenType.CLOSE:
            return  # extra closing brace — tolerate per spec

        if t.type not in (TokenType.SCALAR, TokenType.QUOTED):
            return  # skip unexpected token types

        key = t.value
        nxt = self._peek()

        if nxt.type == TokenType.OPERATOR:
            self._consume()  # consume operator (we only need its existence)
            value = self._parse_value()
            node._set(key, value)
        else:
            # Bare value — positional array element
            node._push(key)

    def _parse_value(self) -> Any:
        t = self._peek()

        if t.type == TokenType.OPEN:
            self._consume()
            return self._parse_block()

        if t.type in (TokenType.SCALAR, TokenType.QUOTED):
            self._consume()
            # Externally-tagged type: `rgb { 100 200 150 }` or `LIST { … }`
            if t.type == TokenType.SCALAR and self._peek().type == TokenType.OPEN:
                self._consume()  # consume '{'
                inner = self._parse_block()
                inner._set("__type__", t.value)
                return inner
            return t.value

        # EOF or CLOSE right after operator — return empty string
        return ""

    def _parse_block(self) -> ClausewitzNode:
        node = ClausewitzNode()
        while True:
            t = self._peek()
            if t.type == TokenType.EOF:
                break  # unclosed brace — tolerate per spec
            if t.type == TokenType.CLOSE:
                self._consume()
                break
            self._parse_item_into(node)
        return node


# ── public API ────────────────────────────────────────────────────────────────


def parse(text: str) -> ClausewitzNode:
    """Parse Clausewitz-format text and return the root ClausewitzNode."""
    tokens = tokenize(text)
    parser = _Parser(iter(tokens))
    return parser.parse_document()
