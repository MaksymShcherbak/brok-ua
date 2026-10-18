#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qa_check.py -- comprehensive automated QA for the BROK: The InvestiGator
Ukrainian translation.

One pass over the corpus (brok/*.txt + brokvn/*.txt, or UA/*.txt in the old layout) and the four reference docs
(guide.md, glossary.md, formality.md, common-errors.md), producing a
severity-ranked, capped report meant to be read by a human or by a model:

    python D:/brok-ua/tools/qa_check.py
    python D:/brok-ua/tools/qa_check.py --file TPC15D --limit 15
    python D:/brok-ua/tools/qa_check.py --only russian,structure
    python D:/brok-ua/tools/qa_check.py --json qa.json      # uncapped, machine-readable
    python D:/brok-ua/tools/qa_check.py --list-rules

Severities
    ERROR  a rule of the guide or a rendering invariant is certainly broken
    WARN   almost certainly a defect; read the line before changing it
    INFO   candidate for a second review (deliberate choices land here)

Exit code: 1 when any ERROR was found, else 0 (--fail-on warn|never changes
that).  Read-only: this script never writes to the repository.

Model of the corpus
    # comment                      ignored (speaker, banner, NB, IMPORTANT)
    ### <english>                  the source line
    KEY=value                      the translation; value may hold <col=...>,
                                   <spr=...>, <button=...>, {placeholder},
                                   and # / ## runs that ARE line breaks
    Files are UTF-8 + BOM + CRLF.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import unicodedata

# --------------------------------------------------------------------------
# rule table: id -> (severity, category, one-line description)
# --------------------------------------------------------------------------
RULES = collections.OrderedDict([
    # -- file integrity ----------------------------------------------------
    ('file-bom', ('ERROR', 'integrity', 'file has no UTF-8 BOM')),
    ('file-eol', ('ERROR', 'integrity', 'bare LF line ending (export is CRLF)')),
    ('file-noeol', ('WARN', 'integrity', 'file does not end with a newline')),
    ('file-dupkey', ('ERROR', 'integrity', 'the same KEY appears twice in one file')),
    ('file-noenglish', ('WARN', 'integrity', 'KEY has no "###" English line above it')),
    ('file-orphanenglish', ('WARN', 'integrity', '"###" English line has no KEY under it')),
    ('file-backslash', ('ERROR', 'integrity', 'stray ASCII backslash in a value')),
    ('file-todo', ('WARN', 'integrity', 'TODO / placeholder left in a value')),

    # -- untranslated ------------------------------------------------------
    ('untranslated-empty', ('ERROR', 'untranslated', 'empty value')),
    ('untranslated-copy', ('ERROR', 'untranslated', 'value is the English line copied verbatim')),

    # -- russian -----------------------------------------------------------
    ('rus-letter', ('ERROR', 'russian', 'Russian-only letter (ы э ъ ё) or a foreign Cyrillic letter')),
    ('rus-word', ('ERROR', 'russian', 'Russian word form, not Ukrainian')),
    ('rus-calque', ('WARN', 'russian', 'Russian calque listed in common-errors.md or its family')),

    # -- latin / english inside the text ------------------------------------
    ('lat-homoglyph', ('ERROR', 'latin', 'Latin letter inside a Cyrillic word (mixed script)')),
    ('lat-englishword', ('WARN', 'latin', 'English function word inside a Ukrainian sentence')),
    ('lat-unapproved', ('INFO', 'latin', 'Latin token not approved by the glossary or the brand list')),
    ('lat-nocyrillic', ('WARN', 'latin', 'value holds no Ukrainian text (English left in a slot)')),

    # -- characters and typography ------------------------------------------
    ('char-disallowed', ('ERROR', 'chars', 'character outside the allowed set')),
    ('char-quote', ('ERROR', 'chars', 'typographic quote or apostrophe (plain " and \' only)')),
    ('char-dash', ('ERROR', 'chars', 'typographic dash or minus (plain hyphen only)')),
    ('char-ellipsis', ('ERROR', 'chars', 'ellipsis character instead of three dots')),
    ('char-space-before', ('ERROR', 'chars', 'space before , : ; or a sentence end') ),
    ('char-double-space', ('WARN', 'chars', 'double space')),
    ('char-doubled-word', ('WARN', 'chars', 'the same word twice in a row (typing slip)')),
    ('char-euphony', ('INFO', 'chars', 'в+в / у+у / і+і / й+й — the guide asks for euphony')),
    ('char-space-hash', ('WARN', 'chars', 'space before a #/## line break')),
    ('char-space-after', ('WARN', 'chars', 'missing space after , : . or ;')),
    ('char-ellipsis-order', ('ERROR', 'chars', 'ellipsis written after ? or ! (...? / ...!)')),
    ('char-quote-balance', ('WARN', 'chars', 'odd number of double quotes')),
    ('char-paren-balance', ('WARN', 'chars', 'unbalanced parentheses')),
    ('char-religious', ('INFO', 'chars', 'religious word (guide section 5 bans Бог/Господь in exclamations)')),
    ('char-capital', ('WARN', 'chars', 'common noun capitalised mid-sentence')),
    ('char-lowerstart', ('WARN', 'chars', 'value starts lowercase while the English starts with a capital')),
    ('char-whitespace', ('WARN', 'chars', 'leading/trailing space, NBSP or tab in a value')),

    # -- structure against the English --------------------------------------
    ('st-hash-runs', ('ERROR', 'structure', '#/## run count differs from the English (line breaks)')),
    ('st-tags', ('ERROR', 'structure', 'markup tag set differs from the English')),
    ('st-placeholders', ('ERROR', 'structure', '{placeholder} set differs from the English')),
    ('st-digits', ('INFO', 'structure', 'English 3+ digit number has no counterpart in the value')),
    ('st-finalpunct-missing', ('WARN', 'structure', 'value lacks the English final . ! ?')),
    ('st-finalpunct-added', ('INFO', 'structure', 'value adds a final . ! ? the English does not have')),
    ('st-length', ('INFO', 'structure', 'value length far from the English (possible dropped/added content)')),
    ('st-sentences', ('INFO', 'structure', 'English has 3+ sentences, value has 1')),

    # -- consistency --------------------------------------------------------
    ('cons-within', ('INFO', 'consistency', 'same English line rendered two ways inside one file')),
    ('cons-cross', ('WARN', 'consistency', 'same English line rendered differently from the rest of the corpus')),
    ('cons-key', ('INFO', 'consistency', 'same KEY has different values in two files')),
    ('st-mirror-q', ('WARN', 'structure', 'English ends ?! but the value does not')),
    ('st-mirror-cue', ('WARN', 'structure', 'English cue in parentheses, value lost the parentheses')),

    # -- register and speakers ----------------------------------------------
    ('reg-street', ('WARN', 'register', 'street-speaking character uses the standard form here')),
    ('reg-uniform', ('WARN', 'register', 'speaker with a uniform «ви» rule uses a «ти» form')),

    # -- reference documents ------------------------------------------------
    ('doc-table', ('WARN', 'docs', 'markdown table row has the wrong number of columns')),
    ('doc-dup-original', ('WARN', 'docs', 'glossary: same English term defined twice in one section')),
    ('doc-dup-sense', ('INFO', 'docs', 'glossary: same English term in two sections (sense split)')),
    ('doc-dup-pair', ('WARN', 'docs', 'formality: same speaker/addressee pair listed twice')),
    ('doc-empty-cell', ('WARN', 'docs', 'reference table row has an empty required cell')),
    ('doc-dead-term', ('INFO', 'docs', 'glossary term whose Ukrainian form never appears in the corpus')),
    ('doc-casing', ('WARN', 'docs', 'formality.md and glossary.md disagree on a name casing')),
    ('doc-name-missing', ('INFO', 'docs', 'character of formality.md has no row in the glossary')),
    ('doc-link', ('WARN', 'docs', 'reference document links to a file that does not exist')),
    ('doc-guide-claim', ('INFO', 'docs', 'guide statement the corpus does not follow')),
    ('doc-addressee', ('INFO', 'docs', 'formality.md: addressee has no section of its own')),
    ('doc-common-errors', ('WARN', 'docs', 'common-errors.md: duplicate or empty row')),
    ('doc-progress-file', ('WARN', 'docs', 'progress.md and UA/ disagree about which files exist')),
    ('doc-progress-status', ('WARN', 'docs', 'progress.md status contradicts the key counts')),

    # -- terminology --------------------------------------------------------
    ('term-nameform', ('INFO', 'terms', 'character name line does not use the glossary form')),
])

# --------------------------------------------------------------------------
# character sets and shared regexes
# --------------------------------------------------------------------------
UA_LOWER = 'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя'
UA_UPPER = UA_LOWER.upper()
UA_ALPHA = UA_LOWER + UA_UPPER
APOS = "'"
UA_WORD_CHARS = UA_ALPHA + APOS + '-'
PUNCT_TIGHT_RE = re.compile(r'[%s][,;:][%s]' % (UA_ALPHA, UA_ALPHA))


def ua_word(pat):
    """Wrap a pattern so it matches whole Ukrainian words only."""
    return re.compile('(?<![%s])%s(?![%s])' % (re.escape(UA_WORD_CHARS), pat, re.escape(UA_WORD_CHARS)))


KEY_RE = re.compile(r'^([A-Za-z0-9_.\-|]+)=(.*)$')
TAG_RE = re.compile(r'<[^<>]+>')
PLACEHOLDER_RE = re.compile(r'\{[^{}]*\}')
HASH_RUN_RE = re.compile(r'#+')

# values of these key families are UI control names / debug console strings:
# they are English on purpose and are exempt from the latin and digit checks
KEY_EXEMPT = re.compile(r'^(INPUT_|GAMEPAD_|KEY_|INTERFACE_DEBUG_|ACCESSIBILITY_)')
# ACCESS.txt turns key names into narrator text; the keys stay Latin
ACCESS_KEY_EXEMPT = re.compile(r'^(INPUT_|GAMEPAD_|KEY_|INTERFACE_DEBUG_)')

# --- Russian-only word forms (none of them can be a Ukrainian word) --------
RU_WORDS = """
что чтобы если тоже сейчас очень будет можно нужно только даже ничего нет
было был была были быть они она его это этот эта эти ещё еще разве здесь
спасибо привет пожалуйста человек город дверь жизнь время деньги думаешь
знаешь говоришь хорошо плохо надо значит тебя меня когда где может
должен должна должны после между кроме около вокруг почему всегда никогда
иногда опять снова вместе конечно наверное возможно который которая которые
куда откуда зачем лучше хуже больше меньше почти совсем раньше позже
вместо против чей чья чьё чьи чем
""".split()

RU_LETTERS = re.compile('[ыэъёЫЭЪЁћђљњџўјЂЋЉЊЏЎЈ]')

STREET_FORMS = ['шо', 'ше', 'шоб', 'шось', 'нашо', 'нішо', 'якшо']
STANDARD_FORMS_OF_STREET = ['що', 'щоб', 'щось', 'нащо', 'ніщо', 'якщо', 'немає']

RU_WORD_RES = [(w, ua_word(re.escape(w))) for w in RU_WORDS]
STREET_RES = [(w, ua_word(re.escape(w))) for w in
              ['шо', 'ше', 'шоб', 'шось', 'нашо', 'нішо', 'якшо']]
STANDARD_RES = [(w, ua_word(re.escape(w))) for w in STANDARD_FORMS_OF_STREET]

# --- calques: (id, regex, short why) --------------------------------------
CALQUES = [
    ('yavlyayetsya', r"(?<![А-Яа-яІіЇїЄєҐґ'’])являєть?ся\b|(?<![А-Яа-яІіЇїЄєҐґ'’])являються\b|"
                    r"(?<![А-Яа-яІіЇїЄєҐґ'’])являвся\b|(?<![А-Яа-яІіЇїЄєҐґ'’])являлася\b|"
                    r"(?<![А-Яа-яІіЇїЄєҐґ'’])являє собою\b",
     '«являється» is a Russian calque; use «є», «це»'),
    ('pryimaty-uchast', r'\bприйм\w* (участь|участі)\b', '«приймати участь» -> «брати участь»'),
    ('na-protyazi', r'\bна протязі\b', '«на протязі» -> «протягом»'),
    ('v-zalezhnosti', r'\b[ву] залежності\b', '«в залежності від» -> «залежно від»'),
    ('v-danyi-moment', r'\b[ву] (даний|даному|даного|данний) момент\w*|\bна даний момент\b',
     '«в даний момент» -> «зараз», «наразі»'),
    ('sliduyuchyi', r'\bслідуюч\w*', '«слідуючий» -> «наступний»'),
    ('samyi-superl', r'\b(самий|сама|саме|самі) (кращ\w*|сильніш\w*|розумніш\w*|важливіш\w*|'
                    r'швидш\w*|крутіш\w*|класн\w*|цікавіш\w*|зручніш\w*|простіш\w*)',
     '«самий кращий» -> «найкращий»'),
    ('buvshyi', r'\bбувш\w*', '«бувший» -> «колишній»'),
    ('virno', r'\b(не)?вірно\b', '«вірно» -> «правильно», «слушно» (вірно = віддано)'),
    ('dyakuyuchy', r'\bдякуючи\b', '«дякуючи» -> «завдяки» (as a preposition)'),
    ('spivpadaty', r'\bспівпад\w*', '«співпадати» -> «збігатися»'),
    ('pryishlyosya', r'\b(мені|тобі|йому|їй|нам|вам|їм|їм) прийшлося\b', '«мені прийшлося» -> «мені довелося»'),
    ('vybachayus', r'\bвибачаюсь\b', '«вибачаюсь» -> «перепрошую», «вибачте»'),
    ('v-tomu-chysli', r'\b[ву] тому числі\b', '«в тому числі» -> «зокрема»'),
    ('v-pershu-cherhu', r'\b[ву] першу чергу\b', '«в першу чергу» -> «насамперед»'),
    ('tak-yak', r'\bтак як\b', '«так як» -> «оскільки», «бо»'),
    ('v-toi-chas-yak', r'\b[ву] той час як\b', '«в той час як» -> «тоді як»'),
    ('maye-sens', r'\bма[єю]ть? сенс\b', '«має сенс» -> «логічно», «зрозуміло»'),
    ('ty-v-poryadku', r'\b(ти|ви) в порядку\b', '«ти в порядку?» -> «ти як?», «усе гаразд?»'),
    ('nemaye-problem', r'\bнема[є]? проблем\b', '«немає проблем» -> «нема за що», «дрібниці»'),
    ('zrobyty-rishennya', r'\bзроби\w* рішення\b', '«зробити рішення» -> «ухвалити рішення», «вирішити»'),
    ('pryynyaty-dush', r'\bприйм\w* душ\b|\bприйня\w* душ\b', '«прийняти душ» -> «піти під душ»'),
    ('zakryty-dveri', r'\b(закрити|закрив|закрила|закрили|закрийте|закрий|закриває|закривай) '
                      r'(двері|вікно|вікна|дверцята)\b', 'двері/вікно -> «зачинити»'),
    ('vidkryty-dveri', r'\b(відкрити|відкрив|відкрила|відкрили|відкрийте|відкрий|відкриває|відкривай) '
                       r'(двері|вікно|вікна|дверцята)\b', 'двері/вікно -> «відчинити»'),
    ('vmyknuty-svitlo', r'\b(в|у)?(включити|включив|включила|включили|виключити|виключив|виключила|виключили)\b'
                        r'(?=[^.!?]{0,25}(світло|світла|екран|живлення|прилад|пристро))',
     '«включити/виключити» -> «увімкнути/вимкнути»'),
    ('odyagnuty-rich', r'\bодягн\w* (пальто|куртк\w*|шапк\w*|штани|чобот\w*|костюм|сорочк\w*)',
     'річ на себе -> «надіти», «вдягти»'),
    ('zrobyty-dzvinok', r'\bзроби\w* дзвінок\b|\bроби\w* дзвін\w*', '«зробити дзвінок» -> «подзвонити»'),
    ('vzyaty-perervu', r'\bвзя\w* перерву\b|\bбер\w* перерву\b', '«взяти перерву» -> «зробити перерву»'),
    ('kusok', r'\bкус(ок|ка|ки|ків|ком)\b', '«кусок» -> «шматок»'),
    ('drevnii', r'\bдревн\w*', '«древній» -> «стародавній»'),
    ('na-rahunok', r'\bна рахунок\b', '«на рахунок» (щодо) -> «щодо»'),
    ('v-yakosti', r'\b[ву] якості\b', '«в якості» -> «як»'),
    ('po-vidnoshennyu', r'\bпо відношенню до\b', '«по відношенню до» -> «щодо»'),
    ('v-kintsi-kintsiv', r'\b[ву] кінці кінців\b', '«в кінці кінців» -> «зрештою»'),
    ('pryimaty-miry', r'\b(прийм|вжива)\w* мір\w*', '«приймати міри» -> «вживати заходів»'),
    ('v-lyubomu', r'\b[ву] любому\b', '«в любому разі» -> «у будь-якому разі»'),
    ('po-krainii-miri', r'\bпо крайній мір[іи]\b', '«по крайній мірі» -> «принаймні»'),
    ('v-osnovnomu', r'\b[ву] основному\b', '«в основному» -> «переважно»'),
    ('pry-neobhidnosti', r'\bпри необхідності\b', '«при необхідності» -> «за потреби»'),
    ('v-tsilomu', r'\b[ву] цілому\b', '«в цілому» -> «загалом»'),
    ('z-druhoi-storony', r'\bз (другої|іншої) сторони\b', '«з другої сторони» -> «з іншого боку»'),
    ('ne-dylyachys', r'\bне дивлячись на\b', '«не дивлячись на» -> «попри», «незважаючи на»'),
    ('na-sogodni', r'\bна сьогоднішній день\b', '«на сьогоднішній день» -> «на сьогодні», «нині»'),
    ('vidpovidno-z', r'\bвідповідно з\b', '«відповідно з» -> «відповідно до»'),
    ('bilsh-krashchyi', r'\bбільш (кращ\w*|зручніш\w*|простіш\w*|швидш\w*|цікавіш\w*)'
                        r'|\bнайбільш (оптимальн\w*|кращ\w*)', 'double comparative'),
    ('v-techi', r'\b[ву] течі[їю]\b', '«в течії» -> «протягом»'),
    ('na-ukraini', r'\bна Україні\b', '«на Україні» -> «в Україні»'),
    ('potomu', r'\b(роки|років|години|годин|дні|днів|тижні|тижнів|місяці|місяців) потому\b',
     '«роки потому» -> «роки тому»'),
    ('po-mistevyi', r'\bпо (вулиці|вулицях|місту|містах|кімнаті|коридору|поверсі)\b',
     '«по вулиці» -> «вулицею» (instrumental)'),
    ('nosyty-nazvu', r'\bнос(ить|ять) назву\b', '«носити назву» -> «називається»'),
    ('pryimaty-mistse', r'\bприйма\w* місце\b', '«приймати місце» -> «відбуватися»'),
    ('v-yakosti-tse', r'\bв ролі\b', None),
]
CALQUES = [(cid, re.compile(rx, re.IGNORECASE), why) for cid, rx, why in CALQUES if why]

# --- English function words (a bare one inside a Ukrainian sentence) ------
# a Latin guard is needed too: «THAT» must not match inside «FIRSTHAT»
BOUND = re.escape(UA_ALPHA + APOS) + r'A-Za-z0-9_\-'
EN_WORDS = re.compile(r'(?<![%s])(?:%s)(?![%s])' % (
    BOUND,
    '|'.join("""
the and you your yours is are was were not for with this that these those from have has had
will would can could should shall what why how when where who whom yes ok well just but or
of to in on at it its my me he she they we do does did get got make take go come look know
think want need here there all some any now then than too very only also more most much many
good bad new old big small right wrong again still out up down please sorry hello hi hey
""".split()),
    BOUND), re.I)

# --- religious exclamations (guide section 5) ------------------------------
DOUBLED_WORD = re.compile(r'(?<![%s])([%s]{2,})\s+\1(?![%s])' % (UA_ALPHA, UA_ALPHA, UA_ALPHA),
                          re.IGNORECASE)
DOUBLED_WORD_OK = {'ну', 'ха', 'хе', 'хи', 'хо', 'так', 'дуже', 'от', 'ой', 'ай', 'е', 'а', 'о', 'і'}
EUPHONY = re.compile(r'(?<![%s])(в\s+в|у\s+у|і\s+і|й\s+й)(?=[%s])' % (UA_ALPHA, UA_ALPHA),
                     re.IGNORECASE)
EUPHONY_WHY = {'в': '«в» before a в-word -> «у»', 'у': '«у» before a у-word -> «в»',
               'і': '«і» before an і-word -> «й»', 'й': '«й» before a й-word -> «і»'}
RELIGIOUS_RE = re.compile(
    r'\b(Бог|Бога|Богу|Богом|Богові|Боже|бог|бога|богу|богом|богові|боже|'
    r'Господь|Господи|Господа|Господу|Господом|господь|господи|господа|господу|'
    r'Всевишній|Всевишнього|всевишній|всевишнього)\b')

# --- permitted Latin vocabulary --------------------------------------------
BRAND_LATIN = """
Steam Workshop Xbox PlayStation Nintendo Switch Pro Controller Deck iOS Android Discord
Google Play Games Game Center Facebook Twitter Instagram YouTube Twitch Kickstarter
COWCAT COWCATGames BROK BROKVN InvestiGator Brawl Bar Point Click RPG FPS HUD VR QR VIP
USB PNG JPG JPEG ZIP OGG MP3 WAV TXT PDF CSV NMER NUB DLC OST TTS AI NPC HDD SSD
F1 F2 F3 F4 F5 F6 F7 F8 F9 F10 F11 F12 Numpad Alt Ctrl Shift Tab Enter Esc Escape
Backspace Delete Insert Home End Page Up Down Lock Num Scroll Pause Capslock Command
Option Control Space Beta Alpha Demo Save Load Options Menu View Start Select Back
MasterTweaks GrfGrin Rogue-like Slithery Snek
Together Email Web App Series One iCloud Drive Bluetooth Python Blue Synth Store
Options Menu View Start Select Back Remote Beat Brawl Bar First Hat Discord NUB NMER
VIP HUD PNG JPG JPEG ZIP OGG MP3 WAV TXT PDF CSV OST DLC TTS px em up www https http
Xbox360 PS4 PS5 XboxOne SteamDeck SwitchPro GameCenter Cowcat COWCATGames R.I.P
""".split()

# whole Latin phrases that are titles/terms, not stray English
APPROVED_PHRASES = {
    'the brawl bar', 'brawl bar', 'brok the investigator', 'brok: the brawl bar',
    'brawl bar (бійцівський бар)', 'first hat', 'point & click', "beat 'em up",
    'beat em up', 'steam workshop', 'nintendo eshop', 'steam deck',
    'switch pro controller', 'xbox one / series', 'xbox one', 'xbox 360',
    'playstation', 'icloud drive', 'web app', 'remote play together',
    'brawl bar dlc', 'cowcat games', 'brokvn engine', 'natal tail',
    'celsius strays', 'demetrios', 'the first hat', 'steam', 'gog',
}


# --- street register -------------------------------------------------------

# --- speaker label -> character name (formality.md lookup) ------------------
SPEAKER_ALIASES = {
    'Brok': 'Брок', 'Graff': 'Графф', 'Ott': 'Отт', 'Wes': 'Вес', 'Shay': 'Шей',
    'Dee': 'Ді', 'Mink': 'д-р Мінк', 'Guard': 'Охоронець', 'Director': 'Директорка',
    'Klay': 'Клей', 'Gherkin': 'д-р Геркін', 'Sin': 'Сін', 'Hacker': 'Гакер',
    'Tidy': 'Тайді', 'Tramp': 'Волоцюга', 'Bolt': 'Болт', 'Chiefrat': 'Шеф Верескунів',
    'Dart': 'Дарт', 'Kins': 'Кінс', 'RJ': 'Р. Джей', 'Blindbot': 'Сліпий бот',
    'Stubot': 'Студбот', 'Tribot': 'Трибот', 'Hyena': 'Щелепка', 'Frog': 'Бульбашка',
    'Hedgehog': 'Пазл', 'Shark': 'Гострозуб', 'Iguana': 'Айві', 'Clint': 'Клінт',
    'Lia': 'Лія', 'Hush': 'д-р Гаш', 'Tribotcop': 'Трибот-коп', 'Nmerchat': 'NMER',
    'Pipemail': 'Поштомат', 'Flower': 'Крамарка', 'Rat': 'Верескун', 'Rats': 'Верескуни',
    'Various': 'Різні', 'Unknown': '???', 'MeetyMe': 'Голограф',
}
NON_SPEAKER_LABEL = re.compile(
    r'^(LANGUAGE|FORMALITY|PROOFREAD|IMPORTANT|NB|Word play|NOTE|TRANSLATOR|TODO|'
    r'As in|In the sense|The |There |This |Starting |Check |Keep |Do not|Don)', re.I)


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------
class Row:
    __slots__ = ('file', 'line', 'key', 'en', 'ua', 'speaker')

    def __init__(self, file, line, key, en, ua, speaker):
        self.file, self.line, self.key = file, line, key
        self.en, self.ua, self.speaker = en, ua, speaker


class Finding:
    __slots__ = ('rule', 'file', 'line', 'key', 'msg', 'snippet')

    def __init__(self, rule, file, line, key, msg, snippet=''):
        self.rule, self.file, self.line = rule, file, line
        self.key, self.msg, self.snippet = key, msg, snippet


FINDINGS = collections.defaultdict(list)


def add(rule, file, line, key, msg, snippet=''):
    FINDINGS[rule].append(Finding(rule, file, line, key, msg, snippet))


def fmt_counter(counter):
    return ', '.join('%s×%d' % (k, n) for k, n in sorted(counter.items())) or '—'


def clip(text, n=88):
    text = (text or '').replace('\r', ' ').replace('\n', ' ')
    return text if len(text) <= n else text[:n - 1] + '…'


# --------------------------------------------------------------------------
# corpus loading
# --------------------------------------------------------------------------
def load_file(path, name):
    raw = open(path, 'rb').read()
    if not raw.startswith(b'\xef\xbb\xbf'):
        add('file-bom', name, 1, '', 'no BOM')
    text = raw.decode('utf-8-sig', errors='replace')
    crlf = text.count('\r\n')
    lf = text.count('\n') - crlf
    if lf:
        add('file-eol', name, 0, '', '%d bare LF line ending(s)' % lf)
    if not raw.endswith(b'\n'):
        add('file-noeol', name, 0, '', 'last line has no newline')

    rows, seen = [], {}
    speaker = None
    pending_en = None
    pending_line = 0
    for lineno, line in enumerate(text.replace('\r\n', '\n').split('\n'), 1):
        if line.startswith('###') and line.lstrip('#').strip():
            if pending_en is not None:
                add('file-orphanenglish', name, pending_line, '',
                    'no KEY under this English line', clip(pending_en))
            pending_en = line[3:].strip()
            pending_line = lineno
            continue
        if line.startswith('#'):
            label = line.lstrip('#').strip()
            if label and len(label) <= 30 and not NON_SPEAKER_LABEL.match(label) \
                    and re.match(r"^[A-Za-zА-Яа-яІіЇїЄєҐґ][\w .'’\-]*$", label):
                speaker = label
            continue
        m = KEY_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key in seen:
            add('file-dupkey', name, lineno, key, 'first seen on line %d' % seen[key])
        seen[key] = lineno
        if pending_en is None:
            add('file-noenglish', name, lineno, key, 'key without a "###" source line', clip(value))
        rows.append(Row(name, lineno, key, pending_en, value, speaker))
        pending_en = None
    if pending_en is not None:
        add('file-orphanenglish', name, pending_line, '',
            'no KEY under this English line (end of file)', clip(pending_en))
    return rows


CORPUS_DIRS = ('UA', 'brok', 'brokvn')  # UA/ = old single-project layout, one dir per site project


def corpus_dirs(root):
    return [os.path.join(root, d) for d in CORPUS_DIRS if os.path.isdir(os.path.join(root, d))]


def corpus_files(root, only=None):
    """[(name, path)] of every corpus .txt. A name can appear twice (TCOMMON in both projects)."""
    out = []
    for d in corpus_dirs(root):
        for f in sorted(os.listdir(d)):
            if f.endswith('.txt'):
                name = f[:-4]
                if only and name not in only:
                    continue
                out.append((name, os.path.join(d, f)))
    return out


def corpus_text(root):
    """All values of the corpus, lowercased, for whole-corpus term lookups.

    Read directly: running the full row checks twice would duplicate findings.
    """
    out = []
    for _name, path in corpus_files(root):
        text = open(path, 'rb').read().decode('utf-8-sig', errors='replace')
        for line in text.replace('\r\n', '\n').split('\n'):
            if line and not line.startswith('#') and '=' in line:
                out.append(line.split('=', 1)[1].lower())
    return ' '.join(out)


def load_corpus(root, only=None):
    pairs = corpus_files(root, only)
    seen = {}
    for n, _p in pairs:
        seen[n] = seen.get(n, 0) + 1
    rows = []
    for n, path in pairs:
        label = n + '.txt'
        if seen[n] > 1:
            label = os.path.basename(os.path.dirname(path)) + '/' + label
        rows.extend(load_file(path, label))
    return rows


# --------------------------------------------------------------------------
# reference documents
# --------------------------------------------------------------------------
def md_tables(path):
    """[(header_cells, [(lineno, cells)], first_line)] for every markdown table."""
    out, cur = [], []
    for i, line in enumerate(open(path, encoding='utf-8-sig').read().split('\n'), 1):
        if line.strip().startswith('|'):
            cur.append((i, line))
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    tables = []
    for block in out:
        cells = [cells_of(line) for _, line in block]
        tables.append((cells[0], list(zip([i for i, _ in block], cells)), block[0][0]))
    return tables


def cells_of(line):
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def is_sep(cells):
    return all(c and set(c) <= set('-: ') for c in cells)


def count_keys(path):
    """(empty values, total keys) of one translation file."""
    empty = total = 0
    text = open(path, 'rb').read().decode('utf-8-sig', errors='replace')
    for line in text.replace('\r\n', '\n').split('\n'):
        if not line or line.startswith('#') or '=' not in line:
            continue
        m = KEY_RE.match(line)
        if not m:
            continue
        total += 1
        if not m.group(2).strip():
            empty += 1
    return empty, total


def load_glossary(root):
    """[(section, english, ukrainian, notes, lineno)] for every glossary row."""
    path = os.path.join(root, 'glossary.md')
    rows, section = [], ''
    for i, line in enumerate(open(path, encoding='utf-8-sig').read().split('\n'), 1):
        m = re.match(r'^\*\*(.+?)\*\*\s*$', line.strip())
        if m:
            section = m.group(1)
            continue
        if not line.strip().startswith('|'):
            continue
        c = cells_of(line)
        if len(c) < 2 or is_sep(c) or c[0] in ('Оригінал',):
            continue
        rows.append((section, c[0], c[1], c[2] if len(c) > 2 else '', i))
    return rows


def load_formality(root):
    """(headings[(name, gender, line)], pairs[(speaker, addressee, form, line)], uniform{speaker: 'ти'|'ви'})"""
    path = os.path.join(root, 'formality.md')
    headings, pairs, uniform = [], [], {}
    speaker = None
    for i, line in enumerate(open(path, encoding='utf-8-sig').read().split('\n'), 1):
        m = re.match(r'^##\s+(.+?)\s*\((Ч|Ж)\)\s*$', line)
        if m:
            speaker = m.group(1).strip()
            headings.append((speaker, m.group(2), i))
            continue
        if line.startswith('## '):
            speaker = None
            continue
        m = re.search(r'\*\*Всі звертання на\s*[«"]?(ти|ви)[»"]?', line)
        if m and speaker:
            uniform[speaker] = m.group(1)
            continue
        if line.strip().startswith('|'):
            c = cells_of(line)
            if len(c) >= 2 and c[0] and c[0] != 'Звертання до:' and not is_sep(c) and speaker:
                pairs.append((speaker, c[0], c[1], i))
    return headings, pairs, uniform


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def strip_markup(value):
    return TAG_RE.sub(' ', PLACEHOLDER_RE.sub(' ', value))


def tags_of(value):
    return collections.Counter(t.split('=')[0].strip().lower() for t in TAG_RE.findall(value))


def placeholders_of(value):
    return collections.Counter(p.strip() for p in PLACEHOLDER_RE.findall(value))


def hash_runs(value):
    return collections.Counter(len(m.group(0)) for m in HASH_RUN_RE.finditer(value))


def digits_of(text):
    return collections.Counter(re.findall(r'\d+', text))


def final_char(text):
    s = text.rstrip()
    while s and s[-1] in '")':
        s = s[:-1].rstrip()
    return s[-1:] if s else ''


def sentence_count(text):
    t = re.sub(r'\b(Dr|Mr|Mrs|Ms|St|vs|etc|No)\.', r'\1', text)
    return len(re.findall(r'[.!?](?=\s|$)', t))


def is_approved_latin(token, approved):
    t = token.strip('.,;:!?()[]"-')
    low = t.lower()
    if len(low) <= 2:
        return True
    if low in approved:
        return True
    for a in approved:
        if len(a) >= 4 and low.startswith(a) and len(low) - len(a) <= 2:
            return True
    return False


STREET_SWAP = [('шо', 'що'), ('шоб', 'щоб'), ('шось', 'щось'), ('нашо', 'нащо'),
               ('нішо', 'ніщо'), ('якшо', 'якщо'), ('ше', 'ще'), ('нема', 'немає')]


def looks_like_title(text):
    """«The Brawl Bar», «Celsius Strays», «Demetrios» — Latin titles stay Latin."""
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text)
    if not words:
        return False
    small = {'the', 'of', 'and', 'a', 'an', 'in', 'to', 'for'}
    return all(w[0].isupper() or w.lower() in small for w in words)


def latin_run_at(text, start):
    """The whole Latin run (title/phrase) the match at `start` belongs to."""
    ok = lambda c: c.isascii() and (c.isalpha() or c in " '&-")
    left, right = start, start
    while left > 0 and ok(text[left - 1]):
        left -= 1
    while right < len(text) and ok(text[right]):
        right += 1
    run = re.sub(r'\s+', ' ', text[left:right]).strip(' .:-')
    return run


def standardise(text):
    """Fold the guide's street spellings onto the standard ones."""
    out = text
    for street, standard in STREET_SWAP:
        out = re.sub(r'(?<![%s])%s(?![%s])' % (UA_ALPHA, street, UA_ALPHA),
                     standard, out, flags=re.IGNORECASE)
    return out


def approved_latin(glossary_rows):
    """Latin vocabulary that may legitimately stand in the Ukrainian text.

    Only the Ukrainian column counts: it is the side that says how a term is
    written in the game (BROKVN Engine, Point & Click, Start). The English
    column holds source prose whose words are not a licence to leave English
    in a value.
    """
    ok = set(w.lower() for w in BRAND_LATIN)
    for _s, _en, uk, _n, _l in glossary_rows:
        for token in re.findall(r'[A-Za-z][A-Za-z0-9\-]*', uk):
            ok.add(token.lower())
    return ok


# --------------------------------------------------------------------------
# corpus checks
# --------------------------------------------------------------------------
def check_rows(rows, refs):
    uniform, species = refs['uniform'], refs['species']
    approved = refs['approved']
    street_roster = collections.defaultdict(lambda: [0, 0])   # speaker -> [street, standard]
    street_lines = collections.defaultdict(list)

    for r in rows:
        v, en, key = r.ua, r.en or '', r.key

        # ---- untranslated -------------------------------------------------
        if not v.strip():
            add('untranslated-empty', r.file, r.line, key, 'empty value', clip(en))
            continue
        if en and v.strip() == en.strip() and not KEY_EXEMPT.match(key):
            tokens = re.findall(r"[A-Za-z][A-Za-z'&.\-]*", v)
            plain = strip_markup(v).strip()
            vowels = re.search('[%saeiouyAEIOUY]' % UA_ALPHA, plain)
            if tokens and plain and vowels and not looks_like_title(plain) \
                    and not all(is_approved_latin(t, approved) for t in tokens):
                add('untranslated-copy', r.file, r.line, key, 'English copied into the value', clip(v))

        # ---- integrity -----------------------------------------------------
        if '\\' in v:
            add('file-backslash', r.file, r.line, key, 'ASCII backslash in value', clip(v))
        if re.search(r'TODO|FIXME|=<[^>]*>', v):
            add('file-todo', r.file, r.line, key, 'placeholder left in value', clip(v))

        # ---- Russian -------------------------------------------------------
        m = RU_LETTERS.search(v)
        if m:
            add('rus-letter', r.file, r.line, key,
                'letter %r (%s)' % (m.group(0), unicodedata.name(m.group(0), '?')), clip(v))
        for w, rx in RU_WORD_RES:
            if rx.search(v):
                add('rus-word', r.file, r.line, key, 'Russian word «%s»' % w, clip(v))
        for cid, rx, why in CALQUES:
            mm = rx.search(v)
            if mm:
                add('rus-calque', r.file, r.line, key, '%s: «%s»' % (why, mm.group(0)), clip(v))

        # ---- latin / homoglyphs -------------------------------------------
        body = strip_markup(v)
        for token in re.findall(r"[%sA-Za-z][%sA-Za-z'’\-]*" % (UA_ALPHA, UA_ALPHA), body):
            has_ua = any(c in UA_ALPHA for c in token)
            has_lat = bool(re.search('[A-Za-z]', token))
            if has_ua and has_lat and '-' not in token:
                add('lat-homoglyph', r.file, r.line, key, 'mixed-script token «%s»' % token, clip(v))
        if any(c in UA_ALPHA for c in body):
            for mm in EN_WORDS.finditer(body):
                run = latin_run_at(body, mm.start()).lower()
                if run in APPROVED_PHRASES or is_approved_latin(mm.group(0), approved):
                    continue
                add('lat-englishword', r.file, r.line, key,
                    'English «%s» in a Ukrainian line' % mm.group(0), clip(v))
                break
        if not KEY_EXEMPT.match(key) and not any(c in UA_ALPHA for c in body):
            stripped = re.sub(r'\s+', ' ', body).strip()
            words = stripped.split(' ')
            func = len(EN_WORDS.findall(stripped))
            toks = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", stripped) if len(w) > 2]
            approved_n = sum(1 for w in toks if is_approved_latin(w, approved))
            if len(words) >= 3 and func >= 2 and (not toks or approved_n * 2 < len(toks)):
                add('lat-nocyrillic', r.file, r.line, key,
                    'no Ukrainian text (%d English words)' % len(words), clip(v))
        if not KEY_EXEMPT.match(key):
            for tm in re.finditer(r"[A-Za-z][A-Za-z'\-]*", body):
                token = tm.group(0)
                if is_approved_latin(token, approved):
                    continue
                if not re.search('[aeiouyAEIOUY]', token):
                    continue                      # «Zzz», «zZ» — snoring
                if latin_run_at(body, tm.start()).lower() in APPROVED_PHRASES:
                    continue
                add('lat-unapproved', r.file, r.line, key, 'Latin token «%s»' % token, clip(v))
                break

        # ---- characters ----------------------------------------------------
        for ch in v:
            if ch in UA_ALPHA or ch.isdigit() or ch == ' ':
                continue
            if ch.isascii() and ch.isalpha():
                continue
            if 'CYRILLIC' in unicodedata.name(ch, ''):
                # rus-letter reports these by name, one finding per line
                continue
            if ch in '.,!?;:-"\'()[]{}<> =/#%+&*@_':
                continue
            if ch in '’‘‚‛`´':
                add('char-quote', r.file, r.line, key, 'typographic apostrophe %r' % ch, clip(v))
            elif ch in '“”„«»':
                add('char-quote', r.file, r.line, key, 'typographic quote %r' % ch, clip(v))
            elif ch in '—–−':
                add('char-dash', r.file, r.line, key, 'typographic dash %r' % ch, clip(v))
            elif ch == '…':
                add('char-ellipsis', r.file, r.line, key, 'ellipsis character U+2026', clip(v))
            elif ch in '\u00a0\t\u2009\u202f':
                add('char-whitespace', r.file, r.line, key, 'NBSP/tab inside the value', clip(v))
            else:
                add('char-disallowed', r.file, r.line, key,
                    '%r U+%04X %s' % (ch, ord(ch), unicodedata.name(ch, '?')), clip(v))

        if v != v.strip():
            add('char-whitespace', r.file, r.line, key, 'leading/trailing space', clip(v))
        if '  ' in v.strip():
            add('char-double-space', r.file, r.line, key, 'double space', clip(v))
        dm = DOUBLED_WORD.search(v)
        if dm and dm.group(1).lower() not in DOUBLED_WORD_OK:
            add('char-doubled-word', r.file, r.line, key,
                '«%s %s»' % (dm.group(1), dm.group(1)), clip(v))
        em = EUPHONY.search(v)
        if em:
            add('char-euphony', r.file, r.line, key,
                '«%s» — %s' % (em.group(0), EUPHONY_WHY[em.group(0)[0].lower()]), clip(v))
        if re.search(r'\s#{1,2}[^#]', v) and not re.search(r'\s#\s*$', v):
            add('char-space-hash', r.file, r.line, key, 'space before a line break', clip(v))
        if re.search(r' [:,;]', v):
            add('char-space-before', r.file, r.line, key, 'space before punctuation', clip(v))
        if re.search(r' [.!?](?![.\w])', v):
            add('char-space-before', r.file, r.line, key, 'space before sentence end', clip(v))
        if re.search(r'[?!]\.\.\.', v):
            add('char-ellipsis-order', r.file, r.line, key, 'ellipsis after ? or !', clip(v))
        if PUNCT_TIGHT_RE.search(v):
            add('char-space-after', r.file, r.line, key, 'missing space after punctuation', clip(v))
        if v.count('"') % 2:
            add('char-quote-balance', r.file, r.line, key, 'odd number of double quotes', clip(v))
        if v.count('(') != v.count(')'):
            add('char-paren-balance', r.file, r.line, key, 'unbalanced parentheses', clip(v))
        if RELIGIOUS_RE.search(v):
            add('char-religious', r.file, r.line, key, 'religious exclamation', clip(v))
        if v[:1] in UA_LOWER and en[:1].isupper():
            add('char-lowerstart', r.file, r.line, key, 'lowercase sentence start', clip(v))

        # ---- structure against the English ---------------------------------
        if en:
            if hash_runs(en) != hash_runs(v):
                add('st-hash-runs', r.file, r.line, key,
                    'EN %s vs UA %s' % (dict(hash_runs(en)), dict(hash_runs(v))), clip(v))
            if tags_of(en) != tags_of(v):
                en_bal = collections.Counter()
                for t in TAG_RE.findall(en):
                    name = t.split('=')[0].strip().lower()
                    en_bal[name] += -1 if name.startswith('/') else 1
                note = ' (the English tag balance is broken too)' if any(en_bal.values()) else ''
                add('st-tags', r.file, r.line, key,
                    'EN %s vs UA %s%s'
                    % (fmt_counter(tags_of(en)), fmt_counter(tags_of(v)), note), clip(v))
            if placeholders_of(en) != placeholders_of(v):
                add('st-placeholders', r.file, r.line, key,
                    'EN %s vs UA %s' % (sorted(placeholders_of(en)), sorted(placeholders_of(v))), clip(v))
            if not ACCESS_KEY_EXEMPT.match(key):
                ed = collections.Counter(d for d in digits_of(en) if len(d) >= 3)
                ud = digits_of(v)
                if ed and not ud:
                    add('st-digits', r.file, r.line, key,
                        'EN has %s, UA has no digit' % ','.join(sorted(ed)), clip(v))
            abstail = re.search(r'(?:\b[A-Z]|[A-Z]\.[A-Z]|\b(?:Dr|Mr|Mrs|Ms|St|vs|etc|No|Jr|Sr))\.$',
                                en.rstrip())
            cue_ua = v.lstrip().startswith('(')
            if not en.startswith('(') and not cue_ua and not abstail \
                    and r.file != 'ACCESS.txt':
                fe, fv = final_char(en), final_char(v)
                if fe in '.!?' and fv not in '.!?':
                    add('st-finalpunct-missing', r.file, r.line, key,
                        'EN ends %r, UA ends %r' % (fe, fv), clip(v))
                elif fe not in '.!?' and fv in '.!?':
                    add('st-finalpunct-added', r.file, r.line, key,
                        'EN ends %r, UA ends %r' % (fe, fv), clip(v))
            if en.rstrip().endswith('?!') and not v.rstrip().endswith('?!'):
                add('st-mirror-q', r.file, r.line, key, 'EN ends «?!»', clip(v))
            if en.lstrip().startswith('(') and not v.lstrip().startswith('('):
                add('st-mirror-cue', r.file, r.line, key, 'EN is a stage cue in parentheses', clip(v))
            if len(en.strip()) >= 40:
                ratio = len(v.strip()) / max(1, len(en.strip()))
                if ratio < 0.5 or ratio > 2.3:
                    add('st-length', r.file, r.line, key, 'length ratio %.2f' % ratio,
                        '%s => %s' % (clip(en, 50), clip(v, 50)))
                if sentence_count(en) >= 3 and sentence_count(v) <= 1:
                    add('st-sentences', r.file, r.line, key,
                        'EN %d sentences, UA %d' % (sentence_count(en), sentence_count(v)), clip(v))

        # ---- register ------------------------------------------------------
        if r.speaker:
            sp = r.speaker
            for _form, rx in STREET_RES:
                street_roster[sp][0] += len(rx.findall(v))
            std = 0
            for form, rx in STANDARD_RES:
                n = len(rx.findall(v))
                if n:
                    std += n
                    street_lines[sp].append((r, form))
            street_roster[sp][1] += std

    # street register: only speakers who demonstrably speak street elsewhere
    for sp, (nstreet, _nstd) in street_roster.items():
        if nstreet < 3:
            continue
        for r, form in street_lines.get(sp, [])[:6]:
            add('reg-street', r.file, r.line, r.key,
                '«%s» in the voice of %s (street form used %d times)' % (form, sp, nstreet), clip(r.ua))

    # uniform ти/ви rule (formality.md "Всі звертання на «ти»/«ви»")
    ty = ua_word(r'(ти|тебе|тобі|тобою|твій|твоя|твоє|твої|твого|твоєї|твоїх|твоїм|твоєю)')
    vy = ua_word(r'(ви|вас|вам|вами|ваш|ваша|ваше|ваші|вашого|вашої|ваших|вашим|вашою)')
    for r in rows:
        if not r.speaker:
            continue
        name = species.get(r.speaker, SPEAKER_ALIASES.get(r.speaker, r.speaker))
        rule = uniform.get(name)
        if not rule:
            continue
        # only the formal rule is checkable: a «ти» form there is a slip, while
        # «ви» in the voice of a familiar character is usually a plural address
        if rule == 'ви' and ty.search(r.ua):
            add('reg-uniform', r.file, r.line, r.key,
                '%s addresses everyone on «ви», line has a «ти» form' % name, clip(r.ua))


def check_consistency(rows):
    # same English inside one file
    per_file = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        if r.en and r.ua.strip():
            per_file[r.file][r.en].append(r)
    for file, groups in per_file.items():
        for en, rs in groups.items():
            vals = collections.Counter(r.ua.strip() for r in rs)
            if len(vals) < 2:
                continue
            parts = ['%s ×%d' % (clip(v, 46), c) for v, c in vals.most_common(3)]
            add('cons-within', file, rs[0].line, rs[0].key,
                '%s — %s' % (clip(en, 52), ' | '.join(parts)), clip(en))

    # same English across files
    groups = collections.defaultdict(collections.Counter)
    where = {}
    for r in rows:
        if r.en and r.ua.strip():
            groups[r.en][r.ua.strip()] += 1
            where.setdefault(r.en, []).append(r)
    for en, vals in groups.items():
        if len(vals) < 2:
            continue
        total = sum(vals.values())
        top_val, top_n = vals.most_common(1)[0]
        if total < 3 or re.fullmatch(r'[A-Za-z0-9]{1,3}', top_val.strip()):
            continue
        minority = [(v, c) for v, c in vals.most_common() if v != top_val]
        if top_n / total >= 0.7:
            for v, c in minority[:3]:
                if standardise(v).lower() == standardise(top_val).lower():
                    continue
                r = next(x for x in where[en] if x.ua.strip() == v)
                add('cons-cross', r.file, r.line, r.key,
                    'minority: %s | majority ×%d: %s' % (clip(v, 46), top_n, clip(top_val, 46)),
                    clip(en, 70))

    # same KEY, different value across files
    by_key = collections.defaultdict(collections.Counter)
    where_key = {}
    for r in rows:
        if r.ua.strip():
            by_key[r.key][r.ua.strip()] += 1
            where_key.setdefault(r.key, []).append(r)
    for key, vals in by_key.items():
        if len(vals) < 2:
            continue
        files = {r.file for r in where_key[key]}
        if len(files) < 2 or 'ACCESS.txt' in files:
            # ACCESS.txt repeats other files for the screen reader; its wording
            # legitimately differs (ти vs ви, glossed labels)
            continue
        r = where_key[key][0]
        parts = ['%s: %s' % (f, clip(v, 40)) for f, v in
                 ((x.file, x.ua.strip()) for x in where_key[key])]
        add('cons-key', r.file, r.line, key, ' | '.join(parts[:3]))


# --------------------------------------------------------------------------
# reference document checks
# --------------------------------------------------------------------------
def check_docs(root, refs):
    glossary, headings, pairs = refs['glossary'], refs['headings'], refs['pairs']
    guide_path = os.path.join(root, 'guide.md')
    formality_path = os.path.join(root, 'formality.md')
    glossary_path = os.path.join(root, 'glossary.md')
    errors_path = os.path.join(root, 'common-errors.md')

    # table column counts
    for path in (guide_path, glossary_path, formality_path, errors_path):
        name = os.path.basename(path)
        for header, body, _first in md_tables(path):
            n = len(header)
            for lineno, cells in body:
                if is_sep(cells):
                    continue
                if len(cells) != n:
                    add('doc-table', name, lineno, '',
                        '%d columns, header has %d' % (len(cells), n), clip(' | '.join(cells)))

    # glossary: duplicate originals
    by_en = collections.defaultdict(list)
    for section, en, uk, _notes, lineno in glossary:
        by_en[en.strip().lower()].append((section, uk, lineno))
    for en, entries in by_en.items():
        if len(entries) < 2:
            continue
        forms = {u.strip() for _s, u, _l in entries}
        sections = {sec for sec, _u, _l in entries}
        rule = 'doc-dup-original' if len(sections) == 1 else 'doc-dup-sense'
        if len(forms) > 1:
            add(rule, 'glossary.md', entries[1][2], '',
                '«%s» defined %d times: %s' % (en, len(entries), ' / '.join(sorted(forms))))
        else:
            add(rule, 'glossary.md', entries[1][2], '',
                'duplicate row for «%s» (same rendering)' % en)

    # glossary: empty cells
    for section, en, uk, _notes, lineno in glossary:
        if not en.strip() or not uk.strip():
            add('doc-empty-cell', 'glossary.md', lineno, '', 'empty cell in «%s»' % (en or uk))

    # formality: duplicate / conflicting pairs
    seen_pairs = collections.defaultdict(list)
    for sp, addr, form, lineno in pairs:
        seen_pairs[(sp, addr)].append((form, lineno))
    for (sp, addr), entries in seen_pairs.items():
        if len(entries) > 1:
            forms = {f for f, _l in entries}
            kind = 'conflicting' if len(forms) > 1 else 'duplicate'
            add('doc-dup-pair', 'formality.md', entries[1][1], '',
                '%s row: %s -> %s (%s)' % (kind, sp, addr, ', '.join(forms)))

    # formality names vs glossary character table
    char_rows = [(en, uk) for section, en, uk, _n, _l in glossary if section == 'Персонажі']
    glossary_names = {}
    for en, uk in char_rows:
        for alt in uk.split(' / '):
            alt = alt.strip().strip('"')
            if alt:
                glossary_names.setdefault(alt.lower(), (en, alt))
    for name, _gender, lineno in headings:
        if name.lower() in glossary_names:
            _en, canonical = glossary_names[name.lower()]
            if canonical != name:
                add('doc-casing', 'formality.md', lineno, '',
                    'heading «%s» vs glossary «%s»' % (name, canonical))
            continue
        # a full name in the glossary counts (Графф in «Графф Філш»)
        hit = [canon for (_en, canon) in glossary_names.values()
               if re.search(r'(?<![%s])%s(?![%s])' % (UA_ALPHA, re.escape(name), UA_ALPHA), canon)]
        if hit:
            continue
        add('doc-name-missing', 'formality.md', lineno, '',
            '«%s» has no row in glossary.md (Персонажі)' % name)

    # glossary: term never used in the corpus
    corpus_text = refs['corpus_lower']
    for section, en, uk, _notes, lineno in glossary:
        alts = [a.strip() for a in uk.split(' / ')]
        stems = []
        for alt in alts:
            first = re.sub(r'[^%s]' % UA_ALPHA, ' ', alt).split()
            if first and len(first[0]) >= 4:
                stems.append(first[0][:max(4, len(first[0]) - 2)].lower())
        if not stems:
            continue
        if any(s in corpus_text for s in stems):
            continue
        add('doc-dead-term', 'glossary.md', lineno, '',
            '«%s» = «%s» never appears in the corpus' % (en, uk))

    # links between reference docs
    for path in (guide_path, formality_path, glossary_path, errors_path):
        name = os.path.basename(path)
        text = open(path, encoding='utf-8-sig').read()
        for target in re.findall(r'\]\(([^)#]+\.md)\)', text):
            if not os.path.exists(os.path.join(root, target)):
                add('doc-link', name, 0, '', 'link to missing file %r' % target)

    # formality: an addressee who has no section of its own
    section_names = {h[0] for h in headings}
    addr_seen = {}
    for _sp, addr, _form, lineno in pairs:
        if not addr or set(addr) <= set('-: '):
            continue
        addr_seen.setdefault(addr, lineno)
    for addr, lineno in addr_seen.items():
        if addr in section_names:
            continue
        if re.match(r'^(Верескуни|Різні|Unknown|Various)$', addr):
            continue
        add('doc-addressee', 'formality.md', lineno, '',
            '«%s» is an addressee but has no section' % addr)

    # common-errors: duplicate wrong forms, empty cells
    ce = []
    for header, body, _first in md_tables(errors_path):
        for lineno, cells in body:
            if len(cells) == 4 and cells[0] and not is_sep(cells) and cells[0] != 'Original':
                ce.append((lineno, cells))
    wrong = collections.Counter(c[1] for _l, c in ce if c[1])
    for _l, c in ce:
        if not c[1].strip() or not c[2].strip():
            add('doc-common-errors', 'common-errors.md', _l, '',
                'empty «%s» cell' % ('Помилка' if not c[1].strip() else 'Краще'))
        elif wrong[c[1]] > 1:
            add('doc-common-errors', 'common-errors.md', _l, '',
                '«%s» listed %d times' % (c[1], wrong[c[1]]))

    # progress.md vs the actual files and key counts
    progress_path = os.path.join(root, 'progress.md')
    if os.path.exists(progress_path):
        prog, status_cols = [], None
        for header, body, _first in md_tables(progress_path):
            for lineno, cells in body:
                if is_sep(cells) or not cells[0] or cells[0] == 'Item':
                    continue
                prog.append((lineno, cells))
        by_name = {}
        for n, p in corpus_files(root):
            by_name.setdefault(n, []).append(p)
        files = set(by_name)
        items = {c[0] for _l, c in prog}
        for lineno, cells in prog:
            name = cells[0]
            if name not in files:
                add('doc-progress-file', 'progress.md', lineno, '',
                    'row «%s» has no corpus file' % name)
                continue
            if len(by_name[name]) > 1:
                continue  # same file name in two projects: a progress row cannot be attributed
            empties, total = count_keys(by_name[name][0])
            statuses = [c.lower() for c in cells[1:] if c]
            done = all('done' in st for st in statuses) if statuses else False
            if done and empties:
                add('doc-progress-status', 'progress.md', lineno, '',
                    '«%s» is Done but %d/%d keys are empty' % (name, empties, total))
            elif not done and total and not empties:
                add('doc-progress-status', 'progress.md', lineno, '',
                    '«%s» is not Done but every key is filled' % name)
        for name in sorted(files - items):
            add('doc-progress-file', 'progress.md', 0, '',
                '%s has no row in progress.md' % name)

    # guide claims the corpus does not follow
    hint_rows = [r for r in refs['rows'] if r.file.startswith('THINTS')]
    if hint_rows:
        ty = ua_word(r'(ти|тебе|тобі|твій|твоя|твоє|твої)')
        ty_lines = [r for r in hint_rows if ty.search(r.ua)]
        line_no = 0
        for i, line in enumerate(open(guide_path, encoding='utf-8-sig').read().split('\n'), 1):
            if 'звертається до гравця' in line:
                line_no = i
                break
        add('doc-guide-claim', 'guide.md', line_no, '',
            'section 3 says the game always addresses the player on «ви», but %d of %d hint lines '
            'use «ти»' % (len(ty_lines), len(hint_rows)))


# --------------------------------------------------------------------------
# terminology alignment (character names)
# --------------------------------------------------------------------------
def check_terms(rows, refs):
    glossary = refs['glossary']
    for section, en, uk, _notes, _lineno in glossary:
        if section != 'Персонажі' or ' / ' in uk:
            continue
        if not re.fullmatch(r"[A-Z][A-Za-z.'\-]*( [A-Z][A-Za-z.'\-]*)?", en):
            continue
        head = re.sub(r'[^%s]' % UA_ALPHA, '', uk.split(' ')[0])
        if len(head) < 3:
            continue
        stem = (head[:3] if len(head) <= 6 else head[:4]).lower()
        rx = re.compile(r'(?<![A-Za-z])' + re.escape(en) + r'(?![A-Za-z])')
        hits = [r for r in rows if r.en and rx.search(r.en)]
        if not hits:
            continue
        bad = [r for r in hits if stem not in r.ua.lower()]
        if bad and len(bad) / len(hits) <= 0.3:
            for r in bad[:2]:
                add('term-nameform', r.file, r.line, r.key,
                    '«%s» is «%s» in the glossary' % (en, uk), clip(r.ua))


# --------------------------------------------------------------------------
# reporting

# --------------------------------------------------------------------------
# markdown table output (the shape a report is delivered in)
# --------------------------------------------------------------------------
# rules whose findings are structurally uninformative in a report: kept out of
# the table unless --table-all. Each entry is one line of why.
TABLE_HIDE = {
    'cons-within': 'same English rendered differently across speakers/registers',
    'cons-key': 'same key reused by ACCESS.txt and a dialogue file (ти/ви split)',
    'lat-unapproved': 'brands, file paths, URLs, e-mail addresses',
    'term-nameform': 'the English word is also an ordinary noun (bolt, puzzles)',
    'st-finalpunct-added': 'a period added to a UI line is harmless',
    'doc-dup-sense': 'one English word, two senses, two sections',
}


def md_cell(text, width):
    text = (text or '').replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')
    if len(text) > width:
        text = text[:width - 1] + '…'
    return text


def print_table(args):
    sev_rank = {'ERROR': 0, 'WARN': 1, 'INFO': 2}
    min_rank = {'info': 2, 'warn': 1, 'error': 0}[args.min_sev]
    cats = [c.strip() for c in args.only.split(',')] if args.only else None

    active = []
    for rid, (sev, cat, desc) in RULES.items():
        if sev_rank[sev] > min_rank:
            continue
        if cats and cat not in cats:
            continue
        if args.file and cat == 'docs':
            continue            # a one-file pass is a proofread, not a docs audit
        if not args.table_all and rid in TABLE_HIDE:
            continue
        if FINDINGS.get(rid):
            active.append(rid)
    cat_order = ['untranslated', 'structure', 'consistency', 'russian', 'register',
                 'latin', 'docs', 'chars', 'integrity', 'terms']
    active.sort(key=lambda rid: (sev_rank[RULES[rid][0]],
                                 cat_order.index(RULES[rid][1])
                                 if RULES[rid][1] in cat_order else 99, rid))

    hidden = {rid: len(FINDINGS[rid]) for rid in FINDINGS
              if not args.table_all and rid in TABLE_HIDE and FINDINGS[rid]
              and not (args.file and RULES[rid][1] == 'docs')
              and not (cats and RULES[rid][1] not in cats)}
    total = sum(len(FINDINGS[rid]) for rid in active)

    per_rule = args.table_per_rule
    if active and per_rule * len(active) > args.table_limit:
        per_rule = max(1, args.table_limit // len(active))

    print('| ID | Sev | Where | Key | Issue |')
    print('| --- | --- | --- | --- | --- |')
    counters = {'ERROR': 0, 'WARN': 0, 'INFO': 0}
    shown = 0
    for rid in active:
        sev = RULES[rid][0]
        items = sorted(FINDINGS[rid], key=lambda f: (f.file, f.line))
        for f in items[:per_rule]:
            counters[sev] += 1
            shown += 1
            where = f.file if not f.line else '%s:%d' % (f.file, f.line)
            print('| %s%d | %s | %s | %s | %s |'
                  % (sev[0], counters[sev], sev, md_cell(where, 22),
                     md_cell(f.key, 30), md_cell(f.msg, 96)))
    print()
    print('rows %d of %d findings · E%d W%d I%d'
          % (shown, total, counters['ERROR'], counters['WARN'], counters['INFO']))
    if total > shown:
        left = collections.Counter()
        for rid in active:
            if len(FINDINGS[rid]) > per_rule:
                left[rid] = len(FINDINGS[rid]) - per_rule
        print('left out by the per-rule cap: '
              + ', '.join('%s %d' % (r, n) for r, n in left.most_common()))
    for rid, n in sorted(hidden.items(), key=lambda kv: -kv[1]):
        print('filtered (%s): %d — %s' % (rid, n, TABLE_HIDE[rid]))
    failed = counters['ERROR'] if args.fail_on == 'error' else (
        counters['ERROR'] + counters['WARN'] if args.fail_on == 'warn' else 0)
    return 1 if failed else 0


# --------------------------------------------------------------------------
def report(args):
    sev_rank = {'ERROR': 0, 'WARN': 1, 'INFO': 2}
    min_rank = {'info': 2, 'warn': 1, 'error': 0}[args.min_sev]
    cats = [c.strip() for c in args.only.split(',')] if args.only else None
    if cats:
        known = {cat for _sev, cat, _d in RULES.values()}
        bad = [c for c in cats if c not in known]
        if bad:
            sys.exit('unknown category %r — use one of: %s'
                     % (', '.join(bad), ', '.join(sorted(known))))

    active = []
    for rid, (sev, cat, desc) in RULES.items():
        if sev_rank[sev] > min_rank:
            continue
        if cats and cat not in cats:
            continue
        if FINDINGS.get(rid):
            active.append(rid)
    active.sort(key=lambda rid: (sev_rank[RULES[rid][0]], -len(FINDINGS[rid]), rid))

    counts = collections.Counter(RULES[rid][0] for rid in active for _ in FINDINGS[rid])
    total_findings = sum(counts.values())

    per_file = collections.defaultdict(lambda: collections.Counter())
    for rid in active:
        for f in FINDINGS[rid]:
            per_file[f.file][RULES[rid][0]] += 1

    print('BROK UA QA  |  root %s  |  %d files / %d keys parsed  |  %d findings'
          % (args.root, len(args.files_seen), args.keys_parsed, total_findings))
    print('severity: ERROR %d · WARN %d · INFO %d  |  limit %d per rule'
          % (counts['ERROR'], counts['WARN'], counts['INFO'], args.limit))
    print()

    if per_file:
        print('%-14s %6s %5s %5s %5s' % ('file', 'ERROR', 'WARN', 'INFO', 'total'))
        for name, c in sorted(per_file.items(), key=lambda kv: (-kv[1]['ERROR'], -sum(kv[1].values()))):
            print('%-14s %6d %5d %5d %5d'
                  % (name, c['ERROR'], c['WARN'], c['INFO'], sum(c.values())))
        print()

    if args.brief:
        for rid in active:
            sev, cat, desc = RULES[rid]
            print('%-6s %-24s %-12s %4d  %s' % (sev, rid, cat, len(FINDINGS[rid]), desc))
        failed = counts['ERROR'] if args.fail_on == 'error' else (
            counts['ERROR'] + counts['WARN'] if args.fail_on == 'warn' else 0)
        return 1 if failed else 0

    for rid in active:
        sev, cat, desc = RULES[rid]
        items = FINDINGS[rid]
        print('== [%s] %s — %s  (%d)' % (sev, rid, desc, len(items)))
        by_file = collections.Counter(f.file for f in items)
        if len(by_file) > 1:
            print('   files: ' + ', '.join('%s %d' % (f, n) for f, n in by_file.most_common(8)))
        for f in items[:args.limit]:
            loc = f.file if not f.line else '%s:%d' % (f.file, f.line)
            key = (' ' + f.key) if f.key else ''
            print('   %-28s%s  %s' % (loc, key, f.msg))
            if f.snippet and args.snippets:
                print('      · %s' % f.snippet)
        if len(items) > args.limit:
            print('   … +%d more' % (len(items) - args.limit))
        print()

    if args.json:
        data = []
        for rid in active:
            sev, cat, desc = RULES[rid]
            for f in FINDINGS[rid]:
                data.append(dict(rule=rid, severity=sev, category=cat, description=desc,
                                 file=f.file, line=f.line, key=f.key,
                                 message=f.msg, snippet=f.snippet))
        with open(args.json, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print('json: %s (%d findings)' % (args.json, len(data)))

    failed = counts['ERROR'] if args.fail_on == 'error' else (
        counts['ERROR'] + counts['WARN'] if args.fail_on == 'warn' else 0)
    return 1 if failed else 0


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Automated QA for the BROK UA translation.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='D:/brok-ua', help='repository root (default D:/brok-ua)')
    ap.add_argument('--file', action='append', default=None,
                    help='restrict corpus checks to these file names (repeatable)')
    ap.add_argument('--only', default=None,
                    help='categories: integrity,untranslated,russian,latin,chars,structure,'
                         'consistency,register,docs,terms')
    ap.add_argument('--min-sev', default='info', choices=['info', 'warn', 'error'])
    ap.add_argument('--limit', type=int, default=12, help='max items printed per rule (default 12)')
    ap.add_argument('--json', default=None, help='write all findings to this JSON file')
    ap.add_argument('--fail-on', default='error', choices=['error', 'warn', 'never'])
    ap.add_argument('--no-snippets', dest='snippets', action='store_false', help='hide snippets')
    ap.add_argument('--brief', action='store_true',
                    help='summary table and rule counts only, no finding lines')
    ap.add_argument('--table', action='store_true',
                    help='markdown table with E/W/I ids, filtered to report-worthy rules')
    ap.add_argument('--table-all', action='store_true',
                    help='include the rules --table filters out by default')
    ap.add_argument('--table-limit', type=int, default=80, help='max table rows (default 80)')
    ap.add_argument('--table-per-rule', type=int, default=6,
                    help='max rows per rule (default 6)')
    ap.add_argument('--list-rules', action='store_true', help='print the rule table and exit')
    args = ap.parse_args(argv)

    if args.list_rules:
        print('%-26s %-6s %-12s %s' % ('rule', 'sev', 'category', 'description'))
        for rid, (sev, cat, desc) in RULES.items():
            print('%-26s %-6s %-12s %s' % (rid, sev, cat, desc))
        return 0

    dirs = corpus_dirs(args.root)
    if not dirs:
        sys.exit('ABORT: no corpus directory (UA/, brok/ or brokvn/) under %r (use --root)' % args.root)
    args.files_seen = [n for n, _p in corpus_files(args.root)]
    rows = load_corpus(args.root, args.file)
    args.keys_parsed = len(rows)
    if not rows:
        sys.exit('ABORT: parsed 0 rows — wrong --root or --file?')

    glossary = load_glossary(args.root)
    headings, pairs, uniform = load_formality(args.root)
    species = load_species(args.root)
    refs = dict(glossary=glossary, headings=headings, pairs=pairs, uniform=uniform,
                species=species, approved=approved_latin(glossary), rows=rows,
                corpus_lower=corpus_text(args.root))

    check_rows(rows, refs)
    if not args.file:
        check_consistency(rows)
        check_terms(rows, refs)
    check_docs(args.root, refs)
    if args.table:
        return print_table(args)
    return report(args)


def load_species(root):
    """# Speaker labels in dialogue files can be species: CHAR_HYENA=Щелепка."""
    out = {}
    found = {n: p for n, p in corpus_files(root) if n in ('TDLC', 'TCOMMON', 'TPC21')}
    for name in ('TDLC', 'TCOMMON', 'TPC21'):
        path = found.get(name)
        if not path:
            continue
        for line in open(path, encoding='utf-8-sig', newline='').read().split('\r\n'):
            m = re.match(r'^CHAR_([A-Z0-9_]+)=(\S.*)$', line)
            if m:
                label = m.group(1).split('_')[-1]
                out[label.capitalize()] = m.group(2).strip()
                out[label] = m.group(2).strip()
    return out


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    sys.exit(main())
