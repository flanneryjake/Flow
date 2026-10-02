"""Jarvis in Discord: chat with TARS and get Jarvis alerts in Jake's server.

Runs on the homebase (the 5060 laptop) next to TARS.

- Chat: @mention the bot, DM it, or type in a channel named #jarvis. The text goes to TARS
  (POST {TARS_URL} {"text": ...}) and the reply comes back in the same channel. Only the bot's owner
  (whoever owns the Discord application) and DISCORD_ALLOWED_USERS may talk to it, because TARS can
  file cards on the task board.
- Alerts: POST http://127.0.0.1:8796/notify {"title", "body" (or "message"), "url"} posts to #jarvis-alerts.
  The watchdog's phone pushes are copied here too.
- Cards: every 2 minutes it checks flanneryjake/jarvis-tasks for cards that are newly staged (waiting for
  approval) or newly needs-jake, and posts each once in #jarvis-alerts. Staged cards without the `pin`
  label get an Approve button (owner only); pin cards still need the phone app's PIN.
- The bot creates #jarvis and #jarvis-alerts in each server it is in, if they are missing.

Token: env DISCORD_BOT_TOKEN, else C:\\Jarvis\\secrets\\discord-bot.txt. It is never logged.
"""
import asyncio
import json
import logging
import os
import sys
import urllib.request

import discord
from aiohttp import web

HOME = os.environ.get('JARVIS_DISCORD_HOME', r'C:\Jarvis\discord' if os.name == 'nt' else os.path.expanduser('~/.jarvis-discord'))
SECRET_FILE = os.environ.get('DISCORD_TOKEN_FILE', r'C:\Jarvis\secrets\discord-bot.txt')
TARS_URLS = [u for u in os.environ.get('TARS_URL', 'http://127.0.0.1:8790/chat,http://100.85.255.99:8790/chat').split(',') if u]
NOTIFY_BIND = os.environ.get('DISCORD_NOTIFY_BIND', '127.0.0.1')
NOTIFY_PORT = int(os.environ.get('DISCORD_NOTIFY_PORT', '8796'))
CHAT_CHANNEL = os.environ.get('DISCORD_CHAT_CHANNEL', 'jarvis')
ALERT_CHANNEL = os.environ.get('DISCORD_ALERT_CHANNEL', 'jarvis-alerts')
REPO = os.environ.get('JARVIS_TASKS_REPO', 'flanneryjake/jarvis-tasks')
CARD_POLL = int(os.environ.get('DISCORD_CARD_POLL', '120'))
ALLOWED_GUILDS = {int(x) for x in os.environ.get('DISCORD_ALLOWED_GUILDS', '').replace(';', ',').split(',') if x.strip().isdigit()}
STATE_FILE = os.path.join(HOME, 'state.json')
MAX_LEN = 1900  # Discord's limit is 2000 characters per message

log = logging.getLogger('jarvis-discord')


# ---------------------------------------------------------------------------- small helpers (tested)

def read_token():
    t = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
    if not t and os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, encoding='utf-8-sig') as f:
            t = f.read().strip()
    if t.lower().startswith('bot '):
        t = t[4:].strip()
    return t


def split_message(text, limit=MAX_LEN):
    """Split a long reply on line breaks (then spaces) so each piece fits in one Discord message."""
    text = (text or '').strip() or '(no reply)'
    parts = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit)
        if cut < limit // 2:
            cut = text.rfind(' ', 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


def clean_prompt(content, bot_id):
    """Drop the bot's own @mention from a message so TARS sees just the question."""
    for m in (f'<@{bot_id}>', f'<@!{bot_id}>'):
        content = content.replace(m, ' ')
    return ' '.join(content.split())


def should_answer(is_dm, mentioned, channel_name, author_allowed, is_bot):
    if is_bot or not author_allowed:
        return False
    return is_dm or mentioned or (channel_name or '').lower() == CHAT_CHANNEL


def guild_allowed(guild_id, guild_owner_id, owner_ids, allowed_guilds):
    return guild_owner_id in owner_ids or guild_id in allowed_guilds


def card_events(issues, seen):
    """New (number, status, issue) pairs worth posting: a card that became staged or needs-jake since last seen.

    `seen` maps str(number) -> the status we last posted for it. Returns (events, new_seen)."""
    events, new_seen = [], {}
    for i in issues:
        if 'pull_request' in i:
            continue
        names = [l['name'] for l in i.get('labels', [])]
        status = next((n.split(':', 1)[1] for n in names if n.startswith('status:')), '')
        if status not in ('staged', 'needs-jake'):
            continue
        key = str(i['number'])
        new_seen[key] = status
        if seen.get(key) != status:
            events.append((i['number'], status, i))
    return events, new_seen


def load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    os.makedirs(HOME, exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------------------- TARS and GitHub (blocking, run in threads)

def ask_tars(text):
    body = json.dumps({'text': text}).encode('utf-8')
    last = None
    for url in TARS_URLS:
        try:
            req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=240) as r:
                return json.loads(r.read().decode('utf-8')).get('reply') or '(TARS gave an empty reply)'
        except Exception as e:  # try the next address
            last = e
    log.warning('TARS unreachable: %s', last)
    return "I can't reach TARS on the homebase right now. It may be restarting; try again in a minute."


def gh(method, path, body=None):
    tok = os.environ.get('GITHUB_TASKS_TOKEN')
    if not tok:
        raise RuntimeError('GITHUB_TASKS_TOKEN is not set')
    req = urllib.request.Request(f'https://api.github.com{path}', method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/vnd.github+json',
                                          'User-Agent': 'jarvis-discord'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or 'null')


def open_attention_cards():
    out = []
    for label in ('status:staged', 'status:needs-jake'):
        out += gh('GET', f'/repos/{REPO}/issues?state=open&per_page=100&labels={label}') or []
    return out


def approve_card(number, who):
    for p in (os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ghq'), r'C:\Jarvis\ghq'):
        if os.path.exists(os.path.join(p, 'ghq.py')) and p not in sys.path:
            sys.path.insert(0, p)
    import ghq  # the shared queue client: same labels, comment and Worker wake as the phone app
    issue = ghq.api('GET', ghq.repo_path(f'/issues/{number}'))
    if 'pin' in ghq.label_names(issue):
        return False, 'This card needs the PIN, so approve it in the phone app.'
    ghq.approve(number, by=f'Discord ({who})')
    return True, f'Approved #{number}. A Worker will pick it up.'


# ---------------------------------------------------------------------------- the bot

class ApproveView(discord.ui.View):
    """Buttons for a card. custom_id carries the number, so a click still works after the bot restarts."""

    def __init__(self, number=None, url=None, can_approve=True):
        super().__init__(timeout=None)
        if number is None:
            return
        if can_approve:
            self.add_item(discord.ui.Button(label='Approve', style=discord.ButtonStyle.success,
                                            custom_id=f'jarvis:approve:{number}'))
        if url:
            self.add_item(discord.ui.Button(label='Open', style=discord.ButtonStyle.link, url=url))


class JarvisBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.allowed = {int(x) for x in os.environ.get('DISCORD_ALLOWED_USERS', '').replace(';', ',').split(',') if x.strip().isdigit()}
        self.owner_ids = set()
        self.state = load_state()
        self.web_runner = None

    # --- startup
    async def setup_hook(self):
        info = await self.application_info()
        owner = info.team.members if info.team else [info.owner]
        self.owner_ids = {u.id for u in owner}
        self.allowed |= self.owner_ids
        app = web.Application()
        app.router.add_post('/notify', self.http_notify)
        app.router.add_get('/health', self.http_health)
        self.web_runner = web.AppRunner(app)
        await self.web_runner.setup()
        await web.TCPSite(self.web_runner, NOTIFY_BIND, NOTIFY_PORT).start()
        self.loop.create_task(self.card_loop())

    async def on_ready(self):
        log.info('Logged in as %s in %d server(s)', self.user, len(self.guilds))
        for g in list(self.guilds):
            if await self.keep_guild(g):
                await self.ensure_channels(g)

    async def on_guild_join(self, guild):
        if await self.keep_guild(guild):
            await self.ensure_channels(guild)

    async def keep_guild(self, guild):
        """The bot is public in the Developer Portal, so anyone with the invite link could add it. It stays only in
        servers Jake owns (or DISCORD_ALLOWED_GUILDS), so card titles and alerts never land in someone else's server."""
        if guild_allowed(guild.id, guild.owner_id, self.owner_ids, ALLOWED_GUILDS):
            return True
        log.warning('Leaving server %s (%s): not owned by Jake', guild.name, guild.id)
        try:
            await guild.leave()
        except discord.HTTPException:
            pass
        return False

    async def ensure_channels(self, guild):
        for name, topic in ((CHAT_CHANNEL, 'Talk to Jarvis here (no @ needed).'),
                            (ALERT_CHANNEL, 'Jarvis alerts and cards waiting for approval.')):
            if not discord.utils.get(guild.text_channels, name=name):
                try:
                    await guild.create_text_channel(name, topic=topic, reason='Jarvis setup')
                    log.info('Created #%s in %s', name, guild.name)
                except discord.Forbidden:
                    log.warning('No permission to create #%s in %s', name, guild.name)

    def alert_channels(self):
        return [c for g in self.guilds if guild_allowed(g.id, g.owner_id, self.owner_ids, ALLOWED_GUILDS)
                for c in g.text_channels if c.name == ALERT_CHANNEL]

    async def send_alert(self, content=None, embed=None, view=None):
        sent = 0
        for c in self.alert_channels():
            try:
                kw = {'content': content, 'embed': embed}
                if view is not None:
                    kw['view'] = view
                await c.send(**kw)
                sent += 1
            except discord.HTTPException as e:
                log.warning('Alert to #%s failed: %s', c.name, e)
        if not sent and self.owner_ids:  # no server channel yet: DM the owner instead
            try:
                user = await self.fetch_user(next(iter(self.owner_ids)))
                kw = {'content': content, 'embed': embed}
                if view is not None:
                    kw['view'] = view
                await user.send(**kw)
                sent = 1
            except discord.HTTPException as e:
                log.warning('Alert DM failed: %s', e)
        return sent

    # --- chat
    async def on_message(self, message):
        if self.user is None or message.author.id == self.user.id:
            return
        is_dm = message.guild is None
        mentioned = self.user in message.mentions
        if not should_answer(is_dm, mentioned, getattr(message.channel, 'name', ''),
                             message.author.id in self.allowed, message.author.bot):
            if mentioned and not message.author.bot and message.author.id not in self.allowed:
                await message.reply("Sorry, I only take requests from Jake.", mention_author=False)
            return
        text = clean_prompt(message.content, self.user.id)
        if not text:
            await message.reply('Yes?', mention_author=False)
            return
        async with message.channel.typing():
            reply = await asyncio.to_thread(ask_tars, text)
        for i, part in enumerate(split_message(reply)):
            if i == 0:
                await message.reply(part, mention_author=False)
            else:
                await message.channel.send(part)

    # --- buttons
    async def on_interaction(self, interaction):
        cid = (interaction.data or {}).get('custom_id', '')
        if not cid.startswith('jarvis:approve:'):
            return
        if interaction.user.id not in self.owner_ids:
            await interaction.response.send_message('Only Jake can approve cards.', ephemeral=True)
            return
        number = int(cid.rsplit(':', 1)[1])
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ok, msg = await asyncio.to_thread(approve_card, number, interaction.user.name)
        except Exception as e:
            ok, msg = False, f'Approving #{number} failed: {e}'
        await interaction.followup.send(msg, ephemeral=True)
        if ok:
            try:
                emb = interaction.message.embeds[0] if interaction.message.embeds else None
                if emb:
                    emb.color = discord.Color.green()
                    emb.set_footer(text=f'Approved by {interaction.user.name}')
                await interaction.message.edit(embed=emb, view=ApproveView(number, f'https://github.com/{REPO}/issues/{number}', can_approve=False))
            except discord.HTTPException:
                pass

    # --- card watcher
    async def card_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self.check_cards()
            except Exception as e:
                log.warning('Card check failed: %s', e)
            await asyncio.sleep(CARD_POLL)

    async def check_cards(self):
        issues = await asyncio.to_thread(open_attention_cards)
        first_run = 'seen' not in self.state
        events, new_seen = card_events(issues, self.state.get('seen', {}))
        if first_run:
            staged = sum(1 for _, s, _ in events if s == 'staged')
            needs = len(events) - staged
            sent = await self.send_alert(f'Jarvis is connected. Right now {staged} card(s) are waiting for approval and '
                                         f'{needs} need an answer from you: https://github.com/{REPO}/issues')
            if not sent:
                return  # not in a server yet: say hello on a later check instead of losing it
        else:
            for number, status, issue in events[:10]:  # a burst (e.g. the nightly batch) posts 10 and a summary
                await self.post_card(number, status, issue)
            if len(events) > 10:
                await self.send_alert(f'...and {len(events) - 10} more: https://github.com/{REPO}/issues?q=is%3Aopen+label%3Astatus%3Astaged')
        self.state['seen'] = new_seen
        save_state(self.state)

    async def post_card(self, number, status, issue):
        names = [l['name'] for l in issue.get('labels', [])]
        url = issue.get('html_url') or f'https://github.com/{REPO}/issues/{number}'
        if status == 'staged':
            title, color = f'Waiting for approval: #{number}', discord.Color.gold()
        else:
            title, color = f'Needs you: #{number}', discord.Color.orange()
        emb = discord.Embed(title=title, description=f"**{issue.get('title', '')}**", url=url, color=color)
        tags = ', '.join(n for n in names if not n.startswith('status:'))
        if tags:
            emb.set_footer(text=tags[:200])
        can = status == 'staged' and 'pin' not in names
        await self.send_alert(embed=emb, view=ApproveView(number, url, can_approve=can))

    # --- local HTTP
    async def http_notify(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'ok': False, 'error': 'send JSON'}, status=400)
        title = str(data.get('title') or 'Jarvis')[:250]
        body = str(data.get('body') or data.get('message') or '')[:4000]
        emb = discord.Embed(title=title, description=body or None, color=discord.Color.blue())
        url = data.get('url')
        if isinstance(url, str) and url.startswith('http'):
            emb.url = url
        sent = await self.send_alert(embed=emb)
        return web.json_response({'ok': sent > 0, 'channels': sent})

    async def http_health(self, request):
        return web.json_response({'ok': self.is_ready(), 'user': str(self.user), 'servers': len(self.guilds),
                                  'alert_channels': len(self.alert_channels())})


def main():
    os.makedirs(HOME, exist_ok=True)
    logging.basicConfig(filename=os.path.join(HOME, 'discord.log'), level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    token = read_token()
    if not token:
        log.error('No Discord token: set DISCORD_BOT_TOKEN or put it in %s', SECRET_FILE)
        sys.exit(2)
    bot = JarvisBot()
    bot.run(token, log_handler=None)


if __name__ == '__main__':
    main()
