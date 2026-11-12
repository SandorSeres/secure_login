#!/usr/bin/env python3
"""Parse a research goal markdown file into the Research Harness project fields.

Reads the goal file path from argv[1] (or $GOAL_FILE) and prints a JSON object:
  title, question, objectives[], scope_in[], scope_out[], criteria[], freeform[]

Headings are classified in a language-tolerant way (Hungarian + English), so a
goal file may use `## Célok`, `## Scope`, `## Mit NEM ...`, `## Döntési kritériumok`,
`## Kutatási kérdés` or plain `KUTATASI KERDES:` label lines.
"""
import json
import os
import re
import sys

TRANS = str.maketrans({
    'á': 'a', 'é': 'e', 'í': 'i', 'ö': 'o', 'ő': 'o', 'ú': 'u', 'ü': 'u', 'ű': 'u',
    'Á': 'a', 'É': 'e', 'Í': 'i', 'Ö': 'o', 'Ő': 'o', 'Ú': 'u', 'Ü': 'u', 'Ű': 'u',
})


def norm(value):
    value = value.replace('\u2013', '-').replace('\u2014', '-')
    return re.sub(r'\s+', ' ', value).strip().lower().translate(TRANS)


def classify(name):
    def any_of(*words):
        return any(word in name for word in words)
    if re.match(r'^(\d+[.)]?\s+)?[a-z][a-z0-9/. -]{0,24}\s+-\s+', name):
        return None
    if any_of('kerdes', 'question', 'problem', 'felvetes', 'celpont'):
        return 'question'
    if any_of('nem ', 'nem-', 'nem,', ' kivetel', 'kizar', 'kivul', 'out of scope',
              'non-goal', 'scope out', 'hataron', 'limitacio', 'fenyeget'):
        return 'out'
    if any_of('kriterium', 'dontesi', 'donatas', 'elfogadas', 'ervenyesites',
              'validalas', 'criteria', 'ertekeles'):
        return 'crit'
    if any_of('scope', 'escopus', 'lefedes', 'celterulet', 'vonatkozik', 'beleertve',
              'terulet', 'modsz', 'hatrter', 'kontextus', 'keret'):
        return 'in'
    if any_of('cel', 'objective', 'erdem', 'feladat', 'hatokor', 'kotelezes'):
        return 'objective'
    return None


def parse(text):
    title = ''
    question = ''
    sections = {'objective': [], 'in': [], 'out': [], 'crit': []}
    loose = []
    freeform = []
    current = None
    pending = []

    def flush():
        for item in pending:
            (sections[current] if current in sections else loose).append(item)
        del pending[:]

    def absorb_continuation(value):
        if loose and loose[-1].endswith(':'):
            loose[-1] = loose[-1] + ' ' + value
            return True
        return False

    for raw in re.split(r'\r?\n', text):
        line = raw.rstrip()
        if not line.strip():
            continue
        heading = re.match(r'^(#{1,6})\s+(.*?)\s*#*$', line)
        if heading:
            flush()
            level = len(heading.group(1))
            if level == 1 and not title:
                title = heading.group(2).strip()
                current = None
                continue
            current = classify(norm(heading.group(2))) or 'freeform'
            continue
        bullet = re.match(r'^\s*(?:[-*+]|\d+[.)])\s+(.*)$', line)
        if bullet and bullet.group(1).strip():
            pending.append(bullet.group(1).strip())
            continue
        label = re.match(r'^\s*(?:\*\*|__)?([^:*_]{2,60}?)(?:\*\*|__)?\s*[:\-]\s+(.+)$', line)
        if label:
            key, value = norm(label.group(1)), label.group(2).strip()
            if 'kerdes' in key or 'question' in key:
                if not question:
                    question = value
                    current = 'question-continuation'
                continue
            if current is None or current == 'question':
                loose.append(f'{label.group(1).strip()}: {value}')
                current = 'pending-label'
                continue
        if current == 'question-continuation':
            if not re.match(r'^\s*(?:[-*+]|\d+[.)])\s+', line):
                question = (question + ' ' + line.strip()).strip()
                continue
            current = None
        if current == 'pending-label':
            current = None
        if current == 'freeform':
            freeform.append(line.strip())
            continue
        pending.append(line.strip())
    flush()

    if not title:
        title = freeform[0] if freeform else 'Evidence-Based Research projekt'
    if not question:
        question = (loose[0] if loose
                    else sections['objective'][0] if sections['objective']
                    else freeform[0] if freeform else title)
    return {
        'title': title,
        'question': question,
        'objectives': sections['objective'] + loose,
        'scope_in': sections['in'],
        'scope_out': sections['out'],
        'criteria': sections['crit'],
        'freeform': freeform,
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('GOAL_FILE', '')
    if not path or not os.path.isfile(path):
        print(f'ERROR: goal file not found: {path!r}', file=sys.stderr)
        return 2
    with open(path, encoding='utf-8', errors='replace') as handle:
        print(json.dumps(parse(handle.read()), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
