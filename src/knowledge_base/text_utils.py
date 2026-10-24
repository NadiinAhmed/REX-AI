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
    # Function/filler words that must never count as a course topic (bug B1/B2: "into",
    # "build" or "where" alone were enough to produce a recommendation).
    "into", "but", "don", "dont", "where", "there", "then", "than", "when", "who", "why",
    "will", "should", "could", "your", "our", "we", "they", "them", "its", "at", "by", "from",
    "up", "out", "as", "if", "all", "other", "kind", "type", "help", "tell", "show", "give",
    "find", "looking", "look", "go", "going", "use", "using", "build", "make", "made", "new",
    "cheaper", "cheap", "expensive", "less", "under", "below", "over", "very", "too", "so",
    "not", "no", "yes", "ok", "okay", "please", "thanks", "thank", "hi", "hello", "hey",
    "programs", "program", "track", "tracks", "academy", "offer", "available", "there",
    "take", "join", "enroll", "register", "apply", "sign",
    # Arabic (normalized forms)
    "في", "من", "الي", "على", "علي", "عن", "و", "او", "انا", "اريد", "عايز", "عاوز", "ابغي",
    "هل", "ما", "ماذا", "كورس", "كورسات", "دوره", "دورات", "تعلم", "اتعلم", "مهتم", "ممكن", "لو",
    "ان", "هو", "هي", "عندكم", "عندك", "اي", "ايه", "شيء", "حاجه",
    "ابقي", "بس", "لكن", "ازاي", "كيف", "فين", "اين", "متي", "امتي", "مين", "ليه", "لماذا",
    "ارخص", "اقل", "اكتر", "اكثر", "جدا", "برنامج", "برامج", "مسار", "مسارات", "الاكاديميه",
    "اكاديميه", "اسجل", "التسجيل", "تسجيل", "اشترك",
    # Question-attribute words (fees, schedule, ...) are never topics (bug B4)
    "مواعيد", "موعد", "ميعاد", "اسعار", "سعر", "مدربين", "مدرب", "مدرسين", "محتوي", "مده",
    "متطلبات", "رسوم", "تكلفه", "بكام", "بكم",
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
    # Official catalog domains (Andalusia Academy portfolio 2026-2027)
    "testing": "testing", "tester": "testing", "test": "testing", "tests": "testing",
    "qa": "testing", "quality": "testing", "اختبار": "testing", "اختبارات": "testing",
    "تيستنج": "testing", "تيستر": "testing", "جوده": "testing",
    "flutter": "flutter", "فلاتر": "flutter", "dart": "flutter", "دارت": "flutter",
    "mobile": "mobile", "موبايل": "mobile", "تطبيقات": "mobile", "app": "mobile", "apps": "mobile",
    "android": "mobile", "ios": "mobile", "اندرويد": "mobile",
    "engineering": "engineering", "هندسه": "engineering", "مهندس": "engineering",
    "engineer": "engineering",
    "dotnet": ".net", "net": ".net", "دوت": ".net", "csharp": "c#",
    "رياكت": "react", "فرونت": "frontend", "باك": "backend", "فول": "full", "ستاك": "stack",
    "fullstack": "full stack",
    "scientist": "science", "علم": "science", "علوم": "science",
    "llm": "ai", "llms": "ai", "rag": "ai", "تعلم الاله": "machine learning",
    "learning": "learning",
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
