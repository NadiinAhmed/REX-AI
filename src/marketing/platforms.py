"""Platform format rules, shared by the generator (as instructions) and the evaluator (as checks).

The numbers are TEAM GUIDELINE values chosen for readable MVP output, not the platforms'
official limits. Change them here and both generation and evaluation follow.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.marketing.models import Platform


@dataclass(frozen=True)
class PlatformSpec:
    platform: Platform
    label_en: str
    label_ar: str
    max_body_chars: int
    min_hashtags: int = 0
    max_hashtags: int = 0
    needs_subject: bool = False
    max_subject_chars: int = 80
    max_headline_chars: int = 100
    max_cta_chars: int = 80
    style_en: str = ""

    def label(self, lang: str) -> str:
        return self.label_ar if lang == "ar" else self.label_en

    def instructions(self) -> str:
        parts = [f"Body at most {self.max_body_chars} characters.", f"Headline at most {self.max_headline_chars} characters.",
                 f"CTA at most {self.max_cta_chars} characters, one clear action."]
        if self.max_hashtags == 0:
            parts.append("No hashtags.")
        else:
            parts.append(f"Between {self.min_hashtags} and {self.max_hashtags} hashtags.")
        if self.needs_subject:
            parts.append(f"Include an email subject line of at most {self.max_subject_chars} characters.")
        if self.style_en:
            parts.append(self.style_en)
        return " ".join(parts)


PLATFORM_SPECS: dict[Platform, PlatformSpec] = {
    Platform.SOCIAL_CAPTION: PlatformSpec(Platform.SOCIAL_CAPTION, "Social media caption", "منشور سوشيال ميديا",
                                          max_body_chars=600, max_hashtags=5,
                                          style_en="Short, scroll-stopping first line."),
    Platform.FACEBOOK: PlatformSpec(Platform.FACEBOOK, "Facebook ad", "إعلان فيسبوك", max_body_chars=900,
                                    max_hashtags=3, style_en="Ad copy: hook, benefit, proof from the data, CTA."),
    Platform.INSTAGRAM: PlatformSpec(Platform.INSTAGRAM, "Instagram ad", "إعلان إنستجرام", max_body_chars=1500,
                                     min_hashtags=3, max_hashtags=10,
                                     style_en="Visual, energetic, short paragraphs."),
    Platform.LINKEDIN: PlatformSpec(Platform.LINKEDIN, "LinkedIn post", "منشور لينكدإن", max_body_chars=2000,
                                    max_hashtags=5, style_en="Professional, career-focused."),
    Platform.EMAIL: PlatformSpec(Platform.EMAIL, "Marketing email", "بريد تسويقي", max_body_chars=3000,
                                 needs_subject=True, style_en="Greeting, 2-3 short paragraphs, single CTA."),
    Platform.WHATSAPP: PlatformSpec(Platform.WHATSAPP, "WhatsApp message", "رسالة واتساب", max_body_chars=700,
                                    style_en="Personal and brief, like a message from a person."),
}


def spec_for(platform: Platform) -> PlatformSpec:
    return PLATFORM_SPECS[platform]
