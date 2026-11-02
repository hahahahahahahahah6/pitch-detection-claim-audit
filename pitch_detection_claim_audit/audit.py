"""Offline documentation and AST inspection. Source is never executed."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import sys

from . import __version__
from .phrases import PITCH, NOPITCH

GENERIC = {'detect_pitch', 'estimate_pitch', 'get_pitch', 'pitch_detect',
           'extract_pitch', 'pitch_track', 'track_pitch'}


def normalize(text):
    return ' '.join(text.lower().replace('’', "'").replace("n't", ' not').replace('-', ' ').split())


def pattern(phrases):
    return re.compile(r'\b(?:' + '|'.join(re.escape(p) for p in
                      sorted({normalize(p) for p in phrases}, key=lambda p: (-len(p), p))) + r')\b')


POSITIVE = pattern(PITCH)
NEGATIVE = pattern(NOPITCH)


def claims(text):
    lines = []
    fence = None
    for line in text.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
                lines.append('')
        elif match:
            fence = match[1]
            lines.append('')
        else:
            lines.append(line)
    positive = negative = False
    for paragraph in re.split(r'\n\s*\n', '\n'.join(lines)):
        paragraph = normalize(paragraph)
        negative |= bool(NEGATIVE.search(paragraph))
        positive |= bool(POSITIVE.search(NEGATIVE.sub(' ', paragraph)))
    return positive, negative


def evidence(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    modules, imported = {}, {}
    shadow = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    # Imports in nested scopes are not module bindings.
    def bindings(nodes):
        for n in nodes:
            if isinstance(n, ast.Import):
                for a in n.names:
                    if a.name in {'librosa', 'crepe', 'torchcrepe', 'aubio'}:
                        modules[a.asname or a.name] = a.name
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    imported[a.asname or a.name] = (n.module, a.name)
            elif not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for field, value in ast.iter_fields(n):
                    if isinstance(value, list):
                        bindings([v for v in value if isinstance(v, ast.stmt)])
    bindings(tree.body)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            origin, name = imported.get(f.id, (None, None))
            if origin == 'librosa' and name in {'piptrack', 'yin'}:
                return True
            if name in GENERIC and name not in shadow and f.id not in shadow:
                return True
        elif isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name):
                lib = modules.get(f.value.id)
                if (lib == 'librosa' and f.attr in {'piptrack', 'yin'} or
                    lib in {'crepe', 'torchcrepe'} and f.attr == 'predict' or
                    lib == 'aubio' and f.attr == 'pitch'):
                    return True
            if f.attr in GENERIC | {'to_pitch'} and f.attr not in shadow:
                return True
    return False


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise OSError('root must be a readable, non-symlink directory')
    positive = negative = production = testing = False
    documents, sources = [], []
    prefixes = [Path(p).as_posix().strip('/') for p in ignore]
    def ignored(rel):
        return any(rel == p or rel.startswith(p + '/') for p in prefixes)
    def onerror(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink()
                         and not ignored((base / d).relative_to(root).as_posix()))
        for name in sorted(files):
            path = base / name
            rel = path.relative_to(root)
            if path.is_symlink() or ignored(rel.as_posix()):
                continue
            if rel.as_posix() == 'README.md' or path.suffix == '.md' and 'docs' in rel.parts[:-1]:
                p, n = claims(path.read_text(encoding='utf-8'))
                positive |= p
                negative |= n
                documents.append(rel.as_posix())
            if path.suffix == '.py':
                sources.append(rel.as_posix())
                if {'fixtures', 'docs', 'examples'} & set(rel.parts[:-1]):
                    continue
                found = evidence(path.read_text(encoding='utf-8-sig'))
                if 'tests' in rel.parts[:-1] or name.startswith('test_') or name.endswith('_test.py'):
                    testing |= found
                else:
                    production |= found
    findings = []
    if positive and not production:
        findings.append({'id': 'documented-pitch-detection-claim-false',
                         'severity': 'MEDIUM' if testing else 'HIGH',
                         'message': 'Evidence exists only in tests.' if testing else 'No production evidence supports the documentation.'})
    if negative and production:
        findings.append({'id': 'documented-no-pitch-detection-claim-false', 'severity': 'HIGH',
                         'message': 'Production evidence contradicts the documented denial.'})
    return {'verdict': 'FAIL' if findings else 'PASS', 'findings': findings,
            'documents': documents, 'sources': sources,
            'production_evidence': production, 'test_evidence': testing}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '%s: error: %s\n' % (self.prog, message))


def main(argv=None):
    parser = Parser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='PATH')
    parser.add_argument('--fail-on', choices=['HIGH', 'MEDIUM', 'LOW'], default='HIGH')
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ignore)
    except (OSError, UnicodeError) as exc:
        print('error: %s' % exc, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(result['verdict'])
        for finding in result['findings']:
            print('{severity} {id}: {message}'.format(**finding))
        if args.verbose:
            print('Documents: %s; sources: %s' % (len(result['documents']), len(result['sources'])))
    ranks = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    return 2 if any(ranks[f['severity']] >= ranks[args.fail_on] for f in result['findings']) else 0
