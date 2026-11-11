"""Match subtitle search results to one specific video release.

parse_release() pulls version facts out of a video file name or a release
name; score() rates a candidate subtitle against them; pick_file() chooses
the right file (episode, language) out of a downloaded subtitle pack.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

SOURCES = [
    ('bluray', r'blu-?ray|bdrip|bd-?remux|remux|bdmv|uhd\W?bd|\bbd\b'),
    ('web', r'web-?dl|webrip|\bweb\b|\bnf\b|\bamzn\b|\bdsnp\b|\bhmax\b|\batvp\b'),
    ('hdtv', r'hdtv|\btvrip\b'),
    ('dvd', r'dvdrip|\bdvd\b'),
]
RESOLUTIONS = [('2160p', r'2160p|\b4k\b|\buhd\b'), ('1080p', r'1080[pi]'), ('720p', r'720p'), ('480p', r'480p')]
EDITIONS = {
    'imax': r'\bimax\b',
    'extended': r'extended|加长',
    'directors': r"director'?s?\W?cut|\bdc\b|导演剪辑",
    'uncut': r'\buncut\b|未删减',
    'theatrical': r'theatrical|院线',
}
LANG_TAGS = [
    ('双语', r'双语|简英|繁英|中英|chs\s*&\s*eng|cht\s*&\s*eng|chs\.eng|简繁英'),
    ('简体', r'简体|简中|\bchs\b|\bgb\b|\bsc\b|zh-?cn|zh-?hans|简'),
    ('繁体', r'繁体|繁中|\bcht\b|big5|\btc\b|zh-?tw|zh-?hant|繁'),
    ('英文', r'英文|\beng?\b|english'),
]
SUB_EXTS = {'.srt', '.ass', '.ssa', '.vtt', '.sup', '.sub', '.idx', '.smi'}
_SPLIT = re.compile(r'[\s._\-\[\]()【】（）]+')


def _norm(text: str) -> str:
    return ' ' + re.sub(r'[._]+', ' ', text.lower()) + ' '


def _first(table, text):
    for name, pattern in table:
        if re.search(pattern, text, re.I):
            return name
    return None


def episode_of(text: str) -> tuple[int | None, int | None]:
    """(season, episode) from a name; either may be None."""
    m = re.search(r'\bs(\d{1,2})\s?e(\d{1,4})\b', text, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    season = None
    m = re.search(r'\bs(\d{1,2})\b|第\s*([一二三四五六七八九十\d]+)\s*季|season\s*(\d{1,2})', text, re.I)
    if m:
        raw = next(g for g in m.groups() if g)
        season = int(raw) if raw.isdigit() else _cn_num(raw)
    m = re.search(r'\bep?(\d{1,4})\b|第\s*(\d{1,4})\s*[集话話]', text, re.I)
    episode = int(next(g for g in m.groups() if g)) if m else None
    return season, episode


def _cn_num(text: str) -> int | None:
    digits = '一二三四五六七八九'
    if text == '十':
        return 10
    if text.startswith('十'):
        return 10 + digits.index(text[1]) + 1 if len(text) > 1 else 10
    if '十' in text:
        tens, _, ones = text.partition('十')
        return (digits.index(tens) + 1) * 10 + (digits.index(ones) + 1 if ones else 0)
    return digits.index(text) + 1 if text in digits else None


def parse_release(name: str) -> dict:
    stem = Path(name).stem if Path(name).suffix.lower() in {
        '.mkv', '.mp4', '.m4v', '.avi', '.ts', '.m2ts', '.mov', '.wmv', '.rmvb', '.flv', '.webm'} else name
    text = _norm(stem)
    year = None
    for m in re.finditer(r'(?<![\dx×])(19[2-9]\d|20[0-4]\d)(?![\dx×]|p)', stem):
        year = int(m.group(1))
    season, episode = episode_of(stem)
    group = None
    m = re.search(r'-\s?([A-Za-z0-9@&]{2,20})(?:\[[^\]]*\])?$', stem.strip())
    if m and not re.fullmatch(r'\d{3,4}p|x26[45]|h26[45]|hevc|avc|dts|ac3|aac|hdr|sdr|imax', m.group(1), re.I):
        group = m.group(1)
    title = re.split(r'(?<!\d)(?:19[2-9]\d|20[0-4]\d)(?!\d)|\bs\d{1,2}e?\d*\b|\b\d{3,4}p\b|第\s*[一二三四五六七八九十\d]+\s*[季集话話]', stem, maxsplit=1, flags=re.I)[0]
    title = re.sub(r'^\s*\[[^\]]*\]\s*', '', title)
    title = ' '.join(t for t in _SPLIT.split(title) if t).strip()
    return {
        'title': title, 'year': year, 'season': season, 'episode': episode, 'group': group,
        'source': _first(SOURCES, text), 'resolution': _first(RESOLUTIONS, text),
        'editions': sorted(k for k, p in EDITIONS.items() if re.search(p, text, re.I)),
    }


def languages(text: str) -> list[str]:
    found = [name for name, pattern in LANG_TAGS if re.search(pattern, text, re.I)]
    return found


def score(video: dict, candidate_text: str, fmt: str = '', language: str = '',
          downloads: int | str | None = None, lang_pref: list[str] | None = None) -> tuple[int | None, list[str]]:
    """Return (score, reasons). Score None means excluded (certainly a different video)."""
    lang_pref = lang_pref or ['双语', '简体', '繁体']
    text = _norm(candidate_text)
    reasons, total = [], 0

    if video.get('episode') is not None:
        c_season, c_episode = episode_of(candidate_text)
        if c_season is not None and video.get('season') is not None and c_season != video['season']:
            return None, ['季数不符']
        if c_episode is not None:
            if c_episode != video['episode']:
                return None, ['集数不符']
            total += 10
            reasons.append('集数一致')
        elif c_season is not None or re.search(r'全集|合集|complete|全\d+集', candidate_text, re.I):
            reasons.append('季包，需从压缩包中挑选')
        else:
            total -= 15
            reasons.append('未标明集数')

    c_years = [int(y) for y in re.findall(r'(?<![\dx×])(19[2-9]\d|20[0-4]\d)(?![\dx×]|p)', candidate_text)]
    if video.get('year') and c_years:
        if video['year'] in c_years:
            total += 10
            reasons.append('年份一致')
        elif not any(abs(y - video['year']) <= 1 for y in c_years):
            return None, ['年份不符']

    group = video.get('group')
    if group and re.search(r'(?<![a-z0-9])' + re.escape(group.lower()) + r'(?![a-z0-9])', text):
        total += 40
        reasons.append(f'制作组一致（{group}）')

    c_source = _first(SOURCES, text)
    if video.get('source') and c_source:
        if c_source == video['source']:
            total += 15
            reasons.append('片源一致')
        else:
            total -= 20
            reasons.append('片源不同')

    c_res = _first(RESOLUTIONS, text)
    if video.get('resolution') and c_res == video['resolution']:
        total += 5
        reasons.append('分辨率一致')

    c_editions = {k for k, p in EDITIONS.items() if re.search(p, text, re.I)}
    v_editions = set(video.get('editions') or [])
    if c_editions != v_editions:
        total -= 15 * len(c_editions ^ v_editions)
        reasons.append('版本不同（' + '/'.join(sorted(c_editions ^ v_editions)) + '）')
    elif v_editions:
        total += 5
        reasons.append('版本一致')

    langs = languages(candidate_text + ' ' + (language or ''))
    for rank, name in enumerate(lang_pref):
        if name in langs:
            total += max(2, 10 - rank * 3)
            reasons.append(name)
            break
    else:
        if langs == ['英文']:
            total -= 10
            reasons.append('仅英文')

    fmt = (fmt or '').upper()
    if fmt in {'ASS', 'SSA', 'SRT'}:
        total += 3
    elif fmt in {'SUP', 'SUB', 'IDX'}:
        total -= 5
        reasons.append('图形字幕')

    try:
        count = int(str(downloads or 0).replace(',', ''))
    except ValueError:
        count = 0
    total += min(5, int(math.log10(count + 1)))
    return total, reasons


def pick_file(files: list[Path], video: dict, lang_pref: list[str] | None = None) -> Path | None:
    """Choose one subtitle file from a download, matching the episode and preferred language."""
    lang_pref = lang_pref or ['双语', '简体', '繁体']
    subs = [Path(f) for f in files if Path(f).suffix.lower() in SUB_EXTS and Path(f).is_file()]
    if video.get('episode') is not None:
        matched = []
        for f in subs:
            season, episode = episode_of(f.name)
            if episode == video['episode'] and (season is None or video.get('season') is None
                                                or season == video['season']):
                matched.append(f)
        if not matched and len(subs) == 1:
            matched = subs if episode_of(subs[0].name)[1] is None else []
        subs = matched
    if not subs:
        return None

    def rank(f: Path):
        langs = languages(f.name)
        lang_rank = next((i for i, name in enumerate(lang_pref) if name in langs), len(lang_pref))
        fmt_rank = {'.ass': 0, '.ssa': 0, '.srt': 1}.get(f.suffix.lower(), 2)
        return lang_rank, fmt_rank, -f.stat().st_size

    return sorted(subs, key=rank)[0]


def link_lang(path: Path) -> str:
    langs = languages(path.name)
    if '繁体' in langs and '简体' not in langs and '双语' not in langs:
        return 'zh-TW'
    return 'zh-CN'
