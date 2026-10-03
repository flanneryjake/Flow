"""Command cards (cross-connect phase 4c): a cloud thread files a jarvis-tasks card, the 5060 runs one node command.

A command card is an open issue labelled `node-cmd` whose body carries
    <!-- jarvis:nodecmd {"target": "junk", "cmd": "restart_service", "args": {"name": "searxng"}} -->
It is never status:approved or staged, so the Workers never claim it. Each poll, run_node_cards():
  1. runs the command once through client.call (signed with this PC's key; the target's approvals.json and rate
     limits decide whether it may run),
  2. writes ONE result comment (marked <!-- jarvis:nodecmd-result -->), never more, and
  3. closes the card.
A card that already has a result comment is only closed, never run again, so a failed close can't repeat a restart.
restart_pc is refused here: a whole-PC restart stays on the app's PIN button and the board wipe.
"""
import json
import re

LABEL = 'node-cmd'
MARK_RE = re.compile(r'<!--\s*jarvis:nodecmd\s+(\{.*?\})\s*-->', re.S)
RESULT_MARK = '<!-- jarvis:nodecmd-result -->'
CARD_CMDS = ('status', 'logs', 'restart_service', 'run_fix', 'wake')
TARGETS = ('5060', 'rig', 'junk', 'hal9000')


def parse(body):
    """(target, cmd, args) from a card body, or raise ValueError saying what is wrong."""
    m = MARK_RE.search(body or '')
    if not m:
        raise ValueError('no jarvis:nodecmd marker in the card body')
    try:
        spec = json.loads(m.group(1))
    except ValueError:
        raise ValueError('the jarvis:nodecmd marker is not valid JSON') from None
    target, cmd, args = spec.get('target'), spec.get('cmd'), spec.get('args') or {}
    if target not in TARGETS:
        raise ValueError(f'unknown target {target!r}')
    if cmd not in CARD_CMDS:
        raise ValueError(f'{cmd!r} is not allowed on a command card (allowed: {", ".join(CARD_CMDS)})')
    if not isinstance(args, dict):
        raise ValueError('args must be an object')
    return target, cmd, args


def result_text(target, cmd, out):
    if out.get('ok'):
        head = f'Ran `{cmd}` on **{target}**: {out.get("summary") or "ok"}.'
    else:
        head = f'`{cmd}` on **{target}** did not run: {out.get("error", "no reason given")}.'
    detail = json.dumps({k: v for k, v in out.items() if k not in ('ok', 'node', 'cmd')}, indent=1)[:3000]
    return f'{RESULT_MARK}\n{head}\n\n```json\n{detail}\n```'


def run_node_cards(gh, call, repo='flanneryjake/jarvis-tasks', log=print, limit=5, issues=None, report=None):
    """One poll. gh(method, path, body=None) -> parsed JSON; call(target, cmd, args) -> agent reply. Returns cards seen.

    The hub passes `issues` from its own ETag'd list call (so an idle queue costs nothing) and `report(n, text)`
    = ghq.progress, which edits one comment under the E5 breaker; without them this lists and comments itself.
    """
    if issues is None:
        issues = gh('GET', f'/repos/{repo}/issues?state=open&labels={LABEL}&per_page={limit}') or []
    for issue in issues:
        n = issue['number']
        comments = gh('GET', f'/repos/{repo}/issues/{n}/comments?per_page=100') or []
        if not any(RESULT_MARK in (c.get('body') or '') for c in comments):
            try:
                target, cmd, args = parse(issue.get('body'))
                out = call(target, cmd, args)
                text = result_text(target, cmd, out)
            except ValueError as e:
                text = f'{RESULT_MARK}\nNot run: {e}.'
            except Exception as e:  # noqa: BLE001  (unreachable peer, missing key): report, never retry blindly
                text = f'{RESULT_MARK}\nNot run: {type(e).__name__}.'
            if report:
                report(n, text)
            else:
                gh('POST', f'/repos/{repo}/issues/{n}/comments', {'body': text})
            log(f'node-cmd #{n}: {text.splitlines()[1][:120]}')
        gh('PATCH', f'/repos/{repo}/issues/{n}', {'state': 'closed', 'state_reason': 'completed'})
    return len(issues)
