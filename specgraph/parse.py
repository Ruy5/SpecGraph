from __future__ import annotations

import json

FENCE = "---"


class FrontmatterError(ValueError):
    pass


def read_text(path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def split_document(text: str) -> tuple[str | None, str, str, str]:
    """Return ``(frontmatter, body, bom, newline)``.

    ``body`` is the exact text after the closing fence line, so a rewrite can
    put the prose back without touching it.
    """
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    raw = text[len(bom) :]
    first_nl = raw.find("\n")
    first_line = raw if first_nl < 0 else raw[: first_nl + 1]
    newline = "\r\n" if "\r\n" in first_line else "\n"
    if not raw.startswith("---"):
        return None, text, bom, newline
    if first_nl < 0 or raw[:first_nl].strip("\r") != FENCE:
        return None, text, bom, newline
    rest = raw[first_nl + 1 :]
    cursor = 0
    while cursor <= len(rest):
        next_nl = rest.find("\n", cursor)
        line = rest[cursor:] if next_nl < 0 else rest[cursor:next_nl]
        if line.strip("\r") == FENCE:
            closing = next_nl + 1 if next_nl >= 0 else len(rest)
            return rest[:cursor], rest[closing:], bom, newline
        if next_nl < 0:
            break
        cursor = next_nl + 1
    return None, text, bom, newline


def join_document(frontmatter: str, body: str, bom: str = "", newline: str = "\n") -> str:
    payload = frontmatter.strip("\r\n").replace("\r\n", "\n")
    fence_nl = "\r\n" if newline == "\r\n" else "\n"
    if fence_nl == "\r\n":
        payload = payload.replace("\n", "\r\n")
    return f"{bom}---{fence_nl}{payload}{fence_nl}---{fence_nl}{body}"


def splice_frontmatter(text: str, data: dict) -> str:
    _current, body, bom, newline = split_document(text)
    if _current is None:
        raise FrontmatterError("file has no frontmatter")
    return join_document(dump_mapping(data), body, bom, newline)


def parse_document(text: str) -> tuple[dict | None, str]:
    frontmatter, body, _bom, _newline = split_document(text)
    if frontmatter is None:
        return None, body
    try:
        return parse_mapping_text(frontmatter), body
    except FrontmatterError:
        return None, text


def parse_mapping_text(text: str) -> dict:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    data, index = _parse_map(lines, 0, 0)
    while index < len(lines) and lines[index].strip() == "":
        index += 1
    if index < len(lines):
        raise FrontmatterError(f"trailing frontmatter at line {index + 1}: {lines[index]!r}")
    return data


def dump_mapping(data: dict) -> str:
    rendered = _dump_map(data, 0)
    return ("\n".join(rendered) + "\n") if rendered else ""


def link_entries(frontmatter: dict, kind: str) -> list[dict]:
    raw = frontmatter.get(kind)
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    entries = []
    for item in items:
        if isinstance(item, str) and item:
            entries.append({"id": item})
        elif isinstance(item, dict) and item.get("id"):
            entries.append(dict(item))
    return entries


def string_list(frontmatter: dict, key: str) -> list[str]:
    raw = frontmatter.get(key)
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str) and item]
    return []


def scalar(frontmatter: dict, key: str) -> str | None:
    value = frontmatter.get(key)
    if isinstance(value, str) and value != "":
        return value
    return None


def _parse_map(lines: list[str], start: int, indent: int) -> tuple[dict, int]:
    data: dict = {}
    index = start
    while index < len(lines):
        raw = lines[index]
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            index += 1
            continue
        current = _indent(raw)
        if current < indent:
            break
        if current > indent:
            raise FrontmatterError(f"unexpected indent at line {index + 1}")
        stripped = _strip_comment(raw.strip())
        if stripped.startswith("- "):
            break
        if ":" not in stripped:
            raise FrontmatterError(f"expected key at line {index + 1}")
        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = rest.strip()
        index += 1
        if rest == "":
            value, index = _parse_nested(lines, index, indent)
        elif rest in {"|", ">", "|-", "|+", ">-", ">+"}:
            value, index = _parse_block_scalar(lines, index, indent)
        else:
            value = _parse_value(rest)
        data[key] = value
    return data, index


def _parse_nested(lines: list[str], start: int, parent_indent: int):
    index = start
    while index < len(lines) and (lines[index].strip() == "" or lines[index].lstrip().startswith("#")):
        index += 1
    if index >= len(lines):
        return None, index
    if _indent(lines[index]) <= parent_indent:
        return None, start
    current = _indent(lines[index])
    if lines[index].lstrip().startswith("- "):
        return _parse_list(lines, index, current)
    return _parse_map(lines, index, current)


def _parse_list(lines: list[str], start: int, indent: int):
    items = []
    index = start
    while index < len(lines):
        raw = lines[index]
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            index += 1
            continue
        current = _indent(raw)
        if current < indent:
            break
        if current > indent:
            raise FrontmatterError(f"unexpected indent at line {index + 1}")
        content = _strip_comment(raw.strip())
        if not content.startswith("-"):
            break
        item = content[1:].strip()
        index += 1
        if item == "":
            value, index = _parse_nested(lines, index, indent)
            items.append(value)
            continue
        if _looks_like_key(item):
            key, rest = item.split(":", 1)
            nested: dict = {}
            rest = rest.strip()
            if rest == "":
                value, index = _parse_nested(lines, index, indent)
                nested[key.strip()] = value
            else:
                nested[key.strip()] = _parse_value(rest)
            extra, index = _parse_map(lines, index, indent + 2)
            nested.update(extra)
            items.append(nested)
            continue
        items.append(_parse_value(item))
    return items, index


def _parse_block_scalar(lines: list[str], start: int, parent_indent: int):
    index = start
    block: list[str] = []
    while index < len(lines):
        raw = lines[index]
        if raw.strip() == "":
            block.append("")
            index += 1
            continue
        if _indent(raw) <= parent_indent:
            break
        block.append(raw.strip())
        index += 1
    while block and block[-1] == "":
        block.pop()
    return "\n".join(block), index


def _looks_like_key(text: str) -> bool:
    if text[:1] in {"'", '"'}:
        return False
    name, sep, _rest = text.partition(":")
    if not sep or not name:
        return False
    return all(char.isalnum() or char in "-_" for char in name)


def _parse_value(text: str):
    if text in {"null", "~", "Null", "NULL"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        return _parse_flow(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        if text[0] == '"':
            return json.loads(text)
        return text[1:-1].replace("''", "'")
    return text


def _parse_flow(text: str) -> list:
    inner = text[1:-1].strip()
    if inner == "":
        return []
    return [_parse_value(part.strip()) for part in _split_commas(inner)]


def _split_commas(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    for char in text:
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            buf.append(char)
        elif char == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    if buf:
        parts.append("".join(buf))
    return parts


def _strip_comment(text: str) -> str:
    quote = ""
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "#" and (index == 0 or text[index - 1].isspace()):
            return text[:index].rstrip()
    return text


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _dump_map(data: dict, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(_dump_map(value, indent + 2))
        elif isinstance(value, list):
            if not value:
                lines.append(f"{pad}{key}: []")
            elif all(not isinstance(item, (dict, list)) for item in value) and _flow_fits(value):
                rendered = ", ".join(_dump_scalar(item) for item in value)
                lines.append(f"{pad}{key}: [{rendered}]")
            else:
                lines.append(f"{pad}{key}:")
                lines.extend(_dump_list(value, indent + 2))
        elif isinstance(value, str) and "\n" in value:
            lines.append(f"{pad}{key}: |")
            for row in value.split("\n"):
                lines.append(f"{pad}  {row}")
        else:
            lines.append(f"{pad}{key}: {_dump_scalar(value)}")
    return lines


def _dump_list(items: list, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            keys = list(item)
            if not keys:
                lines.append(f"{pad}- {{}}")
                continue
            head, *rest = keys
            head_value = item[head]
            if isinstance(head_value, dict):
                lines.append(f"{pad}- {head}:")
                lines.extend(_dump_map(head_value, indent + 4))
            elif isinstance(head_value, list):
                lines.append(f"{pad}- {head}:")
                lines.extend(_dump_list(head_value, indent + 4))
            else:
                lines.append(f"{pad}- {head}: {_dump_scalar(head_value)}")
            for key in rest:
                value = item[key]
                if isinstance(value, dict):
                    lines.append(f"{pad}  {key}:")
                    lines.extend(_dump_map(value, indent + 4))
                elif isinstance(value, list):
                    lines.append(f"{pad}  {key}:")
                    lines.extend(_dump_list(value, indent + 4))
                else:
                    lines.append(f"{pad}  {key}: {_dump_scalar(value)}")
        else:
            lines.append(f"{pad}- {_dump_scalar(item)}")
    return lines


def _flow_fits(items: list) -> bool:
    return len(", ".join(_dump_scalar(item) for item in items)) <= 100


def _dump_scalar(value) -> str:
    if value is None:
        return "null"
    text = str(value)
    if text == "" or text.strip() != text:
        return json.dumps(text, ensure_ascii=False)
    if text.lower() in {"null", "true", "false", "~", "yes", "no"}:
        return json.dumps(text, ensure_ascii=False)
    if any(char in text for char in ":#{}[]&*!|>%@`'\",\n"):
        return json.dumps(text, ensure_ascii=False)
    return text
