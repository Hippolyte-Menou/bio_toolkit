"""
Shared YAML frontmatter utilities.

Ported verbatim from the vault's _assets/code/common/yaml_utils.py — the single
canonical frontmatter parser. All scripts that read or modify YAML frontmatter
should use these functions instead of rolling their own regex parsing.

Usage:
    from bio_toolkit.util.yaml import parse_frontmatter, extract_frontmatter, update_yaml_field
"""

import re
from typing import Dict, List, Optional, Tuple, Union


def extract_frontmatter(content: str) -> Tuple[Optional[str], str, str]:
    """
    Split a markdown file into (frontmatter, separator, body).

    Returns:
        (frontmatter_text, closing_separator, body_text)
        frontmatter_text is the raw text between the two '---' lines (no delimiters).
        Returns (None, '', content) if no YAML block is found.
    """
    if not content.startswith("---"):
        return None, "", content

    end_match = re.search(r"\n---[ \t]*\n", content[3:])
    if not end_match:
        end_match = re.search(r"\n---[ \t]*$", content[3:])
        if not end_match:
            return None, "", content
        fm = content[3 : 3 + end_match.start()]
        sep = content[3 + end_match.start():]
        return fm, sep, ""

    fm = content[3 : 3 + end_match.start()]
    body_start = 3 + end_match.end()
    body = content[body_start:]
    sep = "\n---\n"
    return fm, sep, body


def parse_frontmatter(content: str) -> Dict[str, str]:
    """
    Extract YAML frontmatter fields from markdown content as a flat dict.

    Handles simple key: value pairs. Multi-line lists are returned as the
    raw string value. Handles quoted values (strips surrounding quotes).
    """
    fm_text, _, _ = extract_frontmatter(content)
    if fm_text is None:
        return {}

    fields = {}
    for line in fm_text.split('\n'):
        line = line.strip()
        if ':' in line and not line.startswith('-'):
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if value:
                fields[key] = value
    return fields


def parse_frontmatter_from_file(filepath: str) -> Dict[str, str]:
    """Convenience: parse frontmatter directly from a file path."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return {}
    return parse_frontmatter(content)


def update_yaml_field(content: str, field: str, value: Union[str, list, None]) -> str:
    """
    Update a YAML field in frontmatter. Handles scalars and lists.

    If the field exists, replaces its value (including multi-line list blocks).
    If the field does not exist, inserts it before the closing '---'.
    """
    if isinstance(value, list):
        list_str = '\n'.join(f'  - {item}' for item in value)
        new_field = f'{field}:\n{list_str}'
    elif value is None or value == '':
        new_field = f'{field}:'
    else:
        new_field = f'{field}: {value}'

    # Match field line + subsequent indented list lines
    pattern = rf'^{re.escape(field)}:(?:[^\n]*)(?:\n[ \t]+-[^\n]*)*'
    updated, count = re.subn(pattern, new_field, content, count=1, flags=re.MULTILINE)
    if count == 0:
        fm_end = content.find('\n---', 4)
        if fm_end != -1:
            updated = content[:fm_end] + '\n' + new_field + content[fm_end:]
        else:
            updated = content
    return updated


def replace_yaml_field_simple(content: str, field: str, new_value: str) -> Tuple[str, bool]:
    """
    Simple regex replacement of a scalar YAML field value.

    Returns (new_content, was_replaced).
    """
    new_content, count = re.subn(
        rf'^{re.escape(field)}:.*$',
        f'{field}: {new_value}',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    return new_content, count > 0


def remove_yaml_keys(frontmatter: str, keys: List[str]) -> str:
    """
    Remove specified keys (and their list values) from raw frontmatter text.

    Handles both scalar and multi-line YAML list values.
    """
    lines = frontmatter.split("\n")
    result = []
    skip_list = False

    for line in lines:
        stripped = line.strip()
        is_target_key = any(stripped.startswith(f"{key}:") for key in keys)
        if is_target_key:
            skip_list = True
            continue
        if skip_list:
            if stripped.startswith("- "):
                continue
            else:
                skip_list = False
        result.append(line)

    return "\n".join(result)
