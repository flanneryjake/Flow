"""Runs baby_skills.py against the live Baby Jarvis and prints a score per skill. python eval_baby_skills.py"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import baby_skills as b  # noqa: E402

GOOD = """# Session 4: Coping With Cravings
## Objectives
Participants name three craving triggers.
## Activities
Urge surfing practice, 10 minutes.
## Closing
If you're in crisis, call or text 988, or call 911 in an emergency."""

CHECKS = [  # (text, sections, expect_pass, expected problem fragment)
    (GOOD, ['Objectives', 'Activities', 'Closing'], True, ''),
    (GOOD.replace('call or text 988, or call 911 in an emergency', 'reach out to staff'), ['Closing'], False, '988'),
    (GOOD.replace('## Activities\n', ''), ['Objectives', 'Activities'], False, 'Activities'),
    (GOOD.replace('Urge surfing practice', '[Insert activity here]'), [], False, 'placeholder'),
    (GOOD + '\nFacilitator: {{facilitator_name}}', [], False, 'placeholder'),
    (GOOD + '\nTODO: add handout link', [], False, 'placeholder'),
]

ROUTES = [  # (request, acceptable routes)
    ('is the rig on', {'laptop'}, 'Machine Health: rig Offline since 00:41 (asleep); homebase OK; laptop OK.'),
    ('write a 1,200-word blog post about sober holidays', {'rig'}),
    ('give me a name for a file about relapse triggers', {'laptop'}),
    ("summarize: watchdog says ollama DOWN on rig, remote control up, 3 cards waiting", {'laptop'}),
    ('tell me a joke', {'laptop'}),
    ('turn this into a list: eggs milk bread', {'laptop'}),
    ('write the B12 facilitator guide', {'rig'}),
    ('write me a cover letter for the clinician job', {'rig'}),
    ('write a python script to rename my photos', {'rig', 'claude'}),
    ('make a 10 week anger management curriculum', {'rig'}),
    ('order more PETG filament', {'claude'}),
    ("what's the weather tomorrow", {'claude'}),
    ('email my supervisor that I will be late', {'claude'}),
    ('publish the new handout on the store', {'claude'}),
    ('look up the new 42 CFR part 2 rules', {'claude'}),
]

HA = [  # (utterance, expect_ok, expected service)
    ('turn off the living room lights', True, 'light.turn_off'),
    ('kitchen lights on', True, 'light.turn_on'),
    ('set the bedroom to 68', True, 'climate.set_temperature'),
    ('turn the heat off upstairs', True, 'climate'),
    ('unlock the front door', False, None),
    ('open the garage', False, None),
    ('disarm the alarm', False, None),
    ('set the thermostat to 120', False, None),
]


def main():
    score = {}
    print('== check_rig_output')
    ok = 0
    for text, secs, exp, frag in CHECKS:
        r = b.check_rig_output(text, 'Session 4', secs)
        good = r['pass'] == exp and (not frag or frag.lower() in ' '.join(r['problems']).lower())
        ok += good
        print(('OK ' if good else 'BAD'), r['pass'], r['problems'], '|', r['notify'])
    score['check'] = f'{ok}/{len(CHECKS)}'
    print('== route_request')
    ok = 0
    for text, exp, *ctx in ROUTES:
        r = b.route_request(text, *ctx)
        good = r['route'] in exp
        ok += good
        print(('OK ' if good else 'BAD'), r['route'], f"{r['model']}s", '|', text, '|', (r['answer'] or r['why'])[:110].replace('\n', ' '))
    score['route'] = f'{ok}/{len(ROUTES)}'
    print('== ha_intent')
    ok = 0
    for text, exp, svc in HA:
        r = b.ha_intent(text)
        good = r['ok'] == exp and (not svc or svc in json.dumps(r['call']))
        ok += good
        print(('OK ' if good else 'BAD'), r['ok'], json.dumps(r['call']), '|', text, '|', r['why'])
    score['ha'] = f'{ok}/{len(HA)}'
    print(json.dumps(score))


if __name__ == '__main__':
    main()
