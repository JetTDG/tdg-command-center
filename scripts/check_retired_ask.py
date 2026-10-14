#!/usr/bin/env python3
"""Dependency-free release check: Ask Jet lives in Jet AI, not Jet Center.

Run against the exact directory being uploaded, including CLI release candidates:
    python3 scripts/check_retired_ask.py [source_root]
This check never connects to services or imports the application.
"""
import ast
from pathlib import Path
import re
import sys


def violations(root):
    root = Path(root)
    errors = []
    routes = root / 'app/routes/main.py'
    nav = root / 'app/templates/base.html'
    for path in (routes, nav):
        if not path.is_file():
            errors.append(f'Missing required source: {path.relative_to(root)}')
    if errors:
        return errors
    try:
        tree = ast.parse(routes.read_text())
    except SyntaxError as exc:
        return [f'Cannot parse main routes: {exc}']
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in {'route', 'get', 'post', 'add_url_rule'}:
            continue
        values = list(node.args[:1]) + [kw.value for kw in node.keywords if kw.arg == 'rule']
        for value in values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                if value.value.rstrip('/') in {'/ask', '/api/ask'}:
                    errors.append(f'Retired route registered at main.py:{node.lineno}: {value.value}')
    html = nav.read_text()
    if re.search(r'\bmain\.(?:ask|api_ask)\b', html):
        errors.append('Desktop/mobile base navigation references a retired Ask endpoint')
    if re.search(r'href\s*=\s*[\"\']/ask(?:[/?#\"\'])', html, re.I):
        errors.append('Desktop/mobile base navigation links directly to /ask')
    return errors


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    errors = violations(root)
    if errors:
        print('FAIL: retired Ask surface must not ship\n' + '\n'.join(errors))
        return 1
    print('PASS: legacy Ask routes and desktop/mobile navigation remain retired')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
