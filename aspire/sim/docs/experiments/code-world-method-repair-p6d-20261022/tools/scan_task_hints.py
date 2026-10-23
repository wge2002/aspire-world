#!/usr/bin/env python3
"""Scan the p6d skill files and the switch-added prompt text for task-specific hints.

Checks every text a p6d worker sees because of `method_switches` (the two skill files,
the sections `method_contract` adds, the replacement side of `method_edits`) against
the object/part/place nouns of the five campaign tasks and of the LIBERO object
vocabulary. Verbs that name generic subgoal kinds (pick up, place, open, close,
turn on) are allowed by CC_TASK_14 section 1.1 and are not flagged.

  scan_task_hints.py [--out <json>]     exit 1 if any hint term is found
"""
from pathlib import Path
import argparse, json, re, sys

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[3]
sys.path.insert(0, str(SIM / 'scripts/libero'))
import executable_world_profile as ewp  # noqa: E402
import method_switches as ms  # noqa: E402

#: Nouns and qualifiers from the five task stems (prepare-p6d-study.TASKS) ...
TASK_TERMS = ('drawer', 'cabinet', 'middle', 'bottom', 'top drawer', 'stove', 'moka', 'pot',
              'bowl', 'black', 'akita', 'plate', 'ramekin', 'ketchup', 'basket', 'kitchen', 'scene')
#: ... and the LIBERO object / fixture vocabulary (any could be another task's answer).
LIBERO_TERMS = ('milk', 'cookie', 'butter', 'cream cheese', 'chocolate', 'pudding', 'alphabet',
                'soup', 'tomato', 'sauce', 'bbq', 'salad', 'dressing', 'orange juice', 'juice',
                'wine', 'bottle', 'mug', 'cup', 'microwave', 'frying pan', 'pan', 'book',
                'caddy', 'rack', 'tray', 'knob', 'burner', 'faucet', 'sink', 'shelf', 'cabinet door',
                'flat', 'red', 'white', 'yellow', 'porcelain', 'box', 'can', 'lid')
#: Phrases that would give away an answer however generic the nouns.
ANSWER_PATTERNS = (r'\b\d+(\.\d+)?\s*(cm|mm)\b', r'\bseed\s+\d{2}\b(?!-)', r'turns? red',
                   r'glow', r'from the (left|right)\b')
#: Reviewed generic uses (term, context substring, reason): reported, not counted as hints.
REVIEWED = (('scene', 'a part of the scene changed state', 'generic: the scene, no task named'),
            ('box', 'bounding box', 'API term (get_oriented_bounding_box_from_3d_points)'),
            ('can', ' can ', 'modal verb'),
            (r'\bseed\s+\d{2}\b(?!-)', 'seed 51 too', 'protocol: v0 is admitted on development seed 51'))


def reviewed(term, context):
    for allowed, fragment, reason in REVIEWED:
        if allowed == term and fragment in ' ' + context.lower() + ' ':
            return reason
    return None


def texts():
    case = {'method_switches': dict(ms.P6D), 'sweep_parallel': 4}
    yield 'skill:' + ewp.SUBGOAL_SKILL, (SIM / ewp.SUBGOAL_SKILL).read_text()
    yield 'skill:' + ewp.API_M4_SKILL, (SIM / ewp.API_M4_SKILL).read_text()
    yield 'prompt:method_contract', ewp.method_contract(case)
    yield 'prompt:subgoal_goal_edit', ewp.SUBGOAL_GOAL_EDIT[1]
    for index, (_, new) in enumerate(ewp.method_edits(case)):
        yield f'prompt:method_edit[{index}]', new


def scan():
    findings, scanned, accepted = [], [], []
    for name, text in texts():
        lower = text.lower()
        scanned.append({'source': name, 'chars': len(text)})
        for term in TASK_TERMS + LIBERO_TERMS:
            for match in re.finditer(r'\b' + re.escape(term) + r's?\b', lower):
                line = lower.count('\n', 0, match.start()) + 1
                findings.append({'source': name, 'line': line, 'term': term,
                                 'context': text.splitlines()[line - 1].strip()[:160]})
        for pattern in ANSWER_PATTERNS:
            for match in re.finditer(pattern, lower):
                line = lower.count('\n', 0, match.start()) + 1
                findings.append({'source': name, 'line': line, 'term': pattern,
                                 'context': text.splitlines()[line - 1].strip()[:160]})
    for finding in list(findings):
        reason = reviewed(finding['term'], finding['context'])
        if reason:
            findings.remove(finding)
            accepted.append({**finding, 'reason': reason})
    return {'scanned': scanned, 'terms': len(TASK_TERMS + LIBERO_TERMS) + len(ANSWER_PATTERNS),
            'reviewed_generic': accepted, 'findings': findings, 'clean': not findings}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out')
    args = parser.parse_args()
    report = scan()
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + '\n')
    print(text)
    return 0 if report['clean'] else 1


if __name__ == '__main__':
    sys.exit(main())
