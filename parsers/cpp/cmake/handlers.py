"""Helpers for locating and slicing CMake command calls.

These utilities are shared by the CMake config parser and its regex fallback.
They are intentionally stateless to remain easy to test and reuse.
"""

from __future__ import annotations

import re
from typing import List, Tuple


def extract_balanced_parentheses(text: str, start_pos: int) -> str:
    """Return substring inside balanced parentheses starting at `start_pos`.

    Args:
        text: Source text.
        start_pos: Index of the opening parenthesis '(' in `text`.

    Returns:
        The content between the matching parentheses without the outer pair.
        Returns an empty string when no balanced range is found.
    """
    if start_pos >= len(text) or text[start_pos] != "(":
        return ""
    depth = 1
    pos = start_pos + 1
    while pos < len(text) and depth > 0:
        ch = text[pos]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        pos += 1
    if depth == 0:
        return text[start_pos + 1 : pos - 1]
    return ""


def find_cmake_commands(pattern: str, text: str) -> List[Tuple[str, str]]:
    """Find occurrences of a CMake command and split out its target and args.

    This scans for the command name using `pattern`, then identifies the next
    opening parenthesis, extracts the first token inside it as the target, and
    returns the remaining argument text (excluding the target token and
    surrounding whitespace).

    Args:
        pattern: Regex pattern for the command name, including "(\s*\(" prefix.
        text: Full CMake file text.

    Returns:
        List of tuples (target_name, args_text) for each command occurrence.
    """
    results: List[Tuple[str, str]] = []
    for match in re.finditer(pattern, text, re.IGNORECASE):
        start_pos = match.start()
        paren_pos = text.find("(", start_pos)
        if paren_pos == -1:
            continue
        # Extract target token just after '('
        target_match = re.match(r"\s*([A-Za-z0-9_${}\-.:]+)", text[paren_pos + 1 :])
        if not target_match:
            continue
        target_name = target_match.group(1)
        # Extract full content inside the parentheses
        full = extract_balanced_parentheses(text, paren_pos)
        if not full:
            continue
        args_start = len(target_name)
        while args_start < len(full) and full[args_start].isspace():
            args_start += 1
        args_text = full[args_start:]
        results.append((target_name, args_text))
    return results

