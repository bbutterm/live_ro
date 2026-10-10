"""Approved, evidence-bound speech, independent of motor utility selection.

context: now/server_time, state (snapshot), mental (mutable persisted state),
events (actual server envelopes). Persist mental rng_index after each call.
Recent lines/speech_history are echoed sent lines, not generated proposals.
"""
from datetime import datetime, timezone
import hashlib
import random
import re


TEMPLATES = {
    'tired': ('Всё, пауза.', 'Сек, устала.', 'Устал. Не спешим.'),
    'died': ('Лёг. Жадность подвела.', 'Ой, легла.', 'Лёг. Надо осторожнее.'),
    'level_up': ('Ап! почти герой', 'Ну вот, ап', 'Апнул. Потихоньку.'),
    'rude': ('Без наездов.', 'Можно без этого?', 'Давай без грубости.'),
    'bot': ('Ага, голова ИИ-шная', 'Да, я ИИ-житель', 'Да, я ИИ-житель.'),
    'ordinary': ('Ну вот.', 'Сек.', 'Ладно, понял.'),
}


def _time(value):
    if isinstance(value, (float, int)):
        return float(value)
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def _parts(context):
    return context.get('mental', context), context.get('state', context.get('snapshot', {}))


def _now(context):
    return _time(context.get('now', context.get('server_time', 0)))


def _rng(rid, context):
    mental, _ = _parts(context)
    index = mental.get('rng_index', 0)
    date = datetime.fromtimestamp(_now(context), timezone.utc).date().isoformat()
    seed = int.from_bytes(hashlib.sha256(f'{rid}|{date}|{index}|speech'.encode()).digest()[:8], 'big')
    mental['rng_index'] = index+1
    return random.Random(seed)


def _profile(config, rid):
    return {**config.get('speech', {}).get('defaults', {}), **config.get('residents', {}).get(rid, {}).get('speech', {})}


def _heard(context):
    events = context.get('events', [])
    return next((e for e in reversed(events) if e.get('type') == 'heard' and e.get('event_id')), None)


def _bot(context):
    heard = _heard(context)
    return bool(heard and re.search(r'\b(?:бот|ии|bot|ai)\b', heard.get('data', {}).get('text', ''), re.I))


def build_lines(config, rid, context, kind='ordinary'):
    mental, state = _parts(context)
    rng = _rng(rid, context)
    profile = _profile(config, rid)
    tired = mental.get('drives', {}).get('tired', 0)
    maximum = 35 if tired >= 65 else min(80, profile.get('max_chars', 80))
    person = 0 if rid == 'resident_a' else 1 if rid == 'mira' else 2
    heard = _heard(context)
    addressed = heard.get('data', {}) if heard else {}
    channel = addressed.get('channel', 'public')
    target = addressed.get('from') if channel == 'whisper' else None
    rows = []
    def add(template, text, refs=(), promise=None):
        if len(text) <= maximum:
            rows.append({'text': text, 'template_id': template, 'fact_refs': list(refs), 'channel': channel, 'to': target, 'promise_action_id': promise})
    if _bot(context):
        add('bot', TEMPLATES['bot'][person], [heard['event_id']])
    else:
        if tired >= 65:
            add('tired', TEMPLATES['tired'][person])
        # These claims require actual server event envelopes, never narrative memory.
        for event in context.get('events', []):
            if event.get('event_id') and event.get('type') in ('died', 'level_up'):
                if 'server_time' not in event or 0 <= _now(context)-_time(event['server_time']) <= 120:
                    name = event['type']
                    add(name, TEMPLATES[name][person], [event['event_id']])
        if heard:
            text = addressed.get('text', '')
            if re.search(r'\b(?:дурак|тупой|заткнись)\b', text, re.I):
                add('rude', TEMPLATES['rude'][person], [heard['event_id']])
            price = re.search(r'\b([0-9]{1,8})\s*(?:зени|zeny)\b', text, re.I)
            if price:
                add('price', f"За {price[1]} подумаю.", [heard['event_id']])
        action = context.get('accepted_action')
        if action and action.get('accepted') is True and action.get('action_id') and action.get('result') not in ('done', 'failed', 'timeout'):
            if action.get('action') == 'hunt' and action.get('args', {}).get('map') == 'prt_fild08':
                add('promise:hunt', 'Иду к порингам за лутом.', promise=action['action_id'])
        add('unknown', 'Не знаю.')
        add('ordinary', TEMPLATES['ordinary'][person])
        # Trait themes are intentions/questions, not claims about world state.
        traits = config.get('residents', {}).get(rid, {}).get('traits', {})
        descriptions = config.get('traits', {})
        themes = [(name, .2+traits.get(name, .5)) for name in descriptions]
        if themes:
            name = rng.choices([p[0] for p in themes], [p[1] for p in themes])[0]
            topic = descriptions[name].get('description', '')
            # Only approved labels; custom descriptions cannot inject unsafe text.
            approved = {'greed': 'Как там зени?', 'ambition': 'Поговорим про кач?', 'stamina': 'Длинный кач?', 'laziness': 'Может, пауза?', 'social': 'Поговорим?', 'courage': 'Риск — дело тонкое.', 'giving': 'Помощь нужна?', 'curious': 'Какие места любишь?', 'vanity': 'Признание приятно.', 'patience': 'Не спешим.', 'talk': 'Поговорим?', 'humor': 'Немного отвлечёмся?', 'chronotype': 'Когда отдыхаешь?'}
            if topic and name in approved:
                add('theme:'+name, approved[name])
    recent = mental.get('recent_lines', [])[-5:]
    recent = {x.get('text') if isinstance(x, dict) else x for x in recent}
    # Recognition must not be suppressed by repetition filtering.
    rows = [r for r in rows if r['template_id'] == 'bot' or r['text'] not in recent]
    # Keep priority bot/tired/event; deterministic variety elsewhere.
    if not _bot(context):
        head = [r for r in rows if r['template_id'] not in ('ordinary', 'unknown')]
        tail = [r for r in rows if r['template_id'] in ('ordinary', 'unknown')]
        rng.shuffle(tail)
        rows = head+tail
    return rows


def combat_proxy(context):
    _, state = _parts(context)
    action = state.get('current_action') or {}
    if action.get('action') == 'hunt' and state.get('near_monsters'):
        return True
    history = context.get('hp_history', [])
    now = _now(context)
    hp = state.get('hp')
    return hp is not None and any(0 <= now-_time(row['server_time']) <= 10 and row.get('hp', hp) > hp for row in history)


def apply_style(config, rid, context, line):
    mental, state = _parts(context)
    rng = _rng(rid, context)
    profile = _profile(config, rid)
    tired = mental.get('drives', {}).get('tired', 0)
    mood = mental.get('active_mood', 'calm')
    limit = 35 if tired >= 65 else min(80, profile.get('max_chars', 80))
    record = {**line, 'delay_s': 0, 'skipped': False, 'skip_reason': None}
    combat = combat_proxy(context)
    heard = _heard(context)
    bot = line['template_id'] == 'bot'
    reason = None
    now = _now(context)
    if heard and 'server_time' in heard and now-_time(heard['server_time']) > 60:
        reason = 'expired'
    elif not bot:
        history = mental.get('speech_history', [])
        if heard and any(row.get('sender', row.get('to')) == heard['data'].get('from') and 0 <= now-_time(row['server_time']) < 45 for row in history):
            reason = 'sender_cooldown'
        if context.get('resident_pair'):
            pair = context['resident_pair']
            recent = [r for r in history if r.get('pair') == pair and now-_time(r['server_time']) < 3600]
            if len(recent) >= 4:
                reason = 'pair_hour_limit'
            elif context.get('reply_hops', 0) >= 2 or now-mental.get('pair_pause_until', {}).get(pair, 0) < 0:
                reason = 'pair_pause'
        if not heard:
            if state.get('sleeping') or mental.get('derived', {}).get('sleeping'):
                reason = 'sleeping'
            elif now-mental.get('last_spontaneous', -1e9) < 720:
                reason = 'spontaneous_cooldown'
            else:
                traits = config.get('residents', {}).get(rid, {}).get('traits', {})
                p = min(.3, max(0, .04+.18*traits.get('talk', .5)+.08*traits.get('humor', .5)))
                if mood in ('scared', 'irritated'):
                    p *= .5
                if rng.random() >= p:
                    reason = 'no_initiation'
        elif combat and rng.random() < profile.get('skip_reply_in_combat_rate', .35):
            reason = 'combat'
    if reason:
        record.update(skipped=True, skip_reason=reason)
        return record
    delay = rng.uniform(*profile.get('reply_delay_s', [3, 12]))*(1+.8*tired/100)*(1.5 if combat else 1)
    if bot and heard and 'server_time' in heard:
        delay = min(delay, max(0, 60-(now-_time(heard['server_time']))))
    record['delay_s'] = min(40, delay)
    text = line['text']
    # Never corrupt evidence, names, numbers or promises. Typos only safe filler.
    protected = bool(line.get('fact_refs') or line.get('promise_action_id') or re.search(r'\d', text))
    if not protected and rng.random() < profile.get('slang_rate', 0):
        prefix = rng.choice(('ну', 'ладно') if rid == 'resident_a' else ('ой', 'сек') if rid == 'mira' else ('ладно', 'не спеши', ''))
        styled = (prefix+', '+text[0].lower()+text[1:]) if prefix else text
        if len(styled) <= limit:
            text = styled
    if not protected and rng.random() < min(1, profile.get('typo_rate', .02)*(1.5 if tired >= 65 else 1)):
        # One dropped letter in an explicitly approved nonfactual word only.
        text = text.replace('Ладно', 'Ладн', 1) if 'Ладно' in text else text.replace('ладно', 'ладн', 1)
    if mood not in ('scared', 'irritated') and rng.random() < profile.get('emoji_rate', .15) and len(text)+1 <= limit:
        text += ')'
    record['text'] = text
    recent = {r.get('text') if isinstance(r, dict) else r for r in mental.get('recent_lines', [])[-5:]}
    if not bot and text in recent:
        record.update(skipped=True, skip_reason='recent_repeat')
    if len(text) > limit:
        record.update(skipped=True, skip_reason='length')
    return record
