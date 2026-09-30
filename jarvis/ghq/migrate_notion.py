"""Copy the Notion Tasks board into the GitHub tasks repo, one issue per card.

Dry run by default: prints what it would create. Add --apply to create the issues.
Safe to re-run: each issue carries a hidden marker with its Notion page id, and cards already copied are
skipped (or refreshed with --update). Notion is only read, never changed.

Needs NOTION_TOKEN and GITHUB_TASKS_TOKEN (see ghq.py).
  python migrate_notion.py              # dry run, open cards only
  python migrate_notion.py --apply      # create the issues
  python migrate_notion.py --apply --include-done
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

import ghq

TASKS_DB = '7c1c59e927644dfba461c88a67dbd32c'
STATUS_MAP = {'Inbox': 'inbox', 'Staged': 'staged', 'Approved': 'approved', 'In progress': 'approved',
              'Snoozed': 'snoozed'}  # Done -> closed issue; an old In progress claim is re-queued


def notion(method, path, body=None):
    req = urllib.request.Request('https://api.notion.com/v1/' + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header('Authorization', 'Bearer ' + os.environ['NOTION_TOKEN'])
    req.add_header('Notion-Version', '2022-06-28')
    req.add_header('Content-Type', 'application/json')
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:  # Notion allows about 3 requests a second
                time.sleep(float(e.headers.get('Retry-After', 2)))
                continue
            raise


def plain(rich):
    return ''.join(t.get('plain_text', '') for t in rich or [])


def prop(page, name):
    p = page['properties'].get(name) or {}
    t = p.get('type')
    if t == 'title':
        return plain(p['title'])
    if t == 'rich_text':
        return plain(p['rich_text'])
    if t == 'select':
        return (p['select'] or {}).get('name', '')
    if t == 'checkbox':
        return p['checkbox']
    if t == 'date':
        return (p['date'] or {}).get('start', '')
    if t == 'relation':
        return [r['id'].replace('-', '') for r in p['relation']]
    return ''


def page_text(page_id, limit=20000):
    """The card's page content as rough Markdown (text blocks only; files and embeds become links)."""
    out, cursor = [], None
    while True:
        q = f'blocks/{page_id}/children?page_size=100' + (f'&start_cursor={cursor}' if cursor else '')
        res = notion('GET', q)
        for b in res['results']:
            t = b['type']
            v = b.get(t, {})
            text = plain(v.get('rich_text'))
            prefix = {'heading_1': '# ', 'heading_2': '## ', 'heading_3': '### ', 'bulleted_list_item': '- ',
                      'numbered_list_item': '1. ', 'quote': '> ', 'callout': '> '}.get(t, '')
            if t == 'to_do':
                prefix = '- [x] ' if v.get('checked') else '- [ ] '
            if t == 'code':
                out.append(f"```{v.get('language', '')}\n{text}\n```")
            elif t in ('image', 'file', 'pdf', 'video', 'bookmark', 'embed'):
                url = (v.get('external') or v.get('file') or {}).get('url') or v.get('url', '')
                out.append(f'[{t}]({url})' if t in ('bookmark', 'embed') else f'({t} attached in Notion)')
            elif t == 'divider':
                out.append('---')
            elif text or prefix:
                out.append(prefix + text)
        if not res.get('has_more') or sum(len(x) for x in out) > limit:
            break
        cursor = res['next_cursor']
    return '\n'.join(out)[:limit]


def all_cards():
    cards, cursor = [], None
    while True:
        body = {'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        res = notion('POST', f'databases/{TASKS_DB}/query', body)
        cards += res['results']
        if not res.get('has_more'):
            return cards
        cursor = res['next_cursor']


def build(page, number_by_notion):
    status = prop(page, 'Status') or 'Inbox'
    notes = prop(page, 'Notes')
    labels = []
    if status != 'Done':
        s = STATUS_MAP.get(status, 'inbox')
        if notes.upper().startswith('NEEDS JAKE') and s == 'approved':
            s = 'needs-jake'
        labels.append(f'status:{s}')
    labels.append('machine:' + (prop(page, 'Machine') or 'Any').lower())
    if prop(page, 'Priority'):
        labels.append(prop(page, 'Priority').lower())
    if prop(page, 'Type'):
        labels.append('type:' + prop(page, 'Type').lower())
    if prop(page, 'Owner') == 'Jake':
        labels.append('owner:jake')
    if prop(page, 'Approval code'):  # the code itself is not copied
        labels.append('pin')
    project = prop(page, 'Project')
    if project:
        labels.append('project:' + project.lower().replace(' ', '-'))

    pid = page['id'].replace('-', '')
    meta = {'notion_id': pid}
    lines = [f'<!-- jarvis:meta {json.dumps(meta)} -->']
    if notes:
        lines += ['**Notes:** ' + notes, '']
    if prop(page, 'Due'):
        lines += [f"**Due:** {prop(page, 'Due')}", '']
    parents = [f'#{number_by_notion[p]}' if p in number_by_notion else f'https://app.notion.com/p/{p}'
               for p in prop(page, 'Spawned from')]
    if parents:
        lines += ['Spawned from ' + ', '.join(parents), '']
    content = page_text(page['id'])
    if content:
        lines += [content, '']
    log = prop(page, 'Agent log')
    if log:
        lines += ['<details><summary>Last Agent log (from Notion)</summary>', '', '```',
                  log.replace('```', "'''"), '```', '', '</details>', '']
    lines.append(f'Copied from Notion: https://app.notion.com/p/{pid}')
    return {'title': prop(page, 'Task') or '(untitled)', 'body': '\n'.join(lines)[:60000], 'labels': labels,
            'closed': status == 'Done'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='create issues (default is a dry run)')
    ap.add_argument('--include-done', action='store_true')
    ap.add_argument('--update', action='store_true', help='refresh issues copied earlier')
    a = ap.parse_args()

    existing = {}
    for i in ghq.paged(ghq.repo_path('/issues?state=all')):
        nid = ghq.meta_of(i).get('notion_id')
        if nid:
            existing[nid] = i['number']
    cards = [c for c in all_cards() if a.include_done or prop(c, 'Status') != 'Done']
    # parents first, so "Spawned from" can point at the new issue number
    cards.sort(key=lambda c: (bool(prop(c, 'Spawned from')), c['created_time']))
    print(f'{len(cards)} Notion cards, {len(existing)} already in {ghq.REPO}')

    if a.apply:  # make sure project:* labels exist
        have = {l['name'] for l in ghq.paged(ghq.repo_path('/labels'))}
        for c in cards:
            pr = prop(c, 'Project')
            name = 'project:' + pr.lower().replace(' ', '-') if pr else None
            if name and name not in have:
                ghq.api('POST', ghq.repo_path('/labels'), {'name': name, 'color': 'c2e0c6'})
                have.add(name)

    created = skipped = 0
    for c in cards:
        pid = c['id'].replace('-', '')
        if pid in existing and not a.update:
            skipped += 1
            continue
        issue = build(c, existing)
        if not a.apply:
            print(f"  would {'update' if pid in existing else 'create'}: {issue['title'][:70]}  {issue['labels']}")
            continue
        if pid in existing:
            ghq.api('PATCH', ghq.repo_path(f'/issues/{existing[pid]}'),
                    {'title': issue['title'], 'body': issue['body'], 'labels': issue['labels']})
            continue
        new = ghq.api('POST', ghq.repo_path('/issues'),
                      {'title': issue['title'], 'body': issue['body'], 'labels': issue['labels']})
        existing[pid] = new['number']
        if issue['closed']:
            ghq.api('PATCH', ghq.repo_path(f"/issues/{new['number']}"), {'state': 'closed', 'state_reason': 'completed'})
        created += 1
        print(f"  #{new['number']} {issue['title'][:70]}")
        time.sleep(1)  # GitHub asks for about 1 s between content-creating calls
    print(f'created {created}, skipped {skipped} already copied' + ('' if a.apply else ' (dry run: add --apply)'))


if __name__ == '__main__':
    try:
        main()
    except KeyError as e:
        sys.exit(f'missing environment variable {e}')
