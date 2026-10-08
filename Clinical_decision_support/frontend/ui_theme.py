from pathlib import Path

import streamlit as st


THEME_TOKENS = {
    "Light": (
        """
        --bg: #f4f7fb; --surface: #ffffff; --surface-soft: #f8fafc;
        --border: #dbe3ec; --border-strong: #c4d0dc; --text: #172b3a;
        --muted: #607487; --primary: #11796f; --primary-hover: #0d655d;
        --success: #167647; --success-soft: #e3f5eb; --warning: #9a5a08;
        --warning-soft: #fff4d8; --danger: #b42318; --danger-soft: #ffebe9;
        --info: #2459a6; --info-soft: #eaf2ff; --purple: #6b46a1;
        --sidebar-bg: #ffffff; --sidebar-border: #dbe3ec;
        --sidebar-text: #23384a; --sidebar-muted: #718398;
        --sidebar-hover: #eaf6f4;
        """,
        "rgba(244, 247, 251, .96)",
    ),
    "Dark": (
        """
        --bg: #101923; --surface: #182432; --surface-soft: #202e3d;
        --border: #2c3b4a; --border-strong: #405264; --text: #edf3f8;
        --muted: #a9b8c6; --primary: #45b8a6; --primary-hover: #2e9d8c;
        --success: #79d7a0; --success-soft: #173d2b; --warning: #f3c66d;
        --warning-soft: #483a1c; --danger: #ff9c91; --danger-soft: #4a2525;
        --info: #91baff; --info-soft: #1e3552; --purple: #c5a6f2;
        --sidebar-bg: #131f2c; --sidebar-border: #2c3b4a;
        --sidebar-text: #edf3f8; --sidebar-muted: #a9b8c6;
        --sidebar-hover: #203a45;
        """,
        "rgba(16, 25, 35, .96)",
    ),
}


def apply_theme(theme_name: str) -> None:
    theme_vars, header_background = THEME_TOKENS.get(
        theme_name, THEME_TOKENS["Light"]
    )
    css_path = Path(__file__).with_name("theme.css")
    css = css_path.read_text(encoding="utf-8")
    css = css.replace("{theme_vars}", theme_vars).replace(
        "{header_background}", header_background
    )
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
