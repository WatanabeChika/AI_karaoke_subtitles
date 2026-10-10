"""LRC lyric selection and explicit Japanese readings."""

import re
import unicodedata
from typing import Dict, List

TRANSLATION_PAIR_TOLERANCE_SEC = 0.05
TIME_TAG = re.compile(r"\[(\d+):([0-5]?\d)(?:\.(\d{1,3}))?\]")
STAFF = re.compile(
    r"^(?:[作编]?词|[作编]?曲|演唱|后期|混音|制作人?|原唱|翻唱|策划|美工|和声|"
    r"吉他|贝斯|鼓|键盘|弦乐|Vocal|Lyrics?|Music|Arrangement|Mix|Master|Staff|by|翻译)\s*[:：\s]",
    re.IGNORECASE,
)
READING = re.compile(
    r"(?P<base>[\u3400-\u9fff々〆〇ヶ]+)[（(]"
    r"(?P<kana>[ぁ-ゖァ-ヿｦ-ﾟｰﾞﾟー]+)[）)]"
)
ANNOTATION = re.compile(r"^\s*(?:\[.*?\]|【.*?】)\s*$")
INLINE_HARMONY = re.compile(r"[（(](?P<body>[^()（）]+)[）)]")
# These characters are strong signals that a parenthetical line is a Chinese
# translation rather than a Japanese harmony.  Parentheses themselves are not
# enough: Japanese all-kanji harmonies such as （愛） must remain available.
SIMPLIFIED_CHINESE_MARKERS = set(
    "的一是了不在有我他她们这那哪你您也都而及与或但就还很最把被让给"
    "从到对为没能会可将其所个么什谁吗呢吧啊呀哦和说着过来去上下里外"
    "中时现今已才再又如果因为所以然后告诉其实"
)


def _split_translation(text: str) -> str:
    for i, ch in enumerate(text):
        if ch in "/／" and (
            ch == "／" or (i > 0 and i + 1 < len(text) and text[i - 1].isspace() and text[i + 1].isspace())
        ):
            return text[:i].strip()
    return text.strip()


def _clean_parenthetical_gap(text: str) -> str:
    return re.sub(r"[ \t\u3000]+", " ", text).strip()


def split_inline_harmony_parts(text: str) -> List[Dict]:
    """Return main and parenthetical fragments in their displayed order."""
    parts: List[Dict] = []
    cursor = 0
    for index, match in enumerate(INLINE_HARMONY.finditer(text)):
        main_fragment = _clean_parenthetical_gap(text[cursor:match.start()])
        if main_fragment:
            parts.append({"kind": "main", "text": main_fragment})
        harmony = match.group("body").strip()
        if harmony:
            parts.append({"kind": "harmony", "index": index, "text": harmony})
        cursor = match.end()
    main_fragment = _clean_parenthetical_gap(text[cursor:])
    if main_fragment:
        parts.append({"kind": "main", "text": main_fragment})
    return parts


def extract_inline_harmonies(text: str) -> tuple[str, List[Dict]]:
    """Separate inline parentheticals without deciding whether to keep them."""
    matches = list(INLINE_HARMONY.finditer(text))
    if not matches:
        return text, []

    main_text = _clean_parenthetical_gap(INLINE_HARMONY.sub("", text))
    if not any(unicodedata.category(char)[0] in "LN" for char in main_text):
        # A standalone parenthetical line is harmony itself, not an inline
        # candidate competing with another vocal on the same lyric line.
        return text, []

    return main_text, [
        {"index": index, "text": match.group("body").strip()}
        for index, match in enumerate(matches)
        if match.group("body").strip()
    ]


def remove_inline_harmonies(text: str, removed_indices: set[int]) -> str:
    """Remove selected inline parentheticals while preserving the others."""
    index = 0

    def replace(match: re.Match) -> str:
        nonlocal index
        remove = index in removed_indices
        index += 1
        return "" if remove else match.group(0)

    return _clean_parenthetical_gap(INLINE_HARMONY.sub(replace, text))


def _extract_readings(text: str) -> tuple[str, Dict[str, str]]:
    readings: Dict[str, str] = {}

    def replace(match: re.Match) -> str:
        base = match.group("base")
        kana = unicodedata.normalize("NFKC", match.group("kana"))
        kana = "".join(
            chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch
            for ch in kana
        )
        readings[base] = kana
        return base

    return READING.sub(replace, text), readings


def _char_profile(text: str) -> Dict[str, int]:
    return {
        # Do not count Japanese punctuation/iteration marks (for example
        # ``ヽ`` in Chinese emoji text) as kana. Treating those symbols as
        # Japanese made a same-timestamp Chinese translation survive the
        # Japanese lyric filter and subsequently enter CTC alignment.
        "kana": sum(
            (("ぁ" <= ch <= "ゖ") or ("ァ" <= ch <= "ヿ"))
            and ch not in "ヽヾ・ー"
            for ch in text
        ),
        "cjk": sum("\u3400" <= ch <= "\u9fff" for ch in text),
        "latin": sum("a" <= ch.lower() <= "z" for ch in text),
    }


def _is_japanese_parenthetical(text: str) -> bool:
    if not re.fullmatch(r"[（(].+[）)]", text):
        return False
    body = text[1:-1]
    profile = _char_profile(body)
    if profile["kana"]:
        return True
    return not any(char in SIMPLIFIED_CHINESE_MARKERS for char in body)


def _select_cluster(cluster: List[Dict], lang: str) -> List[Dict]:
    if len(cluster) == 1:
        return cluster
    profiles = [_char_profile(item["text"]) for item in cluster]
    if lang.lower().startswith("ja"):
        japanese = [
            item for item, profile in zip(cluster, profiles)
            if profile["kana"] or _is_japanese_parenthetical(item["text"])
        ]
        if japanese:
            return japanese
        return [cluster[0]]
    if lang.lower().startswith("zh"):
        chinese = [item for item, p in zip(cluster, profiles) if p["cjk"] and not p["kana"]]
        return chinese or [cluster[0]]
    if lang.lower().startswith("en"):
        english = [item for item, p in zip(cluster, profiles) if p["latin"] and not p["kana"] and not p["cjk"]]
        return english or [cluster[0]]
    return [cluster[0]]


def parse_lrc_data_strict(lrc_path: str, lang: str = "ja") -> List[Dict]:
    candidates: List[Dict] = []
    with open(lrc_path, "r", encoding="utf-8-sig") as stream:
        for raw in stream:
            line = raw.strip()
            tags = list(TIME_TAG.finditer(line))
            if not tags or tags[0].start() != 0:
                continue
            text = line[tags[-1].end():].strip()
            if not text or STAFF.match(text) or ANNOTATION.fullmatch(text):
                continue
            text = _split_translation(text)
            # Explicit parenthetical readings are metadata, not harmony.
            text, readings = _extract_readings(text)
            main_text, inline_harmonies = extract_inline_harmonies(text)
            inline_parts = split_inline_harmony_parts(text) if inline_harmonies else []
            if not text:
                continue
            for match in tags:
                minutes, seconds, fraction = match.groups()
                timestamp = int(minutes) * 60 + int(seconds) + int((fraction or "0").ljust(3, "0")) / 1000
                candidates.append({
                    "time": timestamp,
                    "text": text,
                    "main_text": main_text,
                    "inline_harmonies": inline_harmonies,
                    "inline_parts": inline_parts,
                    "readings": readings,
                })

    candidates.sort(key=lambda item: item["time"])
    clusters: List[List[Dict]] = []
    for item in candidates:
        if clusters and item["time"] - clusters[-1][-1]["time"] <= TRANSLATION_PAIR_TOLERANCE_SEC:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    selected: List[Dict] = []
    for cluster in clusters:
        for item in _select_cluster(cluster, lang):
            if not selected or selected[-1]["time"] != item["time"] or selected[-1]["text"] != item["text"]:
                selected.append(item)

    return [
        {
            "start_time": line["time"],
            "end_time": selected[i + 1]["time"] if i + 1 < len(selected) else line["time"] + 10,
            "text": line["text"],
            "main_text": line["main_text"],
            "inline_harmonies": line["inline_harmonies"],
            "inline_parts": line["inline_parts"],
            "readings": line["readings"],
        }
        for i, line in enumerate(selected)
    ]
