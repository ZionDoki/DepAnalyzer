"""Tokenization and filtering utilities for CMake argument strings.

This module provides utilities for parsing and validating CMake command arguments,
including token extraction, dependency filtering, and variable pattern matching.
"""

from __future__ import annotations

import re
from typing import List


TOKEN_RE = re.compile(
    r"""
    (\[(=*)\[(?:.|\n)*?\]\2\])   # bracket-style [[...]] with optional = signs
    |("(?:(?:\\.|[^"]\\)*)")     # double-quoted string with escapes
    |('(?:(?:\\.|[^'\\])*)')     # single-quoted
    |([^\s()#]+)                 # unquoted token
    """,
    re.VERBOSE,
)


CMAKE_KEYWORDS = {
    "PUBLIC",
    "PRIVATE",
    "INTERFACE",
    "STATIC",
    "SHARED",
    "MODULE",
    "OBJECT",
    "IMPORTED",
    "ALIAS",
    "EXCLUDE_FROM_ALL",
    "OPTIONAL",
    "REQUIRED",
    "QUIET",
    "COMPONENTS",
}

COMMON_WORDS = {
    "THE",
    "TO",
    "A",
    "AN",
    "AND",
    "OR",
    "FOR",
    "IN",
    "ON",
    "AT",
    "BY",
    "WITH",
    "FROM",
    "AS",
    "IS",
    "ARE",
    "WAS",
    "WERE",
    "BE",
    "BEEN",
    "BEING",
    "HAVE",
    "HAS",
    "HAD",
    "DO",
    "DOES",
    "DID",
    "WILL",
    "WOULD",
    "COULD",
    "SHOULD",
    "MAY",
    "MIGHT",
    "CAN",
    "THIS",
    "THAT",
    "THESE",
    "THOSE",
    "YOU",
    "YOUR",
    "YOURS",
    "IT",
    "ITS",
    "HE",
    "HIS",
    "SHE",
    "HER",
    "HERS",
    "WE",
    "OUR",
    "OURS",
    "THEY",
    "THEIR",
    "THEIRS",
    "LIBRARY",
    "LIBRARIES",
    "TARGET",
    "LINK",
    "LINKS",
    "LINKED",
    "LINKING",
    "INCLUDED",
    "INCLUDE",
    "INCLUDES",
    "INCLUDING",
    "NDK",
    "CMAKE",
    "BUILD",
    "BUILDS",
    "FILE",
    "FILES",
    "PATH",
    "PATHS",
    "SOURCE",
    "SOURCES",
    "CODE",
    "CODES",
    "SPECIFIED",
    "SPECIFIES",
    "SPECIFY",
    "DEFINE",
    "DEFINES",
    "DEFINED",
    "MULTIPLE",
    "SUCH",
    "GRADLE",
    "AUTOMATICALLY",
    "PACKAGES",
    "SHARED",
    "RELATIVE",
    "PROVIDES",
    "SETS",
    "NAMES",
    "CREATES",
}


CMAKE_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def clean_token(token: str) -> str:
    """Normalize a CMake token by removing quotes and trimming whitespace.

    Args:
        token: Raw token string extracted from arguments.

    Returns:
        Cleaned token string without surrounding quotes and extra whitespace.
    """
    if not token:
        return token
    token = token.strip()
    if (token.startswith('"') and token.endswith('"')) or (
        token.startswith("'") and token.endswith("'")
    ):
        token = token[1:-1]
    return token.strip()


def is_valid_dependency(token: str) -> bool:
    """Check whether a token represents a valid dependency target.

    Filters out CMake keywords, common noise words, and invalid tokens.

    Args:
        token: Token to validate.

    Returns:
        True if the token passes basic keyword/noise filters, otherwise False.
    """
    if not token or not token.strip():
        return False

    cleaned = clean_token(token).upper()
    if cleaned in CMAKE_KEYWORDS:
        return False
    if cleaned in COMMON_WORDS:
        return False
    if token.strip().startswith("$<") and token.strip().endswith(">"):
        return False
    if cleaned in {"", "(", ")", "{", "}", "$", "#"}:
        return False
    if len(cleaned) <= 1:
        return False
    if cleaned.strip(".,;:!?-_+*=<>[]{}()") == "":
        return False
    return True


def extract_dependencies_from_args(args: List[str], skip_first: int = 1) -> List[str]:
    """Extract dependency tokens from a command argument list.

    Filters out scope keywords (PUBLIC/PRIVATE/INTERFACE) and invalid tokens.

    Args:
        args: Token list, typically for commands like target_link_libraries.
        skip_first: Number of leading tokens to skip (e.g., target name).

    Returns:
        Filtered list of valid dependency tokens.
    """
    if len(args) <= skip_first:
        return []
    dependencies: List[str] = []
    i = skip_first
    while i < len(args):
        token = args[i]
        if token.upper() in {"PUBLIC", "PRIVATE", "INTERFACE"}:
            i += 1
            continue
        cleaned = clean_token(token)
        if is_valid_dependency(cleaned):
            dependencies.append(cleaned)
        i += 1
    return dependencies


def extract_arg_tokens(arg_text: str) -> List[str]:
    """Tokenize CMake command arguments into a list of tokens.

    Handles bracket arguments, quoted strings, and unquoted tokens while
    removing comments.

    Args:
        arg_text: Text segment inside the parentheses of a CMake command.

    Returns:
        List of cleaned tokens, preserving bracket and quoted segments.
    """
    clean_lines = []
    for line in arg_text.split("\n"):
        comment_pos = line.find("#")
        if comment_pos >= 0:
            line = line[:comment_pos]
        line = line.strip()
        if line:
            clean_lines.append(line)
    cleaned_arg_text = " ".join(clean_lines)

    tokens: List[str] = []
    for m in TOKEN_RE.finditer(cleaned_arg_text):
        br = m.group(1)
        dq = m.group(3)
        sq = m.group(4)
        uq = m.group(5)
        if br:
            inner = re.sub(r"^\[(=*)\[", "", br)
            inner = re.sub(r"\](=*)\]$", "", inner)
            tokens.append(inner.strip())
        elif dq is not None:
            tokens.append(dq[1:-1])
        elif sq is not None:
            tokens.append(sq[1:-1])
        elif uq:
            if not uq.startswith("#"):
                tokens.append(uq)
    return tokens


__all__ = [
    "CMAKE_VAR_PATTERN",
    "CMAKE_KEYWORDS",
    "COMMON_WORDS",
    "TOKEN_RE",
    "clean_token",
    "is_valid_dependency",
    "extract_dependencies_from_args",
    "extract_arg_tokens",
]

