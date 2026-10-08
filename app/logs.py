import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ACTOR_RE = re.compile(r"(?P<ckey>[A-Za-z0-9@_-]+|\*no key\*)/\((?P<char>[^()]*)\)")
LOC_RE = re.compile(r"\((?P<area>[^()]*?) \((?P<x>\d+),(?P<y>\d+),(?P<z>\d+)\)\)\s*$")
ROUND_RE = re.compile(r"round-(\d+)$")
CACHE_ROUNDS = 3

CATEGORIES = [
    ("attack", "Атаки и урон", "#f28b82"),
    ("game-say", "Речь", "#81c995"),
    ("game-whisper", "Шёпот", "#5bb974"),
    ("game-emote", "Эмоции", "#c58af9"),
    ("game-ooc", "OOC", "#9aa0a6"),
    ("game-looc", "LOOC", "#9aa0a6"),
    ("pda", "ПДА и сообщения", "#8ab4f8"),
    ("telecomms", "Рация", "#669df6"),
    ("game-access", "Входы и выходы", "#fcc934"),
    ("admin", "Действия админов", "#fdd663"),
    ("adminprivate", "Админ-приват", "#fde293"),
    ("adminprivate-asay", "Asay", "#fde293"),
    ("uplink", "Аплинк", "#ff8bcb"),
    ("silicon", "Синтетики", "#78d9ec"),
    ("mecha", "Мехи", "#78d9ec"),
    ("paper", "Бумаги", "#e8eaed"),
    ("manifest", "Манифест", "#e8eaed"),
    ("game", "Игра", "#9aa0a6"),
]
CATEGORY_LABELS = {key: label for key, label, _ in CATEGORIES}
CATEGORY_COLORS = {key: color for key, _, color in CATEGORIES}


@dataclass
class Round:
    number: int
    path: Path
    date: str


@dataclass
class Entry:
    index: int
    ts: str
    cat: str
    msg: str
    ckey: str
    char: str
    area: str
    x: int
    y: int
    z: int
    file: str

    @property
    def time(self):
        return self.ts[11:19]


class LogStore:
    def __init__(self, roots):
        self.roots = [Path(root) for root in roots.split(":") if root]
        self.cache = OrderedDict()

    def rounds(self):
        found = []
        for root in self.roots:
            for path in root.glob("*/*/*/round-*"):
                match = ROUND_RE.search(path.name)
                if match and path.is_dir():
                    found.append(Round(int(match.group(1)), path, "-".join(path.parts[-4:-1])))
        return sorted(found, key=lambda r: r.number, reverse=True)

    def round(self, number):
        for item in self.rounds():
            if item.number == number:
                return item
        return None

    def entries(self, round_):
        cached = self.cache.get(round_.number)
        if cached is not None:
            self.cache.move_to_end(round_.number)
            return cached
        entries = sorted(self._read(round_.path), key=lambda e: e.ts)
        for index, entry in enumerate(entries):
            entry.index = index
        self.cache[round_.number] = entries
        while len(self.cache) > CACHE_ROUNDS:
            self.cache.popitem(last=False)
        return entries

    def categories(self, round_):
        counts = {}
        for entry in self.entries(round_):
            counts[entry.cat] = counts.get(entry.cat, 0) + 1
        known = [(k, CATEGORY_LABELS[k], counts[k]) for k, _, _ in CATEGORIES if k in counts]
        other = sorted(((k, k, n) for k, n in counts.items() if k not in CATEGORY_LABELS), key=lambda t: -t[2])
        return known, other

    def span(self, round_):
        entries = self.entries(round_)
        if not entries:
            return None, 0
        start = _dt(entries[0].ts)
        return start, int((_dt(entries[-1].ts) - start).total_seconds())

    def offset(self, round_, entry):
        start, _ = self.span(round_)
        return int((_dt(entry.ts) - start).total_seconds()) if start else 0

    def search(self, round_, query="", ckey="", char="", cats=(), start="", end=""):
        pattern = re.compile(re.escape(query), re.IGNORECASE) if query else None
        ckey = ckey.lower()
        char = char.lower()
        for entry in self.entries(round_):
            if cats and entry.cat not in cats:
                continue
            if start and entry.time < start:
                continue
            if end and entry.time > end:
                continue
            if ckey and ckey not in entry.msg.lower():
                continue
            if char and char not in entry.msg.lower():
                continue
            if pattern and not pattern.search(entry.msg):
                continue
            yield entry

    def context(self, round_, index, radius=25):
        entries = self.entries(round_)
        return entries[max(0, index - radius) : index + radius + 1]

    def _read(self, path):
        for file in sorted(path.glob("*.log.json")):
            name = file.name[: -len(".log.json")]
            with file.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    entry = _parse(line, name)
                    if entry:
                        yield entry


def _parse(line, file):
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    msg = raw.get("msg") or ""
    cat = raw.get("cat") or file
    if cat.startswith("href"):
        return None
    ts = _normalize_ts(raw.get("ts", ""))
    if not ts:
        return None
    actor = ACTOR_RE.search(msg)
    loc = LOC_RE.search(msg)
    return Entry(
        index=0,
        ts=ts,
        cat=cat,
        msg=msg,
        ckey=actor.group("ckey").lower() if actor and actor.group("ckey") != "*no key*" else "",
        char=actor.group("char") if actor else "",
        area=loc.group("area") if loc else "",
        x=int(loc.group("x")) if loc else 0,
        y=int(loc.group("y")) if loc else 0,
        z=int(loc.group("z")) if loc else 0,
        file=file,
    )


def _normalize_ts(ts):
    ts = str(ts)
    if re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", ts):
        return ts
    try:
        return datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S.000")
    except (ValueError, OverflowError):
        return ""


def _dt(ts):
    return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")


def round_map(round_):
    for perf in round_.path.glob("perf-*.csv"):
        parts = perf.stem.split("-", 2)
        if len(parts) == 3:
            return parts[2]
    return None


def round_started(round_):
    for file in ("game", "config"):
        path = round_.path / f"{file}.log.json"
        if path.exists():
            with path.open(encoding="utf-8", errors="replace") as handle:
                first = handle.readline()
            try:
                return datetime.strptime(json.loads(first)["ts"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
    return None
