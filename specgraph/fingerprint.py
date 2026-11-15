from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class Fingerprint:
    parser: str
    symbols: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"parser": self.parser, "symbols": self.symbols}

    @classmethod
    def from_json(cls, payload: dict) -> "Fingerprint":
        return cls(parser=payload.get("parser", "unsupported"), symbols=dict(payload.get("symbols") or {}))


@dataclass(frozen=True)
class SymbolChange:
    symbol: str
    action: str


_CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "script",
    ".jsx": "script",
    ".mjs": "script",
    ".cjs": "script",
    ".ts": "script",
    ".tsx": "script",
}

_FUNC_RE = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(?:<[^;\n{}]*>)?\s*\("
)
_CLASS_RE = re.compile(
    r"(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"
)
_INTERFACE_RE = re.compile(r"(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)")
_TYPE_RE = re.compile(r"(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=")
_ARROW_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?(?:\([^;]*?\)|[A-Za-z_$][\w$]*)\s*=>"
)
_METHOD_RE = re.compile(
    r"(?:async\s+)?(?:get\s+|set\s+)?([A-Za-z_$][\w$]*)\s*(?:<[^;\n{}]*>)?\s*\("
)
_METHOD_SKIP = {"if", "for", "while", "switch", "catch", "function", "return", "do", "else"}


def language_of(path: str) -> str | None:
    dot = path.rfind(".")
    if dot < 0:
        return None
    return _CODE_EXTENSIONS.get(path[dot:].lower())


def fingerprint_source(path: str, source: str) -> Fingerprint:
    if "\0" in source or len(source) > 1_000_000:
        return Fingerprint("unsupported")
    kind = language_of(path)
    if kind == "python":
        return _python(source)
    if kind == "script":
        return Fingerprint("script", _script(source))
    return Fingerprint("unsupported")


def diff_fingerprints(old: Fingerprint | None, new: Fingerprint) -> list[SymbolChange]:
    if old is None:
        keys = list(new.symbols) or ["*"]
        return [SymbolChange(key, "added") for key in keys]
    if old.parser == "unsupported" and new.parser == "unsupported":
        return []
    if old.parser != new.parser or "error" in (old.parser, new.parser):
        if old.parser == new.parser and old.symbols == new.symbols:
            return []
        changes = [SymbolChange(key, "removed") for key in old.symbols if key != "*"]
        changes.extend(SymbolChange(key, "added") for key in new.symbols if key != "*")
        return changes or [SymbolChange("*", "changed")]
    changes: list[SymbolChange] = []
    for key in sorted(set(old.symbols) | set(new.symbols)):
        if key not in old.symbols:
            changes.append(SymbolChange(key, "added"))
        elif key not in new.symbols:
            changes.append(SymbolChange(key, "removed"))
        elif old.symbols[key] != new.symbols[key]:
            changes.append(SymbolChange(key, "changed"))
    return changes


def _python(source: str) -> Fingerprint:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Fingerprint("error", {"*": "syntax"})
    symbols: dict[str, str] = {}
    for node in tree.body:
        _python_statement(node, symbols)
    return Fingerprint("python", symbols)


def _python_statement(node: ast.AST, symbols: dict[str, str], prefix: str = "") -> None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        symbols[prefix + node.name] = _python_signature(node)
        return
    if isinstance(node, ast.ClassDef):
        name = prefix + node.name
        symbols[name] = _python_class_head(node)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols[name + "." + child.name] = _python_signature(child)
            elif isinstance(child, ast.ClassDef):
                _python_statement(child, symbols, name + ".")
        return
    if isinstance(node, ast.Import):
        for alias in node.names:
            symbols["import:" + _alias(alias)] = "import"
        return
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        rendered = ",".join(_alias(alias) for alias in node.names)
        symbols[f"from:{module}:{rendered}"] = "import"


def _python_signature(node: ast.AST) -> str:
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    args = node.args
    positional = list(args.posonlyargs) + list(args.args)
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parts: list[str] = []
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        if index == len(args.posonlyargs) and args.posonlyargs:
            parts.append("/")
        parts.append(_python_arg(arg, default))
    if args.vararg:
        parts.append("*" + _python_arg(args.vararg, None))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parts.append(_python_arg(arg, default))
    if args.kwarg:
        parts.append("**" + _python_arg(args.kwarg, None))
    decorators = ",".join(ast.dump(item, annotate_fields=False) for item in node.decorator_list)
    returns = ast.dump(node.returns, annotate_fields=False) if node.returns else ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{decorators}|({', '.join(parts)})|{returns}"


def _python_arg(arg: ast.arg, default: ast.AST | None) -> str:
    rendered = arg.arg
    if arg.annotation is not None:
        rendered += ":" + ast.dump(arg.annotation, annotate_fields=False)
    if default is not None:
        rendered += "=" + ast.dump(default, annotate_fields=False)
    return rendered


def _python_class_head(node: ast.ClassDef) -> str:
    bases = ",".join(ast.dump(base, annotate_fields=False) for base in node.bases)
    decorators = ",".join(ast.dump(item, annotate_fields=False) for item in node.decorator_list)
    return f"{decorators}|({bases})"


def _alias(alias: ast.alias) -> str:
    if alias.asname:
        return f"{alias.name} as {alias.asname}"
    return alias.name


def _script(source: str) -> dict[str, str]:
    masked = _mask_script(source)
    symbols: dict[str, str] = {}
    depth = 0
    stack: list[tuple[int, str, str]] = []
    index = 0
    length = len(masked)
    while index < length:
        char = masked[index]
        if char == "{":
            depth += 1
            index += 1
            continue
        if char == "}":
            if stack and stack[-1][0] == depth:
                stack.pop()
            depth = max(0, depth - 1)
            index += 1
            continue
        if not _token_start(masked, index):
            index += 1
            continue
        if depth == 0:
            consumed = _script_toplevel(masked, index, symbols, stack, depth)
            if consumed:
                index = consumed
                continue
        elif stack and stack[-1][0] == depth:
            consumed = _script_member(masked, index, symbols, stack[-1])
            if consumed:
                index = consumed
                continue
        index += 1
    return symbols


def _script_toplevel(
    masked: str,
    index: int,
    symbols: dict[str, str],
    stack: list[tuple[int, str, str]],
    depth: int,
) -> int | None:
    window = masked[index:]
    function = _FUNC_RE.match(window)
    if function:
        open_at = index + function.end() - 1
        params = _balanced(masked, open_at, "(", ")")
        symbols["function:" + function.group(1)] = _norm(params)
        return open_at + len(params)
    klass = _CLASS_RE.match(window)
    if klass:
        return _open_block(masked, index, klass, "class", symbols, stack, depth, members=True)
    interface = _INTERFACE_RE.match(window)
    if interface:
        return _open_block(masked, index, interface, "interface", symbols, stack, depth, members=False)
    alias = _TYPE_RE.match(window)
    if alias:
        start = index + alias.end()
        while start < len(masked) and masked[start].isspace():
            start += 1
        if start < len(masked) and masked[start] == "{":
            body = _balanced(masked, start, "{", "}")
            symbols["type:" + alias.group(1)] = _norm(body)
            return start + len(body)
        line_end = masked.find("\n", start)
        symbols["type:" + alias.group(1)] = _norm(masked[start:line_end if line_end >= 0 else None])
        return line_end if line_end >= 0 else len(masked)
    arrow = _ARROW_RE.match(window)
    if arrow:
        raw = arrow.group(0)
        arrow_at = raw.rfind("=>")
        params = raw[:arrow_at]
        eq = params.find("=")
        params = params[eq + 1 :] if eq >= 0 else params
        symbols["function:" + arrow.group(1)] = _norm(params)
        return index + arrow.end()
    return None


def _open_block(masked, index, match, kind, symbols, stack, depth, members: bool) -> int:
    name = match.group(1)
    end = index + match.end()
    brace = masked.find("{", end)
    head = masked[end:brace] if brace >= 0 else masked[end : end + 120]
    if brace < 0 or members:
        symbols[f"{kind}:{name}"] = _norm(head)
        if brace < 0:
            return end
        stack.append((depth + 1, kind, name))
        return brace
    body = _balanced(masked, brace, "{", "}")
    symbols[f"{kind}:{name}"] = _norm(head + body)
    return brace + len(body)


def _script_member(masked: str, index: int, symbols: dict[str, str], frame: tuple[int, str, str]) -> int | None:
    match = _METHOD_RE.match(masked, index)
    if not match or match.group(1) in _METHOD_SKIP:
        return None
    _depth, kind, name = frame
    open_at = match.end() - 1
    params = _balanced(masked, open_at, "(", ")")
    symbols[f"{kind}:{name}.{match.group(1)}"] = _norm(params)
    return open_at + len(params)


def _token_start(text: str, index: int) -> bool:
    if index == 0:
        return True
    previous = text[index - 1]
    return not (previous.isalnum() or previous in "_$")


def _balanced(text: str, open_at: int, opener: str, closer: str) -> str:
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return text[open_at : index + 1]
    return text[open_at:]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _mask_script(source: str) -> str:
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            while index < length and source[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "/" and nxt == "*":
            out.extend("  ")
            index += 2
            while index < length and not (source[index] == "*" and index + 1 < length and source[index + 1] == "/"):
                out.append("\n" if source[index] == "\n" else " ")
                index += 1
            if index < length:
                out.extend("  ")
                index += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            out.append(" ")
            index += 1
            while index < length:
                if source[index] == "\\":
                    out.append(" ")
                    index += 1
                    if index < length:
                        out.append("\n" if source[index] == "\n" else " ")
                        index += 1
                    continue
                if source[index] == quote:
                    out.append(" ")
                    index += 1
                    break
                out.append("\n" if source[index] == "\n" else " ")
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)
