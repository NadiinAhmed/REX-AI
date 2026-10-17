"""Bilingual (Arabic/English) text normalization and query expansion for retrieval."""
from __future__ import annotations

import re

_AR_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u0640]")
_TOKEN = re.compile(r"[a-z0-9+#]+|[\u0621-\u064A]+")

STOPWORDS = {
    # English
    "a", "an", "the", "and", "or", "to", "for", "of", "in", "on", "with", "is", "are", "i",
    "me", "my", "want", "would", "like", "learn", "course", "courses", "about", "what", "which",
    "do", "you", "have", "any", "can", "how", "much", "need", "some", "be", "am", "it", "this",
    "that", "please", "recommend", "suggest", "interested", "become", "work", "job",
    "actually", "really", "also", "maybe", "just", "now", "instead", "more", "get", "know",
    "think", "something", "things", "thing", "good", "best", "start", "career", "field",
    # Arabic (normalized forms)
    "في", "من", "الي", "على", "علي", "عن", "و", "او", "انا", "اريد", "عايز", "عاوز", "ابغي",
    "هل", "ما", "ماذا", "كورس", "كورسات", "دوره", "دورات", "تعلم", "اتعلم", "مهتم", "ممكن", "لو",
    "ان", "هو", "هي", "عندكم", "عندك", "اي", "ايه", "شيء", "حاجه",
    # Arabic preference/filler words that are never course topics
    "مبتدي", "متوسط", "متقدم", "اونلاين", "حضوري", "ميزانيتي", "ميزانيه", "جنيه", "ريال",
    "خريج", "خريجه", "اشتغل", "اعمل", "اصبح", "اكون", "مرحبا", "اهلا", "شكرا", "السلام", "عليكم",
}

# Arabic and English variants mapped to one canonical English term.
SYNONYMS: dict[str, str] = {
    "برمجه": "programming", "برمجة": "programming", "coding": "programming", "developer": "programming",
    "مبرمج": "programming",
    "بايثون": "python",
    "بيانات": "data", "تحليل": "analysis", "analytics": "analysis", "analyst": "analysis",
    "محلل": "analysis",
    "ذكاء": "ai", "اصطناعي": "ai", "artificial": "ai", "intelligence": "ai",
    "اله": "machine learning", "ml": "machine learning",
    "تسويق": "marketing", "رقمي": "digital", "سوشيال": "social", "ميديا": "media",
    "اداره": "management", "مشاريع": "project", "مشروع": "project", "pmp": "project",
    "شبكات": "networking", "network": "networking", "networks": "networking",
    "امن": "cybersecurity", "سيبراني": "cybersecurity", "security": "cybersecurity",
    "cyber": "cybersecurity",
    "تصميم": "design", "جرافيك": "graphic", "واجهات": "ui", "ux": "ui",
    "محاسبه": "accounting", "ماليه": "finance", "financial": "finance",
    "انجليزي": "english", "انجليزيه": "english", "لغه": "language",
    "ويب": "web", "مواقع": "web", "website": "web", "websites": "web",
    "اكسل": "excel", "spreadsheets": "excel",
    "سحابه": "cloud", "سحابيه": "cloud",
}


def normalize_arabic(text: str) -> str:
    text = _AR_DIACRITICS.sub("", text)
    text = re.sub("[إأآا]", "ا", text)
    text = text.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    return text


def _strip_arabic_prefix(token: str) -> str:
    for prefix in ("وال", "بال", "لل", "ال"):
        if token.startswith(prefix) and len(token) - len(prefix) >= 3:
            return token[len(prefix):]
    # Conjunction "و" glued to a word: strip only when the remainder is a known word,
    # so genuine words starting with "و" (e.g. "وظيفه") are left intact.
    if token.startswith("و") and len(token) >= 4:
        rest = _strip_arabic_prefix(token[1:])
        if rest in STOPWORDS or rest in SYNONYMS:
            return rest
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, normalize Arabic, drop stopwords, map synonyms to canonical English."""
    text = normalize_arabic(text.lower())
    tokens: list[str] = []
    for raw in _TOKEN.findall(text):
        tok = _strip_arabic_prefix(raw)
        if tok in STOPWORDS or raw in STOPWORDS or len(tok) < 2:
            continue
        canonical = SYNONYMS.get(tok, tok)
        tokens.extend(canonical.split())
    return tokens


def contains_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))
