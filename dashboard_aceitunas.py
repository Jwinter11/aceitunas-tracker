#!/usr/bin/env python3
# última actualización: 2026-03-23
"""
Dashboard de precios de aceitunas — Aceite Tracker
Uso: streamlit run dashboard_aceitunas.py --server.port 8502
"""

import math
import re
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

DIRECTORIO = Path(__file__).parent

# ---------------------------------------------------------------------------
# Marcas
# ---------------------------------------------------------------------------

MARCAS_DESTACADAS_AC = {"Castell", "Nucete", "La Toscana", "Morixe", "Oliovita", "Vanoli"}
MARCAS_SUPER_AC = {
    "Carrefour", "Jumbo", "Disco", "Vea", "Día", "Coto",
    "Chango Más", "Chango Mas", "La Anónima", "La Anonima",
    "Delicious",   # marca propia de Día
}

COLORES_MARCA_AC = {
    "Castell":     "#2E86AB",
    "Nucete":      "#3B1F2B",
    "La Toscana":  "#B45309",
    "Morixe":      "#16A34A",
    "Oliovita":    "#F18F01",
    "Vanoli":      "#A23B72",
    "Marca Propia":"#6B7280",
    "Otras":       "#9CA3AF",
}

ORDEN_MARCAS_AC = [
    "Castell", "Nucete", "La Toscana", "Morixe", "Oliovita", "Vanoli",
    "Marca Propia", "Otras",
]


# Correcciones directas: DB tiene un nombre incorrecto → nombre real
_MARCA_CORRECCIONES: dict[str, str] = {
    "Toscana":          "La Toscana",
    "Trozos":           "Marvavic",
    "Gordal":           "Ybarra",
    "Premium":          "Castell",
    "La Malaguena":     "La Malagueña",
    "Malagueña":        "La Malagueña",
    "Malague\xf1a":     "La Malagueña",   # encoding fix
}

# Estas extracciones del scraper NO son marcas → se descartan como "Otras"
_PALABRAS_NO_MARCA: set[str] = {
    "Manzanilla", "Enteras", "Entera", "Ajo", "Salmón", "Salm", "Anchoas",
    "Pimiento", "Pimientos", "Orgánicas", "Clásicas", "Clásica", "Ver",
    "Rell.con", "Queso", "Picantes", "Pasta", "Parmesano", "Palmitos",
    "Negr", "Naturales", "Morrones", "Morron", "Morrón", "C/morrón",
    "Jamón", "Jamon", "Jalapeños", "Españolas", "Alcaparras",
    "Aceitunas.verdes", "Check", "Doy", "Picantes", "Aceitunas",
}


def limpiar_marca_ac(marca: str, cadena: str) -> str:
    """Corrige el nombre de marca extraído por el scraper."""
    # Corrección especial por cadena
    if marca in ("Morrón", "Morron", "Morrones", "C/morrón") and cadena == "Coto":
        return "Yovinessa"
    if marca in ("Morrón", "Morron", "Morrones", "C/morrón"):
        return "Marvavic"
    # Correcciones directas
    if marca in _MARCA_CORRECCIONES:
        return _MARCA_CORRECCIONES[marca]
    # Palabras que no son marcas → asignar a la cadena (quedará como Marca Propia)
    if marca in _PALABRAS_NO_MARCA:
        return cadena
    return marca


def categorizar_marca_ac(marca: str) -> str:
    if marca in MARCAS_DESTACADAS_AC:
        return marca
    if marca in MARCAS_SUPER_AC:
        return "Marca Propia"
    return "Otras"


# ---------------------------------------------------------------------------
# Variedades unificadas
# ---------------------------------------------------------------------------

def unificar_variedad(v: str | None) -> str:
    if v is None:
        return "Verde con carozo"
    if "Rellena" in v:
        return "Rellenas"
    if v in ("Verde Picante", "Verde con Ajo", "Verde Ahumada", "Verde Saborizada"):
        return "Saborizadas"
    if v == "Verde":
        return "Verde con carozo"
    if v == "Negra":
        return "Negra con carozo"
    return v  # Verde Descarozada, Negra Descarozada, Kalamata, Mix, etc.


COLORES_VARIEDAD = {
    "Verde con carozo":  "#4CAF50",
    "Verde Descarozada": "#81C784",
    "Verde Rodajada":    "#66BB6A",
    "Negra con carozo":  "#212121",
    "Negra Descarozada": "#424242",
    "Negra Rodajada":    "#616161",
    "Rellenas":          "#FF7043",
    "Saborizadas":       "#795548",
    "Kalamata":          "#4A148C",
    "Mix":               "#90A4AE",
}

COLORS_CADENAS = {
    "Carrefour": "#004B9B", "Jumbo": "#E63329", "Disco": "#00A651",
    "Vea": "#F7931E", "Día": "#ED1C24", "Chango Mas": "#7B2D8B",
    "Coto": "#002D72", "La Anonima": "#C8102E",
}

GRAMAJE_GRUPOS = [
    "1) hasta 140g", "2) 141-230g", "3) 231-330g",
    "4) 331-400g",   "5) 401-600g", "6) 601g+",
]
GRAMAJE_GRUPOS_LABELS = {
    "1) hasta 140g": "hasta 140g", "2) 141-230g": "141-230g",
    "3) 231-330g":   "231-330g",   "4) 331-400g": "331-400g",
    "5) 401-600g":   "401-600g",   "6) 601g+":    "601g+",
}


def gramaje_grupo_label(g): return GRAMAJE_GRUPOS_LABELS.get(g, g or "Sin gramaje")


# ---------------------------------------------------------------------------
# Detección de envase (Doypack / Frasco / Lata / Bandeja / Sin detectar)
# ---------------------------------------------------------------------------

_DOYPACK_TOKENS = {"doypack", "doy", "dp", "sachet", "pouch", "pou", "flexible", "bolsa", "sobre"}
_FRASCO_TOKENS  = {"frasco", "fco", "frco", "vidrio", "pote"}
_LATA_TOKENS    = {"lata", "bote", "tarro"}

_ENVASE_EXCEL   = DIRECTORIO / "revision_envase.xlsx"
_ENVASE_OVERRIDES: dict[str, str] = {}

def _cargar_overrides_envase():
    """Carga las correcciones manuales del Excel de revisión."""
    if not _ENVASE_EXCEL.exists():
        return
    try:
        df_sin = pd.read_excel(_ENVASE_EXCEL, sheet_name="Sin_detectar_REVISAR")
        corr_col = next((c for c in df_sin.columns if "orre" in c.lower()), None)
        if corr_col and "Producto" in df_sin.columns:
            for _, row in df_sin.iterrows():
                prod = str(row["Producto"]).strip()
                val  = str(row[corr_col]).strip() if pd.notna(row[corr_col]) else ""
                if prod and val and val not in ("", "nan"):
                    _ENVASE_OVERRIDES[prod] = val
    except Exception:
        pass

_cargar_overrides_envase()


def _tokenize_ac(text: str) -> list[str]:
    return re.findall(r"[a-záéíóúüñ0-9]+", text.lower())


def detectar_envase_nombre(nombre: str) -> str:
    nombre = (nombre or "").strip()
    # 1) Corrección manual del Excel
    if nombre in _ENVASE_OVERRIDES:
        return _ENVASE_OVERRIDES[nombre]
    # 2) Detección por keywords
    tokens = _tokenize_ac(nombre)
    tok_set = set(tokens)
    doy_hits, fra_hits, lat_hits = [], [], []
    for t in _DOYPACK_TOKENS:
        if t == "doy":
            if "doy" in tok_set:
                doy_hits.append("doy")
        elif t in tok_set:
            doy_hits.append(t)
    for t in _FRASCO_TOKENS:
        if t in tok_set:
            fra_hits.append(t)
    for t in _LATA_TOKENS:
        if t in tok_set:
            lat_hits.append(t)
    total_hits = len(doy_hits) + len(fra_hits) + len(lat_hits)
    if total_hits == 0:
        return "Sin detectar"
    if doy_hits and not fra_hits and not lat_hits:
        return "Doypack"
    if fra_hits and not doy_hits and not lat_hits:
        return "Frasco"
    if lat_hits and not doy_hits and not fra_hits:
        return "Lata"
    return "Sin detectar"


def cc(c): return COLORS_CADENAS.get(c, "#6B7280")
def cv(v): return COLORES_VARIEDAD.get(v, "#9CA3AF")
def cm(m): return COLORES_MARCA_AC.get(m, "#9CA3AF")


def sku_canonico_ac(marca: str, variedad: str, gramos) -> str:
    g_lbl = f"{int(gramos)}g" if gramos and not pd.isna(gramos) else "?"
    return f"{marca} · {variedad} · {g_lbl}"


# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Aceitunas Tracker | Monitor de Precios",
    page_icon="🫒", layout="wide", initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Contraseña
# ---------------------------------------------------------------------------

_MAX_INTENTOS = 5


def _check_password():
    if st.session_state.get("_pwd_ok", False):
        return True
    intentos = st.session_state.get("_intentos", 0)
    if intentos >= _MAX_INTENTOS:
        st.error("Demasiados intentos fallidos. Cerrá y volvé a abrir el navegador.")
        st.stop()
    st.markdown("""
    <div style="display:flex;flex-direction:column;align-items:center;
                justify-content:center;min-height:60vh;gap:1.2rem">
      <div style="font-size:2.5rem">🫒</div>
      <div style="font-size:1.5rem;font-weight:800;color:#0F172A">Aceitunas Tracker</div>
      <div style="font-size:0.9rem;color:#6B7280">Ingresá la contraseña para continuar</div>
    </div>
    """, unsafe_allow_html=True)
    _, col, _ = st.columns([2, 1.5, 2])
    with col:
        pwd = st.text_input("Contraseña", type="password",
                            label_visibility="collapsed", placeholder="Contraseña…")
        if st.button("Entrar", use_container_width=True, type="primary"):
            correct = st.secrets.get("PASSWORD", "")
            if pwd and pwd == correct:
                st.session_state["_pwd_ok"] = True
                st.session_state["_intentos"] = 0
                st.rerun()
            else:
                st.session_state["_intentos"] = intentos + 1
                restantes = _MAX_INTENTOS - st.session_state["_intentos"]
                if restantes > 0:
                    st.error(f"Contraseña incorrecta ({restantes} intento{'s' if restantes != 1 else ''} restante{'s' if restantes != 1 else ''})")
                else:
                    st.rerun()
    st.stop()


_check_password()

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700;800;900&display=swap');
html,body,[class*="css"],.stApp{font-family:'Montserrat',sans-serif!important}

:root{
    --green:#16A34A;--green-light:#22C55E;--green-neon:#4ADE80;
    --green-glow:rgba(22,163,74,0.4);--green-bg:#DCFCE7;
    --white:#FFFFFF;--off-white:#F8FAFC;
    --gray-50:#F9FAFB;--gray-100:#F1F5F9;--gray-200:#E2E8F0;
    --gray-400:#94A3B8;--gray-600:#475569;--gray-900:#0F172A;
    --purple:hsl(261deg 80% 48%);--sidebar-w:230px
}

.stApp{
    background:var(--off-white);
    background-image:
        radial-gradient(ellipse at var(--mx,30%) var(--my,20%),rgba(22,163,74,0.07) 0%,transparent 55%),
        radial-gradient(ellipse at 85% 85%,rgba(34,197,94,0.05) 0%,transparent 50%)
}
.block-container{padding:1.2rem 2rem 3rem;max-width:1400px}
#MainMenu,footer,header{visibility:hidden}

/* ── SIDEBAR ALWAYS OPEN ── */
[data-testid="stSidebar"]{
    background:#FFFFFF!important;
    border-right:1px solid var(--gray-200)!important;
    box-shadow:4px 0 24px rgba(0,0,0,0.06)!important;
    min-width:var(--sidebar-w)!important;
    max-width:var(--sidebar-w)!important;
    transform:translateX(0)!important
}
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"]{display:none!important;visibility:hidden!important}
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p{color:#374151!important}

/* ── SIDEBAR LOGO ── */
.sidebar-logo{font-size:1.1rem;font-weight:800;color:var(--gray-900);letter-spacing:-0.5px;line-height:1.3}
.sidebar-logo .accent{color:var(--green);text-shadow:0 0 20px rgba(22,163,74,0.5)}
.sidebar-sub{font-size:0.66rem;color:var(--gray-400);margin-bottom:0.25rem}
.sidebar-sep{font-size:0.57rem;font-weight:700;text-transform:uppercase;letter-spacing:1.3px;color:var(--gray-400);margin:0.85rem 0 0.28rem;padding-bottom:0.25rem;border-bottom:1px solid var(--gray-200)}

/* ── NAV RADIO AS SIDE MENU ── */
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"]{
    padding:0.38rem 0.65rem!important;border-radius:8px!important;
    align-items:center!important;cursor:pointer!important;margin:1px 0!important;
    border-left:3px solid transparent!important;transition:all 0.2s ease!important
}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"]:hover{
    background:rgba(22,163,74,0.07)!important;
    border-left:3px solid rgba(22,163,74,0.4)!important;
    transform:translateX(2px)!important
}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"][aria-checked="true"]{
    background:linear-gradient(135deg,#DCFCE7,#BBF7D0)!important;
    border-left:4px solid var(--green)!important;
    box-shadow:0 0 20px rgba(22,163,74,0.3),0 4px 12px rgba(22,163,74,0.2)!important;
    transform:translateX(4px)!important;
    margin-left:-1px!important;
    animation:navPressed 0.35s cubic-bezier(.36,.07,.19,.97) both!important
}
@keyframes navPressed{
    0%  {transform:translateX(0) scale(1)}
    20% {transform:translateX(8px) scale(0.96)}
    50% {transform:translateX(3px) scale(0.98)}
    75% {transform:translateX(5px) scale(1.01)}
    100%{transform:translateX(4px) scale(1)}
}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"][aria-checked="true"] p{
    color:var(--green)!important;font-weight:800!important;
    text-shadow:0 0 12px rgba(22,163,74,0.4)!important;
    font-size:0.88rem!important
}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"] > div:first-child{display:none!important}
[data-testid="stSidebar"] [data-testid="stRadio"] p{font-size:0.82rem!important;font-weight:500!important;color:#374151!important;margin:0!important;transition:color 0.2s!important}

/* ── HEADER ── */
.main-header{
    background:linear-gradient(120deg,#064E3B 0%,#065F46 30%,#047857 60%,#059669 100%);
    background-size:300% 300%;
    animation:headerShimmer 10s ease infinite,fadeInDown 0.6s ease;
    padding:1.6rem 2.2rem;border-radius:24px;margin-bottom:1.5rem;
    display:flex;align-items:center;justify-content:space-between;
    box-shadow:0 12px 40px rgba(6,79,67,0.35),0 0 0 1px rgba(74,222,128,0.2);
    position:relative;overflow:hidden;
    transition:box-shadow 0.4s ease,transform 0.3s ease
}
.main-header:hover{
    box-shadow:0 20px 60px rgba(6,79,67,0.45),0 0 40px rgba(74,222,128,0.2);
    transform:translateY(-2px)
}
.main-header::before{
    content:'';position:absolute;top:-60%;right:-8%;width:350px;height:350px;
    background:radial-gradient(circle,rgba(74,222,128,0.2),transparent 65%);
    pointer-events:none;animation:float 6s ease-in-out infinite
}
.main-header::after{
    content:'';position:absolute;bottom:0;left:0;right:0;height:2px;
    background:linear-gradient(90deg,transparent,#4ADE80,#BBFDE8,#4ADE80,transparent);
    animation:shimmerLine 3s linear infinite
}
.header-eyebrow{font-size:0.65rem;font-weight:700;text-transform:uppercase;letter-spacing:2px;color:rgba(187,253,232,0.7);margin-bottom:0.3rem}
.header-left h1{font-size:1.6rem;font-weight:900;color:#fff;margin:0;letter-spacing:-0.8px;text-shadow:0 0 40px rgba(74,222,128,0.4)}
.header-left p{font-size:0.78rem;color:rgba(187,253,232,0.65);margin:0.25rem 0 0}
.header-right{display:flex;flex-direction:column;align-items:flex-end;gap:0.6rem}
.header-badge{
    background:rgba(74,222,128,0.15);border:1px solid rgba(74,222,128,0.4);
    border-radius:50px;padding:0.3rem 1rem;color:#fff!important;font-size:0.75rem;font-weight:700;
    animation:glowPulse 3s ease infinite;backdrop-filter:blur(8px)
}
.header-link-btn{
    background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.25);
    border-radius:50px;padding:0.32rem 1rem;color:#fff!important;font-size:0.73rem;font-weight:700;
    text-decoration:none;letter-spacing:0.5px;transition:all 0.3s ease;
    backdrop-filter:blur(8px)
}
.header-link-btn:hover{
    background:rgba(255,255,255,0.22);border-color:rgba(74,222,128,0.5);
    color:#fff!important;box-shadow:0 0 16px rgba(74,222,128,0.3);transform:scale(1.05)
}

/* ── KPI CARDS ── */
.kpi-card{
    background:#fff;border-radius:20px;padding:1.4rem 1.5rem;
    box-shadow:0 4px 20px rgba(0,0,0,0.07);border-top:4px solid var(--gray-200);
    display:flex;flex-direction:column;
    transition:all 0.35s cubic-bezier(0.34,1.56,0.64,1);
    cursor:default;transform-style:preserve-3d;position:relative;overflow:hidden;
    animation:fadeInUp 0.5s ease both
}
.kpi-card:hover{transform:translateY(-6px) scale(1.02);box-shadow:0 20px 48px rgba(0,0,0,0.12),0 0 0 1px rgba(22,163,74,0.12)}
.kpi-card.green{border-top-color:#16A34A}
.kpi-card.green:hover{box-shadow:0 20px 48px rgba(22,163,74,0.2),0 0 32px rgba(22,163,74,0.15)}
.kpi-card.orange{border-top-color:#F59E0B}
.kpi-card.orange:hover{box-shadow:0 20px 48px rgba(245,158,11,0.2),0 0 32px rgba(245,158,11,0.15)}
.kpi-card.purple{border-top-color:#7C3AED}
.kpi-card.purple:hover{box-shadow:0 20px 48px rgba(124,58,237,0.2),0 0 32px rgba(124,58,237,0.15)}
.kpi-card.red{border-top-color:#EF4444}
.kpi-card.red:hover{box-shadow:0 20px 48px rgba(239,68,68,0.2),0 0 32px rgba(239,68,68,0.15)}
.kpi-card.teal{border-top-color:#0D9488}
.kpi-card.yellow{border-top-color:#EAB308}
.kpi-label{font-size:0.59rem;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--gray-400);margin-bottom:0.5rem}
.kpi-value{font-size:1.8rem;font-weight:900;color:var(--gray-900);line-height:1;transition:color 0.3s}
.kpi-sub{font-size:0.68rem;color:var(--gray-600);margin-top:0.4rem}

/* ── CHART TITLES ── */
.chart-title{font-size:0.78rem;font-weight:700;color:var(--gray-900);margin-bottom:0.7rem;padding-bottom:0.4rem;border-bottom:2px solid #F0FDF4;text-transform:uppercase;letter-spacing:0.6px}
.chart-note{font-size:0.73rem;color:var(--gray-600);margin-top:-0.4rem;margin-bottom:0.75rem}

/* ── EXPANDERS ── */
[data-testid="stExpander"]{background:#fff!important;border:1px solid var(--gray-200)!important;border-radius:16px!important;margin-bottom:0.75rem!important;box-shadow:0 2px 12px rgba(0,0,0,0.05)!important;transition:all 0.3s ease!important}
[data-testid="stExpander"] summary,[data-testid="stExpander"] details > summary,.streamlit-expanderHeader{color:var(--gray-900)!important;font-weight:600!important;background:var(--gray-50)!important;border-radius:16px 16px 0 0!important}
[data-testid="stExpander"] summary *{color:var(--gray-900)!important}
[data-testid="stExpander"] summary:hover{background:#F0FDF4!important}
[data-testid="stExpander"] > div[data-testid="stExpanderDetails"],[data-testid="stExpander"] > div{background:#fff!important}
[data-testid="stExpander"] .element-container,[data-testid="stExpander"] p,[data-testid="stExpander"] span:not([data-testid="collapsedControl"] span){color:var(--gray-900)!important}

/* ── BUTTONS — Uiverse.io style ── */
.stButton > button{
    padding:10px 28px!important;border-radius:50px!important;cursor:pointer!important;
    border:0!important;background-color:white!important;
    box-shadow:rgb(0 0 0/5%) 0 0 8px!important;letter-spacing:1.5px!important;
    text-transform:uppercase!important;font-size:11px!important;
    font-family:'Montserrat',sans-serif!important;font-weight:700!important;
    color:#0F172A!important;transition:all 0.5s ease!important;
    position:relative!important;overflow:hidden!important
}
.stButton > button:hover{
    letter-spacing:3px!important;background-color:hsl(261deg 80% 48%)!important;
    color:hsl(0,0%,100%)!important;box-shadow:rgb(93 24 220) 0px 7px 29px 0px!important
}
.stButton > button:active{
    letter-spacing:3px!important;background-color:hsl(261deg 80% 48%)!important;
    color:hsl(0,0%,100%)!important;box-shadow:rgb(93 24 220) 0px 0px 0px 0px!important;
    transform:translateY(10px)!important;transition:100ms!important
}

/* ── SIDEBAR BUTTONS (compact, no uiverse) ── */
[data-testid="stSidebar"] .stButton > button{
    padding:7px 14px!important;border-radius:10px!important;letter-spacing:0.3px!important;
    text-transform:none!important;font-size:11px!important;font-weight:600!important;
    background:var(--gray-50)!important;color:var(--gray-900)!important;
    box-shadow:0 1px 4px rgba(0,0,0,0.08)!important;border:1px solid var(--gray-200)!important;
    transition:all 0.2s ease!important
}
[data-testid="stSidebar"] .stButton > button:hover{
    background:var(--green-bg)!important;color:var(--green)!important;
    border-color:rgba(22,163,74,0.3)!important;letter-spacing:0.3px!important;
    box-shadow:0 0 12px rgba(22,163,74,0.2)!important;transform:none!important
}
[data-testid="stSidebar"] .stButton > button:active{
    transform:none!important;background:var(--green-bg)!important;
    color:var(--green)!important;box-shadow:none!important
}

/* ── CATEGORY SWITCH BUTTON ── */
.cat-switch-btn{
    display:flex;align-items:center;gap:10px;
    background:linear-gradient(135deg,#064E3B,#065F46);
    border:1px solid rgba(74,222,128,0.25);border-radius:14px;
    padding:0.75rem 1rem;text-decoration:none;
    color:#fff!important;font-size:0.78rem;font-weight:700;
    letter-spacing:0.3px;transition:all 0.3s ease;
    box-shadow:0 4px 15px rgba(6,78,59,0.35);
    position:relative;overflow:hidden;
    margin-top:0.5rem;width:100%;box-sizing:border-box
}
.cat-switch-btn::before{
    content:"";position:absolute;top:-50%;left:-60%;
    width:60%;height:200%;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,0.08),transparent);
    transform:skewX(-20deg);transition:left 0.5s ease
}
.cat-switch-btn:hover::before{left:120%}
.cat-switch-btn:hover{
    background:linear-gradient(135deg,#065F46,#047857);
    box-shadow:0 6px 22px rgba(6,78,59,0.5),0 0 0 1px rgba(74,222,128,0.35);
    transform:translateY(-2px);color:#fff!important
}
.cat-switch-btn:active{transform:translateY(0);box-shadow:0 2px 8px rgba(6,78,59,0.3)}
.cat-switch-icon{font-size:1.4rem;flex-shrink:0}
.cat-switch-text{display:flex;flex-direction:column;gap:1px}
.cat-switch-label{font-size:0.6rem;font-weight:600;opacity:0.75;text-transform:uppercase;letter-spacing:1px}
.cat-switch-name{font-size:0.82rem;font-weight:800;letter-spacing:-0.2px}

/* ── DATAFRAMES ── */
.stDataFrame td,.stDataFrame th{color:var(--gray-900)!important}

/* ── FILTER LABELS ── */
div[data-testid="stSelectbox"] label,div[data-testid="stMultiSelect"] label,
div[data-testid="stRadio"] label,div[data-testid="stRadio"] p,
div[data-testid="stRadio"] div[role="radiogroup"] p{color:var(--gray-900)!important;font-weight:600!important;font-size:0.79rem!important}
div[data-baseweb="radio"] label,div[data-baseweb="radio"] span{color:var(--gray-900)!important;font-weight:600!important}

.filter-bar{display:flex;flex-wrap:wrap;gap:0.6rem;align-items:flex-end;margin-bottom:1rem;background:var(--gray-50);border-radius:12px;padding:0.65rem 0.9rem;border:1px solid var(--gray-200)}
.filter-bar label,.filter-bar p{color:var(--gray-900)!important;font-weight:600!important;font-size:0.76rem!important;margin-bottom:1px!important}

div[data-testid="stSelectbox"] > div > div,div[data-testid="stMultiSelect"] > div > div{font-size:0.82rem!important}
div[data-testid="stSelectbox"] [data-baseweb="select"] > div:first-child{padding-top:4px!important;padding-bottom:4px!important;min-height:34px!important}
div[data-testid="stSelectbox"] [data-baseweb="select"]{border-radius:8px!important}

/* ── MULTISELECT TAGS ── */
[data-baseweb="tag"]{background:var(--green-bg)!important;border:1px solid rgba(22,163,74,0.3)!important}
[data-baseweb="tag"] span{color:var(--green)!important;font-weight:600!important}

/* ── ANIMATIONS ── */
@keyframes fadeInDown{from{opacity:0;transform:translateY(-20px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeInUp{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:translateY(0)}}
@keyframes glowPulse{0%,100%{box-shadow:0 0 8px rgba(74,222,128,0.3)}50%{box-shadow:0 0 24px rgba(74,222,128,0.7),0 0 48px rgba(22,163,74,0.35)}}
@keyframes rippleAnim{to{transform:scale(4);opacity:0}}
@keyframes headerShimmer{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
@keyframes float{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-15px) scale(1.05)}}
@keyframes shimmerLine{0%{background-position:-200% 0}100%{background-position:200% 0}}
@keyframes borderSpin{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}

@media(max-width:768px){
    .block-container{padding:0.6rem 0.8rem 2rem!important}
    .main-header{padding:1rem!important;flex-direction:column!important;gap:0.5rem!important}
    :root{--sidebar-w:200px}
}
</style>
""", unsafe_allow_html=True)

# ── JavaScript: parallax + 3D tilt + glow + ripple ──────────────────────
components.html("""
<script>
(function(){
  function init(){
    var doc = window.parent.document;
    if(!doc) return;

    // PARALLAX BACKGROUND on mouse move
    doc.addEventListener('mousemove', function(e){
      var x = (e.clientX / window.parent.innerWidth * 100).toFixed(1);
      var y = (e.clientY / window.parent.innerHeight * 100).toFixed(1);
      doc.documentElement.style.setProperty('--mx', x + '%');
      doc.documentElement.style.setProperty('--my', y + '%');
    });

    // 3D TILT on KPI cards
    function apply3D(){
      var cards = doc.querySelectorAll('.kpi-card');
      cards.forEach(function(card){
        if(card._3d) return; card._3d = true;
        card.addEventListener('mousemove', function(e){
          var r = card.getBoundingClientRect();
          var x = (e.clientX - r.left) / r.width - 0.5;
          var y = (e.clientY - r.top) / r.height - 0.5;
          card.style.transform = 'translateY(-6px) scale(1.02) perspective(900px) rotateX('+(-y*14)+'deg) rotateY('+(x*14)+'deg)';
          card.style.transition = 'box-shadow 0.1s, border 0.1s';
        });
        card.addEventListener('mouseleave', function(){
          card.style.transform = '';
          card.style.transition = 'all 0.35s cubic-bezier(0.34,1.56,0.64,1)';
        });
      });
    }

    // GLOW BORDER on expanders
    function applyGlow(){
      var exps = doc.querySelectorAll('[data-testid="stExpander"]');
      exps.forEach(function(exp){
        if(exp._glow) return; exp._glow = true;
        exp.addEventListener('mouseenter', function(){
          exp.style.boxShadow = '0 4px 24px rgba(22,163,74,0.14), 0 0 0 1px rgba(22,163,74,0.18)';
          exp.style.transform = 'translateY(-1px)';
        });
        exp.addEventListener('mouseleave', function(){
          exp.style.boxShadow = '0 2px 12px rgba(0,0,0,0.05)';
          exp.style.transform = '';
        });
      });
    }

    // RIPPLE EFFECT on buttons
    function applyRipple(){
      var btns = doc.querySelectorAll('.stButton > button');
      btns.forEach(function(btn){
        if(btn._ripple) return; btn._ripple = true;
        btn.addEventListener('click', function(e){
          var ripple = doc.createElement('span');
          var r = btn.getBoundingClientRect();
          var size = Math.max(r.width, r.height);
          ripple.style.cssText = 'position:absolute;border-radius:50%;pointer-events:none;'
            +'width:'+size+'px;height:'+size+'px;'
            +'left:'+(e.clientX-r.left-size/2)+'px;top:'+(e.clientY-r.top-size/2)+'px;'
            +'background:rgba(255,255,255,0.35);transform:scale(0);'
            +'animation:rippleAnim 0.6s ease;';
          btn.style.position='relative'; btn.style.overflow='hidden';
          btn.appendChild(ripple);
          setTimeout(function(){if(ripple.parentNode) ripple.parentNode.removeChild(ripple);}, 650);
        });
      });
    }

    // RUN
    apply3D(); applyGlow(); applyRipple();

    // Re-apply after Streamlit re-renders
    var observer = new MutationObserver(function(){
      apply3D(); applyGlow(); applyRipple();
    });
    observer.observe(doc.body, {childList:true, subtree:true});
  }

  if(document.readyState==='complete'){
    setTimeout(init, 400);
  } else {
    window.addEventListener('load', function(){ setTimeout(init, 400); });
  }
})();
</script>
""", height=0, scrolling=False)

# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------

DB_PATH = DIRECTORIO / "precios.db"


def _db_mtime():
    return DB_PATH.stat().st_mtime if DB_PATH.exists() else 0


@st.cache_data(ttl=3600)
def cargar_datos_aceitunas(_mtime=None) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM aceitunas ORDER BY fecha")
    except Exception:
        conn.close()
        return pd.DataFrame()
    registros = cur.fetchall()
    conn.close()

    rows = []
    for r in registros:
        g       = r["gramos_sin_escurrir"]
        precio  = r["precio"]
        gondola = r["precio_sin_dto"] or precio
        desc    = round((gondola - precio) / gondola * 100) if gondola > precio else 0
        cadena  = r["supermercado"]
        marca   = limpiar_marca_ac(r["marca"] or "Desconocida", cadena)
        var_raw = r["variedad"] or "Verde"
        var_unif = unificar_variedad(var_raw)
        marca_cat = categorizar_marca_ac(marca)
        rows.append({
            "Fecha":              r["fecha"],
            "Cadena":             r["supermercado"],
            "Marca":              marca,
            "Marca_cat":          marca_cat,
            "Producto":           r["nombre"],
            "SKU_canonico":       sku_canonico_ac(marca, var_unif, g),
            "Variedad":           var_unif,
            "Variedad_raw":       var_raw,
            "Variedad_conf":      r["variedad_confianza"] or "baja",
            "Gramos":             int(g) if g else None,
            "Gramaje":            r["gramaje_grupo"],
            "Gramos_escurrido":   r["gramos_escurrido"],
            "Gramaje_fuente":     r["gramaje_fuente"] or "unknown",
            "Gramaje_conf":       r["gramaje_confianza"] or "baja",
            "Precio":             int(round(gondola)),
            "Precio_oferta":      int(round(precio)),
            "Precio_100g":        round(gondola / g * 100) if g else None,
            "Precio_100g_oferta": round(precio / g * 100) if g else None,
            "Descuento_pct":      desc,
            "En_oferta":          bool(r["en_oferta"]),
            "Producto_id":        r["producto_id"] or "",
            "URL":                r["url"] or "",
            "Envase":             detectar_envase_nombre(r["nombre"] or ""),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"]).dt.normalize()   # normalizar a medianoche
        df["Semana_num"] = df["Fecha"].dt.isocalendar().week.astype(int)
        df["Periodo"] = df["Fecha"].apply(
            lambda d: f"Sem {d.isocalendar().week} · {d.strftime('%b %Y')}"
        )
    return df


df_full = cargar_datos_aceitunas(_mtime=_db_mtime())

if df_full.empty:
    st.error("⚠️ Sin datos de aceitunas. Ejecutá primero: **python scraper_aceitunas.py**")
    st.stop()

# ---------------------------------------------------------------------------
# Helpers de layout
# ---------------------------------------------------------------------------

_BASE_CORE = dict(
    template="plotly_white",
    font=dict(family="Montserrat", size=13, color="#111827"),
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1, font=dict(size=12, color="#111827")),
)


def _kpi_mini(icon: str, titulo: str, valor: str, detalle: str = "") -> None:
    """Mini KPI card para barras de resumen (ej. barra de Ofertas)."""
    st.markdown(f"""
    <div style="background:#fff;border-radius:14px;padding:0.85rem 1rem;
                box-shadow:0 2px 10px rgba(0,0,0,0.07);border-top:3px solid #16A34A;
                text-align:center">
      <div style="font-size:1.3rem;margin-bottom:0.15rem">{icon}</div>
      <div style="font-size:0.6rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.7px;margin-bottom:0.1rem">{titulo}</div>
      <div style="font-size:1.15rem;font-weight:800;color:#111827;line-height:1.1">{valor}</div>
      <div style="font-size:0.63rem;color:#6B7280;margin-top:0.15rem">{detalle}</div>
    </div>""", unsafe_allow_html=True)


def _build_offer_card_html(r, compact: bool = False) -> str:
    """Construye el HTML de una card de oferta individual."""
    url = r.get("URL", "")
    is_url = isinstance(url, str) and url.startswith("http")
    ver = (f'<a href="{url}" target="_blank" '
           f'style="font-size:0.62rem;color:#3B82F6;font-weight:600;text-decoration:none">Ver →</a>'
           ) if is_url else ""
    sku      = str(r.get("SKU_canonico", r.get("Producto", "")))[:60]
    marca_cat = str(r.get("Marca_cat", ""))
    cadena   = str(r.get("Cadena", ""))
    def _safe(v):
        return 0 if (v is None or (isinstance(v, float) and math.isnan(v))) else v
    pof  = _safe(r.get("Precio_oferta"))
    pg   = _safe(r.get("Precio"))
    desc = _safe(r.get("Descuento_pct"))
    color = COLORES_MARCA_AC.get(marca_cat, "#3B82F6")
    pad = "0.35rem 0.6rem" if compact else "0.55rem 0.75rem"
    vs  = "0.82rem" if compact else "0.95rem"
    ss  = "0.68rem" if compact else "0.75rem"
    return (
        f'<div style="background:#fff;border-radius:8px;padding:{pad};'
        f'margin-bottom:0.3rem;border-left:3px solid {color};'
        f'box-shadow:0 1px 4px rgba(0,0,0,0.07)">'
        f'<div style="font-size:{ss};font-weight:700;color:#111827;'
        f'margin-bottom:0.2rem;line-height:1.2">{sku}</div>'
        f'<div style="display:flex;gap:0.9rem;align-items:flex-end">'
        f'<div><div style="font-size:0.52rem;color:#374151;text-transform:uppercase">Precio oferta</div>'
        f'<div style="font-size:{vs};font-weight:800;color:#16A34A">${pof:,.0f}</div></div>'
        f'<div><div style="font-size:0.52rem;color:#374151;text-transform:uppercase">Góndola</div>'
        f'<div style="font-size:{vs};font-weight:800;color:#6B7280"><s>${pg:,.0f}</s></div></div>'
        f'<div><div style="font-size:0.52rem;color:#374151;text-transform:uppercase">Dto.</div>'
        f'<div style="font-size:{vs};font-weight:800;color:#DC2626">-{desc:.0f}%</div></div>'
        f'</div><div style="font-size:0.6rem;color:#374151;margin-top:0.3rem;'
        f'display:flex;justify-content:space-between;align-items:center">'
        f'<span>🏪 {cadena}</span>{ver}</div></div>'
    )


def render_offer_cards(df: pd.DataFrame, compact: bool = False,
                       grid_cols: int = 1, max_height: int = 0) -> None:
    """Renderiza una tabla de ofertas como cards HTML con links."""
    if df.empty:
        st.markdown('<div style="color:#9CA3AF;font-size:0.8rem">Sin ofertas activas.</div>',
                    unsafe_allow_html=True)
        return
    cards = [_build_offer_card_html(r, compact) for _, r in df.iterrows()]
    if grid_cols > 1:
        col_style = f"repeat({grid_cols},1fr)"
        body = "".join(f'<div>{c}</div>' for c in cards)
        inner = (f'<div style="display:grid;grid-template-columns:{col_style};gap:0.4rem">'
                 f'{body}</div>')
    else:
        inner = "\n".join(cards)
    if max_height:
        html = (f'<div style="max-height:{max_height}px;overflow-y:auto;'
                f'padding-right:4px;scrollbar-width:thin">{inner}</div>')
    else:
        html = inner
    st.markdown(html, unsafe_allow_html=True)


def hbar(x_vals, y_vals, colores, textos, titulo_x, altura=340):
    vmax = max(x_vals) if x_vals else 1
    fig = go.Figure(go.Bar(
        x=x_vals, y=y_vals, orientation="h",
        marker_color=colores, text=textos,
        textposition="outside",
        textfont=dict(size=13, color="#111827"),
        cliponaxis=False,
    ))
    fig.update_layout(
        **_BASE_CORE, height=altura,
        margin=dict(l=10, r=220, t=40, b=10),
        xaxis=dict(title=dict(text=titulo_x, font=dict(color="#111827", size=12)),
                   tickprefix="$", tickformat=",",
                   tickfont=dict(size=12, color="#111827"),
                   range=[0, vmax * 1.4]),
        yaxis=dict(tickfont=dict(size=13, color="#111827"),
                   title=dict(font=dict(color="#111827"))),
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# Sidebar — filtros
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("""
    <div class="sidebar-logo">🫒 <span class="accent">Aceitunas</span> Tracker</div>
    <div class="sidebar-sub">Monitor de precios · Argentina</div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="sidebar-sep">Navegación</div>', unsafe_allow_html=True)
    _page_sel = st.radio(
        "Navegación",
        ["📊  Resumen", "🫒  Por Variedad", "🏪  Por Cadena", "🏷️  Por Marca",
         "📈  Evolución", "🔖  Ofertas", "📦  Quiebres", "🔢  Tabla dinámica"],
        key="nav_radio",
        label_visibility="collapsed",
    )
    active_page = _page_sel.split("  ", 1)[1].strip() if "  " in _page_sel else _page_sel.strip()

    st.markdown('<div class="sidebar-sep">Período semanal</div>', unsafe_allow_html=True)
    periodos_disp = sorted(
        df_full["Periodo"].unique(),
        key=lambda p: df_full[df_full["Periodo"] == p]["Fecha"].min(),
    )
    if len(periodos_disp) > 1:
        periodos_sel = st.multiselect("Período", periodos_disp, default=periodos_disp,
                                      label_visibility="collapsed")
    else:
        periodos_sel = periodos_disp
        st.info(f"📅 {periodos_disp[0]}")

    st.markdown("---")
    if st.button("🔄 Actualizar datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    _btn_csv = False  # CSV export removed from UI

    # ── Botón de cambio de categoría ──────────────────────────────────
    st.markdown('<div class="sidebar-sep">Otras categorías</div>', unsafe_allow_html=True)
    components.html("""
<style>
  body{margin:0;padding:0;background:transparent;font-family:'Montserrat',sans-serif}
  a.csb{
    display:flex;align-items:center;gap:10px;
    background:linear-gradient(135deg,#064E3B,#065F46);
    border:1px solid rgba(74,222,128,0.25);border-radius:14px;
    padding:0.75rem 1rem;text-decoration:none;color:#fff;
    font-size:0.78rem;font-weight:700;letter-spacing:0.3px;
    transition:all 0.3s ease;box-shadow:0 4px 15px rgba(6,78,59,0.35);
    position:relative;overflow:hidden;width:100%;box-sizing:border-box
  }
  a.csb::before{
    content:"";position:absolute;top:-50%;left:-60%;width:60%;height:200%;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,0.08),transparent);
    transform:skewX(-20deg);transition:left 0.5s ease
  }
  a.csb:hover::before{left:120%}
  a.csb:hover{
    background:linear-gradient(135deg,#065F46,#047857);
    box-shadow:0 6px 22px rgba(6,78,59,0.5),0 0 0 1px rgba(74,222,128,0.35);
    transform:translateY(-2px)
  }
  a.csb:active{transform:translateY(0)}
  .icon{font-size:1.4rem;flex-shrink:0}
  .txt{display:flex;flex-direction:column;gap:1px}
  .lbl{font-size:0.6rem;font-weight:600;opacity:0.75;text-transform:uppercase;letter-spacing:1px}
  .nm{font-size:0.82rem;font-weight:800;letter-spacing:-0.2px}
  .arr{margin-left:auto;opacity:0.6;font-size:0.8rem}
</style>
<a href="https://olivapricing-argentina.streamlit.app" target="_blank" class="csb">
  <div class="icon">🫙</div>
  <div class="txt">
    <span class="lbl">Ir a</span>
    <span class="nm">Aceite de Oliva</span>
  </div>
  <span class="arr">↗</span>
</a>""", height=72, scrolling=False)

# ── Defaults para variables de filtro eliminadas del sidebar ─────────────
variedades_disp = sorted(df_full["Variedad"].dropna().unique())
variedades_sel  = list(variedades_disp)
cadenas_disp    = sorted(df_full["Cadena"].unique())
cadenas_sel     = list(cadenas_disp)
grupos_disp     = [g for g in GRAMAJE_GRUPOS if df_full["Gramaje"].eq(g).any()]
grupos_labels   = [gramaje_grupo_label(g) for g in grupos_disp]
buckets_sel     = list(grupos_disp)
_envases_orden  = ["Doypack", "Frasco", "Lata", "Bandeja", "Sin detectar"]
envases_disp    = [e for e in _envases_orden if (df_full["Envase"] == e).any()]
envases_sel     = list(envases_disp)
metrica_sel     = "Precio góndola ($)"

# ---------------------------------------------------------------------------
# Filtro base
# ---------------------------------------------------------------------------

mask_base = (
    df_full["Periodo"].isin(periodos_sel)
    & df_full["Cadena"].isin(cadenas_sel)
    & df_full["Variedad"].isin(variedades_sel)
    & (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(buckets_sel))
    & df_full["Envase"].isin(envases_sel)
)

dff   = df_full[mask_base].copy()
df_of = df_full[mask_base & df_full["En_oferta"]].copy()
df_ult = df_full[df_full["Fecha"] == df_full["Fecha"].max()].copy()

# Métrica seleccionada en sidebar
_met_kg  = metrica_sel == "$/kg"
_met_lbl = "$/kg" if _met_kg else "Precio góndola ($)"
if _met_kg:
    dff["_met"]    = dff["Precio_100g"] * 10
    df_ult["_met"] = df_ult["Precio_100g"] * 10
else:
    dff["_met"]    = dff["Precio"]
    df_ult["_met"] = df_ult["Precio"]

fecha_max_str = df_full["Fecha"].max().strftime("%d/%m/%Y")
n_sem = df_full["Periodo"].nunique()

if dff.empty:
    st.warning("Sin datos con los filtros seleccionados.")
    st.stop()

if _btn_csv:
    cols_exp = ["Periodo", "Cadena", "Marca", "Marca_cat", "Variedad", "Gramaje",
                "Gramos", "Producto", "Precio", "Precio_oferta", "Precio_100g",
                "Precio_100g_oferta", "Descuento_pct", "En_oferta", "URL"]
    with st.sidebar:
        st.download_button(
            "📥 Descargar CSV",
            dff[cols_exp].to_csv(index=False).encode("utf-8-sig"),
            "aceitunas_tracker.csv", "text/csv",
            use_container_width=True, key="dl_csv",
        )

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown(f"""
<div class="main-header">
  <div class="header-left">
    <div class="header-eyebrow">🫒 Monitor de Precios</div>
    <h1>Dashboard de precios MT</h1>
    <p>{fecha_max_str} &nbsp;·&nbsp; {len(df_ult):,} productos
       &nbsp;·&nbsp; {df_ult['Cadena'].nunique()} cadenas
       &nbsp;·&nbsp; {n_sem} semana{"s" if n_sem > 1 else ""} acumulada{"s" if n_sem > 1 else ""}</p>
  </div>
  <div class="header-right">
    <div class="header-badge">🫒 Aceitunas</div>
  </div>
</div>""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TABS
# ---------------------------------------------------------------------------


# ── TAB 1: Resumen ────────────────────────────────────────────────────────
if active_page == "Resumen":
    # ── KPIs ─────────────────────────────────────────────────────────────
    dff_g        = dff.dropna(subset=["Precio_100g"])
    precio_prom  = dff_g["Precio"].mean()
    pkg_prom     = dff_g["Precio_100g"].mean() * 10
    p100_min     = dff_g.groupby("Cadena")["Precio_100g"].mean()
    cadena_barata = p100_min.idxmin() if not p100_min.empty else "—"
    n_oferta     = len(df_of)
    pct_of       = n_oferta / max(len(dff), 1) * 100
    desc_prom    = df_of["Descuento_pct"].mean() if not df_of.empty else 0
    variedad_top = dff["Variedad"].value_counts().idxmax() if not dff.empty else "—"
    marcas_n     = dff["Marca_cat"].nunique()

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    kpis = [
        ("",       "SKUs relevados",    f"{dff['SKU_canonico'].nunique():,}", f"{dff['Cadena'].nunique()} cadenas"),
        ("green",  "$ promedio",        f"${precio_prom:,.0f}" if precio_prom else "—", "precio góndola"),
        ("teal",   "$/kg promedio",     f"${pkg_prom:,.0f}" if pkg_prom else "—", "base sin escurrir"),
        ("orange", "Cadena más barata", cadena_barata, "menor $/kg promedio"),
        ("purple", "Variedad top",      variedad_top,  "más SKUs en góndola"),
        ("red",    "En oferta",         f"{n_oferta:,}", f"{pct_of:.0f}% del total"),
        ("yellow", "Dto. prom.",        f"{desc_prom:.0f}%" if desc_prom > 0 else "—", f"{marcas_n} marcas"),
    ]
    for col, (cls, label, val, sub) in zip([c1, c2, c3, c4, c5, c6, c7], kpis):
        with col:
            st.markdown(f"""<div class="kpi-card {cls}">
                <div class="kpi-label">{label}</div>
                <div class="kpi-value" style="font-size:{'1.0rem' if len(val)>12 else '1.35rem' if len(val)>8 else '1.7rem'};word-break:break-word">{val}</div>
                <div class="kpi-sub">{sub}</div>
            </div>""", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    # ── Novedades ────────────────────────────────────────────────────────
    _pord = sorted(df_full["Periodo"].unique(),
                   key=lambda p: df_full[df_full["Periodo"] == p]["Fecha"].min())
    _ult_p  = _pord[-1] if _pord else None
    _pen_p  = _pord[-2] if len(_pord) >= 2 else None
    _fecha_max = df_full["Fecha"].max()
    _all_dates = sorted(df_full["Fecha"].unique())

    # Cambios de precio ≥ 3% vs semana anterior
    _cambios: list[dict] = []
    if _ult_p and _pen_p:
        # Comparar producto a producto (no agrupado por SKU_canonico) para
        # evitar mezclar productos distintos bajo el mismo bucket de gramaje.
        _df_u = (dff[dff["Periodo"] == _ult_p]
                 .groupby(["Cadena", "Producto"])
                 .agg(Precio=("Precio", "mean"), URL=("URL", "first"))
                 .reset_index())
        _df_p = (dff[dff["Periodo"] == _pen_p]
                 .groupby(["Cadena", "Producto"])["Precio"].mean()
                 .reset_index())
        _merged = _df_u.merge(_df_p, on=["Cadena", "Producto"], suffixes=("_n", "_v"))
        for _, _row in _merged.iterrows():
            _pn, _pv = float(_row["Precio_n"]), float(_row["Precio_v"])
            if _pv == 0:
                continue
            _cp = (_pn - _pv) / _pv * 100
            if abs(_cp) >= 3:
                _url_c = _row.get("URL", "")
                _cambios.append({"cadena": _row["Cadena"], "sku": _row["Producto"],
                                 "viejo": _pv, "nuevo": _pn, "pct": _cp,
                                 "url": _url_c if isinstance(_url_c, str) else ""})
        _cambios.sort(key=lambda x: abs(x["pct"]), reverse=True)

    # Ofertas activas en última fecha
    _of_now = df_full[
        (df_full["Fecha"] == _fecha_max) &
        df_full["En_oferta"] &
        df_full["Cadena"].isin(cadenas_sel)
    ].copy()

    _top_of: list[dict] = []
    _dest_of: list[dict] = []
    if not _of_now.empty:
        _of_agg = (_of_now
                   .groupby(["Cadena", "SKU_canonico", "Marca_cat"])
                   .agg(desc=("Descuento_pct", "max"),
                        pof=("Precio_oferta", "min"),
                        pg=("Precio", "mean"),
                        url=("URL", "first"))
                   .reset_index()
                   .sort_values("desc", ascending=False)
                   .reset_index(drop=True))
        _top_of   = _of_agg.head(3).to_dict("records")
        _MARCAS_TOP3 = {"La Toscana", "Castell", "Nucete"}
        _dest_of  = _of_agg[_of_agg["Marca_cat"].isin(_MARCAS_TOP3)].to_dict("records")

    with st.expander("🔔 Novedades", expanded=True):
            _cn_l, _cn_r, _cn_dest = st.columns(3, gap="large")

            with _cn_l:
                st.markdown('<div class="chart-note">📊 Cambios de precio vs semana anterior</div>',
                            unsafe_allow_html=True)
                if not _cambios:
                    st.markdown('<div style="color:#9CA3AF;font-size:0.8rem">Sin cambios significativos esta semana.</div>',
                                unsafe_allow_html=True)
                else:
                    for _c in _cambios[:8]:
                        _arr = "▲" if _c["pct"] > 0 else "▼"
                        _clr = "#EF4444" if _c["pct"] > 0 else "#16A34A"
                        _ver = (f'<a href="{_c["url"]}" target="_blank" '
                                f'style="font-size:0.62rem;color:#3B82F6;font-weight:600;'
                                f'text-decoration:none;display:block;margin-top:3px">Ver →</a>'
                                ) if _c.get("url", "").startswith("http") else ""
                        st.markdown(f"""
                        <div style="display:flex;align-items:center;gap:0.8rem;
                                    background:#FAFAFA;border-radius:9px;
                                    padding:0.55rem 0.85rem;margin-bottom:0.4rem;
                                    border-left:4px solid {_clr}">
                          <div style="flex:1;min-width:0">
                            <div style="font-size:0.77rem;font-weight:700;color:#111827;word-break:break-word">{_c['sku']}</div>
                            <div style="font-size:0.69rem;color:#6B7280">{_c['cadena']}</div>
                            {_ver}
                          </div>
                          <div style="text-align:right;white-space:nowrap;flex-shrink:0">
                            <span style="font-size:0.88rem;font-weight:800;color:{_clr}">{_arr} {abs(_c['pct']):.1f}%</span><br>
                            <span style="font-size:0.68rem;color:#9CA3AF">${_c['viejo']:,.0f} → ${_c['nuevo']:,.0f}</span>
                          </div>
                        </div>""", unsafe_allow_html=True)

            with _cn_r:
                st.markdown('<div class="chart-note">🏷️ Top ofertas activas esta semana</div>',
                            unsafe_allow_html=True)
                _MEDALS = ["🥇", "🥈", "🥉"]
                if not _top_of:
                    st.markdown('<div style="color:#9CA3AF;font-size:0.8rem">Sin ofertas activas.</div>',
                                unsafe_allow_html=True)
                else:
                    for _i, _o in enumerate(_top_of):
                        _o_url = _o.get("url", "")
                        _ver_o = (f'<a href="{_o_url}" target="_blank" '
                                  f'style="font-size:0.62rem;color:#3B82F6;font-weight:600;text-decoration:none">Ver →</a>'
                                  ) if _o_url and _o_url.startswith("http") else ""
                        st.markdown(
                        f'<div style="background:#fff;border-radius:8px;padding:0.55rem 0.75rem;'
                        f'margin-bottom:0.4rem;border-left:3px solid #3B82F6;'
                        f'box-shadow:0 1px 4px rgba(0,0,0,0.07)">'
                        f'<div style="font-size:0.75rem;font-weight:700;color:#111827;margin-bottom:0.35rem">'
                        f'{_MEDALS[_i] if _i < 3 else "⭐"} {_o["SKU_canonico"][:55]}</div>'
                        f'<div style="display:flex;gap:1.2rem;align-items:flex-end">'
                        f'<div><div style="font-size:0.58rem;color:#374151;text-transform:uppercase">Precio oferta</div>'
                        f'<div style="font-size:0.95rem;font-weight:800;color:#16A34A">${_o["pof"]:,.0f}</div></div>'
                        f'<div><div style="font-size:0.58rem;color:#374151;text-transform:uppercase">Góndola</div>'
                        f'<div style="font-size:0.95rem;font-weight:800;color:#6B7280"><s>${_o["pg"]:,.0f}</s></div></div>'
                        f'<div><div style="font-size:0.58rem;color:#374151;text-transform:uppercase">Dto.</div>'
                        f'<div style="font-size:0.95rem;font-weight:800;color:#DC2626">-{_o["desc"]:.0f}%</div></div>'
                        f'</div>'
                        f'<div style="font-size:0.65rem;color:#374151;margin-top:0.3rem;'
                        f'display:flex;justify-content:space-between;align-items:center">'
                        f'<span>🏪 {_o["Cadena"]}</span>{_ver_o}</div>'
                        f'</div>',
                        unsafe_allow_html=True)

            with _cn_dest:
                st.markdown('<div class="chart-note">⭐ La Toscana · Castell · Nucete</div>',
                            unsafe_allow_html=True)
                if not _dest_of:
                    st.markdown('<div style="color:#9CA3AF;font-size:0.8rem">Sin ofertas activas para estas marcas.</div>',
                                unsafe_allow_html=True)
                else:
                    for _od in _dest_of[:5]:
                        _clr_d = COLORES_MARCA_AC.get(_od["Marca_cat"], "#3B82F6")
                        _od_url = _od.get("url", "")
                        _ver_d = (f'<a href="{_od_url}" target="_blank" '
                                  f'style="font-size:0.62rem;color:#3B82F6;font-weight:600;text-decoration:none">Ver →</a>'
                                  ) if _od_url and _od_url.startswith("http") else ""
                        st.markdown(
                        f'<div style="background:#fff;border-radius:8px;padding:0.55rem 0.75rem;'
                        f'margin-bottom:0.4rem;border-left:3px solid {_clr_d};'
                        f'box-shadow:0 1px 4px rgba(0,0,0,0.07)">'
                        f'<div style="font-size:0.75rem;font-weight:700;color:#111827;margin-bottom:0.35rem">'
                        f'⭐ {_od["SKU_canonico"][:55]}</div>'
                        f'<div style="display:flex;gap:1.2rem;align-items:flex-end">'
                        f'<div><div style="font-size:0.58rem;color:#374151;text-transform:uppercase">Precio oferta</div>'
                        f'<div style="font-size:0.95rem;font-weight:800;color:#16A34A">${_od["pof"]:,.0f}</div></div>'
                        f'<div><div style="font-size:0.58rem;color:#374151;text-transform:uppercase">Góndola</div>'
                        f'<div style="font-size:0.95rem;font-weight:800;color:#6B7280"><s>${_od["pg"]:,.0f}</s></div></div>'
                        f'<div><div style="font-size:0.58rem;color:#374151;text-transform:uppercase">Dto.</div>'
                        f'<div style="font-size:0.95rem;font-weight:800;color:#DC2626">-{_od["desc"]:.0f}%</div></div>'
                        f'</div>'
                        f'<div style="font-size:0.65rem;color:#374151;margin-top:0.3rem;'
                        f'display:flex;justify-content:space-between;align-items:center">'
                        f'<span>🏪 {_od["Cadena"]}</span>{_ver_d}</div>'
                        f'</div>',
                        unsafe_allow_html=True)

    # ── Insights ─────────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("💡 Insights del mercado", expanded=True):
        def _insight_card(icon, titulo, valor, detalle, color="#0F3460"):
            st.markdown(f"""
            <div style="background:#fff;border-radius:12px;padding:0.9rem 1.1rem;
                        border-left:4px solid {color};box-shadow:0 1px 6px rgba(0,0,0,0.07)">
              <div style="font-size:1.2rem;margin-bottom:0.2rem">{icon}</div>
              <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:0.12rem">{titulo}</div>
              <div style="font-size:1.05rem;font-weight:800;color:#111827;line-height:1.2;margin-bottom:0.18rem">{valor}</div>
              <div style="font-size:0.71rem;color:#374151;line-height:1.4">{detalle}</div>
            </div>""", unsafe_allow_html=True)

        _ins = dff.copy()
        _ins_g = _ins.dropna(subset=["Precio_100g"])
        _cad_p100 = (_ins_g.groupby(["Cadena", "SKU_canonico"])["Precio_100g"].mean()
                     .reset_index().groupby("Cadena")["Precio_100g"].mean().reset_index(name="p100"))
        _cad_barata = _cad_p100.sort_values("p100").iloc[0] if not _cad_p100.empty else None
        _cad_cara   = _cad_p100.sort_values("p100").iloc[-1] if not _cad_p100.empty else None
        _sku_x_marca = (_ins.groupby("Marca_cat")["SKU_canonico"].nunique()
                        .reset_index(name="n").sort_values("n", ascending=False))
        _cad_x_marca = (_ins.groupby("Marca_cat")["Cadena"].nunique()
                        .reset_index(name="n").sort_values("n", ascending=False))
        _of_x_cad = (df_full[df_full["Cadena"].isin(cadenas_sel)]
                     .groupby("Cadena")["En_oferta"].mean().mul(100)
                     .reset_index(name="pct").sort_values("pct", ascending=False))
        _of_x_marca = (df_full[df_full["Cadena"].isin(cadenas_sel)]
                       .groupby("Marca_cat")["En_oferta"].mean().mul(100)
                       .reset_index(name="pct").sort_values("pct", ascending=False))

        _ri1, _ri2, _ri3, _ri4 = st.columns(4, gap="medium")
        with _ri1:
            if not _sku_x_marca.empty:
                r = _sku_x_marca.iloc[0]
                _insight_card("📦", "Marca con más SKUs activos",
                              str(r["Marca_cat"]), f"{int(r['n'])} SKUs distintos", "#0F3460")
        with _ri2:
            if not _cad_x_marca.empty:
                r = _cad_x_marca.iloc[0]
                _insight_card("🌐", "Marca con más presencia",
                              str(r["Marca_cat"]), f"activa en {int(r['n'])} cadenas", "#7C3AED")
        with _ri3:
            if _cad_barata is not None:
                _insight_card("✅", "Cadena más barata",
                              _cad_barata["Cadena"], f"${_cad_barata['p100']:,.0f}/100g promedio", "#16A34A")
        with _ri4:
            if _cad_cara is not None:
                _insight_card("🏅", "Cadena más cara",
                              _cad_cara["Cadena"], f"${_cad_cara['p100']:,.0f}/100g promedio", "#7C3AED")

        st.markdown("<br>", unsafe_allow_html=True)
        _ri5, _ri6, _ri7, _ri8 = st.columns(4, gap="medium")
        with _ri5:
            if not _of_x_cad.empty:
                r = _of_x_cad.iloc[0]
                _insight_card("🏪", "Cadena con más ofertas",
                              r["Cadena"], f"{r['pct']:.0f}% de sus productos en oferta", "#B45309")
        with _ri6:
            _dest_of_pct = _of_x_marca[_of_x_marca["Marca_cat"].isin(MARCAS_DESTACADAS_AC)]
            if not _dest_of_pct.empty:
                r = _dest_of_pct.iloc[0]
                _insight_card("🔥", "Marca destacada con más descuentos",
                              r["Marca_cat"], f"{r['pct']:.0f}% de registros en oferta", "#DC2626")
        with _ri7:
            _top_var = dff["Variedad"].value_counts()
            if not _top_var.empty:
                _insight_card("🫒", "Variedad más relevada",
                              _top_var.index[0], f"{int(_top_var.iloc[0])} registros", "#4CAF50")
        with _ri8:
            _n_marcas_dest = dff[dff["Marca_cat"].isin(MARCAS_DESTACADAS_AC)]["Marca_cat"].nunique()
            _n_skus_dest   = dff[dff["Marca_cat"].isin(MARCAS_DESTACADAS_AC)]["SKU_canonico"].nunique()
            _insight_card("🏷️", "Marcas destacadas",
                          f"{_n_marcas_dest} presentes", f"{_n_skus_dest} SKUs distintos", "#2E86AB")

    # ── Distribución general ─────────────────────────────────────────────
    st.markdown("---")
    with st.expander("📊 Resumen de SKUs", expanded=True):
        c_pie, c_pie2, c_bar = st.columns([1, 1, 2])
        with c_pie:
            st.markdown('<div class="chart-title">SKUs por variedad</div>', unsafe_allow_html=True)
            var_cnt = dff["Variedad"].value_counts().reset_index()
            var_cnt.columns = ["Variedad", "SKUs"]
            fig_pie = px.pie(var_cnt, values="SKUs", names="Variedad",
                             color="Variedad", color_discrete_map=COLORES_VARIEDAD, hole=0.4)
            fig_pie.update_traces(textposition="inside", textinfo="percent+label",
                                  textfont=dict(color="#111827"))
            fig_pie.update_layout(**_BASE_CORE, height=320,
                                  margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
            st.plotly_chart(fig_pie, use_container_width=True)

        with c_pie2:
            st.markdown('<div class="chart-title">SKUs por tipo de envase</div>', unsafe_allow_html=True)
            _env_cnt = dff["Envase"].value_counts().reset_index()
            _env_cnt.columns = ["Envase", "SKUs"]
            _colores_env = {
                "Doypack":      "#F59E0B",
                "Frasco":       "#3B82F6",
                "Lata":         "#6B7280",
                "Bandeja":      "#10B981",
                "Sin detectar": "#E5E7EB",
            }
            fig_pie2 = px.pie(_env_cnt, values="SKUs", names="Envase",
                              color="Envase", color_discrete_map=_colores_env, hole=0.4)
            fig_pie2.update_traces(textposition="inside", textinfo="percent+label",
                                   textfont=dict(color="#111827"))
            fig_pie2.update_layout(**_BASE_CORE, height=320,
                                   margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
            st.plotly_chart(fig_pie2, use_container_width=True)

        with c_bar:
            st.markdown('<div class="chart-title">$/kg promedio por variedad</div>', unsafe_allow_html=True)
            var_p = (dff.dropna(subset=["Precio_100g"])
                     .groupby("Variedad")["Precio_100g"].mean().mul(10).sort_values().reset_index())
            fig_v = hbar(var_p["Precio_100g"].tolist(), var_p["Variedad"].tolist(),
                         [cv(v) for v in var_p["Variedad"]],
                         [f"${v:,.0f}" for v in var_p["Precio_100g"]], "$/kg",
                         altura=max(280, len(var_p) * 32))
            st.plotly_chart(fig_v, use_container_width=True)

    # ── Movimientos de catálogo ──────────────────────────────────────────
    st.markdown("---")
    with st.expander("🆕 Novedades de catálogo", expanded=True):
        fechas_ord = sorted(df_full["Fecha"].unique())
        _cat_l, _cat_r = st.columns([2, 1])

        with _cat_l:
            st.markdown('<div class="chart-note">🆕 Entradas y ⚠️ salidas vs semana anterior</div>',
                        unsafe_allow_html=True)
            if len(fechas_ord) >= 2:
                f_rec, f_prev = fechas_ord[-1], fechas_ord[-2]
                skus_rec  = set(zip(df_full[df_full["Fecha"] == f_rec]["SKU_canonico"],
                                    df_full[df_full["Fecha"] == f_rec]["Cadena"]))
                skus_prev = set(zip(df_full[df_full["Fecha"] == f_prev]["SKU_canonico"],
                                    df_full[df_full["Fecha"] == f_prev]["Cadena"]))
                movs = pd.DataFrame(
                    [{"SKU": s, "Cadena": c, "Estado": "🆕 Entrada"} for s, c in skus_rec - skus_prev] +
                    [{"SKU": s, "Cadena": c, "Estado": "⚠️ Salida"}  for s, c in skus_prev - skus_rec]
                )
                if not movs.empty:
                    st.dataframe(movs, use_container_width=True, hide_index=True, height=300)
                else:
                    st.info("Sin cambios de catálogo entre las dos últimas semanas.")
            else:
                st.info("Sin datos de semana anterior para comparar. Se muestran los productos actuales.")

        with _cat_r:
            _cat_r_hdr, _cat_r_ord = st.columns([2, 1])
            with _cat_r_hdr:
                st.markdown('<div class="chart-note">🏷️ Todas las ofertas activas esta semana</div>',
                            unsafe_allow_html=True)
            with _cat_r_ord:
                _of_all_sort = st.radio("Ord.", ["Descuento", "Marca"], horizontal=True,
                                        key="of_all_ord", label_visibility="collapsed")
            _of_all = df_full[
                (df_full["Fecha"] == df_full["Fecha"].max()) & df_full["En_oferta"]
            ][["Cadena", "Marca_cat", "SKU_canonico", "Producto", "Descuento_pct",
               "Precio", "Precio_oferta", "URL"]].copy()
            if _of_all_sort == "Marca":
                _mk_ord_all = {m: i for i, m in enumerate(ORDEN_MARCAS_AC)}
                _of_all["_mk_ord"] = _of_all["Marca_cat"].map(_mk_ord_all).fillna(99)
                _of_all = _of_all.sort_values(["_mk_ord", "SKU_canonico"]).drop(columns="_mk_ord")
            else:
                _of_all = _of_all.sort_values("Descuento_pct", ascending=False)
            render_offer_cards(_of_all, compact=True, max_height=420)


# ── TAB 2: Por Variedad ───────────────────────────────────────────────────
if active_page == "Por Variedad":
    _t2c1, _t2c2, _t2c3, _t2sp = st.columns([1, 1, 1, 3])
    with _t2c1:
        st.markdown('<p style="color:#111827;font-size:0.78rem;font-weight:700;margin-bottom:1px">Variedad</p>', unsafe_allow_html=True)
        _t2_var = st.selectbox("Variedad", ["Todas"] + variedades_disp, key="t2_var", label_visibility="collapsed")
    with _t2c2:
        st.markdown('<p style="color:#111827;font-size:0.78rem;font-weight:700;margin-bottom:1px">Gramaje</p>', unsafe_allow_html=True)
        _t2_gram_lbl = st.selectbox("Gramaje", ["Todos"] + grupos_labels, key="t2_gram", label_visibility="collapsed")
    with _t2c3:
        st.markdown('<p style="color:#111827;font-size:0.78rem;font-weight:700;margin-bottom:1px">Envase</p>', unsafe_allow_html=True)
        _t2_envase = st.selectbox("Envase", ["Todos"] + envases_disp, key="t2_envase", label_visibility="collapsed")
    st.markdown("---")

    dff_t2 = dff.copy()
    if _t2_var != "Todas":
        dff_t2 = dff_t2[dff_t2["Variedad"] == _t2_var]
    if _t2_gram_lbl != "Todos":
        _t2_gram_key = next((g for g, l in zip(grupos_disp, grupos_labels) if l == _t2_gram_lbl), None)
        if _t2_gram_key:
            dff_t2 = dff_t2[dff_t2["Gramaje"] == _t2_gram_key]
    if _t2_envase != "Todos":
        dff_t2 = dff_t2[dff_t2["Envase"] == _t2_envase]

    with st.expander("🌡️ Precio promedio por variedad y cadena", expanded=True):
        st.markdown(f'<div class="chart-title">{_met_lbl} promedio por variedad y cadena</div>',
                    unsafe_allow_html=True)
        pivot = (dff_t2.dropna(subset=["_met"])
                 .groupby(["Variedad", "Cadena"])["_met"]
                 .mean().round(0).unstack("Cadena"))
        if not pivot.empty:
            text_v = [[f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                      for row in pivot.values]
            fig_hm = go.Figure(go.Heatmap(
                z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
                colorscale="RdYlGn_r",
                text=text_v, texttemplate="%{text}",
                textfont=dict(size=12, color="#111827"),
                colorbar=dict(title=_met_lbl, tickprefix="$", tickformat=",",
                              tickfont=dict(color="#111827"),
                              title_font=dict(color="#111827")),
            ))
            fig_hm.update_layout(**_BASE_CORE, height=max(360, len(pivot) * 40 + 80),
                                 margin=dict(l=10, r=10, t=20, b=10),
                                 xaxis=dict(tickfont=dict(size=13, color="#111827"), side="top"),
                                 yaxis=dict(tickfont=dict(size=13, color="#111827")))
            st.plotly_chart(fig_hm, use_container_width=True)

    with st.expander("📋 Resumen por variedad", expanded=False):
        var_resumen = (dff_t2.groupby("Variedad").agg(
            SKUs=("SKU_canonico", "nunique"),
            Precio_prom=("_met", "mean"),
            En_oferta_pct=("En_oferta", lambda s: s.mean() * 100),
            Cadenas=("Cadena", "nunique"),
        ).round(0).reset_index())
        var_resumen["Precio_prom"] = var_resumen["Precio_prom"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—")
        var_resumen["En_oferta_pct"] = var_resumen["En_oferta_pct"].apply(lambda v: f"{v:.0f}%")
        var_resumen.columns = ["Variedad", "SKUs únicos", f"{_met_lbl} prom.", "% en oferta", "Cadenas"]
        st.dataframe(var_resumen, use_container_width=True, hide_index=True)


# ── TAB 3: Por Cadena ─────────────────────────────────────────────────────
if active_page == "Por Cadena":
    # ── Filtros globales del tab (arriba de todo) ──────────────────────────
    _c3f1, _c3f2, _c3f3, _c3sp = st.columns([1, 1, 1, 3])
    with _c3f1:
        st.markdown('<p style="color:#111827;font-size:0.78rem;font-weight:700;margin-bottom:1px">Variedad</p>', unsafe_allow_html=True)
        _c3_var = st.selectbox("Variedad", ["Todas"] + variedades_disp, key="c3_var", label_visibility="collapsed")
    with _c3f2:
        st.markdown('<p style="color:#111827;font-size:0.78rem;font-weight:700;margin-bottom:1px">Gramaje</p>', unsafe_allow_html=True)
        _c3_gram_lbl = st.selectbox("Gramaje", ["Todos"] + grupos_labels, key="c3_gram", label_visibility="collapsed")
    with _c3f3:
        st.markdown('<p style="color:#111827;font-size:0.78rem;font-weight:700;margin-bottom:1px">Envase</p>', unsafe_allow_html=True)
        _c3_envase = st.selectbox("Envase", ["Todos"] + envases_disp, key="c3_envase", label_visibility="collapsed")

    # Aplica el filtro a TODOS los gráficos del tab
    dff_c3 = dff.copy()
    if _c3_var != "Todas":
        dff_c3 = dff_c3[dff_c3["Variedad"] == _c3_var]
    if _c3_gram_lbl != "Todos":
        _c3_gram_key = next((g for g, l in zip(grupos_disp, grupos_labels) if l == _c3_gram_lbl), None)
        if _c3_gram_key:
            dff_c3 = dff_c3[dff_c3["Gramaje"] == _c3_gram_key]
    if _c3_envase != "Todos":
        dff_c3 = dff_c3[dff_c3["Envase"] == _c3_envase]
    st.markdown("---")

    with st.expander(f"{_met_lbl} promedio & Productos por cadena", expanded=True):
        col_l, col_r = st.columns([3, 2], gap="large")
        with col_l:
            cad_p = (dff_c3.dropna(subset=["_met"])
                     .groupby("Cadena")["_met"].mean()
                     .reset_index().sort_values("_met"))
            fig_c = hbar(cad_p["_met"].tolist(), cad_p["Cadena"].tolist(),
                         [cc(c) for c in cad_p["Cadena"]],
                         [f"${v:,.0f}" for v in cad_p["_met"]], _met_lbl)
            st.plotly_chart(fig_c, use_container_width=True)
        with col_r:
            df_pie_c = dff_c3.groupby("Cadena").size().reset_index(name="n")
            fig_pie_c = go.Figure(go.Pie(
                labels=df_pie_c["Cadena"], values=df_pie_c["n"],
                marker_colors=[cc(c) for c in df_pie_c["Cadena"]],
                hole=0.55, textinfo="label+percent",
                textposition="outside",
                textfont=dict(size=12, color="#111827"),
            ))
            fig_pie_c.update_layout(**_BASE_CORE, height=320,
                                    margin=dict(l=10, r=10, t=40, b=40), showlegend=False)
            st.plotly_chart(fig_pie_c, use_container_width=True)

    with st.expander(f"Distribución de precios por cadena ({_met_lbl})", expanded=True):
        st.markdown('<div class="chart-note">Caja = rango intercuartil (Q1–Q3) · Línea central = mediana · Bigotes = 1.5×IQR</div>',
                    unsafe_allow_html=True)
        _p10 = float(dff_c3["_met"].dropna().quantile(0.10)) if not dff_c3.empty else 0
        _p90 = float(dff_c3["_met"].dropna().quantile(0.90)) if not dff_c3.empty else 3000
        fig_box_c = go.Figure()
        for cadena in sorted(dff_c3["Cadena"].unique()):
            sub = dff_c3[dff_c3["Cadena"] == cadena]["_met"].dropna()
            if sub.empty:
                continue
            fig_box_c.add_trace(go.Box(
                y=sub, name=cadena, marker_color=cc(cadena),
                boxmean=True, line_width=2, marker=dict(size=4, opacity=0.4),
            ))
        fig_box_c.update_layout(**_BASE_CORE, height=420,
                                yaxis=dict(title=_met_lbl, tickprefix="$", tickformat=",",
                                           tickfont=dict(size=12, color="#111827"),
                                           range=[max(0, _p10 * 0.7), _p90 * 1.25]),
                                xaxis=dict(tickfont=dict(size=13, color="#111827")),
                                showlegend=False)
        st.plotly_chart(fig_box_c, use_container_width=True)

    with st.expander(f"{_met_lbl} promedio — Cadena × Marca", expanded=True):
        pivot_cm = (dff_c3.dropna(subset=["_met"])
                    .groupby(["Marca_cat", "Cadena"])["_met"]
                    .mean().round(0).unstack("Cadena"))
        pivot_cm = pivot_cm.reindex([m for m in ORDEN_MARCAS_AC if m in pivot_cm.index])
        if not pivot_cm.empty:
            text_cm = [[f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                       for row in pivot_cm.values]
            fig_hm_cm = go.Figure(go.Heatmap(
                z=pivot_cm.values, x=pivot_cm.columns.tolist(), y=pivot_cm.index.tolist(),
                colorscale="RdYlGn_r",
                text=text_cm, texttemplate="%{text}",
                textfont=dict(size=12, color="#111827"),
                colorbar=dict(title=_met_lbl, tickprefix="$", tickformat=",",
                              tickfont=dict(color="#111827"),
                              title_font=dict(color="#111827")),
            ))
            fig_hm_cm.update_layout(**_BASE_CORE, height=max(320, len(pivot_cm) * 48 + 80),
                                    xaxis=dict(tickfont=dict(size=13, color="#111827"), side="top"),
                                    yaxis=dict(tickfont=dict(size=13, color="#111827")))
            st.plotly_chart(fig_hm_cm, use_container_width=True)

    with st.expander(f"{_met_lbl} mínimo por cadena y marca", expanded=True):
        df_min_cm = dff_c3.dropna(subset=["_met"]).groupby(["Marca_cat", "Cadena"])["_met"].min().reset_index()
        df_min_cm["Marca_cat"] = pd.Categorical(df_min_cm["Marca_cat"],
                                                 categories=ORDEN_MARCAS_AC, ordered=True)
        df_min_cm = df_min_cm.sort_values("Marca_cat")
        fig_min = px.bar(df_min_cm, x="Marca_cat", y="_met", color="Cadena",
                         barmode="group", color_discrete_map=COLORS_CADENAS,
                         labels={"_met": f"{_met_lbl} mínimo", "Marca_cat": ""},
                         height=420, category_orders={"Marca_cat": ORDEN_MARCAS_AC})
        fig_min.update_layout(**_BASE_CORE,
                              yaxis=dict(tickprefix="$", tickformat=",",
                                         tickfont=dict(size=12, color="#111827")),
                              xaxis=dict(tickfont=dict(size=13, color="#111827"), tickangle=-20))
        st.plotly_chart(fig_min, use_container_width=True)


# ── TAB 4: Por Marca ──────────────────────────────────────────────────────
if active_page == "Por Marca":
    _mk_fv, _mk_fg, _mk_fe = st.columns(3)
    with _mk_fv:
        st.markdown('<p style="color:#111827;font-size:0.8rem;font-weight:600;margin-bottom:2px">Variedad</p>', unsafe_allow_html=True)
        var_mk = st.selectbox("Variedad", ["Todas"] + variedades_disp, key="mk_var", label_visibility="collapsed")
    with _mk_fg:
        st.markdown('<p style="color:#111827;font-size:0.8rem;font-weight:600;margin-bottom:2px">Gramaje</p>', unsafe_allow_html=True)
        gram_mk_labels = ["Todos"] + grupos_labels
        gram_mk_sel    = st.selectbox("Gramaje", gram_mk_labels, key="mk_gram", label_visibility="collapsed")
    with _mk_fe:
        st.markdown('<p style="color:#111827;font-size:0.8rem;font-weight:600;margin-bottom:2px">Envase</p>', unsafe_allow_html=True)
        envase_mk_sel = st.selectbox("Envase", ["Todos"] + envases_disp, key="mk_envase", label_visibility="collapsed")

    dff_mk = dff.copy()
    if var_mk != "Todas":
        dff_mk = dff_mk[dff_mk["Variedad"] == var_mk]
    if gram_mk_sel != "Todos":
        gram_mk_key = next((g for g, l in zip(grupos_disp, grupos_labels) if l == gram_mk_sel), None)
        if gram_mk_key:
            dff_mk = dff_mk[dff_mk["Gramaje"] == gram_mk_key]
    if envase_mk_sel != "Todos":
        dff_mk = dff_mk[dff_mk["Envase"] == envase_mk_sel]

    with st.expander("📊 Ranking y distribución de marcas", expanded=True):
        c_l, c_r = st.columns(2)
        with c_l:
            st.markdown(f'<div class="chart-title">Ranking de marcas por {_met_lbl} promedio</div>',
                        unsafe_allow_html=True)
            mk_p = (dff_mk.dropna(subset=["_met"])
                    .groupby("Marca_cat")["_met"].mean().sort_values().reset_index())
            _mk_colors = [COLORES_MARCA_AC.get(m, "#6B7280") for m in mk_p["Marca_cat"]]
            fig_mk = hbar(mk_p["_met"].tolist(), mk_p["Marca_cat"].tolist(),
                          _mk_colors,
                          [f"${v:,.0f}" for v in mk_p["_met"]], _met_lbl,
                          altura=max(300, len(mk_p) * 34))
            st.plotly_chart(fig_mk, use_container_width=True)

        with c_r:
            st.markdown('<div class="chart-title">SKUs únicos en góndola por marca</div>',
                        unsafe_allow_html=True)
            mk_sku = (dff_mk.groupby("Marca_cat")["SKU_canonico"]
                      .nunique().reset_index(name="SKUs")
                      .sort_values("SKUs", ascending=False))
            fig_sku = go.Figure(go.Bar(
                x=mk_sku["Marca_cat"], y=mk_sku["SKUs"],
                marker_color=[COLORES_MARCA_AC.get(m, "#6B7280") for m in mk_sku["Marca_cat"]],
                text=mk_sku["SKUs"], textposition="outside",
                textfont=dict(color="#111827"),
            ))
            fig_sku.update_layout(**_BASE_CORE, height=340,
                                  margin=dict(l=10, r=10, t=40, b=10),
                                  yaxis_title="SKUs únicos", showlegend=False,
                                  xaxis=dict(tickfont=dict(color="#111827")))
            st.plotly_chart(fig_sku, use_container_width=True)

    with st.expander("🌡️ Heatmap marca × cadena", expanded=True):
        st.markdown(f'<div class="chart-title">Heatmap marca × cadena ({_met_lbl} promedio)</div>',
                    unsafe_allow_html=True)
        pivot_mk_c = (dff_mk.dropna(subset=["_met"])
                      .groupby(["Marca_cat", "Cadena"])["_met"]
                      .mean().round(0).unstack("Cadena"))
        pivot_mk_c = pivot_mk_c.reindex([m for m in ORDEN_MARCAS_AC if m in pivot_mk_c.index])
        if not pivot_mk_c.empty:
            text_mk_c = [[f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                         for row in pivot_mk_c.values]
            fig_hm_mk = go.Figure(go.Heatmap(
                z=pivot_mk_c.values, x=pivot_mk_c.columns.tolist(), y=pivot_mk_c.index.tolist(),
                colorscale="RdYlGn_r",
                text=text_mk_c, texttemplate="%{text}",
                textfont=dict(size=12, color="#111827"),
                colorbar=dict(title=_met_lbl, tickprefix="$", tickformat=",",
                              tickfont=dict(color="#111827"),
                              title_font=dict(color="#111827")),
            ))
            fig_hm_mk.update_layout(**_BASE_CORE,
                                    height=max(300, len(pivot_mk_c) * 40 + 80),
                                    margin=dict(l=10, r=10, t=30, b=10),
                                    xaxis=dict(tickfont=dict(size=13, color="#111827")),
                                    yaxis=dict(tickfont=dict(size=13, color="#111827")))
            st.plotly_chart(fig_hm_mk, use_container_width=True)

    with st.expander("📋 Resumen por marca", expanded=False):
        mk_resumen = (dff_mk.groupby("Marca_cat").agg(
            SKUs=("SKU_canonico", "nunique"),
            Precio_prom=("_met", "mean"),
            En_oferta_pct=("En_oferta", lambda s: s.mean() * 100),
            Cadenas=("Cadena", "nunique"),
            Variedades=("Variedad", "nunique"),
        ).round(1).reset_index().sort_values("SKUs", ascending=False))
        mk_resumen["Precio_prom"] = mk_resumen["Precio_prom"].apply(
            lambda v: f"${v:,.0f}" if pd.notna(v) else "—")
        mk_resumen["En_oferta_pct"] = mk_resumen["En_oferta_pct"].apply(lambda v: f"{v:.0f}%")
        mk_resumen.columns = ["Marca", "SKUs únicos", f"{_met_lbl} prom.", "% en oferta",
                              "Cadenas presentes", "Variedades"]
        st.dataframe(mk_resumen, use_container_width=True, hide_index=True)


# ── TAB 5: Evolución ──────────────────────────────────────────────────────
if active_page == "Evolución":
    # Solo filtros Gramaje y Envase — Variedad/Cadena/Marca ya están en la barra lateral
    _ev_g1, _ev_g2, _ev_sp = st.columns([1, 1, 4])
    with _ev_g1:
        st.markdown('<p style="color:#111827;font-size:0.8rem;font-weight:700;margin-bottom:1px">Gramaje</p>', unsafe_allow_html=True)
        gram_ev_lbl = st.selectbox("Gramaje", ["Todos"] + grupos_labels, key="ev_gram", label_visibility="collapsed")
    with _ev_g2:
        st.markdown('<p style="color:#111827;font-size:0.8rem;font-weight:700;margin-bottom:1px">Envase</p>', unsafe_allow_html=True)
        envase_ev = st.selectbox("Envase", ["Todos"] + envases_disp, key="ev_envase", label_visibility="collapsed")
    st.markdown("---")

    dff_ev = dff.dropna(subset=["Precio_100g"]).copy()
    if gram_ev_lbl != "Todos":
        _gram_ev_key = next((g for g, l in zip(grupos_disp, grupos_labels) if l == gram_ev_lbl), None)
        if _gram_ev_key:
            dff_ev = dff_ev[dff_ev["Gramaje"] == _gram_ev_key]
    if envase_ev != "Todos":
        dff_ev = dff_ev[dff_ev["Envase"] == envase_ev]

    if dff_ev.empty:
        st.info("Sin datos con esta selección.")
    else:
        with st.expander("📈 Evolución de precio promedio ($/kg)", expanded=True):
            st.markdown('<div class="chart-title">Evolución de $/kg promedio en el tiempo</div>', unsafe_allow_html=True)
            grp = dff_ev.groupby("Fecha")["Precio_100g"].mean().mul(10).round(0).reset_index()
            fig_ev = go.Figure(go.Scatter(
                x=grp["Fecha"], y=grp["Precio_100g"],
                mode="lines+markers", name="$/kg",
                line=dict(color="#0F3460", width=2),
                marker=dict(size=7),
            ))
            fig_ev.update_layout(**_BASE_CORE, height=380,
                                 margin=dict(l=10, r=10, t=40, b=10),
                                 yaxis=dict(tickprefix="$", tickformat=",",
                                            tickfont=dict(color="#111827")),
                                 xaxis=dict(tickfont=dict(color="#111827"),
                                            type="date", tickformat="%d %b '%y"))
            st.plotly_chart(fig_ev, use_container_width=True)

        with st.expander("🔖 Góndola vs. precio con oferta", expanded=True):
          if dff_ev["En_oferta"].any():
            st.markdown('<div class="chart-title">Precio góndola vs. precio con oferta</div>',
                        unsafe_allow_html=True)
            grp_gond = (dff_ev.groupby("Fecha")["Precio_100g"].mean().mul(10).round(0).reset_index()
                        .rename(columns={"Precio_100g": "Góndola ($/kg)"}))
            grp_ofr  = (dff_ev.groupby("Fecha")["Precio_100g_oferta"].mean().mul(10).round(0).reset_index()
                        .rename(columns={"Precio_100g_oferta": "Con oferta ($/kg)"}))
            merged_ev = grp_gond.merge(grp_ofr, on="Fecha")
            fig_ov = go.Figure()
            fig_ov.add_trace(go.Scatter(x=merged_ev["Fecha"], y=merged_ev["Góndola ($/kg)"],
                                        name="Góndola", mode="lines+markers",
                                        line=dict(color="#0F3460", width=2)))
            fig_ov.add_trace(go.Scatter(x=merged_ev["Fecha"], y=merged_ev["Con oferta ($/kg)"],
                                        name="Con oferta", mode="lines+markers",
                                        line=dict(color="#00B050", width=2, dash="dot")))
            fig_ov.update_layout(**_BASE_CORE, height=300, margin=dict(l=10, r=10, t=30, b=10),
                                 yaxis=dict(tickprefix="$", tickformat=",",
                                            tickfont=dict(color="#111827")),
                                 xaxis=dict(tickfont=dict(color="#111827"),
                                            type="date", tickformat="%d %b '%y"))
            st.plotly_chart(fig_ov, use_container_width=True)
          st.markdown('<div class="chart-note">$/kg promedio del segmento filtrado.</div>',
                      unsafe_allow_html=True)


# ── TAB 6: Ofertas ────────────────────────────────────────────────────────
if active_page == "Ofertas":
    df_of_ult = df_ult[df_ult["En_oferta"]].copy()
    if df_of_ult.empty:
        st.info("Sin productos en oferta en la última semana.")
    else:
        df_of_ult = df_of_ult.sort_values("Descuento_pct", ascending=False)

        # ── Barra resumen de ofertas ──────────────────────────────────
        _n_of      = len(df_of_ult)
        _pct_of    = _n_of / len(df_ult) * 100 if len(df_ult) > 0 else 0
        _dto_prom  = df_of_ult["Descuento_pct"].mean()
        _dto_max   = df_of_ult["Descuento_pct"].max()
        _cad_of    = df_of_ult["Cadena"].nunique()
        _marca_top_of = (df_of_ult["Marca_cat"].value_counts().index[0]
                         if not df_of_ult["Marca_cat"].value_counts().empty else "—")
        _n_marca_top  = (df_of_ult["Marca_cat"].value_counts().iloc[0]
                         if not df_of_ult["Marca_cat"].value_counts().empty else 0)
        _oc1, _oc2, _oc3, _oc4, _oc5, _oc6 = st.columns(6)
        with _oc1:
            _kpi_mini("🏷️", "Ofertas activas", str(_n_of), "productos en descuento")
        with _oc2:
            _kpi_mini("📊", "% del catálogo", f"{_pct_of:.0f}%", "SKUs con oferta")
        with _oc3:
            _kpi_mini("📉", "Dto. promedio", f"{_dto_prom:.0f}%", "sobre precio góndola")
        with _oc4:
            _kpi_mini("🔥", "Dto. máximo", f"{_dto_max:.0f}%", "mayor descuento activo")
        with _oc5:
            _kpi_mini("🏪", "Cadenas activas", str(_cad_of), "con productos en oferta")
        with _oc6:
            _kpi_mini("🏆", "Marca con más", _marca_top_of, f"{_n_marca_top} ofertas")
        st.markdown("---")

        with st.expander("📊 Descuentos por cadena y variedad", expanded=True):
            c_l, c_r = st.columns(2)
            with c_l:
                of_cad = (df_of_ult.groupby("Cadena")["Descuento_pct"]
                          .mean().sort_values(ascending=False).reset_index())
                fig_of2 = go.Figure(go.Bar(
                    x=of_cad["Cadena"], y=of_cad["Descuento_pct"],
                    marker_color=[cc(c) for c in of_cad["Cadena"]],
                    text=[f"{v:.0f}%" for v in of_cad["Descuento_pct"]],
                    textposition="outside", textfont=dict(color="#111827"),
                ))
                fig_of2.update_layout(**_BASE_CORE, height=340,
                                      margin=dict(l=10, r=10, t=40, b=10),
                                      yaxis=dict(title="% descuento promedio",
                                                 tickfont=dict(color="#111827")),
                                      xaxis=dict(tickfont=dict(color="#111827")),
                                      showlegend=False)
                st.plotly_chart(fig_of2, use_container_width=True)

            with c_r:
                of_var = (df_of_ult.groupby("Variedad")["Descuento_pct"]
                          .mean().sort_values(ascending=False).reset_index())
                fig_of_var = go.Figure(go.Bar(
                    x=of_var["Variedad"], y=of_var["Descuento_pct"],
                    marker_color=[cv(v) for v in of_var["Variedad"]],
                    text=[f"{v:.0f}%" for v in of_var["Descuento_pct"]],
                    textposition="outside", textfont=dict(color="#111827"),
                ))
                fig_of_var.update_layout(**_BASE_CORE, height=340,
                                         margin=dict(l=10, r=10, t=40, b=10),
                                         yaxis=dict(title="% descuento promedio",
                                                    tickfont=dict(color="#111827")),
                                         xaxis=dict(tickfont=dict(color="#111827"), tickangle=-20),
                                         showlegend=False)
                st.plotly_chart(fig_of_var, use_container_width=True)

        with st.expander("🔖 Detalle de ofertas activas", expanded=True):
            _of_ord_col, _ = st.columns([1, 3])
            with _of_ord_col:
                st.markdown('<p style="color:#111827;font-size:0.82rem;font-weight:700;margin-bottom:2px">🔃 Ordenar por</p>', unsafe_allow_html=True)
                _of_orden = st.radio("Ordenar por", ["Mayor descuento", "Marca"],
                                     horizontal=True, key="of_orden", label_visibility="collapsed")
            _df_of_cards = df_of_ult[["Cadena", "Marca_cat", "SKU_canonico", "Producto",
                                       "Descuento_pct", "Precio", "Precio_oferta", "URL"]].copy()
            if _of_orden == "Marca":
                _marca_orden_map = {m: i for i, m in enumerate(ORDEN_MARCAS_AC)}
                _df_of_cards["_mk_ord"] = _df_of_cards["Marca_cat"].map(_marca_orden_map).fillna(99)
                _df_of_cards = _df_of_cards.sort_values(["_mk_ord", "SKU_canonico"]).drop(columns="_mk_ord")
            render_offer_cards(_df_of_cards, compact=False, grid_cols=3, max_height=600)


# ── TAB 7: Quiebres ───────────────────────────────────────────────────────
if active_page == "Quiebres":
    st.markdown(
        '<div class="chart-note">Un <b>quiebre</b> ocurre cuando un producto estaba disponible '
        'en un período y dejó de aparecer en el siguiente. '
        '✓ verde = presente &nbsp;·&nbsp; ✗ rojo = quiebre &nbsp;·&nbsp; — gris = sin datos.</div>',
        unsafe_allow_html=True,
    )

    _qb_colorscale = [
        [0.00, "#FCA5A5"], [0.33, "#FCA5A5"],
        [0.34, "#F3F4F6"], [0.66, "#F3F4F6"],
        [0.67, "#86EFAC"], [1.00, "#86EFAC"],
    ]

    _qb_fa, _qb_fb, _qb_fc, _ = st.columns([1, 1, 1, 3])
    with _qb_fa:
        st.markdown('<p style="color:#111827;font-size:0.82rem;font-weight:700;margin-bottom:1px">🏷️ Marca</p>', unsafe_allow_html=True)
        _qb_marca_opts = sorted(df_full["Marca_cat"].unique())
        _qb_marca = st.selectbox("Marca", _qb_marca_opts, key="qb_marca", label_visibility="collapsed")
    with _qb_fb:
        st.markdown('<p style="color:#111827;font-size:0.82rem;font-weight:700;margin-bottom:1px">🏪 Cadena</p>', unsafe_allow_html=True)
        _qb_cad_opts = ["Todas las cadenas"] + sorted(
            df_full[df_full["Marca_cat"] == _qb_marca]["Cadena"].unique()
        )
        _qb_cadena = st.selectbox("Cadena", _qb_cad_opts, key="qb_cadena", label_visibility="collapsed")
    with _qb_fc:
        st.markdown('<p style="color:#111827;font-size:0.82rem;font-weight:700;margin-bottom:1px">📅 Temporalidad</p>', unsafe_allow_html=True)
        _qb_gran = st.selectbox("Temporalidad", ["Semanal", "Mensual"], key="qb_gran", label_visibility="collapsed")

    _qb_src = df_full[df_full["Marca_cat"] == _qb_marca].copy()
    if _qb_cadena != "Todas las cadenas":
        _qb_src = _qb_src[_qb_src["Cadena"] == _qb_cadena].copy()

    if _qb_src.empty:
        st.info("Sin datos para la selección.")
    else:
        if _qb_gran == "Mensual":
            _qb_src["_pqb"] = _qb_src["Fecha"].dt.strftime("%b %Y")
            _seen_m: list = []
            for _x in (_qb_src[["Fecha", "_pqb"]].drop_duplicates()
                       .sort_values("Fecha")["_pqb"].tolist()):
                if _x not in _seen_m:
                    _seen_m.append(_x)
            _qb_cols_ord = _seen_m
        else:
            _qb_src["_pqb"] = _qb_src["Periodo"]
            _qb_cols_ord = sorted(
                _qb_src["_pqb"].unique(),
                key=lambda p: df_full[df_full["Periodo"] == p]["Fecha"].min(),
            )

        _qb_pres  = _qb_src.groupby(["_pqb", "SKU_canonico"]).size().reset_index(name="_n")
        _qb_pivot = (
            _qb_pres.pivot(index="SKU_canonico", columns="_pqb", values="_n")
            .reindex(columns=[c for c in _qb_cols_ord if c in _qb_pres["_pqb"].unique()])
            .fillna(0)
        )

        if not _qb_pivot.empty:
            with st.expander("📅 Evolución de SKU por temporalidad", expanded=True):
                _qb_status = _qb_pivot.copy().astype(float)
                _qb_text   = _qb_pivot.copy().astype(object)
                for _ri in range(len(_qb_pivot)):
                    _seen_flag = False
                    for _ci in range(len(_qb_pivot.columns)):
                        _v = _qb_pivot.iloc[_ri, _ci]
                        if _v > 0:
                            _qb_status.iloc[_ri, _ci] = 1
                            _qb_text.iloc[_ri, _ci]   = "✓"
                            _seen_flag = True
                        elif _seen_flag:
                            _qb_status.iloc[_ri, _ci] = -1
                            _qb_text.iloc[_ri, _ci]   = "✗"
                        else:
                            _qb_status.iloc[_ri, _ci] = 0
                            _qb_text.iloc[_ri, _ci]   = "—"

                fig_qb = go.Figure(go.Heatmap(
                    z=_qb_status.values,
                    x=_qb_status.columns.tolist(),
                    y=_qb_status.index.tolist(),
                    colorscale=_qb_colorscale, zmin=-1, zmax=1,
                    text=_qb_text.values, texttemplate="%{text}",
                    textfont=dict(size=13, color="#111827"),
                    showscale=False, xgap=2, ygap=2,
                ))
                fig_qb.update_layout(**_BASE_CORE,
                                     height=max(280, len(_qb_pivot) * 40 + 100),
                                     xaxis=dict(tickfont=dict(size=11, color="#111827"),
                                                side="bottom", tickangle=-20),
                                     yaxis=dict(tickfont=dict(size=11, color="#111827")))
                st.plotly_chart(fig_qb, use_container_width=True)

                _qb_n_breaks = (_qb_status == -1).sum(axis=1)
                _qb_with_breaks = _qb_n_breaks[_qb_n_breaks > 0].sort_values(ascending=False)
                if not _qb_with_breaks.empty:
                    st.markdown('<div class="chart-title">Resumen de quiebres por SKU</div>',
                                unsafe_allow_html=True)
                    _qb_unit = {"Semanal": "semanas", "Mensual": "meses"}[_qb_gran]
                    _qb_rows = []
                    for _sk, _nb in _qb_with_breaks.items():
                        _per_afect = [_qb_status.columns[_ci]
                                      for _ci in range(len(_qb_status.columns))
                                      if _qb_status.loc[_sk, _qb_status.columns[_ci]] == -1]
                        _qb_rows.append({
                            "SKU": _sk,
                            f"Quiebres ({_qb_unit})": int(_nb),
                            "Períodos afectados": ", ".join(_per_afect),
                        })
                    _qb_col, _ = st.columns([3, 1])
                    with _qb_col:
                        st.dataframe(pd.DataFrame(_qb_rows), hide_index=True)
                else:
                    st.success("✅ No se detectaron quiebres en el período seleccionado.")

    # ── Presencia SKU × Cadena ───────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="chart-title">📍 Presencia por cadena — SKU × Cadena</div>',
                unsafe_allow_html=True)

    _qb_src_marca = df_full[df_full["Marca_cat"] == _qb_marca].copy()
    if not _qb_src_marca.empty:
        _qb_fechas_disp = sorted(df_full["Fecha"].unique())
        if _qb_gran == "Mensual":
            _seen_mes: dict = {}
            for _f in _qb_fechas_disp:
                _k = pd.Timestamp(_f).strftime("%b %Y")
                _seen_mes.setdefault(_k, []).append(_f)
            _qb_per_labels = list(_seen_mes.keys())
            _qb_per_map    = _seen_mes
        else:
            _seen_sem: dict = {}
            for _f in _qb_fechas_disp:
                _ts = pd.Timestamp(_f)
                _k  = f"Sem {_ts.isocalendar().week} · {_ts.strftime('%b %Y')}"
                _seen_sem.setdefault(_k, []).append(_f)
            _qb_per_labels = list(_seen_sem.keys())
            _qb_per_map    = _seen_sem

        _pres_lbl = st.selectbox("🗓️ Período a visualizar", _qb_per_labels,
                                  index=len(_qb_per_labels) - 1, key="qb_pres_ventana")
        _pres_fechas = _qb_per_map[_pres_lbl]

        st.markdown(
            f'<div class="chart-note">🟢 activo en <b>{_pres_lbl}</b> &nbsp;·&nbsp; '
            '🔴 estuvo antes pero no en este período &nbsp;·&nbsp; — nunca en esa cadena.</div>',
            unsafe_allow_html=True)

        _ventana_df    = _qb_src_marca[_qb_src_marca["Fecha"].isin(_pres_fechas)]
        _pres_set      = set(zip(_ventana_df["SKU_canonico"], _ventana_df["Cadena"]))
        _todos_skus    = sorted(_qb_src_marca["SKU_canonico"].unique())
        _todas_cad     = sorted(df_full["Cadena"].unique())
        _historial_set = set(zip(_qb_src_marca["SKU_canonico"], _qb_src_marca["Cadena"]))

        _z, _txt = [], []
        for _sk in _todos_skus:
            _rz, _rt = [], []
            for _cd in _todas_cad:
                if (_sk, _cd) in _pres_set:
                    _rz.append(1);  _rt.append("✓")
                elif (_sk, _cd) in _historial_set:
                    _rz.append(-1); _rt.append("✗")
                else:
                    _rz.append(0);  _rt.append("—")
            _z.append(_rz); _txt.append(_rt)

        fig_pres = go.Figure(go.Heatmap(
            z=_z, x=_todas_cad, y=_todos_skus,
            colorscale=_qb_colorscale, zmin=-1, zmax=1,
            text=_txt, texttemplate="%{text}",
            textfont=dict(size=13, color="#111827"),
            showscale=False, xgap=3, ygap=3,
        ))
        fig_pres.update_layout(**_BASE_CORE,
                               height=max(200, len(_todos_skus) * 38 + 80),
                               xaxis=dict(tickfont=dict(size=12, color="#111827"), side="top"),
                               yaxis=dict(tickfont=dict(size=11, color="#111827")))
        st.plotly_chart(fig_pres, use_container_width=True)


# ── TAB 8: Tabla dinámica ─────────────────────────────────────────────────
if active_page == "Tabla dinámica":
    c_row, c_col, c_met = st.columns(3)
    with c_row:
        pivot_fila = st.selectbox("Filas", ["Variedad", "Marca_cat", "Cadena", "Gramaje", "Envase"],
                                   format_func=lambda x: x.replace("Marca_cat", "Marca"),
                                   key="piv_row")
    with c_col:
        opciones_col = [o for o in ["Cadena", "Variedad", "Periodo"] if o != pivot_fila]
        pivot_col = st.selectbox("Columnas", opciones_col,
                                  format_func=lambda x: x.replace("Periodo", "Período"),
                                  key="piv_col")
    with c_met:
        pivot_met = st.selectbox("Métrica",
                                  ["$/100g promedio", "SKUs únicos", "% en oferta"],
                                  key="piv_met")

    if pivot_met == "$/100g promedio":
        tbl = (dff.dropna(subset=["Precio_100g"])
               .groupby([pivot_fila, pivot_col])["Precio_100g"]
               .mean().round(0).unstack(pivot_col))
        fmt = "${:,.0f}"
    elif pivot_met == "SKUs únicos":
        tbl = (dff.groupby([pivot_fila, pivot_col])["SKU_canonico"]
               .nunique().unstack(pivot_col).fillna(0).astype(int))
        fmt = "{:,}"
    else:
        tbl = (dff.groupby([pivot_fila, pivot_col])["En_oferta"]
               .mean().mul(100).round(1).unstack(pivot_col))
        fmt = "{:.1f}%"

    if tbl.empty:
        st.info("Sin datos para esta combinación.")
    else:
        fila_lbl = pivot_fila.replace("Marca_cat", "Marca")
        col_lbl  = pivot_col.replace("Periodo", "Período")
        st.markdown(f'<div class="chart-title">{pivot_met} · {fila_lbl} × {col_lbl}</div>',
                    unsafe_allow_html=True)
        st.dataframe(tbl.style.format(fmt, na_rep="—"), use_container_width=True)


# ── TAB 9: Base ───────────────────────────────────────────────────────────
if active_page == "Base":
    st.markdown('<div class="chart-title">Datos completos</div>', unsafe_allow_html=True)

    c_s1, c_s2, c_s3 = st.columns([3, 1, 1])
    with c_s1:
        busqueda = st.text_input("Buscar en nombre", placeholder="ej: rellena, nucete…",
                                 label_visibility="collapsed")
    with c_s2:
        solo_oferta = st.checkbox("Solo en oferta")
    with c_s3:
        solo_destacadas = st.checkbox("Solo marcas dest.")

    df_base = dff.copy()
    if busqueda:
        df_base = df_base[df_base["Producto"].str.contains(busqueda, case=False, na=False)]
    if solo_oferta:
        df_base = df_base[df_base["En_oferta"]]
    if solo_destacadas:
        df_base = df_base[df_base["Marca_cat"].isin(MARCAS_DESTACADAS_AC)]

    cols_base = ["Periodo", "Cadena", "Marca", "Marca_cat", "Variedad", "Gramaje",
                 "Envase", "Producto", "Gramos", "Precio", "Precio_oferta",
                 "Precio_100g", "Precio_100g_oferta", "Descuento_pct",
                 "En_oferta", "Gramaje_conf", "Gramaje_fuente", "URL"]
    st.dataframe(
        df_base[cols_base]
        .sort_values(["Cadena", "Variedad", "Precio_100g"])
        .rename(columns={
            "Marca_cat":           "Categoría",
            "Precio":              "Góndola ($)",
            "Precio_oferta":       "Oferta ($)",
            "Precio_100g":         "$/100g",
            "Precio_100g_oferta":  "$/100g oferta",
            "Descuento_pct":       "Dto. %",
            "En_oferta":           "En oferta",
            "Gramaje_conf":        "Conf. gramaje",
            "Gramaje_fuente":      "Fuente gramaje",
            "Gramos":              "Gramos (g)",
        }),
        use_container_width=True, hide_index=True, height=600,
    )
