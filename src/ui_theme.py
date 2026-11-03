"""Andalusia Academy look for the Streamlit app, taken from the academy's own programs portfolio:
deep maroon banners, a copper accent rule, cream cards with a maroon left edge, tracked small labels.

Colors live in .streamlit/config.toml (buttons, links, widgets) and in BRAND below (custom CSS).
apply_theme() is called once per run in app.py, so every page, including the admin pages, gets it.
"""
from __future__ import annotations

import base64
from html import escape

import streamlit as st

from src.config import PROJECT_ROOT

LOGO = PROJECT_ROOT / "assets" / "andalusia_logo.png"      # full logo (cover, sidebar)
MARK = PROJECT_ROOT / "assets" / "andalusia_mark.png"      # square mark (chat avatar, favicon)

BRAND = {
    "maroon": "#6B1F1F",     # banners, headings, primary buttons
    "brick": "#8E3B34",      # small labels on white
    "copper": "#B5603F",     # accent rules
    "rose": "#E8B7A3",       # labels on maroon
    "cream": "#FBF7F2",      # cards, sidebar
    "tan": "#DCC7AF",        # card borders
    "muted": "#6F6463",      # captions
}

_CSS = """
<style>
/* Thin maroon-to-copper rule across the top, as on the portfolio's cover. */
[data-testid="stHeader"] {{ background: #FFFFFF;
  border-top: 4px solid {maroon}; border-image: linear-gradient(90deg, {maroon}, {copper}) 1; }}
/* Arabic paragraphs pick their own direction. */
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li
  {{ unicode-bidi: plaintext; text-align: start; }}
/* Page titles on admin pages: maroon with a short copper rule, like the portfolio's section titles. */
[data-testid="stHeading"] h1 {{ color: {maroon}; font-weight: 800; letter-spacing: -0.01em; }}
[data-testid="stHeading"] h1::after {{ content: ""; display: block; width: 56px; height: 4px;
  margin-top: .5rem; background: {copper}; }}
[data-testid="stHeading"] h2, [data-testid="stHeading"] h3 {{ color: {maroon}; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: {muted}; opacity: 1; }}
/* Banner: the track header from the portfolio. */
.rex-banner {{ background: {maroon}; color: #FFFFFF; border-radius: 6px 6px 0 0;
  border-bottom: 3px solid {copper}; padding: 1.1rem 1.4rem 1rem; margin-bottom: 1.25rem; }}
.rex-banner .kicker {{ color: {rose}; font-size: .72rem; letter-spacing: .22em; text-transform: uppercase; }}
.rex-banner .title {{ font-size: 1.85rem; font-weight: 800; line-height: 1.2; margin: .2rem 0 0; }}
.rex-banner .sub {{ color: #F3E3DA; font-size: .95rem; margin-top: .45rem; }}
/* Cards with a maroon left edge (portfolio "Job market ready" boxes). Keyed containers only. */
[class*="st-key-card_"] {{ background: {cream}; border: 1px solid {tan}; border-left: 4px solid {maroon};
  border-radius: 6px; padding: 1rem 1.1rem; }}
[class*="st-key-card_"] h3 {{ font-size: .8rem; letter-spacing: .16em; text-transform: uppercase; color: {brick}; }}
[class*="st-key-card_"] [data-baseweb="input"] {{ background: #FFFFFF; border: 1px solid {tan}; }}
[class*="st-key-card_"] [data-baseweb="input"] input {{ background: #FFFFFF; }}
.rex-banner .sub div {{ text-align: start; }}
.rex-signed-in {{ border-left: 4px solid {maroon}; background: #FFFFFF; color: {maroon}; font-weight: 600;
  padding: .55rem .8rem; border-radius: 4px; }}
/* Sources list and other expanders: cream, tan border. */
[data-testid="stExpander"] details {{ background: {cream}; border-color: {tan}; }}
[data-testid="stSidebar"] {{ border-right: 1px solid {tan}; }}
.rex-cover {{ text-align: center; margin: .5rem 0 2rem; }}
.rex-cover .title {{ color: {maroon}; font-size: 3rem; font-weight: 800; letter-spacing: .02em; margin-top: .6rem; }}
.rex-cover .rule {{ width: 64px; height: 4px; background: {copper}; margin: .6rem auto .9rem; }}
.rex-cover .tagline {{ color: {muted}; font-size: .8rem; letter-spacing: .22em; text-transform: uppercase; }}
.rex-footer {{ margin-top: 2.5rem; padding-top: .7rem; border-top: 1px solid {tan}; text-align: center;
  color: #A89A92; font-size: .72rem; letter-spacing: .2em; text-transform: uppercase; }}
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS.format(**BRAND), unsafe_allow_html=True)
    if LOGO.exists():
        st.logo(str(LOGO), size="large")


def banner(title: str, kicker: str = "Andalusia Academy", subtitle: str | None = None) -> None:
    """Maroon page header. All text is escaped, so it is safe for any string."""
    lines = "".join(f'<div dir="auto">{escape(line)}</div>' for line in (subtitle or "").splitlines())
    sub = f'<div class="sub">{lines}</div>' if lines else ""
    st.markdown(f'<div class="rex-banner"><div class="kicker">{escape(kicker)}</div>'
                f'<div class="title">{escape(title)}</div>{sub}</div>', unsafe_allow_html=True)


def cover(title: str, tagline: str) -> None:
    """Centered cover block for the Welcome page: logo, maroon title, copper rule, tagline."""
    logo = ""
    if LOGO.exists():
        data = base64.b64encode(LOGO.read_bytes()).decode()
        logo = f'<img src="data:image/png;base64,{data}" alt="Andalusia Academy" width="120">'
    st.markdown(f'<div class="rex-cover">{logo}<div class="title">{escape(title)}</div>'
                f'<div class="rule"></div><div class="tagline">{escape(tagline)}</div></div>',
                unsafe_allow_html=True)


def footer() -> None:
    st.markdown('<div class="rex-footer">Andalusia Academy · REX AI · 2026–2027</div>', unsafe_allow_html=True)


def assistant_avatar() -> str | None:
    return str(MARK) if MARK.exists() else None


USER_AVATAR = ":material/person:"


def signed_in_note(text: str) -> None:
    st.markdown(f'<div class="rex-signed-in">{escape(text)}</div>', unsafe_allow_html=True)
