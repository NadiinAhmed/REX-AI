"""Content generators. Both implement the same `ContentGenerator` protocol.

LLMContentGenerator   LangChain structured output (Pydantic schema) -> JSON fallback -> error
TemplateContentGenerator  deterministic copy built only from approved facts (no API key needed)

The agent tries the LLM first and falls back to templates on any GenerationError, so a demo
never fails because of a provider outage or a malformed model answer.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from src.marketing.models import CampaignBrief, CampaignObjective, CourseFacts, DraftVariation, Platform, StrategyPlan
from src.marketing.platforms import spec_for
from src.marketing.prompt_builder import MarketingPromptBuilder
from src.services.llm_service import ChatMessage, LLMError, LLMService
from src.utils.logging_config import get_logger

logger = get_logger("marketing.generator")


class GenerationError(Exception):
    """The generator could not produce valid variations. The caller falls back."""


class ContentGenerator(Protocol):
    name: str

    def generate(self, brief: CampaignBrief, facts: CourseFacts, strategy: StrategyPlan, platform: Platform,
                 lang: str, feedback: list[str]) -> list[DraftVariation]: ...


# ---------------------------------------------------------------- LLM output schema
class LLMVariation(BaseModel):
    angle: str = ""
    headline: str
    body: str
    cta: str
    hashtags: list[str] = Field(default_factory=list)
    email_subject: str | None = None


class LLMDraftSet(BaseModel):
    """The structured output we ask the model for."""
    variations: list[LLMVariation] = Field(min_length=1)


def _clean_hashtag(tag: str) -> str | None:
    tag = re.sub(r"[\s#]+", "", tag.strip())
    return f"#{tag}" if tag else None


def _to_variations(result: object, brief: CampaignBrief, platform: Platform, lang: str) -> list[DraftVariation]:
    try:
        draft_set = result if isinstance(result, LLMDraftSet) else LLMDraftSet.model_validate(result)
        out = []
        for v in draft_set.variations[: brief.variations]:
            subject = (v.email_subject or v.headline) if platform == Platform.EMAIL else None
            tags = [t for t in (_clean_hashtag(h) for h in v.hashtags) if t] if spec_for(platform).max_hashtags else []
            out.append(DraftVariation(platform=platform, language=lang, angle=v.angle, headline=v.headline.strip(),
                                      body=v.body.strip(), cta=v.cta.strip(), hashtags=tags, email_subject=subject))
    except (ValidationError, TypeError) as exc:
        raise GenerationError(f"Model output did not match the schema: {exc}") from exc
    if not out:
        raise GenerationError("Model returned no variations.")
    return out


def _parse_json(text: str) -> object:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = clean.find("{"), clean.rfind("}")
    if start == -1 or end == -1:
        raise GenerationError("Model output contained no JSON object.")
    try:
        return json.loads(clean[start:end + 1])
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Model output was not valid JSON: {exc.msg}") from exc


class LLMContentGenerator:
    def __init__(self, llm: LLMService, prompts: MarketingPromptBuilder | None = None) -> None:
        self._llm = llm
        self._prompts = prompts or MarketingPromptBuilder()
        self.name = f"llm:{llm.name}"

    def generate(self, brief: CampaignBrief, facts: CourseFacts, strategy: StrategyPlan, platform: Platform,
                 lang: str, feedback: list[str]) -> list[DraftVariation]:
        variables = self._prompts.variables(brief, facts, strategy, platform, lang, feedback)
        chat_model = getattr(self._llm, "chat_model", None)
        if chat_model is not None:                       # LangChain path: typed structured output
            try:
                structured = chat_model.with_structured_output(LLMDraftSet)
                return _to_variations(structured.invoke(self._prompts.messages(variables)), brief, platform, lang)
            except NotImplementedError:
                pass                                     # model without tool calling: use the JSON path
            except GenerationError as exc:
                logger.warning("Structured output invalid (%s); trying the JSON path", exc)
            except Exception as exc:                     # provider errors, parser errors
                logger.warning("Structured output failed (%s); trying the JSON path", type(exc).__name__)
        messages = self._prompts.messages(variables, json_mode=True)
        try:
            text = self._llm.generate(messages[0].content, [ChatMessage("user", messages[1].content)])
        except LLMError as exc:
            raise GenerationError(str(exc)) from exc
        return _to_variations(_parse_json(text), brief, platform, lang)


# ---------------------------------------------------------------- deterministic templates
_CTA = {
    CampaignObjective.AWARENESS: ("Learn more about the program", "اعرف المزيد عن البرنامج"),
    CampaignObjective.LEAD_GENERATION: ("Request the course details", "اطلب تفاصيل البرنامج"),
    CampaignObjective.ENROLLMENT: ("Register your interest now", "سجّل اهتمامك الآن"),
    CampaignObjective.RE_ENGAGEMENT: ("Continue your journey: request the details", "استكمل رحلتك واطلب التفاصيل"),
}


def _first_sentence(text: str | None, limit: int = 260) -> str:
    if not text:
        return ""
    sentence = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[:limit].rsplit(" ", 1)[0] + "…"


def _camel(text: str) -> str:
    return "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", text))


def _items(value: str | None, n: int = 3) -> list[str]:
    return [v.strip() for v in (value or "").split(";") if v.strip()][:n]


class TemplateContentGenerator:
    """Deterministic, bilingual copy that states only facts present in CourseFacts."""
    name = "deterministic"

    def generate(self, brief: CampaignBrief, facts: CourseFacts, strategy: StrategyPlan, platform: Platform,
                 lang: str, feedback: list[str]) -> list[DraftVariation]:
        spec = spec_for(platform)
        objective = brief.objective or CampaignObjective.AWARENESS
        ar = lang == "ar"
        title = (facts.title_ar or facts.title) if ar else facts.title
        careers = _items(facts.value("career_paths"))
        tools = _items(facts.value("tools"))
        hours = f"{facts.duration_hours:g}" if facts.duration_hours else None
        cta = _CTA[objective][1 if ar else 0]

        headlines = self._headlines(title, careers, tools, ar)
        lines_common = []
        audience = brief.audience or ", ".join(_items(facts.value("target_audience"), 2))
        if audience:
            lines_common.append(f"لمن هذا البرنامج: {audience}." if ar else f"Who it's for: {audience}.")
        if hours:
            lines_common.append(f"مدة البرنامج: {hours} ساعة." if ar else f"Duration: {hours} hours.")
        if careers:
            lines_common.append(("يؤهلك لمسارات مثل: " if ar else "Career paths: ") + ", ".join(careers) + ".")
        variations = []
        for i in range(brief.variations):
            key, headline = headlines[i % len(headlines)]
            body_lines = [self._opening(key, facts, tools, ar)] + lines_common
            if facts.value("capstone"):
                body_lines.append("ستنفّذ مشروع تخرج عملي يضاف إلى ملف أعمالك." if ar
                                  else f"Capstone: {_first_sentence(facts.value('capstone'), 180)}")
            for offer in facts.offers:                     # approved + currently valid only
                body_lines.append(("العرض: " if ar else "Offer: ") + offer.describe(lang))
            if "fee" in facts.missing_fields and objective != CampaignObjective.AWARENESS:
                body_lines.append("تواصل معنا لمعرفة الرسوم والمواعيد." if ar
                                  else "Contact us for fees and dates.")
            body = "\n".join(line for line in body_lines if line)
            if len(body) > spec.max_body_chars:
                body = body[: spec.max_body_chars].rsplit("\n", 1)[0]
            tags = self._hashtags(facts, careers, ar)[: spec.max_hashtags] if spec.max_hashtags else []
            variations.append(DraftVariation(
                platform=platform, language=lang,
                angle=(strategy.variation_angles[i] if i < len(strategy.variation_angles) else key),
                headline=headline[: spec.max_headline_chars], body=body, cta=cta, hashtags=tags,
                email_subject=headline[: spec.max_subject_chars] if spec.needs_subject else None))
        return variations

    @staticmethod
    def _headlines(title: str, careers: list[str], tools: list[str], ar: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if careers:
            out.append(("career", f"ابدأ طريقك نحو {careers[0]} مع {title}" if ar
                        else f"Start your path to {careers[0]} with {title}"))
        out.append(("project", f"{title}: تعلّم بالتطبيق العملي" if ar else f"{title}: learn by building real work"))
        if tools:
            out.append(("skills", f"اكتسب مهارات {', '.join(tools)}" if ar else f"Get hands-on with {', '.join(tools)}"))
        return out

    @staticmethod
    def _opening(key: str, facts: CourseFacts, tools: list[str], ar: bool) -> str:
        if ar:
            name = facts.title_ar or facts.title
            return {"career": f"برنامج {name} يجهزك لسوق العمل.",
                    "project": f"في برنامج {name} تتعلم من خلال العمل على مشروعات عملية.",
                    "skills": f"في برنامج {name} تعمل على أدوات مثل {', '.join(tools)}."}.get(key, f"برنامج {name}.")
        if key == "skills" and tools:
            return f"In {facts.title} you work with tools such as {', '.join(tools)}."
        return _first_sentence(facts.value("description")) or f"{facts.title}."

    @staticmethod
    def _hashtags(facts: CourseFacts, careers: list[str], ar: bool) -> list[str]:
        tags = [f"#{_camel(facts.title)}"] + [f"#{_camel(c)}" for c in careers[:2]]
        tags += ["#تدريب", "#مهارات", "#وظائف"] if ar else ["#Training", "#Skills", "#Careers"]
        return list(dict.fromkeys(t for t in tags if len(t) > 1))
