#!/usr/bin/env python3
"""
Dashboard de precios de aceite de oliva — Aceite Tracker
Uso: streamlit run dashboard.py
"""

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

DIRECTORIO = Path(__file__).parent

# ── Marcas ────────────────────────────────────────────────────────────────
MARCAS_DESTACADAS = {"La Toscana","Zuelo","Oliovita","Natura","Nucete","Cocinero","Lira"}
MARCAS_SUPER      = {"Carrefour","Jumbo","Disco","Vea","Día","Coto","Chango Más","La Anónima"}
def categorizar(marca: str) -> str:
    if marca in MARCAS_DESTACADAS: return marca
    if marca in MARCAS_SUPER:      return "Marca Propia"
    return "Otras"

COLORES_CAT = {
    "La Toscana":"#2E86AB","Zuelo":"#A23B72","Oliovita":"#F18F01","Natura":"#C73E1D",
    "Nucete":"#3B1F2B","Cocinero":"#44BBA4","Lira":"#E94F37",
    "Marca Propia":"#6B7280","Otras":"#D1D5DB",
}
COLORS_CADENAS = {
    "Carrefour":"#004B9B","Jumbo":"#E63329","Disco":"#00A651","Vea":"#F7931E",
    "Día":"#ED1C24","Chango Más":"#7B2D8B","Coto":"#002D72","La Anónima":"#C8102E",
}

# Buckets de gramaje estándar
GRAMAJE_BUCKETS: dict[str, tuple[int,int]] = {
    "250 ml":  (200,  349),
    "500 ml":  (350,  649),
    "750 ml":  (650,  849),
    "1 L":     (850,  1249),
    "1.5 L":   (1250, 1749),
    "2 L":     (1750, 2499),
    "3 L":     (2500, 3999),
    "5 L+":    (4000, 99999),
}

def bucket_gramaje(ml) -> str | None:
    if ml is None or (isinstance(ml, float) and pd.isna(ml)):
        return None
    for etiq, (lo, hi) in GRAMAJE_BUCKETS.items():
        if lo <= int(ml) <= hi:
            return etiq
    return None

# ── Canonicalización de SKU ───────────────────────────────────────────────
def _norm_sku(s: str) -> str:
    s = s.lower()
    for a, b in [("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ü","u"),("ñ","n")]:
        s = s.replace(a, b)
    return s

_VARIANTE_PATS = [
    # flavored / infused — most specific first
    (r"con\s+aji|aji\s+picante",            "Con Ají"),
    (r"con\s+albahaca",                      "Con Albahaca"),
    (r"con\s+limon",                         "Con Limón"),
    (r"con\s+ajo\b",                         "Con Ajo"),
    # harvest type
    (r"cosecha\s+tardia",                    "Cosecha Tardía"),
    (r"cosecha\s+temprana",                  "Cosecha Temprana"),
    # olive varieties
    (r"changlot",                            "Changlot"),
    (r"coratina",                            "Coratina"),
    (r"arbequina",                           "Arbequina"),
    (r"organico|organica",                   "Orgánico"),
    (r"mediterraneo",                        "Mediterráneo"),
    (r"andino",                              "Andino"),
    (r"fuerte",                              "Fuerte"),
    # line/style
    (r"sin\s+tacc",                          "Sin TACC"),
    (r"lata",                                "Lata"),
    (r"aerosol|rocio\s+vegetal",             "Aerosol"),
    (r"\bbox\b",                             "Box"),
    (r"intenso|intensa",                     "Intenso"),
    (r"suave",                               "Suave"),
    (r"clasic[oa]",                          "Clásico"),
]

def _ml_label(ml) -> str:
    if ml is None: return "?"
    ml = int(ml)
    if ml < 350:   return f"{ml} ml"   # aerosoles, 250ml
    if ml < 1000:  return f"{ml} ml"
    l = ml / 1000
    return f"{int(l)} L" if l == int(l) else f"{l:.1f} L"

def canonicalizar_sku(marca_raw: str, nombre: str, ml) -> str:
    """Devuelve un nombre canónico para el SKU: Marca [Variante] [Formato] Tamaño."""
    n = _norm_sku(nombre)

    # Zuelo usa letras sueltas en Coto: "C Zuelo", "S Zuelo", "I Zuelo"
    variante = None
    if "zuelo" in n:
        if re.search(r"\bc\s+zuelo\b", n):   variante = "Clásico"
        elif re.search(r"\bs\s+zuelo\b", n): variante = "Suave"
        elif re.search(r"\bi\s+zuelo\b", n): variante = "Intenso"

    if variante is None:
        for pat, lbl in _VARIANTE_PATS:
            if re.search(pat, n):
                variante = lbl
                break

    # Formato: solo Oliovita distingue PET vs Vidrio
    formato = None
    if marca_raw == "Oliovita":
        if re.search(r"\bpet\b", n):
            formato = "PET"
        else:
            formato = "Vidrio"

    parts = [marca_raw]
    if variante:
        parts.append(variante)
    if formato:
        parts.append(formato)
    parts.append(_ml_label(ml))
    return " ".join(parts)

# ── Configuración página ──────────────────────────────────────────────────
st.set_page_config(page_title="Aceite Tracker | Monitor de Precios",
                   page_icon="🫒", layout="wide", initial_sidebar_state="expanded")

# ── Protección por contraseña ─────────────────────────────────────────────
_MAX_INTENTOS = 5

def _check_password():
    if st.session_state.get("_pwd_ok", False):
        return True

    intentos = st.session_state.get("_intentos", 0)
    if intentos >= _MAX_INTENTOS:
        st.error("Demasiados intentos fallidos. Cerrá y volvé a abrir el navegador.")
        st.stop()

    st.markdown("""
    <style>
      [data-testid="stAppViewContainer"] {
        background: linear-gradient(135deg, #f0f9ff 0%, #e8f5e9 50%, #fff8e1 100%);
      }
      [data-testid="stHeader"] { background: transparent !important; }
      .login-card {
        background: white;
        border-radius: 20px;
        padding: 3rem 2.5rem 2.5rem 2.5rem;
        box-shadow: 0 20px 60px rgba(0,0,0,0.10), 0 4px 16px rgba(0,0,0,0.06);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 0.6rem;
        max-width: 360px;
        margin: 8vh auto 0 auto;
      }
      .login-icon {
        font-size: 3.2rem;
        line-height: 1;
        margin-bottom: 0.4rem;
      }
      .login-title {
        font-size: 1.6rem;
        font-weight: 800;
        color: #0F172A;
        letter-spacing: -0.5px;
      }
      .login-subtitle {
        font-size: 0.88rem;
        color: #6B7280;
        margin-bottom: 0.8rem;
      }
      .login-divider {
        width: 40px;
        height: 3px;
        background: linear-gradient(90deg, #22c55e, #16a34a);
        border-radius: 4px;
        margin: 0.3rem 0 1rem 0;
      }
    </style>
    <div class="login-card">
      <div class="login-icon">🫒</div>
      <div class="login-title">Aceite Tracker</div>
      <div class="login-divider"></div>
      <div class="login-subtitle">Ingresá la contraseña para continuar</div>
    </div>
    """, unsafe_allow_html=True)
    _, col, _ = st.columns([2, 1.5, 2])
    with col:
        st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
        pwd = st.text_input("Contraseña", type="password", label_visibility="collapsed",
                            placeholder="🔒  Contraseña…")
        if st.button("Entrar →", use_container_width=True, type="primary"):
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
    --sidebar-w:230px
}

.stApp{
    background:var(--off-white);
    background-image:
        radial-gradient(ellipse at var(--mx,30%) var(--my,20%),rgba(22,163,74,0.07) 0%,transparent 55%),
        radial-gradient(ellipse at 85% 85%,rgba(34,197,94,0.05) 0%,transparent 50%)
}
.block-container{padding:1.5rem 2rem 3rem;max-width:1500px}
#MainMenu,footer,header{visibility:hidden}

/* ── Sidebar: pegada a la izquierda, siempre visible ── */
[data-testid="stSidebar"]{
    background:#FFFFFF!important;
    border-right:1px solid var(--gray-200)!important;
    box-shadow:4px 0 24px rgba(0,0,0,0.06)!important;
    min-width:var(--sidebar-w)!important;max-width:var(--sidebar-w)!important;
    position:fixed!important;left:0!important;top:0!important;
    height:100vh!important;z-index:999!important;
    transform:none!important;transition:none!important
}
/* Ocultar todos los botones de colapsar/expandir */
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
button[kind="header"]{display:none!important;visibility:hidden!important}
section[data-testid="stSidebarContent"]{display:block!important;visibility:visible!important}
/* Contenido principal: respetar el ancho del sidebar */
[data-testid="stAppViewContainer"]{padding-left:var(--sidebar-w)!important}
.block-container{padding-left:1.5rem!important;padding-right:2rem!important;max-width:100%!important}
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p{color:var(--gray-600)!important}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h3{color:var(--gray-900)!important}

.sidebar-logo{font-size:1.1rem;font-weight:800;color:var(--gray-900);letter-spacing:-0.5px;line-height:1.3}
.sidebar-logo .accent{color:var(--green);text-shadow:0 0 20px rgba(22,163,74,0.5)}
.sidebar-sub{font-size:0.66rem;color:var(--gray-400);margin-bottom:0.25rem}
.sidebar-sep{font-size:0.57rem;font-weight:700;text-transform:uppercase;letter-spacing:1.3px;
    color:var(--gray-400);margin:0.85rem 0 0.28rem;padding-bottom:0.25rem;
    border-bottom:1px solid var(--gray-200)}

/* ── Header ── */
.main-header{
    background:linear-gradient(120deg,#064E3B 0%,#065F46 30%,#047857 60%,#059669 100%);
    background-size:300% 300%;
    animation:headerShimmer 10s ease infinite,fadeInDown 0.6s ease;
    padding:1.6rem 2.2rem;border-radius:24px;margin-bottom:1.5rem;
    display:flex;align-items:center;justify-content:space-between;
    box-shadow:0 12px 40px rgba(6,79,67,0.35),0 0 0 1px rgba(74,222,128,0.2);
    position:relative;overflow:hidden;transition:box-shadow 0.4s ease,transform 0.3s ease
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
.header-eyebrow{font-size:0.65rem;font-weight:700;text-transform:uppercase;
    letter-spacing:2px;color:rgba(187,253,232,0.7);margin-bottom:0.3rem}
.header-left h1{font-size:1.6rem;font-weight:900;color:#fff;margin:0;
    letter-spacing:-0.8px;text-shadow:0 0 40px rgba(74,222,128,0.4)}
.header-left p{font-size:0.78rem;color:rgba(187,253,232,0.65);margin:0.25rem 0 0}
.header-badge{
    background:rgba(74,222,128,0.15);border:1px solid rgba(74,222,128,0.4);
    border-radius:50px;padding:0.3rem 1rem;color:#fff!important;
    font-size:0.75rem;font-weight:700;animation:glowPulse 3s ease infinite;
    backdrop-filter:blur(8px)
}
.header-link-btn{
    background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.25);
    border-radius:50px;padding:0.32rem 1rem;color:#fff!important;
    font-size:0.73rem;font-weight:700;text-decoration:none;letter-spacing:0.5px;
    transition:all 0.3s ease;backdrop-filter:blur(8px)
}
.header-link-btn:hover{
    background:rgba(255,255,255,0.22);border-color:rgba(74,222,128,0.5);
    color:#fff!important;box-shadow:0 0 16px rgba(74,222,128,0.3);transform:scale(1.05)
}

/* ── KPI Cards ── */
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
.kpi-card.blue{border-top-color:#3B82F6}
.kpi-card.blue:hover{box-shadow:0 20px 48px rgba(59,130,246,0.2),0 0 32px rgba(59,130,246,0.15)}
.kpi-label{font-size:0.59rem;font-weight:700;text-transform:uppercase;
    letter-spacing:1.5px;color:var(--gray-400);margin-bottom:0.5rem}
.kpi-value{font-size:1.8rem;font-weight:900;color:var(--gray-900);
    line-height:1;transition:color 0.3s}
.kpi-sub{font-size:0.68rem;color:var(--gray-600);margin-top:0.4rem}

/* ── Chart titles ── */
.chart-title{font-size:0.78rem;font-weight:700;color:var(--gray-900);
    margin-bottom:0.7rem;padding-bottom:0.4rem;border-bottom:2px solid #F0FDF4;
    text-transform:uppercase;letter-spacing:0.6px}
.chart-note{font-size:0.73rem;color:var(--gray-600);margin-top:-0.4rem;margin-bottom:0.75rem}

/* ── Buttons ── */
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
    letter-spacing:3px!important;background-color:var(--green)!important;
    color:#fff!important;box-shadow:rgba(22,163,74,0.5) 0px 7px 29px 0px!important
}
.stButton > button:active{
    letter-spacing:3px!important;background-color:var(--green)!important;
    color:#fff!important;box-shadow:rgba(22,163,74,0.5) 0px 0px 0px 0px!important;
    transform:translateY(10px)!important;transition:100ms!important
}
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

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"]{background:#fff;border-radius:12px 12px 0 0;
    padding:0.4rem 0.4rem 0;gap:0.2rem;box-shadow:0 1px 6px rgba(0,0,0,0.06)}
.stTabs [data-baseweb="tab"]{font-size:0.84rem;font-weight:600;color:#6B7280;
    border-radius:8px 8px 0 0;padding:0.55rem 1.2rem}
.stTabs [aria-selected="true"]{color:var(--green)!important;background:#F0FDF4!important}
.stTabs [data-baseweb="tab-panel"]{background:#fff;border-radius:0 0 14px 14px;
    padding:1.5rem 1.6rem;box-shadow:0 2px 8px rgba(0,0,0,0.06)}

/* ── Expanders ── */
[data-testid="stExpander"]{background:#fff!important;border:1px solid var(--gray-200)!important;
    border-radius:16px!important;margin-bottom:0.75rem!important;
    box-shadow:0 2px 12px rgba(0,0,0,0.05)!important;transition:all 0.3s ease!important}
[data-testid="stExpander"] summary,
[data-testid="stExpander"] details > summary,
.streamlit-expanderHeader{color:var(--gray-900)!important;font-weight:600!important;
    background:var(--gray-50)!important;border-radius:16px 16px 0 0!important}
[data-testid="stExpander"] summary:hover{background:#F0FDF4!important}

/* ── Filter labels ── */
.stSelectbox label,.stMultiSelect label{color:#111827!important;font-size:0.72rem!important;
    font-weight:700!important;text-transform:uppercase!important;letter-spacing:.04em!important}

/* ── Multiselect tags ── */
[data-baseweb="tag"]{background:var(--green-bg)!important;
    border:1px solid rgba(22,163,74,0.3)!important}
[data-baseweb="tag"] span{color:var(--green)!important;font-weight:600!important}

/* ── Animations ── */
@keyframes fadeInDown{from{opacity:0;transform:translateY(-20px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeInUp{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:translateY(0)}}
@keyframes glowPulse{0%,100%{box-shadow:0 0 8px rgba(74,222,128,0.3)}50%{box-shadow:0 0 24px rgba(74,222,128,0.7),0 0 48px rgba(22,163,74,0.35)}}
@keyframes headerShimmer{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
@keyframes float{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-15px) scale(1.05)}}
@keyframes shimmerLine{0%{background-position:-200% 0}100%{background-position:200% 0}}
@keyframes rippleAnim{to{transform:scale(4);opacity:0}}
@keyframes navPressed{
    0%  {transform:translateX(0) scale(1)}
    20% {transform:translateX(8px) scale(0.96)}
    50% {transform:translateX(3px) scale(0.98)}
    75% {transform:translateX(5px) scale(1.01)}
    100%{transform:translateX(4px) scale(1)}
}

/* ── Mobile ── */
@media(max-width:768px){
    .block-container{padding:0.6rem 0.8rem 2rem!important}
    .main-header{padding:1rem!important;flex-direction:column!important;gap:0.5rem!important}
    :root{--sidebar-w:200px}
    .stTabs [data-baseweb="tab-list"]{overflow-x:auto!important;flex-wrap:nowrap!important;
        -webkit-overflow-scrolling:touch!important;scrollbar-width:none!important}
    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar{display:none}
    .stTabs [data-baseweb="tab"]{font-size:0.72rem!important;padding:0.4rem 0.7rem!important;white-space:nowrap!important}
    .header-right{display:none!important}
}
@media(max-width:480px){
    .block-container{padding:0.4rem 0.4rem 2rem!important}
    .stTabs [data-baseweb="tab"]{font-size:0.65rem!important;padding:0.35rem 0.5rem!important}
}

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
    transform:translateX(4px)!important;margin-left:-1px!important;
    animation:navPressed 0.35s cubic-bezier(.36,.07,.19,.97) both!important
}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"][aria-checked="true"] p{
    color:var(--green)!important;font-weight:800!important;
    text-shadow:0 0 12px rgba(22,163,74,0.4)!important;font-size:0.88rem!important
}
[data-testid="stSidebar"] [data-testid="stRadio"] [data-baseweb="radio"] > div:first-child{display:none!important}
[data-testid="stSidebar"] [data-testid="stRadio"] p{font-size:0.82rem!important;font-weight:500!important;color:#374151!important;margin:0!important;transition:color 0.2s!important}
</style>
""", unsafe_allow_html=True)

# ── Interactividad JS (parallax, 3D tilt, glow, ripple) ──────────────────
import streamlit.components.v1 as _comp_ui
_comp_ui.html("""
<script>
(function(){
  var doc = window.parent.document;
  // Parallax background on mouse move
  doc.addEventListener('mousemove', function(e){
    var x = (e.clientX / window.parent.innerWidth * 100).toFixed(1);
    var y = (e.clientY / window.parent.innerHeight * 100).toFixed(1);
    doc.documentElement.style.setProperty('--mx', x + '%');
    doc.documentElement.style.setProperty('--my', y + '%');
  });

  function apply3D(){
    var cards = doc.querySelectorAll('.kpi-card');
    cards.forEach(function(card){
      card.addEventListener('mousemove', function(e){
        var r = card.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width - 0.5;
        var y = (e.clientY - r.top) / r.height - 0.5;
        card.style.transform = 'translateY(-6px) scale(1.02) perspective(900px) rotateX('+(-y*14)+'deg) rotateY('+(x*14)+'deg)';
      });
      card.addEventListener('mouseleave', function(){
        card.style.transform = '';
      });
    });
  }

  function applyGlow(){
    var exps = doc.querySelectorAll('[data-testid="stExpander"]');
    exps.forEach(function(exp){
      exp.addEventListener('mouseenter', function(){
        exp.style.boxShadow = '0 4px 24px rgba(22,163,74,0.14),0 0 0 1px rgba(22,163,74,0.18)';
      });
      exp.addEventListener('mouseleave', function(){
        exp.style.boxShadow = '';
      });
    });
  }

  function applyRipple(){
    var btns = doc.querySelectorAll('.stButton > button');
    btns.forEach(function(btn){
      btn.addEventListener('click', function(e){
        var r = btn.getBoundingClientRect();
        var rpl = doc.createElement('span');
        var sz = Math.max(r.width, r.height);
        rpl.style.cssText = 'position:absolute;border-radius:50%;background:rgba(22,163,74,0.35);'+
          'width:'+sz+'px;height:'+sz+'px;'+
          'left:'+(e.clientX-r.left-sz/2)+'px;top:'+(e.clientY-r.top-sz/2)+'px;'+
          'transform:scale(0);opacity:1;pointer-events:none;'+
          'animation:rippleAnim 0.6s linear forwards';
        btn.appendChild(rpl);
        setTimeout(function(){rpl.remove()},650);
      });
    });
  }

  function init(){apply3D();applyGlow();applyRipple();}
  setTimeout(init, 800);
  var obs = new MutationObserver(function(){setTimeout(init,400);});
  obs.observe(doc.body, {childList:true, subtree:true});
})();
</script>
""", height=0, scrolling=False)

# ── Detección automática mobile ───────────────────────────────────────────
import streamlit.components.v1 as _components
_components.html("""
<script>
(function(){
    var w = window.innerWidth || document.documentElement.clientWidth;
    var p = new URLSearchParams(window.parent.location.search);
    var already = p.get('m');
    if(w <= 768 && already !== '1'){
        p.set('m','1'); window.parent.location.search = p.toString();
    } else if(w > 768 && already === '1'){
        p.delete('m'); window.parent.location.search = p.toString();
    }
})();
</script>
""", height=0, scrolling=False)

is_mobile = st.query_params.get("m", "0") == "1"

# helpers de layout responsivo
def _rcols(*desktop_weights):
    """En mobile devuelve una sola columna, en desktop las columnas pedidas."""
    if is_mobile:
        return st.columns([1])
    return st.columns(list(desktop_weights))

def _chart_h(desktop=420, mobile=280):
    return mobile if is_mobile else desktop

# ── Extracción de marca ───────────────────────────────────────────────────
_ALIAS = {
    "familia zuccardi":"Familia Zuccardi","filippo berio":"Filippo Berio",
    "ciudad del lago":"Ciudad Del Lago","pietro coricelli":"Pietro Coricelli",
    "cuisine & co":"Cousine & Co","cousine & co":"Cousine & Co",
    "dv catena":"DV Catena","la toscana":"La Toscana",
    "la española":"La Española","la riojana":"La Riojana",
    "del monte":"Del Monte","de cecco":"De Cecco",
    "zuccardi":"Familia Zuccardi","filippo":"Filippo Berio",
    "cuisine":"Cousine & Co","cousine":"Cousine & Co",
    "costaflores":"Costaflores","yancanello":"Yancanello",
    "carbonell":"Carbonell","colavita":"Colavita",
    "fritolim":"Fritolim","rastrilla":"Rastrilla",
    "cocinero":"Cocinero","oliovita":"Oliovita",
    "kirkland":"Kirkland","olitalia":"Olitalia",
    "cañuelas":"Cañuelas","casalta":"Casalta",
    "monini":"Monini","morixe":"Morixe",
    "nucete":"Nucete","cortijo":"Cortijo",
    "castell":"Castell","borges":"Borges",
    "ybarra":"Ybarra","natura":"Natura",
    "cecco":"De Cecco","vigil":"Vigil",
    "zuelo":"Zuelo","laur":"Laur",
    "zucco":"Zucco","lopez":"Lopez",
    "pisi":"Pisi","lira":"Lira",
    "check":"Check","best":"Best",
    "cook":"Cook","gallo":"Gallo",
    "carm":"Carm","carrefour":"Carrefour",
    "jumbo":"Jumbo","disco":"Disco",
    "vea":"Vea","coto":"Coto",
    "día":"Día","dia":"Día",
}
_ALIAS_SORTED = sorted(_ALIAS.items(), key=lambda x: -len(x[0]))
_NO_M = {
    "ml","cc","lt","lts","ltr","gr","grm","kg","g","l",
    "aceite","oliva","extra","virgen","virgen-extra","extra-virgen","extravirgen",
    "organico","clasico","clásico","suave","intenso","blend","picual","arbequina",
    "botella","lata","envase","pet","vidrio","aerosol","de","en","con","sin",
    "el","la","bot","x","y","e","o","a","premium","seleccion","selección",
    "natural","tradicional","especial","tacc","bío","bio","puro","pura",
}

def _marca(nombre: str, guardada) -> str:
    if guardada and guardada not in ("Otra",""):
        return guardada
    n = nombre.lower()
    for alias, canon in _ALIAS_SORTED:
        if alias in n:
            return canon
    for w in nombre.split():
        p = w.lower().strip(".,()-/&°")
        if len(p) >= 3 and p not in _NO_M and not re.search(r"\d", p):
            return w.strip(".,()-/&°")
    return "Otras"

# ── Carga de datos ────────────────────────────────────────────────────────
def _historial_mtime():
    db = DIRECTORIO / "precios.db"
    if db.exists():
        return db.stat().st_mtime
    p = DIRECTORIO / "historial_precios.json"
    return p.stat().st_mtime if p.exists() else 0

_URL_BASE_CADENA = {
    "Jumbo":       "https://www.jumbo.com.ar",
    "Disco":       "https://www.disco.com.ar",
    "Vea":         "https://www.vea.com.ar",
    "Coto":        "https://www.cotodigital.com.ar",
    "La Anonima":  "https://www.laanonima.com.ar",
    "La Anónima":  "https://www.laanonima.com.ar",
    "Chango Mas":  "https://www.masonline.com.ar",
    "Chango Más":  "https://www.masonline.com.ar",
}

def _build_url(superm: str, pid: str, nombre: str) -> str | None:
    nombre_enc = quote_plus(nombre)
    _base = _URL_BASE_CADENA.get(superm, "")
    if _base and pid.startswith("/"):
        return _base + pid
    elif superm in ("Día", "Dia"):
        _nfkd = unicodedata.normalize("NFKD", nombre.lower())
        _slug = re.sub(r"[^a-z0-9]+", "-", "".join(
            c for c in _nfkd if not unicodedata.combining(c))).strip("-")
        return f"https://diaonline.supermercadosdia.com.ar/{_slug}-{pid}/p"
    elif superm == "Carrefour":
        return f"https://www.carrefour.com.ar/busca?q={nombre_enc}"
    elif superm in ("Chango Mas", "Chango Más"):
        return f"https://www.masonline.com.ar/busca?q={nombre_enc}"
    elif superm in ("La Anonima", "La Anónima"):
        return f"https://www.laanonima.com.ar/busca/?q={nombre_enc}"
    return None


@st.cache_data(ttl=3600, hash_funcs={})
def cargar_datos(_mtime=None) -> pd.DataFrame:
    import sqlite3
    db = DIRECTORIO / "precios.db"
    rows = []

    if db.exists():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM precios ORDER BY fecha")
        registros = cur.fetchall()
        conn.close()
        for r in registros:
            fecha   = r["fecha"]
            ml      = r["ml"]
            precio  = r["precio"]
            gondola = r["precio_sin_dto"] or precio
            pl_g    = round(gondola / ml * 1000) if (ml and ml > 0) else None
            desc    = round((gondola - precio) / gondola * 100) if gondola > precio else 0
            marca_r = _marca(r["nombre"], r["marca"])
            pid     = r["producto_id"] or ""
            superm  = r["supermercado"]
            prod_url = _build_url(superm, pid, r["nombre"])
            rows.append({
                "Fecha":         fecha,
                "Cadena":        superm,
                "Marca_raw":     marca_r,
                "Marca":         categorizar(marca_r),
                "Producto":      r["nombre"],
                "SKU_canonico":  canonicalizar_sku(marca_r, r["nombre"], ml),
                "Tamaño_ml":     ml,
                "Gramaje":       bucket_gramaje(ml),
                "Precio":        int(round(gondola)),
                "Precio_litro":  int(round(pl_g)) if pl_g else None,
                "Precio_oferta": int(round(precio)),
                "Descuento_pct": desc,
                "En_oferta":     bool(r["en_oferta"]),
                "Producto_key":  pid or r["nombre"],
                "Producto_url":  prod_url,
            })
    else:
        path = DIRECTORIO / "historial_precios.json"
        if not path.exists():
            return pd.DataFrame()
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
        for sem in hist.get("semanas", []):
            fecha = sem["fecha"]
            for p in sem.get("productos", []):
                ml      = p.get("ml")
                precio  = p["precio"]
                gondola = p.get("precio_sin_dto") or precio
                pl_g    = round(gondola / ml * 1000) if (ml and ml > 0) else None
                desc    = round((gondola - precio) / gondola * 100) if gondola > precio else 0
                marca_r = _marca(p["nombre"], p.get("marca"))
                pid     = p.get("producto_id") or ""
                superm  = p["supermercado"]
                prod_url = _build_url(superm, pid, p["nombre"])
                rows.append({
                    "Fecha":         fecha,
                    "Cadena":        superm,
                    "Marca_raw":     marca_r,
                    "Marca":         categorizar(marca_r),
                    "Producto":      p["nombre"],
                    "SKU_canonico":  canonicalizar_sku(marca_r, p["nombre"], ml),
                    "Tamaño_ml":     ml,
                    "Gramaje":       bucket_gramaje(ml),
                    "Precio":        int(round(gondola)),
                    "Precio_litro":  int(round(pl_g)) if pl_g else None,
                    "Precio_oferta": int(round(precio)),
                    "Descuento_pct": desc,
                    "En_oferta":     bool(p.get("en_oferta", False)),
                    "Producto_key":  p.get("producto_id") or p["nombre"],
                    "Producto_url":  prod_url,
                })

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"])
        df["Semana_num"] = df["Fecha"].dt.isocalendar().week.astype(int)
        df["Periodo"] = df["Fecha"].apply(
            lambda d: f"Sem {d.isocalendar().week} · {d.strftime('%b %Y')}"
        )
    return df

df_full = cargar_datos(_mtime=_historial_mtime())
if df_full.empty:
    st.error("⚠️ Sin datos. Ejecutá primero: **python scraper.py**")
    st.stop()

# ── Helpers ───────────────────────────────────────────────────────────────
def cc(c): return COLORS_CADENAS.get(c, "#6B7280")
def cm(m): return COLORES_CAT.get(m, "#9CA3AF")

_BASE_CORE = dict(
    template="plotly_white",
    font=dict(family="Inter", size=13, color="#111827"),
    plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
    legend=dict(orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1, font=dict(size=12, color="#111827")),
)
BASE = {**_BASE_CORE, "margin": dict(l=10, r=10, t=40, b=10)}

def _pchart(fig, use_container_width=True, **kw):
    """Renderiza un gráfico Plotly forzando precios sin decimales en hover y ejes."""
    for tr in fig.data:
        t = getattr(tr, "type", "")
        if t in ("scatter", "scattergl") and not getattr(tr, "hovertemplate", None):
            tr.hovertemplate = "<b>%{fullData.name}</b><br>%{x}<br><b>$%{y:,.0f}</b><extra></extra>"
        elif t == "bar":
            orient = getattr(tr, "orientation", "v")
            if orient == "h" and not getattr(tr, "hovertemplate", None):
                tr.hovertemplate = "<b>%{y}</b><br><b>$%{x:,.0f}</b><extra></extra>"
            elif not getattr(tr, "hovertemplate", None):
                tr.hovertemplate = "<b>%{x}</b><br><b>$%{y:,.0f}</b><extra></extra>"
        elif t == "heatmap":
            if not getattr(tr, "hovertemplate", None):
                tr.hovertemplate = "%{y} · %{x}<br><b>$%{z:,.0f}</b><extra></extra>"
    fig.update_yaxes(tickformat=",.0f")
    fig.update_xaxes(tickformat=None)   # x puede ser texto/fecha, no tocar
    st.plotly_chart(fig, use_container_width=use_container_width, **kw)

orden_cats = ["La Toscana","Zuelo","Oliovita","Natura","Nucete","Cocinero","Lira",
              "Marca Propia","Otras"]

# ── SIDEBAR ─────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div class="sidebar-logo">🫒 <span class="accent">Aceite</span> Tracker</div>
    <div class="sidebar-sub">Monitor de precios · Argentina</div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="sidebar-sep">Navegación</div>', unsafe_allow_html=True)
    _page_sel = st.radio(
        "Navegación",
        ["📊  Resumen", "🏪  Por Cadena", "🏷️  Por Marca",
         "📈  Evolución", "🔖  Ofertas", "⚖️  Comparativa",
         "🎯  Mi Marca", "📦  Quiebres", "🔢  Tabla dinámica"],
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
    if st.button("🔄  Actualizar datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown('<div class="sidebar-sep">Otras categorías</div>', unsafe_allow_html=True)
    _comp_ui.html("""
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
<a href="https://aceitunaspricing-argentina.streamlit.app" target="_blank" class="csb">
  <div class="icon">🫒</div>
  <div class="txt">
    <span class="lbl">Ir a</span>
    <span class="nm">Aceitunas Tracker</span>
  </div>
  <span class="arr">↗</span>
</a>""", height=72, scrolling=False)

# ── Valores por defecto: siempre todos ───────────────────────────────────
cadenas_sel       = sorted(df_full["Cadena"].unique())
cats_disp         = [c for c in orden_cats if c in df_full["Marca"].unique()]
cats_sel          = cats_disp
buckets_con_datos = [e for e in GRAMAJE_BUCKETS if df_full["Gramaje"].eq(e).any()]
gram_sel          = buckets_con_datos
inflacion_mensual = 6.0

# ── Deflactor de inflación (fijo 6%) ─────────────────────────────────────
_semanas_ord = sorted(df_full["Fecha"].unique())
_n_sem_total = len(_semanas_ord)
_factor_sem  = inflacion_mensual / 100 / 4.33
_fecha_a_factor = {
    f: 1.0 / ((1 + _factor_sem) ** (_n_sem_total - 1 - i))
    for i, f in enumerate(_semanas_ord)
}
df_full["_deflactor"] = df_full["Fecha"].map(_fecha_a_factor)
df_full["Precio_real"]       = (df_full["Precio"]       * df_full["_deflactor"]).round(0).astype("Int64")
df_full["Precio_litro_real"] = (df_full["Precio_litro"] * df_full["_deflactor"]).round(0).astype("Int64")

# ── Filtro base ───────────────────────────────────────────────────────────
mask_base = (
    df_full["Periodo"].isin(periodos_sel) &
    df_full["Cadena"].isin(cadenas_sel)   &
    df_full["Marca"].isin(cats_sel)        &
    (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(gram_sel))
)

dff   = df_full[mask_base].copy()          # todos, precio siempre = góndola
df_of = df_full[mask_base & df_full["En_oferta"]].copy()  # solo ofertas

df_ult        = df_full[df_full["Fecha"] == df_full["Fecha"].max()].copy()
n_sem         = df_full["Periodo"].nunique()
fecha_max_str = df_full["Fecha"].max().strftime("%d/%m/%Y")

if dff.empty:
    st.warning("Sin datos con los filtros seleccionados.")
    st.stop()

# ── Header ──────────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="main-header">
  <div class="header-left">
    <div class="header-eyebrow">🫒 Monitor de Precios</div>
    <h1>Aceite de Oliva · Tracker</h1>
    <p>{fecha_max_str} &nbsp;·&nbsp; {len(df_ult):,} productos
       &nbsp;·&nbsp; {df_ult['Cadena'].nunique()} cadenas
       &nbsp;·&nbsp; {n_sem} semana{"s" if n_sem>1 else ""} acumulada{"s" if n_sem>1 else ""}</p>
  </div>
  <div class="header-right">
    <div class="header-badge">🫙 Aceite de Oliva</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── KPIs ──────────────────────────────────────────────────────────────────
precio_prom   = dff["Precio"].mean()
pl_prom       = dff["Precio_litro"].dropna().mean()
cadena_barata = (dff.dropna(subset=["Precio_litro"])
                    .groupby("Cadena")["Precio_litro"].mean().idxmin()
                 if not dff["Precio_litro"].dropna().empty else "—")
n_en_oferta  = len(df_of)
pct_oferta   = n_en_oferta / len(df_full[mask_base]) * 100 if len(df_full[mask_base]) > 0 else 0
desc_prom_v  = df_of["Descuento_pct"].mean() if not df_of.empty else 0
ahorro_prom  = (df_of["Precio"] - df_of["Precio_oferta"]).mean() if not df_of.empty else 0

c1,c2,c3,c4,c5,c6 = st.columns(6)
kpis = [
    ("blue",   "Productos relevados",    f"{dff['SKU_canonico'].nunique():,}", f"{dff['Cadena'].nunique()} cadenas"),
    ("green",  "Precio prom. góndola",f"${precio_prom:,.0f}",     "precio sin descuento"),
    ("orange", "Precio/litro prom.",  f"${pl_prom:,.0f}" if pl_prom else "—", "promedio por litro"),
    ("purple", "Cadena más barata",   cadena_barata,               "menor precio/litro"),
    ("red",    "Productos en oferta", f"{n_en_oferta:,}",          f"{pct_oferta:.0f}% del total"),
    ("teal",   "Ahorro prom. oferta", f"${ahorro_prom:,.0f}" if ahorro_prom > 0 else "—",
               f"dto. {desc_prom_v:.0f}%" if desc_prom_v > 0 else "sin datos"),
]
for col,(cls,label,val,sub) in zip([c1,c2,c3,c4,c5,c6], kpis):
    with col:
        st.markdown(f"""<div class="kpi-card {cls}">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value" style="font-size:{'1.2rem' if len(val)>9 else '1.7rem'}">{val}</div>
            <div class="kpi-sub">{sub}</div>
        </div>""", unsafe_allow_html=True)

st.markdown('<div style="margin-bottom:0.5rem"></div>', unsafe_allow_html=True)

# ── Función auxiliar: barra horizontal ───────────────────────────────────
def hbar(df_x, df_y, colores, textos, titulo_x, altura=320):
    vmax = max(df_x) if len(df_x) else 1
    fig  = go.Figure(go.Bar(
        x=df_x, y=df_y, orientation="h",
        marker_color=colores, text=textos,
        textposition="outside",
        textfont=dict(size=13, color="#111827"),
        cliponaxis=False,
    ))
    fig.update_layout(**_BASE_CORE, height=altura,
        margin=dict(l=10, r=220, t=40, b=10),
        xaxis=dict(title=titulo_x, tickprefix="$", tickformat=",",
                   tickfont=dict(size=12, color="#111827"),
                   range=[0, vmax * 1.4]),
        yaxis=dict(tickfont=dict(size=13, color="#111827")),
        showlegend=False,
    )
    return fig

def gram_filter(key, source=None):
    """Devuelve (dff_local, etiqueta) filtrado por gramaje con selectbox."""
    src = source if source is not None else dff
    opts = ["Todos los gramajes"] + [e for e in GRAMAJE_BUCKETS if src["Gramaje"].eq(e).any()]
    sel  = st.selectbox("📦 Gramaje", opts, key=key)
    out  = src if sel == "Todos los gramajes" else src[src["Gramaje"] == sel]
    return out, sel

# ══════════════════════════════════════════════════════════════════════════
# TAB 1 · RESUMEN
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "📊  Resumen":
    st.markdown('<div style="margin-top:1.8rem"></div>', unsafe_allow_html=True)

    # ── Insights compactos ────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("💡 Insights del mercado", expanded=True):
        def _insight_card(icon, titulo, valor, detalle, color="#0F3460"):
            st.markdown(f"""
            <div style="background:#FFFFFF;border-radius:12px;padding:0.9rem 1.1rem;
                        border-left:4px solid {color};
                        box-shadow:0 1px 6px rgba(0,0,0,0.07)">
              <div style="font-size:1.2rem;margin-bottom:0.2rem">{icon}</div>
              <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;
                          letter-spacing:0.5px;margin-bottom:0.12rem">{titulo}</div>
              <div style="font-size:1.05rem;font-weight:800;color:#111827;
                          line-height:1.2;margin-bottom:0.18rem">{valor}</div>
              <div style="font-size:0.71rem;color:#374151;line-height:1.4">{detalle}</div>
            </div>""", unsafe_allow_html=True)

        _ins = dff.copy()
        _ins_pl = _ins.dropna(subset=["Precio_litro"])
        _by_marca_ins = (_ins.groupby("Marca_raw").agg(
            precio_medio=("Precio","mean"), n=("Precio","count"),
        ).reset_index())
        _pl_s1i = (_ins_pl.groupby(["Marca_raw","SKU_canonico","Cadena"])["Precio_litro"].mean().reset_index())
        _pl_s2i = (_pl_s1i.groupby(["Marca_raw","SKU_canonico"])["Precio_litro"].mean().reset_index())
        _by_marca_pli = (_pl_s2i.groupby("Marca_raw")["Precio_litro"].mean().reset_index().rename(columns={"Precio_litro":"pl_medio"}))
        _by_marca_ins = _by_marca_ins.merge(_by_marca_pli, on="Marca_raw", how="left")
        _of_ins_r = (df_full[df_full["Cadena"].isin(cadenas_sel)]
                     .groupby("Marca_raw")["En_oferta"].apply(lambda d: d.mean()*100)
                     .reset_index(name="pct_oferta"))
        _by_marca_ins = _by_marca_ins.merge(_of_ins_r, on="Marca_raw", how="left")
        _cad_pl_i = (_ins_pl.groupby(["Cadena","SKU_canonico"])["Precio_litro"].mean().reset_index()
                     .groupby("Cadena")["Precio_litro"].mean().reset_index(name="pl_medio"))
        _cadena_barata_i = _cad_pl_i.sort_values("pl_medio").iloc[0] if not _cad_pl_i.empty else None
        _cadena_cara_i   = _cad_pl_i.sort_values("pl_medio").iloc[-1] if not _cad_pl_i.empty else None
        _marca_of_i = (_by_marca_ins[_by_marca_ins["Marca_raw"].isin(MARCAS_DESTACADAS)]
                       .dropna(subset=["pct_oferta"]).sort_values("pct_oferta", ascending=False))
        _sku_por_marca = (_ins.groupby("Marca_raw")["SKU_canonico"].nunique()
                          .reset_index(name="n_skus").sort_values("n_skus", ascending=False))
        _sku_cad2 = (_ins.groupby("Marca_raw")["Cadena"].nunique()
                     .reset_index(name="n_cad").sort_values("n_cad", ascending=False))
        _marca_mas_skus_i = _sku_por_marca.iloc[0] if not _sku_por_marca.empty else None
        _marca_mas_cad_i  = _sku_cad2.iloc[0]      if not _sku_cad2.empty else None

        st.markdown('<div class="chart-title">📦 Portfolio activo &nbsp;·&nbsp; 🏬 Cadenas</div>', unsafe_allow_html=True)
        _ri1a, _ri1b, _ri1c, _ri1d = st.columns(4, gap="medium")
        with _ri1a:
            if _marca_mas_skus_i is not None:
                _insight_card("📦","Marca con más SKUs activos", str(_marca_mas_skus_i["Marca_raw"])[:40],
                              f"{int(_marca_mas_skus_i['n_skus'])} SKUs distintos","#0F3460")
        with _ri1b:
            if _marca_mas_cad_i is not None:
                _insight_card("🌐","Marca con más presencia", str(_marca_mas_cad_i["Marca_raw"])[:40],
                              f"activa en {int(_marca_mas_cad_i['n_cad'])} cadenas","#7C3AED")
        with _ri1c:
            if _cadena_barata_i is not None:
                _insight_card("✅","Cadena más barata", _cadena_barata_i["Cadena"],
                              f"${_cadena_barata_i['pl_medio']:,.0f}/L promedio","#16A34A")
        with _ri1d:
            if _cadena_cara_i is not None:
                _insight_card("🏅","Cadena más cara", _cadena_cara_i["Cadena"],
                              f"${_cadena_cara_i['pl_medio']:,.0f}/L promedio","#7C3AED")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">🏷️ Marcas &nbsp;·&nbsp; 🔥 Ofertas</div>', unsafe_allow_html=True)
        _ri2a, _ri2b, _ri2c, _ri2d = st.columns(4, gap="medium")
        _g500 = (_ins_pl[_ins_pl["Gramaje"]=="500 ml"]
                 .groupby(["SKU_canonico","Cadena"])["Precio_litro"].mean().reset_index()
                 .groupby("SKU_canonico").agg(pl=("Precio_litro","mean"), n=("Cadena","count"))
                 .reset_index().sort_values("pl"))
        _b500_i = _g500.iloc[0]  if not _g500.empty else None
        _c500_i = _g500.iloc[-1] if not _g500.empty else None
        with _ri2a:
            if _b500_i is not None:
                _insight_card("🏆","Más barato 500 ml", _b500_i["SKU_canonico"][:40],
                              f"${_b500_i['pl']:,.0f}/L · {int(_b500_i['n'])} cadena(s)","#16A34A")
        with _ri2b:
            if _c500_i is not None:
                _insight_card("💎","Más caro 500 ml", _c500_i["SKU_canonico"][:40],
                              f"${_c500_i['pl']:,.0f}/L · {int(_c500_i['n'])} cadena(s)","#7C3AED")
        with _ri2c:
            if not _marca_of_i.empty:
                _mo_i = _marca_of_i.iloc[0]
                _insight_card("🔥","Marca con más descuentos", _mo_i["Marca_raw"],
                              f"{_mo_i['pct_oferta']:.0f}% de registros en oferta","#DC2626")
        with _ri2d:
            _cad_of_i = (df_full[df_full["Cadena"].isin(cadenas_sel)]
                         .groupby("Cadena")["En_oferta"].apply(lambda d: d.mean()*100)
                         .reset_index(name="pct").sort_values("pct", ascending=False))
            if not _cad_of_i.empty:
                _co_i = _cad_of_i.iloc[0]
                _insight_card("🏪","Cadena con más ofertas", _co_i["Cadena"],
                              f"{_co_i['pct']:.0f}% de sus productos en oferta","#B45309")

    # ── Notificaciones: cambios de precio semana a semana ─────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    _notif_fechas = sorted(df_full["Fecha"].unique())
    if len(_notif_fechas) >= 2:
        _fn_act = _notif_fechas[-1]
        # Buscar el scrape más cercano a 7 días atrás (semana a semana)
        _target_ant = pd.Timestamp(_fn_act) - pd.Timedelta(days=7)
        _fn_ant = min(_notif_fechas[:-1], key=lambda f: abs(pd.Timestamp(f) - _target_ant))
        _fn_ant_str = pd.Timestamp(_fn_ant).strftime("%d/%m/%Y")
        _fn_act_str = pd.Timestamp(_fn_act).strftime("%d/%m/%Y")

        _df_ant = df_full[df_full["Fecha"] == _fn_ant]
        _df_act = df_full[df_full["Fecha"] == _fn_act]
        _ant_tiene_pid = (_df_ant["Producto_key"] != _df_ant["Producto"]).any()
        _act_tiene_pid = (_df_act["Producto_key"] != _df_act["Producto"]).any()
        _KEY = ["Producto_key", "Cadena"] if (_ant_tiene_pid and _act_tiene_pid) else ["Producto", "Cadena"]
        _DISP_KEY = "Producto_key" if _KEY[0] == "Producto_key" else "Producto"
        _gond_ant = (_df_ant[~_df_ant["En_oferta"]]
                     [_KEY + ["Precio", "Marca_raw"]].drop_duplicates(subset=_KEY)
                     .rename(columns={"Precio": "p_ant"}))
        _act_extra = [c for c in ["Precio", "Marca_raw", "Producto", "Producto_url"] if c not in _KEY]
        _gond_act = (_df_act[~_df_act["En_oferta"]]
                     [_KEY + _act_extra].drop_duplicates(subset=_KEY)
                     .rename(columns={"Precio": "p_act"}))

        _cambios = (_gond_ant.merge(_gond_act, on=_KEY + ["Marca_raw"])
                    .assign(delta=lambda d: d["p_act"] - d["p_ant"],
                            delta_pct=lambda d: ((d["p_act"] / d["p_ant"]) - 1) * 100)
                    .query("delta != 0")
                    .sort_values("delta_pct"))

        _subas = _cambios[_cambios["delta"] > 0].sort_values("delta_pct", ascending=False)
        _bajas_all      = _cambios[_cambios["delta"] < 0].sort_values("delta_pct")
        _bajas          = _bajas_all[_bajas_all["delta_pct"] >= -15]
        _bajas_verificar = _bajas_all[_bajas_all["delta_pct"] < -15]

        _sin_of_ant = (df_full[(df_full["Fecha"] == _fn_ant) & (~df_full["En_oferta"])]
                       [_KEY].drop_duplicates())
        _of_extra = [c for c in ["Precio", "Precio_oferta", "Descuento_pct", "Producto", "Producto_url", "Marca_raw"] if c not in _KEY]
        _con_of_act = (df_full[(df_full["Fecha"] == _fn_act) & df_full["En_oferta"]]
                       .drop_duplicates(subset=_KEY)
                       [_KEY + _of_extra]
                       .rename(columns={"Precio": "p_gond", "Precio_oferta": "p_of",
                                        "Descuento_pct": "desc"}))
        _nuevas_of = _con_of_act.merge(_sin_of_ant, on=_KEY, how="inner")

        _total    = len(_cambios)
        _total_of = len(_con_of_act)
        _total_ver = len(_bajas_verificar)
        _hay_algo = _total > 0 or _total_of > 0

        _partes = []
        if _total > 0:
            _partes.append(f"{_total} cambio{'s' if _total != 1 else ''} de precio")
        if _total_of > 0:
            _partes.append(f"{_total_of} oferta{'s' if _total_of != 1 else ''} activa{'s' if _total_of != 1 else ''}")
        _label = ("🔔 " + " · ".join(_partes) + f" · {_fn_ant_str} → {_fn_act_str}"
                  if _hay_algo else
                  f"✅ Sin cambios de precio · {_fn_ant_str} → {_fn_act_str}")

        def _fila(sku, cadena, flecha, pct_str, p_de, p_a, color_flecha, url=""):
            _ver = (f'&nbsp;<a href="{url}" target="_blank" '
                    f'style="font-size:0.68rem;color:#3B82F6;font-weight:600">Ver →</a>'
                    if (isinstance(url, str) and url.startswith("http")) else "")
            return (
                f"<div style='padding:5px 0;border-bottom:1px solid #E5E7EB'>"
                f"<span style='font-size:0.82rem;color:#111827'>"
                f"<b style='word-break:break-word'>{sku}</b><br>"
                f"<span style='color:#6B7280'>{cadena}</span>&nbsp;&nbsp;"
                f"<span style='color:{color_flecha}'>{flecha} {pct_str}</span>"
                f"&nbsp;&nbsp;${p_de:,.0f} → <b>${p_a:,.0f}</b>"
                f"{_ver}"
                f"</span></div>"
            )

        def _titulo_col(emoji, texto, n, subtitulo=None):
            sub = subtitulo if subtitulo is not None else f"{_fn_ant_str} → {_fn_act_str}"
            st.markdown(
                f"<p style='font-size:0.95rem;font-weight:700;color:#111827;margin:0 0 4px 0'>"
                f"{emoji} {texto} ({n})</p>"
                f"<p style='font-size:0.75rem;color:#6B7280;margin:0 0 8px 0'>"
                f"{sub}</p>",
                unsafe_allow_html=True)

        with st.expander(_label, expanded=_hay_algo):
            if not _hay_algo:
                st.info("Ningún cambio detectado entre las últimas dos semanas.")
            else:
                _col_s, _col_b, _col_o, _col_dest = st.columns(4, gap="large")
                with _col_s:
                    _titulo_col("🔴", "Subas", len(_subas))
                    if _subas.empty:
                        st.markdown("<span style='color:#111827;font-size:0.82rem'>Ninguna suba.</span>", unsafe_allow_html=True)
                    for _, r in _subas.head(3).iterrows():
                        st.markdown(_fila(r["Producto"], r["Cadena"], "▲",
                                          f"{r['delta_pct']:+.1f}%",
                                          r["p_ant"], r["p_act"], "#DC2626"),
                                    unsafe_allow_html=True)
                with _col_b:
                    _titulo_col("🟢", "Bajas de góndola", len(_bajas))
                    if _bajas.empty:
                        st.markdown("<span style='color:#111827;font-size:0.82rem'>Ninguna baja confirmada.</span>", unsafe_allow_html=True)
                    for _, r in _bajas.head(3).iterrows():
                        st.markdown(_fila(r["Producto"], r["Cadena"], "▼",
                                          f"{r['delta_pct']:+.1f}%",
                                          r["p_ant"], r["p_act"], "#16A34A"),
                                    unsafe_allow_html=True)
                with _col_o:
                    _n_of_total = len(_con_of_act) + len(_bajas_verificar)
                    _titulo_col("🏷️", "Ofertas activas", _n_of_total, subtitulo="")
                    # Unificar y ordenar todo por descuento mayor a menor
                    _of_unif = []
                    for _, r in _con_of_act.iterrows():
                        _of_unif.append({
                            "Producto": r["Producto"], "Cadena": r["Cadena"],
                            "pct": float(r["desc"]), "pct_str": f"{r['desc']:.0f}% dto.",
                            "p_de": r["p_gond"], "p_a": r["p_of"],
                            "url": r.get("Producto_url", ""),
                        })
                    for _, r in _bajas_verificar.iterrows():
                        _of_unif.append({
                            "Producto": r["Producto"], "Cadena": r["Cadena"],
                            "pct": abs(float(r["delta_pct"])), "pct_str": f"{r['delta_pct']:+.1f}%",
                            "p_de": r["p_ant"], "p_a": r["p_act"],
                            "url": r.get("Producto_url", ""),
                        })
                    _of_unif.sort(key=lambda x: x["pct"], reverse=True)
                    if not _of_unif:
                        st.markdown("<span style='color:#111827;font-size:0.82rem'>Sin ofertas activas.</span>", unsafe_allow_html=True)
                    _of_html = "".join(
                        _fila(_oi["Producto"], _oi["Cadena"], "▼",
                              _oi["pct_str"], _oi["p_de"], _oi["p_a"],
                              "#B45309", _oi["url"])
                        for _oi in _of_unif
                    )
                    st.markdown(
                        f'<div style="max-height:420px;overflow-y:auto;padding-right:4px">{_of_html}</div>',
                        unsafe_allow_html=True,
                    )

                # ── Columna 4: ofertas Zuelo / La Toscana / Oliovita ──────────
                with _col_dest:
                    _MARCAS_DEST = {"Zuelo", "La Toscana", "Oliovita"}
                    _COLORES_DEST = {"Zuelo": "#0F3460", "La Toscana": "#B45309", "Oliovita": "#16A34A"}
                    _dest_of_rows = (_con_of_act[_con_of_act.get("Marca_raw", pd.Series(dtype=str))
                                                 .isin(_MARCAS_DEST)]
                                     .sort_values("desc", ascending=False)
                                     if "Marca_raw" in _con_of_act.columns
                                     else pd.DataFrame())
                    _n_dest = len(_dest_of_rows)
                    _titulo_col("⭐", "Zuelo · Toscana · Oliovita", _n_dest, subtitulo="")
                    if _dest_of_rows.empty:
                        st.markdown("<span style='color:#9CA3AF;font-size:0.82rem'>Sin ofertas activas para estas marcas.</span>",
                                    unsafe_allow_html=True)
                    else:
                        _dest_html_parts = []
                        for _, _dr in _dest_of_rows.iterrows():
                            _dr_marca = _dr.get("Marca_raw", "")
                            _dr_color = _COLORES_DEST.get(_dr_marca, "#3B82F6")
                            _dr_url_raw = _dr.get("Producto_url", "")
                            _dr_url = (_dr_url_raw if (isinstance(_dr_url_raw, str)
                                       and _dr_url_raw.startswith("http")) else "")
                            _dr_ver = (f'&nbsp;<a href="{_dr_url}" target="_blank" '
                                       f'style="font-size:0.68rem;color:#3B82F6;font-weight:600">Ver →</a>'
                                       if _dr_url else "")
                            _dr_prod = str(_dr.get("Producto", _dr.get("Producto_key", "")))
                            _dest_html_parts.append(
                                f"<div style='padding:5px 0 5px 8px;border-bottom:1px solid #E5E7EB;"
                                f"border-left:3px solid {_dr_color};margin-bottom:2px'>"
                                f"<span style='font-size:0.82rem;color:#111827'>"
                                f"<b style='word-break:break-word'>{_dr_prod[:60]}</b><br>"
                                f"<span style='color:#6B7280'>{_dr['Cadena']}</span>&nbsp;&nbsp;"
                                f"<span style='color:#DC2626'>▼ {_dr['desc']:.0f}% dto.</span>"
                                f"&nbsp;&nbsp;${_dr['p_gond']:,.0f} → <b>${_dr['p_of']:,.0f}</b>"
                                f"{_dr_ver}"
                                f"</span></div>"
                            )
                        st.markdown(
                            f'<div style="max-height:420px;overflow-y:auto;padding-right:4px">'
                            + "".join(_dest_html_parts) + "</div>",
                            unsafe_allow_html=True,
                        )


# ══════════════════════════════════════════════════════════════════════════
# TAB 2 · POR CADENA
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "🏪  Por Cadena":
    _fc2a, _fc2b, _ = st.columns([2, 2, 3])
    with _fc2a:
        dff2, _ = gram_filter("gram_tab2")
    with _fc2b:
        _cadenas2_opts = ["Todas las cadenas"] + sorted(dff2["Cadena"].unique().tolist())
        _cadena2_sel = st.selectbox("🏪 Cadena", _cadenas2_opts, key="cadena_tab2")
    if _cadena2_sel != "Todas las cadenas":
        dff2 = dff2[dff2["Cadena"] == _cadena2_sel].copy()

    with st.expander("Precio promedio & Productos por cadena", expanded=True):
        col_l, col_r = st.columns([3, 2], gap="large")

        with col_l:
            df_c = (dff2.groupby("Cadena")["Precio"].mean()
                        .reset_index().sort_values("Precio"))
            fig = hbar(
                df_x=df_c["Precio"].tolist(),
                df_y=df_c["Cadena"].tolist(),
                colores=[cc(c) for c in df_c["Cadena"]],
                textos=[f"${v:,.0f}" for v in df_c["Precio"]],
                titulo_x="Precio promedio ($)",
            )
            _pchart(fig)

        with col_r:
            df_pie = dff2.groupby("Cadena").size().reset_index(name="n")
            fig = go.Figure(go.Pie(
                labels=df_pie["Cadena"], values=df_pie["n"],
                marker_colors=[cc(c) for c in df_pie["Cadena"]],
                hole=0.55, textinfo="label+percent",
                textposition="outside",
                textfont=dict(size=12, color="#111827"),
            ))
            fig.update_layout(**_BASE_CORE, height=320,
                              margin=dict(l=10,r=10,t=40,b=40),
                              showlegend=False)
            _pchart(fig)

    # Box distribución por cadena — IQR estándar, escala enfocada
    with st.expander("Distribución de precios de góndola por cadena", expanded=True):
        st.markdown('<div class="chart-note">Caja = rango intercuartil (Q1–Q3) · Línea central = mediana · Bigotes = 1.5×IQR</div>',
                    unsafe_allow_html=True)
        _precios_box = dff2["Precio"].dropna()
        _p10 = float(_precios_box.quantile(0.10)) if not _precios_box.empty else 0
        _p90 = float(_precios_box.quantile(0.90)) if not _precios_box.empty else 30000
        fig = go.Figure()
        for cadena in sorted(dff2["Cadena"].unique()):
            sub = dff2[dff2["Cadena"]==cadena]["Precio"].dropna()
            if sub.empty:
                continue
            fig.add_trace(go.Box(
                y=sub, name=cadena, marker_color=cc(cadena),
                boxmean=True,
                line_width=2,
                marker=dict(size=4, opacity=0.4),
            ))
        fig.update_layout(**BASE, height=420,
                          yaxis=dict(title="Precio ($)", tickprefix="$", tickformat=",",
                                     tickfont=dict(size=12,color="#111827"),
                                     range=[max(0, _p10 * 0.7), _p90 * 1.25]),
                          xaxis=dict(tickfont=dict(size=13,color="#111827")),
                          showlegend=False)
        _pchart(fig)

    with st.expander("Precio de góndola promedio — Cadena × Marca", expanded=True):
        pivot = (dff2.groupby(["Marca","Cadena"])["Precio"]
                     .mean().round(0).unstack("Cadena"))
        pivot = pivot.reindex([c for c in orden_cats if c in pivot.index])
        if not pivot.empty:
            text_vals = [[f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                         for row in pivot.values]
            fig = go.Figure(go.Heatmap(
                z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
                colorscale="RdYlGn_r",
                text=text_vals, texttemplate="%{text}",
                textfont=dict(size=12, color="#111827"),
                colorbar=dict(title="$", tickprefix="$", tickformat=","),
            ))
            fig.update_layout(**BASE, height=max(320, len(pivot)*48+80),
                              xaxis=dict(tickfont=dict(size=13,color="#111827"), side="top"),
                              yaxis=dict(tickfont=dict(size=13,color="#111827")))
            _pchart(fig)

    with st.expander("Precio de góndola mínimo por cadena y marca", expanded=True):
        df_min = dff2.groupby(["Marca","Cadena"])["Precio"].min().reset_index()
        df_min["Marca"] = pd.Categorical(df_min["Marca"], categories=orden_cats, ordered=True)
        df_min = df_min.sort_values("Marca")
        fig = px.bar(df_min, x="Marca", y="Precio", color="Cadena",
                     barmode="group", color_discrete_map=COLORS_CADENAS,
                     labels={"Precio":"Precio mínimo ($)","Marca":""},
                     height=420, category_orders={"Marca": orden_cats})
        fig.update_layout(**BASE,
                          yaxis=dict(tickprefix="$", tickformat=",",
                                     tickfont=dict(size=12,color="#111827")),
                          xaxis=dict(tickfont=dict(size=13,color="#111827"), tickangle=-20))
        _pchart(fig)

# ══════════════════════════════════════════════════════════════════════════
# TAB 3 · POR MARCA
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "🏷️  Por Marca":
    # Filtros en la misma fila para que ambos gráficos arranquen al mismo nivel
    _fc3a, _fc3b, _ = st.columns([2, 2, 3])
    with _fc3a:
        dff3, _ = gram_filter("gram_tab3")
    with _fc3b:
        cadenas_pie3 = ["Todas las cadenas"] + sorted(dff3["Cadena"].unique().tolist())
        cadena_pie3  = st.selectbox("🏪 Cadena", cadenas_pie3, key="cadena_pie3")

    src_pie3 = dff3 if cadena_pie3 == "Todas las cadenas" else dff3[dff3["Cadena"]==cadena_pie3]

    with st.expander("Precio por marca & Distribución", expanded=True):
        col_l, col_r = st.columns([3, 2], gap="large")

        with col_l:
            df_m = (src_pie3.groupby("Marca")
                        .agg(p_prom=("Precio","mean"), n=("Precio","count"))
                        .reset_index().sort_values("p_prom"))
            fig = hbar(
                df_x=df_m["p_prom"].tolist(),
                df_y=df_m["Marca"].tolist(),
                colores=[cm(m) for m in df_m["Marca"]],
                textos=[f"${v:,.0f}  ({n})" for v,n in zip(df_m["p_prom"], df_m["n"])],
                titulo_x="Precio promedio ($)",
                altura=420,
            )
            _pchart(fig)

        with col_r:
            df_cnt = (src_pie3.groupby("Marca").size()
                              .reset_index(name="n").sort_values("n", ascending=False))
            fig = go.Figure(go.Pie(
                labels=df_cnt["Marca"], values=df_cnt["n"],
                marker_colors=[cm(m) for m in df_cnt["Marca"]],
                hole=0.5, textinfo="label+percent",
                textposition="outside",
                textfont=dict(size=12, color="#111827"),
            ))
            fig.update_layout(**_BASE_CORE, height=420,
                              margin=dict(l=10,r=10,t=40,b=40),
                              showlegend=False)
            _pchart(fig)

    # Heatmap presencia: marca × cadena (SKUs canónicos únicos)
    # Usamos df_full filtrado solo por cadena y marca (sin periodo ni gramaje)
    # para contar el catálogo real, independientemente de los filtros de semana y tamaño
    with st.expander("Presencia por marca y cadena — SKUs distintos", expanded=True):
        st.markdown('<div class="chart-note">Cantidad de SKUs distintos detectados por marca en cada cadena (sobre todo el historial)</div>',
                    unsafe_allow_html=True)
        _pres_src = df_full[
            df_full["Cadena"].isin(cadenas_sel) &
            df_full["Marca"].isin(cats_sel)
        ]
        pres_pivot = (_pres_src.groupby(["Marca","Cadena"])["SKU_canonico"]
                               .nunique().unstack("Cadena").fillna(0))
        pres_pivot = pres_pivot.reindex([c for c in orden_cats if c in pres_pivot.index])
        if not pres_pivot.empty:
            text_pres = [[str(int(v)) if v > 0 else "—" for v in row] for row in pres_pivot.values]
            fig = go.Figure(go.Heatmap(
                z=pres_pivot.values,
                x=pres_pivot.columns.tolist(),
                y=pres_pivot.index.tolist(),
                colorscale=[[0,"#F9FAFB"],[0.01,"#DBEAFE"],[0.5,"#3B82F6"],[1,"#1E3A8A"]],
                text=text_pres, texttemplate="%{text}",
                textfont=dict(size=13, color="#111827"),
                showscale=True,
                colorbar=dict(title="SKUs"),
            ))
            fig.update_layout(**BASE, height=max(280, len(pres_pivot)*44+80),
                              xaxis=dict(tickfont=dict(size=13,color="#111827"), side="top"),
                              yaxis=dict(tickfont=dict(size=13,color="#111827")))
            _pchart(fig)


# ══════════════════════════════════════════════════════════════════════════
# TAB 4 · EVOLUCIÓN
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "📈  Evolución":
    if n_sem < 2:
        st.info("📅 **Solo hay una semana cargada.** "
                "Ejecutá `python scraper.py` la semana que viene para ver la evolución.")

    _fc4a, _fc4b, _fc4c = st.columns([2, 3, 2])
    with _fc4a:
        dff4, _ = gram_filter("gram_tab4")
    with _fc4b:
        _skus_disp4 = sorted(dff4["SKU_canonico"].dropna().unique().tolist())
        _skus_sel4  = st.multiselect(
            "🔍 SKU", _skus_disp4, default=[],
            placeholder="Todos los SKUs", key="sku_filter_ev4",
        )
        if _skus_sel4:
            dff4 = dff4[dff4["SKU_canonico"].isin(_skus_sel4)]

    orden_per = sorted(df_full["Periodo"].unique(),
                       key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())

    _col_precio4 = "Precio"
    _lbl_precio4 = "Precio promedio góndola ($)"

    with st.expander("Evolución precio de góndola promedio por marca", expanded=True):
        cats_evol = [c for c in orden_cats if c not in ("Otras","Marca Propia")]
        df_ev_m = (dff4[dff4["Marca"].isin(cats_evol)]
                       .groupby(["Periodo","Marca"])[_col_precio4].mean()
                       .reset_index().rename(columns={_col_precio4:"_p"}))
        df_ev_m["Periodo"] = pd.Categorical(df_ev_m["Periodo"], categories=orden_per, ordered=True)
        fig = px.line(df_ev_m, x="Periodo", y="_p", color="Marca",
                      markers=True, color_discrete_map=COLORES_CAT,
                      labels={"_p": _lbl_precio4,"Periodo":""},
                      height=460)
        fig.update_traces(line=dict(width=2.5), marker=dict(size=8))
        fig.update_layout(**BASE,
                          yaxis=dict(tickprefix="$", tickformat=",",
                                     tickfont=dict(size=12,color="#111827")),
                          xaxis=dict(tickfont=dict(size=12,color="#111827")),
                          legend_title_text="Marca",
                          legend_title_font_color="#111827")
        _pchart(fig)

        # ── Análisis de composición por marca ────────────────────────────────
        if len(orden_per) >= 2:
            _per_ant4 = orden_per[-2]
            _per_act4 = orden_per[-1]
            _df_ant4  = dff4[(dff4["Periodo"]==_per_ant4) & (dff4["Marca"].isin(cats_evol))]
            _df_act4  = dff4[(dff4["Periodo"]==_per_act4) & (dff4["Marca"].isin(cats_evol))]
            _insights = []
            for _marca4 in cats_evol:
                _ma = _df_ant4[_df_ant4["Marca"]==_marca4]
                _mb = _df_act4[_df_act4["Marca"]==_marca4]
                if _ma.empty or _mb.empty:
                    continue
                _p_ant4 = _ma[_col_precio4].mean()
                _p_act4 = _mb[_col_precio4].mean()
                _delta_pct = (_p_act4 - _p_ant4) / _p_ant4 * 100
                if abs(_delta_pct) < 4:
                    continue
                _key4 = ["SKU_canonico", "Cadena"]
                _skus_a = set(map(tuple, _ma[_key4].drop_duplicates().values))
                _skus_b = set(map(tuple, _mb[_key4].drop_duplicates().values))
                _comunes = _skus_a & _skus_b
                _salieron = _skus_a - _skus_b
                _entraron = _skus_b - _skus_a
                _precio_comun_ant = _ma[_ma.set_index(_key4).index.isin(pd.MultiIndex.from_tuples(_comunes)) if _comunes else []
                                        ][_col_precio4].mean() if _comunes else None
                _precio_comun_act = _mb[_mb.set_index(_key4).index.isin(pd.MultiIndex.from_tuples(_comunes)) if _comunes else []
                                        ][_col_precio4].mean() if _comunes else None
                _precio_salieron = (_ma[_ma.apply(lambda r: (r["SKU_canonico"],r["Cadena"]) in _salieron, axis=1)][_col_precio4].mean() if _salieron else None)
                _precio_entraron = (_mb[_mb.apply(lambda r: (r["SKU_canonico"],r["Cadena"]) in _entraron, axis=1)][_col_precio4].mean() if _entraron else None)
                _cads_sal = set(_ma["Cadena"].unique()) - set(_mb["Cadena"].unique())
                _cads_ent = set(_mb["Cadena"].unique()) - set(_ma["Cadena"].unique())
                _arrow = "▲" if _delta_pct > 0 else "▼"
                _color = "#DC2626" if _delta_pct > 0 else "#16A34A"
                _partes_txt = []
                if _comunes and _precio_comun_ant and _precio_comun_act:
                    _dp = (_precio_comun_act - _precio_comun_ant) / _precio_comun_ant * 100
                    _partes_txt.append(f"SKUs en común {'subieron' if _dp>0 else 'bajaron'} {abs(_dp):.1f}%" if abs(_dp) >= 2 else "precios en común sin cambio significativo")
                if _salieron:
                    _vs = "por encima" if _precio_salieron and _precio_salieron > _p_ant4 else "por debajo"
                    _cad_sal_txt = f" (de {', '.join(_cads_sal)})" if _cads_sal else ""
                    _partes_txt.append(f"salieron {len(_salieron)} SKU{'s' if len(_salieron)>1 else ''}{_cad_sal_txt} a ${_precio_salieron:,.0f} ({_vs} del promedio)" if _precio_salieron else f"salieron {len(_salieron)} SKUs{_cad_sal_txt}")
                if _entraron:
                    _vs2 = "por encima" if _precio_entraron and _precio_entraron > _p_ant4 else "por debajo"
                    _cad_ent_txt = f" (de {', '.join(_cads_ent)})" if _cads_ent else ""
                    _partes_txt.append(f"entraron {len(_entraron)} SKU{'s' if len(_entraron)>1 else ''} nuevos{_cad_ent_txt} a ${_precio_entraron:,.0f} ({_vs2} del promedio)" if _precio_entraron else f"entraron {len(_entraron)} SKUs nuevos{_cad_ent_txt}")
                _explicacion = "; ".join(_partes_txt) + "." if _partes_txt else "cambio por variación en el mix de productos."
                _insights.append((_marca4, _delta_pct, _arrow, _color, _p_ant4, _p_act4, _explicacion))
            if _insights:
                st.markdown('<div class="chart-note" style="margin-top:0.5rem">🔍 Análisis de cambios entre períodos</div>', unsafe_allow_html=True)
                for _, (_mn, _dp, _arr, _clr, _pa, _pb, _exp) in enumerate(_insights):
                    _mc = COLORES_CAT.get(_mn, "#6B7280")
                    st.markdown(f"""<div style="display:flex;align-items:flex-start;gap:0.9rem;background:#FAFAFA;border-radius:10px;padding:0.7rem 1rem;margin-bottom:0.5rem;border-left:4px solid {_mc}">
                      <div style="min-width:90px;font-weight:700;color:{_mc};font-size:0.85rem">{_mn}</div>
                      <div style="min-width:120px;font-size:0.85rem"><span style="color:#6B7280">${_pa:,.0f}</span><span style="margin:0 4px;color:#9CA3AF">→</span><span style="font-weight:700;color:{_clr}">${_pb:,.0f}</span><span style="margin-left:6px;font-weight:700;color:{_clr}">{_arr}{abs(_dp):.1f}%</span></div>
                      <div style="font-size:0.78rem;color:#374151;flex:1">{_exp}</div></div>""", unsafe_allow_html=True)

    with st.expander("Evolución precio de góndola promedio por SKU", expanded=True):
        # Filtros propios de este gráfico
        _fsku_a, _fsku_b = st.columns([2, 3])
        with _fsku_a:
            _marcas_sku4_opts = sorted(dff4["Marca"].dropna().unique().tolist())
            _marca_sku4_sel   = st.multiselect("🏷️ Marca", _marcas_sku4_opts,
                                               default=[], placeholder="Todas las marcas",
                                               key="marca_sku_ev4")
        with _fsku_b:
            _src_sku4 = dff4 if not _marca_sku4_sel else dff4[dff4["Marca"].isin(_marca_sku4_sel)]
            _skus_sku4_opts = sorted(_src_sku4["SKU_canonico"].dropna().unique().tolist())
            _sku_sku4_sel   = st.multiselect("🔍 SKU", _skus_sku4_opts,
                                             default=[], placeholder="Seleccioná uno o más SKUs",
                                             key="sku_sku_ev4")

        if not _sku_sku4_sel and not _marca_sku4_sel:
            st.info("Seleccioná una marca o SKU para ver la evolución.")
        else:
            _src_chart4 = _src_sku4 if not _sku_sku4_sel else _src_sku4[_src_sku4["SKU_canonico"].isin(_sku_sku4_sel)]
            df_ev_sku = (_src_chart4
                            .groupby(["Periodo","SKU_canonico"])[_col_precio4].mean()
                            .reset_index().rename(columns={_col_precio4:"_p"}))
            df_ev_sku["Periodo"] = pd.Categorical(df_ev_sku["Periodo"], categories=orden_per, ordered=True)
            _n_skus = df_ev_sku["SKU_canonico"].nunique()
            fig_sku = px.line(df_ev_sku, x="Periodo", y="_p", color="SKU_canonico",
                              markers=True,
                              labels={"_p": _lbl_precio4, "Periodo": "", "SKU_canonico": "SKU"},
                              height=max(460, _n_skus * 22 + 200))
            fig_sku.update_traces(line=dict(width=2), marker=dict(size=7))
            fig_sku.update_layout(**BASE,
                                  yaxis=dict(tickprefix="$", tickformat=",",
                                             tickfont=dict(size=12, color="#111827")),
                                  xaxis=dict(tickfont=dict(size=12, color="#111827")),
                                  legend_title_text="SKU",
                                  legend_title_font_color="#111827",
                                  legend_font_size=11)
            _pchart(fig_sku)

        # ── Análisis de cambios por SKU entre períodos ───────────────────────
        if len(orden_per) >= 2 and (_sku_sku4_sel or _marca_sku4_sel):
            _per_ant4s = orden_per[-2]
            _per_act4s = orden_per[-1]
            _skus_a_analizar = _sku_sku4_sel if _sku_sku4_sel else sorted(_src_chart4["SKU_canonico"].dropna().unique().tolist())
            _insights_sku = []
            for _sku4 in _skus_a_analizar:
                _sa = _src_chart4[(_src_chart4["Periodo"]==_per_ant4s) & (_src_chart4["SKU_canonico"]==_sku4)]
                _sb = _src_chart4[(_src_chart4["Periodo"]==_per_act4s) & (_src_chart4["SKU_canonico"]==_sku4)]
                if _sa.empty and _sb.empty:
                    continue
                _pa_sku = _sa[_col_precio4].mean() if not _sa.empty else None
                _pb_sku = _sb[_col_precio4].mean() if not _sb.empty else None
                _dp_sku = ((_pb_sku - _pa_sku) / _pa_sku * 100) if (_pa_sku and _pb_sku) else None
                _cads_ant = set(_sa["Cadena"].unique())
                _cads_act = set(_sb["Cadena"].unique())
                _cads_sal2 = _cads_ant - _cads_act
                _cads_ent2 = _cads_act - _cads_ant
                _partes_sku = []
                if _cads_ent2:
                    _partes_sku.append(f"entró en {', '.join(sorted(_cads_ent2))}")
                if _cads_sal2:
                    _partes_sku.append(f"salió de {', '.join(sorted(_cads_sal2))}")
                if _dp_sku is not None and abs(_dp_sku) >= 2:
                    _partes_sku.append(f"precio promedio {'subió' if _dp_sku>0 else 'bajó'} {abs(_dp_sku):.1f}%")
                elif _dp_sku is not None:
                    _partes_sku.append("precio sin cambio significativo")
                _exp_sku = "; ".join(_partes_sku) + "." if _partes_sku else "sin cambios detectados."
                _arr_sku = ("▲" if (_dp_sku or 0) > 0 else "▼") if _dp_sku else "→"
                _clr_sku = "#DC2626" if (_dp_sku or 0) > 0 else ("#16A34A" if (_dp_sku or 0) < 0 else "#6B7280")
                _insights_sku.append((_sku4, _dp_sku, _arr_sku, _clr_sku, _pa_sku, _pb_sku, _exp_sku))
            if _insights_sku:
                st.markdown('<div class="chart-note" style="margin-top:0.5rem">🔍 Análisis de cambios entre períodos · por SKU</div>', unsafe_allow_html=True)
                for _, (_sn, _dp2, _arr2, _clr2, _pa2, _pb2, _exp2) in enumerate(_insights_sku):
                    _pa_txt = f"${_pa2:,.0f}" if _pa2 else "—"
                    _pb_txt = f"${_pb2:,.0f}" if _pb2 else "—"
                    _dp_txt = f"{_arr2}{abs(_dp2):.1f}%" if _dp2 else "nuevo"
                    st.markdown(f"""<div style="display:flex;align-items:flex-start;gap:0.9rem;background:#FAFAFA;border-radius:10px;padding:0.7rem 1rem;margin-bottom:0.5rem;border-left:4px solid {_clr2}">
                      <div style="min-width:140px;font-weight:700;color:#111827;font-size:0.82rem">{_sn}</div>
                      <div style="min-width:120px;font-size:0.85rem"><span style="color:#6B7280">{_pa_txt}</span><span style="margin:0 4px;color:#9CA3AF">→</span><span style="font-weight:700;color:{_clr2}">{_pb_txt}</span><span style="margin-left:6px;font-weight:700;color:{_clr2}">{_dp_txt}</span></div>
                      <div style="font-size:0.78rem;color:#374151;flex:1">{_exp2}</div></div>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════
# TAB 5 · OFERTAS
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "🔖  Ofertas":
    # ── Filtro de período propio para Ofertas ──────────────────────────────
    _todos_periodos_of = sorted(df_full["Periodo"].unique(),
                                key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())
    _fc5a, _fc5b, _ = st.columns([2, 2, 3])
    with _fc5a:
        _periodos_of_sel = st.multiselect(
            "📅 Semanas / Meses",
            _todos_periodos_of,
            default=_todos_periodos_of,
            key="periodos_of",
        )
    # Recalcular df_of con el filtro de período propio (además del filtro base)
    _mask_of = (
        df_full["Periodo"].isin(_periodos_of_sel if _periodos_of_sel else _todos_periodos_of) &
        df_full["Cadena"].isin(cadenas_sel) &
        df_full["Marca"].isin(cats_sel) &
        (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(gram_sel)) &
        df_full["En_oferta"]
    )
    df_of5 = df_full[_mask_of].copy()
    _orden_per_of5 = [p for p in _todos_periodos_of
                      if p in (_periodos_of_sel if _periodos_of_sel else _todos_periodos_of)]

    # df solo con la fecha más reciente — para los primeros 3 gráficos
    _fecha_hoy = df_full["Fecha"].max()
    _mask_of_hoy = _mask_of & (df_full["Fecha"] == _fecha_hoy)
    df_of5_hoy = df_full[_mask_of_hoy].copy()

    if df_of5.empty:
        st.info("🏷️ No hay productos en oferta con los filtros actuales.")
    else:
        with st.expander("📊 Resumen de ofertas de hoy", expanded=True):
            # KPIs — solo hoy
            _src_kpi = df_of5_hoy if not df_of5_hoy.empty else df_of5
            _lbl_hoy = str(_fecha_hoy)[:10] if not hasattr(_fecha_hoy, 'strftime') else _fecha_hoy.strftime("%d/%m/%Y")
            st.markdown(f"""
            <div style="background:linear-gradient(135deg,#7C2D12,#C2410C);border-radius:14px;
                        padding:1.2rem 2rem;margin-bottom:1.2rem;display:flex;gap:3rem;align-items:center">
              <div>
                <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                            letter-spacing:1px;color:rgba(255,255,255,0.6)">Ofertas hoy · {_lbl_hoy}</div>
                <div style="font-size:2rem;font-weight:800;color:#fff">{len(_src_kpi):,}</div>
              </div>
              <div>
                <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                            letter-spacing:1px;color:rgba(255,255,255,0.6)">Descuento promedio</div>
                <div style="font-size:2rem;font-weight:800;color:#fff">{_src_kpi["Descuento_pct"].mean():.0f}%</div>
              </div>
              <div>
                <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                            letter-spacing:1px;color:rgba(255,255,255,0.6)">Precio oferta prom.</div>
                <div style="font-size:2rem;font-weight:800;color:#fff">${_src_kpi["Precio_oferta"].mean():,.0f}</div>
              </div>
              <div>
                <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                            letter-spacing:1px;color:rgba(255,255,255,0.6)">Precio góndola prom.</div>
                <div style="font-size:2rem;font-weight:800;color:rgba(255,255,255,0.7)">${_src_kpi["Precio"].mean():,.0f}</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

            col_l, col_r = st.columns([1,1], gap="large")

            with col_l:
                df_desc_c = (_src_kpi.groupby("Cadena")["Descuento_pct"].mean()
                                  .reset_index().sort_values("Descuento_pct"))
                fig = go.Figure(go.Bar(
                    x=df_desc_c["Descuento_pct"], y=df_desc_c["Cadena"],
                    orientation="h",
                    marker_color=[cc(c) for c in df_desc_c["Cadena"]],
                    text=[f"{v:.0f}%" for v in df_desc_c["Descuento_pct"]],
                    textposition="outside",
                    textfont=dict(size=13, color="#111827"),
                    cliponaxis=False,
                ))
                vmax_d = df_desc_c["Descuento_pct"].max()
                fig.update_layout(**_BASE_CORE, height=320,
                                  margin=dict(l=10, r=120, t=40, b=10),
                                  xaxis=dict(title="Descuento %", ticksuffix="%",
                                             tickfont=dict(size=12,color="#111827"),
                                             range=[0, vmax_d * 1.4]),
                                  yaxis=dict(tickfont=dict(size=13,color="#111827")),
                                  showlegend=False)
                _pchart(fig)

            with col_r:
                df_of_cnt = _src_kpi.groupby("Cadena").size().reset_index(name="n")
                fig = go.Figure(go.Pie(
                    labels=df_of_cnt["Cadena"], values=df_of_cnt["n"],
                    marker_colors=[cc(c) for c in df_of_cnt["Cadena"]],
                    hole=0.55, textinfo="label+percent",
                    textposition="outside",
                    textfont=dict(size=12, color="#111827"),
                ))
                fig.update_layout(**_BASE_CORE, height=320,
                                  margin=dict(l=10,r=10,t=40,b=40),
                                  showlegend=False)
                _pchart(fig)

        # Góndola vs oferta por marca
        with st.expander("Precio góndola vs precio oferta por marca", expanded=True):
            st.markdown('<div class="chart-note">La diferencia entre las barras = ahorro de la oferta</div>',
                        unsafe_allow_html=True)
            _gvof_gram_opts = [e for e in GRAMAJE_BUCKETS if df_of5["Gramaje"].eq(e).any()]
            _gvof_gram_sel = st.selectbox("Gramaje", ["Todos"] + _gvof_gram_opts, key="gram_gvof")
            _df_gvof_src = df_of5 if _gvof_gram_sel == "Todos" else df_of5[df_of5["Gramaje"] == _gvof_gram_sel]
            df_gvof = (_df_gvof_src.groupby("Marca")
                            .agg(gondola=("Precio","mean"), oferta=("Precio_oferta","mean"))
                            .reset_index())
            df_gvof["Marca"] = pd.Categorical(df_gvof["Marca"], categories=orden_cats, ordered=True)
            df_gvof = df_gvof.sort_values("Marca")
            fig = go.Figure()
            fig.add_trace(go.Bar(name="Precio góndola", x=df_gvof["Marca"],
                                  y=df_gvof["gondola"], marker_color="#D1D5DB",
                                  text=[f"${v:,.0f}" for v in df_gvof["gondola"]],
                                  textposition="outside",
                                  textfont=dict(size=12,color="#374151")))
            fig.add_trace(go.Bar(name="Precio oferta", x=df_gvof["Marca"],
                                  y=df_gvof["oferta"],
                                  marker_color=[cm(m) for m in df_gvof["Marca"]],
                                  text=[f"${v:,.0f}" for v in df_gvof["oferta"]],
                                  textposition="outside",
                                  textfont=dict(size=12,color="#111827")))
            ymax = df_gvof["gondola"].max() if not df_gvof.empty else 1
            fig.update_layout(**BASE, barmode="overlay", height=420,
                              yaxis=dict(title="Precio ($)", tickprefix="$", tickformat=",",
                                         tickfont=dict(size=12,color="#111827"),
                                         range=[0, ymax * 1.25]),
                              xaxis=dict(tickfont=dict(size=13,color="#111827"), tickangle=-20))
            _pchart(fig)

        # Ofertas en el tiempo por marca y por cadena
        _n_per_of5 = df_of5["Periodo"].nunique()
        if _n_per_of5 >= 2:
            with st.expander("Ofertas en el tiempo por marca & cadena", expanded=True):
                col_ol, col_or = st.columns(2, gap="large")

                with col_ol:
                    df_of_t_m = (df_of5.groupby(["Periodo","Marca"]).size().reset_index(name="n"))
                    df_of_t_m["Periodo"] = pd.Categorical(df_of_t_m["Periodo"],
                                                            categories=_orden_per_of5, ordered=True)
                    fig = px.bar(df_of_t_m, x="Periodo", y="n", color="Marca",
                                 barmode="stack", color_discrete_map=COLORES_CAT,
                                 labels={"n":"Cantidad de ofertas","Periodo":""},
                                 height=380, category_orders={"Marca": orden_cats})
                    fig.update_layout(**BASE,
                                      xaxis=dict(tickfont=dict(size=12,color="#111827"), tickangle=-20),
                                      yaxis=dict(tickfont=dict(size=12,color="#111827")))
                    _pchart(fig)

                with col_or:
                    df_of_t_c = (df_of5.groupby(["Periodo","Cadena"]).size().reset_index(name="n"))
                    df_of_t_c["Periodo"] = pd.Categorical(df_of_t_c["Periodo"],
                                                            categories=_orden_per_of5, ordered=True)
                    fig = px.bar(df_of_t_c, x="Periodo", y="n", color="Cadena",
                                 barmode="stack", color_discrete_map=COLORS_CADENAS,
                                 labels={"n":"Cantidad de ofertas","Periodo":""},
                                 height=380)
                    fig.update_layout(**BASE,
                                      xaxis=dict(tickfont=dict(size=12,color="#111827"), tickangle=-20),
                                      yaxis=dict(tickfont=dict(size=12,color="#111827")))
                    _pchart(fig)

        # Top 20 mejores descuentos
        with st.expander("Top 20 · Mejores descuentos del período", expanded=True):
            df_top = (df_of5.sort_values("Descuento_pct", ascending=False)
                            .head(20)[["Cadena","Marca","Producto","Gramaje",
                                       "Precio","Precio_oferta","Descuento_pct"]]
                            .copy())
            df_top.columns = ["Cadena","Marca","Producto","Gramaje",
                              "Precio góndola ($)","Precio oferta ($)","Descuento %"]
            st.dataframe(df_top, height=400,
                column_config={
                    "Precio góndola ($)":st.column_config.NumberColumn(format="$%d"),
                    "Precio oferta ($)": st.column_config.NumberColumn(format="$%d"),
                    "Descuento %":       st.column_config.NumberColumn(format="%.0f%%"),
                },
                hide_index=True,
            )

        # ── Presencia de ofertas Oliovita & Zuelo × período ──────────────────
        with st.expander("Presencia de ofertas · Oliovita & Zuelo", expanded=True):
            st.markdown('<div class="chart-note">✓ = hubo oferta ese período · — = sin oferta</div>',
                        unsafe_allow_html=True)
            _MARCAS_OF2 = {"Oliovita", "Zuelo", "La Toscana"}
            _dest_periodos = _periodos_of_sel if _periodos_of_sel else _todos_periodos_of

            # Filtros locales: cadena y temporalidad
            _of2_fa, _of2_fb, _of2_fc = st.columns([2, 1.2, 3])
            _cadenas_of2_disp = sorted(
                df_full[df_full["Marca_raw"].isin(_MARCAS_OF2)]["Cadena"].unique()
            )
            with _of2_fa:
                _cadenas_of2_sel = st.multiselect(
                    "Cadena", _cadenas_of2_disp, default=_cadenas_of2_disp,
                    key="of2_cadenas", label_visibility="collapsed",
                    placeholder="Todas las cadenas",
                )
            with _of2_fb:
                _of2_gran = st.selectbox(
                    "Temporalidad", ["Semanal", "Mensual"],
                    key="of2_gran", label_visibility="collapsed",
                )
            _cadenas_of2_act = _cadenas_of2_sel if _cadenas_of2_sel else _cadenas_of2_disp

            _df_dest = df_full[
                df_full["Marca_raw"].isin(_MARCAS_OF2) &
                df_full["Periodo"].isin(_dest_periodos) &
                df_full["Cadena"].isin(_cadenas_of2_act) &
                (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(gram_sel))
            ].copy()

            if not _df_dest.empty:
                # Aplicar temporalidad
                if _of2_gran == "Mensual":
                    _df_dest["_col_per"] = pd.to_datetime(_df_dest["Fecha"]).dt.strftime("%b %Y")
                else:
                    _df_dest["_col_per"] = _df_dest["Periodo"]

                _skus_dest = sorted(_df_dest["SKU_canonico"].unique())
                # Orden de columnas
                if _of2_gran == "Mensual":
                    _pers_dest_ord = list(dict.fromkeys(
                        pd.to_datetime(_df_dest["Fecha"]).dt.to_period("M")
                        .sort_values().astype(str)
                        .map(lambda p: pd.Period(p).strftime("%b %Y"))
                    ))
                else:
                    _pers_dest_ord = [p for p in _orden_per_of5 if p in set(_dest_periodos)]

                # Set de ofertas con la columna de período elegida
                _of_mask = (
                    df_full["En_oferta"] &
                    df_full["Marca_raw"].isin(_MARCAS_OF2) &
                    df_full["Periodo"].isin(_dest_periodos) &
                    df_full["Cadena"].isin(_cadenas_of2_act) &
                    (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(gram_sel))
                )
                _df_of_mask = df_full[_of_mask].copy()
                if _of2_gran == "Mensual":
                    _df_of_mask["_col_per"] = pd.to_datetime(_df_of_mask["Fecha"]).dt.strftime("%b %Y")
                else:
                    _df_of_mask["_col_per"] = _df_of_mask["Periodo"]
                _of_set = set(zip(_df_of_mask["SKU_canonico"], _df_of_mask["_col_per"]))

                # Construir tabla de texto ✓ / —
                _hmap_rows = []
                for _sk in _skus_dest:
                    _row = {"SKU": _sk}
                    for _pe in _pers_dest_ord:
                        _row[_pe] = "✓" if (_sk, _pe) in _of_set else "—"
                    _hmap_rows.append(_row)
                _hmap_df = pd.DataFrame(_hmap_rows).set_index("SKU")
                _hmap_num = _hmap_df.map(lambda x: 1.0 if x == "✓" else 0.0)

                # Celdas compactas: alto fijo pequeño por fila
                _cell_h   = 24          # px por fila
                _header_h = 50          # px para el eje X
                _oh_h = max(120, len(_skus_dest) * _cell_h + _header_h + 20)

                fig_oh = go.Figure(go.Heatmap(
                    z=_hmap_num.values,
                    x=_pers_dest_ord,
                    y=_hmap_num.index.tolist(),
                    text=_hmap_df.values,
                    texttemplate="%{text}",
                    colorscale=[[0, "#F1F5F9"], [1, "#15803D"]],
                    zmin=0, zmax=1,
                    showscale=False,
                    xgap=2, ygap=2,
                    textfont=dict(size=11, color="#111827"),
                ))
                fig_oh.update_layout(
                    **_BASE_CORE,
                    height=_oh_h,
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(tickfont=dict(size=10, color="#374151"), tickangle=-30,
                               side="top"),
                    yaxis=dict(tickfont=dict(size=10, color="#374151"), autorange="reversed"),
                )
                _oh_col, _ = st.columns([1, 2])
                with _oh_col:
                    _pchart(fig_oh)
            else:
                st.info("No hay SKUs de Oliovita o Zuelo con los filtros seleccionados.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 3 (continuación) · DETALLE POR MARCA
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "🏷️  Por Marca":
    with st.expander("🔍 Detalle de marca", expanded=True):
        st.markdown("<hr style='border:none;border-top:2px solid #E5E7EB;margin:1.5rem 0 1rem'>",
                    unsafe_allow_html=True)
        marcas_raw_disp = sorted(dff["Marca_raw"].unique())
        _fc7a, _fc7b, _ = st.columns([2, 3, 2])
        with _fc7a:
            marca_sel7 = st.selectbox("🏷️ Elegí una marca", marcas_raw_disp, key="marca_info")
        with _fc7b:
            skus_marca7 = ["Todos los SKUs"] + sorted(
                dff[dff["Marca_raw"] == marca_sel7]["SKU_canonico"].unique().tolist()
            )
            sku_sel7 = st.selectbox("📦 SKU", skus_marca7, key="sku_info")

        df7 = dff[dff["Marca_raw"] == marca_sel7].copy()
        # Si se seleccionó un SKU específico, filtrar para los KPIs y gráficos de detalle
        df7_sku_filter = df7 if sku_sel7 == "Todos los SKUs" else df7[df7["SKU_canonico"] == sku_sel7]

        if df7.empty:
            st.info("Sin datos para esta marca con los filtros actuales.")
        else:
            # KPIs de la marca (o del SKU seleccionado)
            _src_kpi = df7_sku_filter
            _p_min = _src_kpi["Precio"].min()
            _p_max = _src_kpi["Precio"].max()
            _p_avg = _src_kpi["Precio"].mean()
            _cadenas7 = _src_kpi["Cadena"].nunique()
            _skus7    = _src_kpi["SKU_canonico"].nunique()
            _en_of7   = _src_kpi["En_oferta"].sum()

            k1,k2,k3,k4,k5,k6 = st.columns(6)
            for col,(cls,lab,val,sub) in zip([k1,k2,k3,k4,k5,k6],[
                ("green",  "Precio mínimo",   f"${_p_min:,.0f}", "góndola"),
                ("orange", "Precio promedio", f"${_p_avg:,.0f}", "góndola"),
                ("red",    "Precio máximo",   f"${_p_max:,.0f}", "góndola"),
                ("purple", "Cadenas",          str(_cadenas7),   "donde está presente"),
                ("",       "SKUs distintos",   str(_skus7),      "canónicos"),
                ("teal",   "En oferta",        str(int(_en_of7)), "registros con descuento"),
            ]):
                with col:
                    st.markdown(f"""<div class="kpi-card {cls}">
                        <div class="kpi-label">{lab}</div>
                        <div class="kpi-value" style="font-size:{'1.2rem' if len(val)>9 else '1.7rem'}">{val}</div>
                        <div class="kpi-sub">{sub}</div>
                    </div>""", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)
            col_a, col_b_7 = st.columns([3, 2], gap="large")

            with col_a:
                # Precio promedio por SKU canónico
                st.markdown('<div class="chart-title">Precio de góndola por SKU</div>',
                            unsafe_allow_html=True)
                df7_sku = (df7.groupby("SKU_canonico")
                              .agg(p_avg=("Precio","mean"),
                                   cadenas=("Cadena","nunique"),
                                   en_of=("En_oferta","sum"))
                              .reset_index().sort_values("p_avg"))
                fig = go.Figure(go.Bar(
                    x=df7_sku["p_avg"], y=df7_sku["SKU_canonico"], orientation="h",
                    marker_color="#2E86AB",
                    text=[f"${v:,.0f}  · {int(c)} cadena{'s' if c!=1 else ''}"
                          for v,c in zip(df7_sku["p_avg"], df7_sku["cadenas"])],
                    textposition="outside",
                    textfont=dict(size=12, color="#111827"),
                    cliponaxis=False,
                ))
                vmax7 = df7_sku["p_avg"].max() if not df7_sku.empty else 1
                fig.update_layout(**_BASE_CORE,
                                  height=max(300, len(df7_sku)*38+60),
                                  margin=dict(l=10, r=260, t=40, b=10),
                                  xaxis=dict(title="Precio promedio ($)", tickprefix="$",
                                             tickformat=",", range=[0, vmax7*1.4]),
                                  yaxis=dict(tickfont=dict(size=11, color="#111827")),
                                  showlegend=False)
                _pchart(fig)

            with col_b_7:
                # Precio por cadena (box) — usa el filtro de SKU si está activo
                st.markdown('<div class="chart-title">Precio por cadena</div>',
                            unsafe_allow_html=True)
                fig = go.Figure()
                for cad in sorted(df7_sku_filter["Cadena"].unique()):
                    sub = df7_sku_filter[df7_sku_filter["Cadena"]==cad]["Precio"]
                    fig.add_trace(go.Box(y=sub, name=cad, marker_color=cc(cad),
                                          boxmean=True, line_width=2))
                fig.update_layout(**BASE, height=400,
                                  yaxis=dict(title="Precio ($)", tickprefix="$", tickformat=",",
                                             tickfont=dict(size=12,color="#111827")),
                                  xaxis=dict(tickfont=dict(size=12,color="#111827")),
                                  showlegend=False)
                _pchart(fig)

            # ── Mapa semanal de precios por SKU seleccionado ─────────────────────
            if sku_sel7 != "Todos los SKUs":
                st.markdown(f'<div class="chart-title">Mapa semanal de precios — {sku_sel7}</div>',
                            unsafe_allow_html=True)
                st.markdown('<div class="chart-note">Precio de góndola promedio por semana y cadena. Celda vacía = sin datos ese período</div>',
                            unsafe_allow_html=True)
                orden_per7h = sorted(df7_sku_filter["Periodo"].unique(),
                                     key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())
                hmap_df = (df7_sku_filter
                           .groupby(["Periodo","Cadena"])["Precio"].mean()
                           .reset_index())
                hmap_piv = hmap_df.pivot(index="Cadena", columns="Periodo", values="Precio")
                # Ordenar columnas cronológicamente
                hmap_piv = hmap_piv.reindex(columns=[c for c in orden_per7h if c in hmap_piv.columns])
                if not hmap_piv.empty:
                    # Texto de cada celda
                    text_hmap = [
                        [f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                        for row in hmap_piv.values
                    ]
                    fig = go.Figure(go.Heatmap(
                        z=hmap_piv.values,
                        x=hmap_piv.columns.tolist(),
                        y=hmap_piv.index.tolist(),
                        colorscale=[[0,"#EFF6FF"],[0.5,"#3B82F6"],[1,"#1E3A8A"]],
                        text=text_hmap, texttemplate="%{text}",
                        textfont=dict(size=12, color="#111827"),
                        showscale=True,
                        colorbar=dict(title="Precio ($)", tickprefix="$", tickformat=","),
                        zsmooth=False,
                    ))
                    fig.update_layout(**BASE,
                                      height=max(200, len(hmap_piv)*50+80),
                                      xaxis=dict(tickfont=dict(size=12,color="#111827"),
                                                 side="bottom", tickangle=-20),
                                      yaxis=dict(tickfont=dict(size=12,color="#111827")))
                    _pchart(fig)

            # Tabla detallada
            st.markdown('<div class="chart-title">Detalle completo de registros</div>',
                        unsafe_allow_html=True)
            df7_show = (df7_sku_filter[["Periodo","Cadena","SKU_canonico","Gramaje",
                                        "Precio","Precio_oferta","Descuento_pct","En_oferta"]]
                        .sort_values(["Periodo","Cadena","Precio"]).copy())
            df7_show.columns = ["Semana","Cadena","SKU","Gramaje",
                                 "Precio góndola ($)","Precio oferta ($)","Descuento %","En oferta"]
            st.dataframe(df7_show, height=400,
                column_config={
                    "Precio góndola ($)": st.column_config.NumberColumn(format="$%d"),
                    "Precio oferta ($)":  st.column_config.NumberColumn(format="$%d"),
                    "Descuento %":        st.column_config.NumberColumn(format="%.0f%%"),
                    "En oferta":          st.column_config.CheckboxColumn(),
                },
                hide_index=True,
            )

            # Evolución por SKU canónico si hay varios períodos
            if df7["Periodo"].nunique() > 1:
                st.markdown('<div class="chart-title">Evolución de precio por SKU</div>',
                            unsafe_allow_html=True)
                orden_per7 = sorted(df7["Periodo"].unique(),
                                    key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())
                _df_ev_src = df7_sku_filter
                df7_ev = (_df_ev_src.groupby(["Periodo","SKU_canonico"])["Precio"].mean().reset_index())
                df7_ev["Periodo"] = pd.Categorical(df7_ev["Periodo"],
                                                    categories=orden_per7, ordered=True)
                fig = px.line(df7_ev, x="Periodo", y="Precio", color="SKU_canonico",
                              markers=True, height=420,
                              labels={"Precio":"Precio promedio ($)","Periodo":"","SKU_canonico":"SKU"})
                fig.update_traces(line=dict(width=2.5), marker=dict(size=8))
                fig.update_layout(**BASE,
                                  yaxis=dict(tickprefix="$", tickformat=",",
                                             tickfont=dict(size=12,color="#111827")),
                                  xaxis=dict(tickfont=dict(size=12,color="#111827")))
                _pchart(fig)

# ══════════════════════════════════════════════════════════════════════════
# TAB 6 · COMPARATIVA DE SKUs
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "⚖️  Comparativa":
    st.markdown('<div class="chart-note">Seleccioná dos marcas y luego un SKU de cada una para comparar su precio de góndola en el tiempo</div>',
                unsafe_allow_html=True)

    marcas_comp = sorted(dff["Marca_raw"].unique())
    col_m1, col_m2 = st.columns(2, gap="large")

    with col_m1:
        st.markdown("**Marca 1**")
        marca_c1 = st.selectbox("Marca 1", marcas_comp, key="comp_marca1",
                                 label_visibility="collapsed")
        skus_c1 = sorted(dff[dff["Marca_raw"]==marca_c1]["SKU_canonico"].unique())
        sku_c1  = st.selectbox("SKU 1", skus_c1, key="comp_sku1",
                                label_visibility="collapsed")

    with col_m2:
        st.markdown("**Marca 2**")
        default_m2 = marcas_comp[1] if len(marcas_comp) > 1 else marcas_comp[0]
        idx_m2 = marcas_comp.index(default_m2)
        marca_c2 = st.selectbox("Marca 2", marcas_comp, index=idx_m2, key="comp_marca2",
                                  label_visibility="collapsed")
        skus_c2 = sorted(dff[dff["Marca_raw"]==marca_c2]["SKU_canonico"].unique())
        sku_c2  = st.selectbox("SKU 2", skus_c2, key="comp_sku2",
                                label_visibility="collapsed")

    # Datos de evolución para cada SKU canónico
    orden_per8 = sorted(dff["Periodo"].unique(),
                        key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())

    def sku_evol(sku_name, label):
        df_s = (dff[dff["SKU_canonico"]==sku_name]
                    .groupby("Periodo")["Precio"].mean().reset_index())
        df_s["Periodo"] = pd.Categorical(df_s["Periodo"], categories=orden_per8, ordered=True)
        df_s["SKU"] = label
        return df_s

    # También calcular si hubo oferta por período para cada SKU
    def sku_oferta_por_periodo(sku_name):
        return set(
            dff[(dff["SKU_canonico"]==sku_name) & dff["En_oferta"]]["Periodo"].unique()
        )

    lbl1 = sku_c1
    lbl2 = sku_c2
    df_comp = pd.concat([sku_evol(sku_c1, lbl1), sku_evol(sku_c2, lbl2)], ignore_index=True)
    _of_pers1 = sku_oferta_por_periodo(sku_c1)
    _of_pers2 = sku_oferta_por_periodo(sku_c2)

    if df_comp.empty:
        st.info("No hay datos de evolución para los SKUs seleccionados.")
    else:
        color_map = {lbl1: "#2E86AB", lbl2: "#C73E1D"}

        # Agregar marcadores de oferta sobre el gráfico de líneas
        df_ev1 = sku_evol(sku_c1, lbl1)
        df_ev2 = sku_evol(sku_c2, lbl2)
        df_ev1_of = df_ev1[df_ev1["Periodo"].isin(_of_pers1)]
        df_ev2_of = df_ev2[df_ev2["Periodo"].isin(_of_pers2)]

        fig = px.line(df_comp, x="Periodo", y="Precio", color="SKU",
                      markers=True, color_discrete_map=color_map,
                      labels={"Precio":"Precio góndola ($)","Periodo":""},
                      height=420)
        fig.update_traces(line=dict(width=3), marker=dict(size=8))

        # Marcadores estrella donde hubo oferta
        if not df_ev1_of.empty:
            fig.add_trace(go.Scatter(
                x=df_ev1_of["Periodo"], y=df_ev1_of["Precio"],
                mode="markers", name=f"{lbl1} · en oferta",
                marker=dict(symbol="star", size=16, color="#2E86AB",
                            line=dict(color="#fff", width=1.5)),
                showlegend=True,
            ))
        if not df_ev2_of.empty:
            fig.add_trace(go.Scatter(
                x=df_ev2_of["Periodo"], y=df_ev2_of["Precio"],
                mode="markers", name=f"{lbl2} · en oferta",
                marker=dict(symbol="star", size=16, color="#C73E1D",
                            line=dict(color="#fff", width=1.5)),
                showlegend=True,
            ))

        fig.update_layout(**BASE,
                          yaxis=dict(tickprefix="$", tickformat=",",
                                     tickfont=dict(size=12,color="#111827")),
                          xaxis=dict(tickfont=dict(size=12,color="#111827")))
        _pchart(fig)

        # Mini tabla: ¿hubo oferta ese período?
        if orden_per8:
            with st.expander("Semanas en oferta", expanded=True):
                _of_rows = []
                for _pe in orden_per8:
                    _of_rows.append({
                        "Período": _pe,
                        lbl1[:35]: "✓" if _pe in _of_pers1 else "—",
                        lbl2[:35]: "✓" if _pe in _of_pers2 else "—",
                    })
                _of_tbl = pd.DataFrame(_of_rows)
                st.dataframe(_of_tbl,
                             height=min(400, len(orden_per8)*38+60),
                             hide_index=True)

        # Precio por cadena × período — heatmap para cada SKU
        with st.expander("Precio por cadena y período", expanded=True):
            st.markdown('<div class="chart-note">Precio promedio de góndola por cadena en cada semana/mes</div>',
                        unsafe_allow_html=True)

            def _cad_per_heatmap(sku_name, label, color_hi):
                """Heatmap cadena × período para un SKU dado."""
                _df_cp = (dff[dff["SKU_canonico"] == sku_name]
                          .groupby(["Cadena", "Periodo"])["Precio"].mean()
                          .round(0).unstack("Periodo"))
                _df_cp = _df_cp.reindex(columns=[p for p in orden_per8 if p in _df_cp.columns])
                if _df_cp.empty:
                    st.info(f"Sin datos para {label[:40]}")
                    return
                _txt_cp = [[f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                           for row in _df_cp.values]
                _vmin = float(_df_cp.min().min()) if not _df_cp.empty else 0
                _vmax = float(_df_cp.max().max()) if not _df_cp.empty else 1
                _fig_cp = go.Figure(go.Heatmap(
                    z=_df_cp.values,
                    x=_df_cp.columns.tolist(),
                    y=_df_cp.index.tolist(),
                    colorscale=[[0, "#D1FAE5"], [0.5, "#34D399"], [1, color_hi]],
                    zmin=_vmin, zmax=_vmax,
                    text=_txt_cp, texttemplate="%{text}",
                    textfont=dict(size=12, color="#111827"),
                    showscale=False,
                ))
                _fig_cp.update_layout(
                    **_BASE_CORE,
                    height=max(220, len(_df_cp) * 44 + 100),
                    margin=dict(l=10, r=10, t=50, b=10),
                    title=dict(text=label[:50], font=dict(size=12, color="#374151"), x=0.01),
                    xaxis=dict(tickfont=dict(size=11, color="#111827"), side="top",
                               tickangle=-25),
                    yaxis=dict(tickfont=dict(size=12, color="#111827")),
                )
                _pchart(_fig_cp)

            _col_cp1, _col_cp2 = st.columns(2, gap="large")
            with _col_cp1:
                _cad_per_heatmap(sku_c1, lbl1, "#065F46")
            with _col_cp2:
                _cad_per_heatmap(sku_c2, lbl2, "#7C1D2D")

        # Tabla comparativa por cadena en el último período disponible
        with st.expander("Precio por cadena · último período disponible", expanded=True):
            ult_per8 = orden_per8[-1] if orden_per8 else None
            if ult_per8:
                df_cmp_tbl = dff[(dff["Periodo"]==ult_per8) &
                                  (dff["SKU_canonico"].isin([sku_c1, sku_c2]))]
                df_cmp_tbl = df_cmp_tbl[["Cadena","SKU_canonico","Gramaje","Precio","En_oferta"]].copy()
                df_cmp_tbl.columns = ["Cadena","SKU","Gramaje","Precio góndola ($)","En oferta"]
                st.dataframe(df_cmp_tbl.sort_values(["SKU","Cadena"]),
                    height=300,
                    column_config={
                        "Precio góndola ($)":st.column_config.NumberColumn(format="$%d"),
                        "En oferta":         st.column_config.CheckboxColumn(),
                    },
                    hide_index=True,
                )

        # Diferencia de precio entre los dos SKUs por período
        if df_comp["Periodo"].nunique() > 1:
            with st.expander("Diferencia de precio entre SKUs por período", expanded=True):
                st.markdown('<div class="chart-note">Verde = SKU 1 más barato · Rojo = SKU 2 más barato</div>',
                            unsafe_allow_html=True)
                piv_comp = df_comp.pivot(index="Periodo", columns="SKU", values="Precio")
                if lbl1 in piv_comp.columns and lbl2 in piv_comp.columns:
                    piv_comp["Diferencia"] = piv_comp[lbl1] - piv_comp[lbl2]
                    piv_comp = piv_comp.dropna(subset=["Diferencia"]).reset_index()
                    fig = go.Figure(go.Bar(
                        x=piv_comp["Periodo"], y=piv_comp["Diferencia"],
                        marker_color=["#00B050" if v <= 0 else "#EF4444"
                                      for v in piv_comp["Diferencia"]],
                        text=[f"${v:+,.0f}" for v in piv_comp["Diferencia"]],
                        textposition="outside",
                        textfont=dict(size=12, color="#111827"),
                        cliponaxis=False,
                    ))
                    fig.update_layout(**_BASE_CORE, height=320,
                                      margin=dict(l=10,r=10,t=60,b=40),
                                      xaxis=dict(tickfont=dict(size=12,color="#111827"), tickangle=-20),
                                      yaxis=dict(title="Diferencia ($)", tickprefix="$",
                                                 tickformat=",", tickfont=dict(size=12,color="#111827")),
                                      showlegend=False,
                                      shapes=[dict(type="line", x0=-0.5, x1=len(piv_comp)-0.5,
                                                   y0=0, y1=0,
                                                   line=dict(color="#9CA3AF",width=1.5,dash="dot"))],
                                      title=dict(text=f"{lbl1[:30]} vs {lbl2[:30]}",
                                                 font=dict(size=12,color="#6B7280"), x=0.01))
                    _pchart(fig)

# ══════════════════════════════════════════════════════════════════════════
# TAB 7 · MI MARCA
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "🎯  Mi Marca":
    # ── Selectores de marca y SKU ────────────────────────────────────────
    _marcas_mm_opts = sorted(
        list(MARCAS_DESTACADAS) +
        [m for m in dff["Marca_raw"].unique() if m not in MARCAS_DESTACADAS]
    )
    _mm_def_idx = _marcas_mm_opts.index("La Toscana") if "La Toscana" in _marcas_mm_opts else 0
    _mm_col1, _ = st.columns([2, 3])
    with _mm_col1:
        _mm_sel = st.selectbox("🎯 Marca a analizar", _marcas_mm_opts,
                                index=_mm_def_idx, key="mi_marca_sel")
    _mm_sku_sel = "Todos los SKUs"

    _mm_dff_base = dff[dff["Marca_raw"] == _mm_sel].copy()
    _mm_dff = (_mm_dff_base if _mm_sku_sel == "Todos los SKUs"
               else _mm_dff_base[_mm_dff_base["SKU_canonico"] == _mm_sku_sel].copy())
    _mm_resto = dff[dff["Marca_raw"] != _mm_sel].copy()

    if _mm_dff.empty:
        st.info(f"Sin datos para {_mm_sel} con los filtros actuales.")
    else:
        # ── A) Posicionamiento de precio relativo ────────────────────────
        with st.expander("📍 Posicionamiento de precio relativo", expanded=True):
            # Modo de los KPIs sigue el toggle de la barra
            _mm_kpi_modo = st.session_state.get("mm_modo_bar", "$/Litro")
            if _mm_kpi_modo == "$/Litro":
                _mm_pl_marca = _mm_dff.dropna(subset=["Precio_litro"])
                _mm_pl_resto = _mm_resto.dropna(subset=["Precio_litro"])
                _mm_avg_marca = _mm_pl_marca["Precio_litro"].mean() if not _mm_pl_marca.empty else 0
                _mm_avg_merc  = _mm_pl_resto["Precio_litro"].mean() if not _mm_pl_resto.empty else 0
                _mm_kpi_lbl1, _mm_kpi_lbl2 = "$/L marca", "$/L mercado"
                _mm_kpi_sub1 = f"promedio {_mm_sel}"
                _mm_kpi_sub2 = "promedio resto marcas"
            else:
                _mm_avg_marca = _mm_dff["Precio"].mean() if not _mm_dff.empty else 0
                _mm_avg_merc  = _mm_resto["Precio"].mean() if not _mm_resto.empty else 0
                _mm_kpi_lbl1, _mm_kpi_lbl2 = "$ góndola marca", "$ góndola mercado"
                _mm_kpi_sub1 = f"precio prom. {_mm_sel}"
                _mm_kpi_sub2 = "precio prom. resto marcas"
            # Prima siempre en $/L vs TODO el mercado (no solo "el resto")
            _mm_pl_all = dff.dropna(subset=["Precio_litro"])
            _mm_avg_marca_pl = (_mm_dff.dropna(subset=["Precio_litro"])["Precio_litro"].mean()
                                if not _mm_dff.dropna(subset=["Precio_litro"]).empty else 0)
            _mm_avg_mkt_pl   = _mm_pl_all["Precio_litro"].mean() if not _mm_pl_all.empty else 0
            _mm_prima = ((_mm_avg_marca_pl / _mm_avg_mkt_pl) - 1) * 100 if _mm_avg_mkt_pl > 0 else 0
            _mm_cadenas   = _mm_dff["Cadena"].nunique()

            _mm_k1, _mm_k2, _mm_k3, _mm_k4 = st.columns(4)
            _mm_kpis = [
                ("orange", _mm_kpi_lbl1,      f"${_mm_avg_marca:,.0f}", _mm_kpi_sub1),
                ("",       _mm_kpi_lbl2,      f"${_mm_avg_merc:,.0f}",  _mm_kpi_sub2),
                ("red" if _mm_prima > 0 else "green",
                           "Precio vs mercado",
                           f"{_mm_prima:+.1f}%",
                           "más cara" if _mm_prima > 0 else "más barata"),
                ("purple", "Presencia",       str(_mm_cadenas),         "cadenas donde está listada"),
            ]
            for _col_mm, (_cls, _lab, _val, _sub) in zip([_mm_k1,_mm_k2,_mm_k3,_mm_k4], _mm_kpis):
                with _col_mm:
                    st.markdown(f"""<div class="kpi-card {_cls}">
                        <div class="kpi-label">{_lab}</div>
                        <div class="kpi-value" style="font-size:{'1.2rem' if len(_val)>9 else '1.7rem'}">{_val}</div>
                        <div class="kpi-sub">{_sub}</div>
                    </div>""", unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Toggle $/L vs Góndola por gramaje — botones
            if "mm_modo_bar" not in st.session_state:
                st.session_state["mm_modo_bar"] = "$/Litro"
            _btn_col1, _btn_col2, _btn_gram_col, _ = st.columns([0.7, 0.9, 1.8, 3])
            with _btn_col1:
                if st.button("📊 $/Litro",
                             type="primary" if st.session_state["mm_modo_bar"] == "$/Litro" else "secondary",
                             key="btn_litro"):
                    st.session_state["mm_modo_bar"] = "$/Litro"
                    st.rerun()
            with _btn_col2:
                if st.button("🛒 Góndola",
                             type="primary" if st.session_state["mm_modo_bar"] == "Góndola por gramaje" else "secondary",
                             key="btn_gondola"):
                    st.session_state["mm_modo_bar"] = "Góndola por gramaje"
                    st.rerun()
            _mm_modo_bar = st.session_state["mm_modo_bar"]
            if _mm_modo_bar == "Góndola por gramaje":
                with _btn_gram_col:
                    _mm_gram_opts = ["Todos los gramajes"] + [
                        g for g in GRAMAJE_BUCKETS if dff["Gramaje"].eq(g).any()
                    ]
                    _mm_gram_sel = st.selectbox("Gramaje", _mm_gram_opts,
                                                 key="mm_gram_bar", label_visibility="collapsed")

            with st.container():
                if _mm_modo_bar == "$/Litro":
                    _mm_by_m = (dff.dropna(subset=["Precio_litro"])
                                .groupby("Marca_raw")["Precio_litro"].mean()
                                .reset_index().sort_values("Precio_litro"))
                    _mm_x_col, _mm_x_lbl, _mm_x_fmt = "Precio_litro", "$/L promedio", lambda v: f"${v:,.0f}/L"
                else:
                    _mm_src_gram = dff if _mm_gram_sel == "Todos los gramajes" else dff[dff["Gramaje"]==_mm_gram_sel]
                    _mm_by_m = (_mm_src_gram.groupby("Marca_raw")["Precio"]
                                .mean().reset_index().sort_values("Precio")
                                .rename(columns={"Precio":"Precio_litro"}))
                    _mm_x_col = "Precio_litro"
                    _gram_lbl = "" if _mm_gram_sel == "Todos los gramajes" else f" · {_mm_gram_sel}"
                    _mm_x_lbl = f"Precio góndola prom.{_gram_lbl}"
                    _mm_x_fmt = lambda v: f"${v:,.0f}"

                if not _mm_by_m.empty:
                    _mm_colores = [
                        COLORES_CAT.get(_mm_sel, "#F18F01") if m == _mm_sel else "#E5E7EB"
                        for m in _mm_by_m["Marca_raw"]
                    ]
                    fig_mm_bar = go.Figure(go.Bar(
                        x=_mm_by_m[_mm_x_col], y=_mm_by_m["Marca_raw"],
                        orientation="h", marker_color=_mm_colores,
                        text=[_mm_x_fmt(v) for v in _mm_by_m[_mm_x_col]],
                        textposition="outside", textfont=dict(size=11, color="#111827"),
                        cliponaxis=False,
                    ))
                    fig_mm_bar.update_layout(
                        **_BASE_CORE, height=max(300, len(_mm_by_m)*30+60),
                        margin=dict(l=10, r=160, t=40, b=10),
                        xaxis=dict(title=_mm_x_lbl, tickprefix="$", tickformat=",",
                                   range=[0, float(_mm_by_m[_mm_x_col].max())*1.38]),
                        yaxis=dict(tickfont=dict(size=11, color="#111827")),
                        showlegend=False,
                    )
                    _pchart(fig_mm_bar)

        # ── B) Presencia por cadena — heatmap SKU × cadena ───────────────
        with st.expander("🏪 Presencia por cadena", expanded=True):
            _mm_heat_src = df_full[df_full["Marca_raw"] == _mm_sel]
            if _mm_sku_sel != "Todos los SKUs":
                _mm_heat_src = _mm_heat_src[_mm_heat_src["SKU_canonico"] == _mm_sku_sel]
            _mm_heat_src = _mm_heat_src.copy()
            _mm_pres_piv = (_mm_heat_src.groupby(["SKU_canonico","Cadena"])["Precio"]
                            .mean().round(0).unstack("Cadena"))
            if not _mm_pres_piv.empty:
                _z_vals  = _mm_pres_piv.values
                _x_labs  = _mm_pres_piv.columns.tolist()
                _y_labs  = _mm_pres_piv.index.tolist()
                _z_flat  = [v for row in _z_vals for v in row if not pd.isna(v)]
                _z_thresh = (max(_z_flat) * 0.55) if _z_flat else 0
                fig_mm_h = go.Figure(go.Heatmap(
                    z=_z_vals, x=_x_labs, y=_y_labs,
                    colorscale="Blues",
                    colorbar=dict(title="$", tickprefix="$", tickformat=","),
                ))
                # Anotaciones con color de texto condicional: blanco en celdas oscuras
                for _ri, _ylab in enumerate(_y_labs):
                    for _ci, _xlab in enumerate(_x_labs):
                        _v = _z_vals[_ri][_ci]
                        _txt = f"${_v:,.0f}" if not pd.isna(_v) else "—"
                        _tcol = "white" if (not pd.isna(_v) and _v >= _z_thresh) else "#374151"
                        fig_mm_h.add_annotation(
                            x=_xlab, y=_ylab, text=_txt, showarrow=False,
                            font=dict(size=11, color=_tcol),
                        )
                fig_mm_h.update_layout(
                    **BASE, height=max(280, len(_mm_pres_piv)*42+80),
                    xaxis=dict(tickfont=dict(size=12,color="#111827"), side="top"),
                    yaxis=dict(tickfont=dict(size=11,color="#111827")),
                )
                _pchart(fig_mm_h)

            # KPIs chicos: presencia en cadenas por SKU
            _mm_cad_x_sku = (_mm_heat_src.groupby("SKU_canonico")["Cadena"]
                             .nunique().reset_index(name="n_cad")
                             .sort_values("n_cad", ascending=False))
            if not _mm_cad_x_sku.empty:
                _mm_cols_pres = st.columns(min(6, len(_mm_cad_x_sku)))
                for _ci, (_, _row_pres) in enumerate(
                        _mm_cad_x_sku.head(6).iterrows()):
                    with _mm_cols_pres[_ci]:
                        st.markdown(
                            f"<div style='background:#F9FAFB;border-radius:8px;padding:0.5rem 0.7rem;"
                            f"font-size:0.72rem;text-align:center'>"
                            f"<b style='color:#111827'>{_row_pres['n_cad']}</b><br>"
                            f"<span style='color:#6B7280'>{_row_pres['SKU_canonico'][:22]}…</span></div>",
                            unsafe_allow_html=True)

        # ── C) Comparativa vs competidores ──────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">⚔️ Comparativa vs competidores</div>',
                    unsafe_allow_html=True)

        _mm_otras_marcas = sorted([m for m in dff["Marca_raw"].unique() if m != _mm_sel])
        _mm_cc1, _mm_cc2, _ = st.columns([2,2,3])
        with _mm_cc1:
            _def_comp1 = _mm_otras_marcas[0] if _mm_otras_marcas else None
            _mm_comp1  = st.selectbox("Competidor 1", _mm_otras_marcas,
                                       key="mm_comp1",
                                       index=0 if _mm_otras_marcas else None)
        with _mm_cc2:
            _def_comp2_idx = 1 if len(_mm_otras_marcas) > 1 else 0
            _mm_comp2  = st.selectbox("Competidor 2", _mm_otras_marcas,
                                       key="mm_comp2",
                                       index=_def_comp2_idx if _mm_otras_marcas else None)

        _orden_per_mm = sorted(dff["Periodo"].unique(),
                                key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())

        _mm_df_ev = (dff[dff["Marca_raw"].isin([_mm_sel, _mm_comp1, _mm_comp2])]
                     .dropna(subset=["Precio_litro"])
                     .groupby(["Periodo","Marca_raw"])["Precio_litro"].mean()
                     .reset_index())
        _mm_df_ev["Periodo"] = pd.Categorical(_mm_df_ev["Periodo"],
                                               categories=_orden_per_mm, ordered=True)
        _mm_ev_cmap = {
            _mm_sel:   COLORES_CAT.get(_mm_sel, "#0F3460"),
            _mm_comp1: "#9CA3AF",
            _mm_comp2: "#D1D5DB",
        }

        if len(_orden_per_mm) < 2:
            _mm_df_bar = (_mm_df_ev.groupby("Marca_raw")["Precio_litro"].mean()
                          .reset_index().sort_values("Precio_litro"))
            fig_mm_ev = go.Figure(go.Bar(
                x=_mm_df_bar["Precio_litro"], y=_mm_df_bar["Marca_raw"],
                orientation="h",
                marker_color=[_mm_ev_cmap.get(m,"#9CA3AF") for m in _mm_df_bar["Marca_raw"]],
                text=[f"${v:,.0f}/L" for v in _mm_df_bar["Precio_litro"]],
                textposition="outside", cliponaxis=False,
            ))
            fig_mm_ev.update_layout(**_BASE_CORE, height=260,
                                     margin=dict(l=10,r=160,t=30,b=10),
                                     xaxis=dict(tickprefix="$",tickformat=","),
                                     showlegend=False)
        else:
            fig_mm_ev = px.line(
                _mm_df_ev, x="Periodo", y="Precio_litro", color="Marca_raw",
                markers=True, color_discrete_map=_mm_ev_cmap,
                labels={"Precio_litro":"$/L promedio","Periodo":"","Marca_raw":"Marca"},
                height=400,
            )
            fig_mm_ev.update_traces(line=dict(width=2.5), marker=dict(size=8))
            fig_mm_ev.update_layout(**BASE,
                                     yaxis=dict(tickprefix="$",tickformat=",",
                                                tickfont=dict(size=12,color="#111827")),
                                     xaxis=dict(tickfont=dict(size=12,color="#111827")))
        _pchart(fig_mm_ev)

        # ── D) Comportamiento de ofertas ─────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">🏷️ Comportamiento de ofertas</div>',
                    unsafe_allow_html=True)

        _mm_of_src = df_full[df_full["Marca_raw"] == _mm_sel]
        if _mm_sku_sel != "Todos los SKUs":
            _mm_of_src = _mm_of_src[_mm_of_src["SKU_canonico"] == _mm_sku_sel]
        _mm_of_src = _mm_of_src.copy()
        _mm_n_sem_of = df_full["Periodo"].nunique()
        _mm_of_stats = (_mm_of_src.groupby("SKU_canonico").agg(
            sem_oferta=("En_oferta", lambda s: s.any().sum() if hasattr(s.any(), "__len__") else s.mean()),
            desc_prom =("Descuento_pct", lambda s: s[s > 0].mean() if s[s>0].any() else 0),
        ).reset_index())
        # Calcular % semanas en oferta usando agrupación por periodo
        _mm_of_rate = (_mm_of_src.groupby(["SKU_canonico","Periodo"])["En_oferta"]
                       .max().reset_index()
                       .groupby("SKU_canonico")["En_oferta"]
                       .mean().mul(100).reset_index(name="pct_sem_of"))
        _mm_of_cadenas = (_mm_of_src[_mm_of_src["En_oferta"]]
                          .groupby("SKU_canonico")["Cadena"]
                          .apply(lambda x: ", ".join(sorted(x.unique())))
                          .reset_index(name="cadenas_oferta"))
        _mm_desc_avg = (_mm_of_src[_mm_of_src["En_oferta"]]
                        .groupby("SKU_canonico")["Descuento_pct"]
                        .mean().reset_index(name="desc_prom"))

        _mm_of_tbl = (_mm_of_rate.merge(_mm_desc_avg, on="SKU_canonico", how="left")
                                   .merge(_mm_of_cadenas, on="SKU_canonico", how="left")
                                   .fillna({"desc_prom":0, "cadenas_oferta":"—"}))
        _mm_of_tbl.columns = ["SKU","% sem. en oferta","Dto. prom. (%)","Cadenas donde ofertó"]

        _mm_desc_merc = (df_full[df_full["En_oferta"] & (df_full["Marca_raw"] != _mm_sel)]
                         ["Descuento_pct"].mean())

        _mm_cd1, _mm_cd2 = st.columns(2)
        with _mm_cd1:
            _mm_skus_con_of = _mm_of_src[_mm_of_src["En_oferta"]]["SKU_canonico"].nunique()
            _mm_skus_total  = _mm_of_src["SKU_canonico"].nunique()
            _mm_pct_of_marca = (_mm_skus_con_of / _mm_skus_total * 100) if _mm_skus_total > 0 else 0
            st.markdown(
                f"<div style='background:#FFF7ED;border-radius:10px;padding:0.8rem 1rem;"
                f"border-left:3px solid #F97316'>"
                f"<span style='font-size:0.65rem;text-transform:uppercase;color:#9CA3AF'>"
                f"% del portfolio con descuento · {_mm_sel}</span><br>"
                f"<span style='font-size:1.6rem;font-weight:800;color:#111827'>"
                f"{_mm_pct_of_marca:.1f}%</span></div>",
                unsafe_allow_html=True)
        with _mm_cd2:
            _mm_desc_m = (_mm_of_src[_mm_of_src["En_oferta"]]["Descuento_pct"].mean()
                          if _mm_of_src["En_oferta"].any() else 0)
            st.markdown(
                f"<div style='background:#F0FDF4;border-radius:10px;padding:0.8rem 1rem;"
                f"border-left:3px solid #16A34A'>"
                f"<span style='font-size:0.65rem;text-transform:uppercase;color:#9CA3AF'>"
                f"Dto. prom. marca vs mercado</span><br>"
                f"<span style='font-size:1.6rem;font-weight:800;color:#111827'>"
                f"{_mm_desc_m:.0f}% vs {_mm_desc_merc:.0f}%</span></div>",
                unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        if not _mm_of_tbl.empty:
            st.dataframe(_mm_of_tbl,
                         height=min(400, len(_mm_of_tbl)*38+60), hide_index=True,
                         column_config={
                             "% sem. en oferta": st.column_config.NumberColumn(format="%.1f%%"),
                             "Dto. prom. (%)":   st.column_config.NumberColumn(format="%.0f%%"),
                         })

        # ── E) Tracking de distribución ──────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">📦 Tracking de distribución</div>',
                    unsafe_allow_html=True)

        _mm_dist_src = df_full[df_full["Marca_raw"] == _mm_sel]
        if _mm_sku_sel != "Todos los SKUs":
            _mm_dist_src = _mm_dist_src[_mm_dist_src["SKU_canonico"] == _mm_sku_sel]
        _mm_dist_src = _mm_dist_src.copy()
        _mm_ult_f = df_full["Fecha"].max()
        _mm_dist_rows = []
        for (_sku_d, _cad_d), _grp_d in _mm_dist_src.groupby(["SKU_canonico","Cadena"]):
            _primera = _grp_d["Fecha"].min().strftime("%d/%m/%Y")
            _ultima  = _grp_d["Fecha"].max().strftime("%d/%m/%Y")
            _activo  = "✓ Activo" if _grp_d["Fecha"].max() == _mm_ult_f else "✗ Salió"
            _mm_dist_rows.append({
                "SKU":          _sku_d,
                "Cadena":       _cad_d,
                "Primera vez":  _primera,
                "Última vez":   _ultima,
                "Estado":       _activo,
            })
        if _mm_dist_rows:
            _mm_dist_df = pd.DataFrame(_mm_dist_rows).sort_values(["Estado","SKU"])
            st.dataframe(_mm_dist_df,
                         height=min(500, len(_mm_dist_df)*38+60), hide_index=True)
        else:
            st.info("Sin datos de distribución para esta marca.")

        # ── F) Presencia por cadena — período semanal ───────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">📍 Presencia por cadena — SKU × Cadena</div>',
                    unsafe_allow_html=True)
        _mm_pres_src = df_full[df_full["Marca_raw"] == _mm_sel].copy()
        if _mm_sku_sel != "Todos los SKUs":
            _mm_pres_src = _mm_pres_src[_mm_pres_src["SKU_canonico"] == _mm_sku_sel]
        if not _mm_pres_src.empty:
            _mm_pres_fechas = sorted(df_full["Fecha"].unique())
            _mm_seen_sem: dict = {}
            for _f in _mm_pres_fechas:
                _ts = pd.Timestamp(_f)
                _k = f"Sem {_ts.isocalendar().week} · {_ts.strftime('%b %Y')}"
                if _k not in _mm_seen_sem:
                    _mm_seen_sem[_k] = []
                _mm_seen_sem[_k].append(_f)
            _mm_per_labels = list(_mm_seen_sem.keys())
            _mm_pres_lbl = st.selectbox(
                "🗓️ Período a visualizar",
                _mm_per_labels,
                index=len(_mm_per_labels) - 1,
                key="mm_pres_ventana",
            )
            _mm_pres_fechas_sel = _mm_seen_sem[_mm_pres_lbl]
            st.markdown(
                f'<div class="chart-note">'
                f'🟢 activo en al menos 1 scrape de <b>{_mm_pres_lbl}</b> &nbsp;·&nbsp; '
                f'🔴 estuvo antes pero no en este período &nbsp;·&nbsp; — nunca en esa cadena.'
                f'</div>',
                unsafe_allow_html=True)
            _mm_ventana_df  = _mm_pres_src[_mm_pres_src["Fecha"].isin(_mm_pres_fechas_sel)]
            _mm_pres_set    = set(zip(_mm_ventana_df["SKU_canonico"], _mm_ventana_df["Cadena"]))
            _mm_todos_skus  = sorted(_mm_pres_src["SKU_canonico"].unique())
            _mm_todas_cad   = sorted(df_full["Cadena"].unique())
            _mm_hist_set    = set(zip(_mm_pres_src["SKU_canonico"], _mm_pres_src["Cadena"]))
            _mm_pz, _mm_pt = [], []
            for _sk in _mm_todos_skus:
                _rz, _rt = [], []
                for _cd in _mm_todas_cad:
                    if (_sk, _cd) in _mm_pres_set:
                        _rz.append(1);  _rt.append("✓")
                    elif (_sk, _cd) in _mm_hist_set:
                        _rz.append(-1); _rt.append("✗")
                    else:
                        _rz.append(0);  _rt.append("—")
                _mm_pz.append(_rz); _mm_pt.append(_rt)
            _mm_pres_colorscale = [
                [0.00, "#FCA5A5"], [0.33, "#FCA5A5"],
                [0.34, "#F3F4F6"], [0.66, "#F3F4F6"],
                [0.67, "#86EFAC"], [1.00, "#86EFAC"],
            ]
            _mm_pres_h = max(200, len(_mm_todos_skus) * 38 + 80)
            fig_mm_pres = go.Figure(go.Heatmap(
                z=_mm_pz,
                x=_mm_todas_cad,
                y=_mm_todos_skus,
                colorscale=_mm_pres_colorscale, zmin=-1, zmax=1,
                text=_mm_pt, texttemplate="%{text}",
                textfont=dict(size=13, color="#111827"),
                showscale=False, xgap=3, ygap=3,
            ))
            fig_mm_pres.update_layout(
                **BASE,
                height=_mm_pres_h,
                xaxis=dict(tickfont=dict(size=12, color="#111827"), side="top"),
                yaxis=dict(tickfont=dict(size=11, color="#111827")),
            )
            _pchart(fig_mm_pres)
        else:
            st.info("Sin datos de presencia para esta marca.")

# TAB 9 · QUIEBRES DE STOCK
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "📦  Quiebres":
    st.markdown(
        '<div class="chart-note">Un <b>quiebre</b> ocurre cuando un producto estaba disponible '
        'en un período y dejó de aparecer el siguiente. '
        '✓ verde = presente &nbsp;·&nbsp; ✗ rojo = quiebre &nbsp;·&nbsp; — gris = sin datos.</div>',
        unsafe_allow_html=True,
    )

    _qb_colorscale = [
        [0.00, "#FCA5A5"], [0.33, "#FCA5A5"],
        [0.34, "#F3F4F6"], [0.66, "#F3F4F6"],
        [0.67, "#86EFAC"], [1.00, "#86EFAC"],
    ]

    # Filtros: Marca · Cadena · Temporalidad
    _qlbl = '<p style="font-size:0.7rem;font-weight:700;color:#111827;text-transform:uppercase;letter-spacing:.05em;margin:0 0 2px">'
    _qb_fa, _qb_fb, _qb_fc, _ = st.columns([2, 2, 2, 3])
    with _qb_fa:
        st.markdown(_qlbl + "🏷️ Marca</p>", unsafe_allow_html=True)
        _qb_marcas_opts = sorted(df_full["Marca_raw"].unique())
        _qb_marca = st.selectbox("Marca", _qb_marcas_opts, key="qb_marca", label_visibility="collapsed")
    with _qb_fb:
        st.markdown(_qlbl + "🏪 Cadena</p>", unsafe_allow_html=True)
        _qb_cadenas_opts = ["Todas las cadenas"] + sorted(df_full[df_full["Marca_raw"] == _qb_marca]["Cadena"].unique())
        _qb_cadena = st.selectbox("Cadena", _qb_cadenas_opts, key="qb_cadena", label_visibility="collapsed")
    with _qb_fc:
        st.markdown(_qlbl + "📅 Temporalidad</p>", unsafe_allow_html=True)
        _qb_gran = st.selectbox("Temporalidad", ["Semanal", "Mensual", "Diario"], key="qb_gran", label_visibility="collapsed")

    # Fuente: marca + (cadena o todas)
    if _qb_cadena == "Todas las cadenas":
        _qb_src = df_full[df_full["Marca_raw"] == _qb_marca].copy()
    else:
        _qb_src = df_full[
            (df_full["Marca_raw"] == _qb_marca) &
            (df_full["Cadena"] == _qb_cadena)
        ].copy()

    if _qb_src.empty:
        st.info("Sin datos para la selección.")
    else:
        # Columna de período según temporalidad
        if _qb_gran == "Diario":
            _qb_src["_pqb"] = _qb_src["Fecha"].dt.strftime("%d/%m/%Y")
            _qb_cols_ord = (
                _qb_src[["Fecha", "_pqb"]].drop_duplicates()
                .sort_values("Fecha")["_pqb"].tolist()
            )
        elif _qb_gran == "Mensual":
            _qb_src["_pqb"] = _qb_src["Fecha"].dt.strftime("%b %Y")
            _seen_m: list = []
            for _x in (_qb_src[["Fecha","_pqb"]].drop_duplicates()
                       .sort_values("Fecha")["_pqb"].tolist()):
                if _x not in _seen_m: _seen_m.append(_x)
            _qb_cols_ord = _seen_m
        else:  # Semanal
            _qb_src["_pqb"] = _qb_src["Periodo"]
            _qb_cols_ord = sorted(
                _qb_src["_pqb"].unique(),
                key=lambda p: df_full[df_full["Periodo"] == p]["Fecha"].min(),
            )

        # Pivot: filas = SKU, columnas = período
        _qb_pres = _qb_src.groupby(["_pqb", "SKU_canonico"]).size().reset_index(name="_n")
        _qb_pivot = (
            _qb_pres.pivot(index="SKU_canonico", columns="_pqb", values="_n")
            .reindex(columns=[c for c in _qb_cols_ord if c in _qb_pres["_pqb"].unique()])
            .fillna(0)
        )

        if not _qb_pivot.empty:
            # Matriz de estado: 1=presente, -1=quiebre, 0=sin datos previos
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

            fig = go.Figure(go.Heatmap(
                z=_qb_status.values,
                x=_qb_status.columns.tolist(),
                y=_qb_status.index.tolist(),
                colorscale=_qb_colorscale, zmin=-1, zmax=1,
                text=_qb_text.values, texttemplate="%{text}",
                textfont=dict(size=13, color="#111827"),
                showscale=False, xgap=2, ygap=2,
            ))
            fig.update_layout(
                **BASE,
                height=max(280, len(_qb_pivot) * 40 + 100),
                xaxis=dict(tickfont=dict(size=11, color="#111827"),
                           side="bottom", tickangle=-20),
                yaxis=dict(tickfont=dict(size=11, color="#111827")),
            )
            _pchart(fig)

            # Tabla resumen por SKU
            _qb_n_breaks = (_qb_status == -1).sum(axis=1)
            _qb_with_breaks = _qb_n_breaks[_qb_n_breaks > 0].sort_values(ascending=False)
            if not _qb_with_breaks.empty:
                st.markdown('<div class="chart-title">Resumen de quiebres por SKU</div>',
                            unsafe_allow_html=True)
                _qb_unit = {"Diario": "días", "Semanal": "semanas", "Mensual": "meses"}[_qb_gran]
                _qb_rows = []
                for _sk, _nb in _qb_with_breaks.items():
                    _qb_per_afect = [
                        _qb_status.columns[_ci]
                        for _ci in range(len(_qb_status.columns))
                        if _qb_status.loc[_sk, _qb_status.columns[_ci]] == -1
                    ]
                    _qb_rows.append({
                        "SKU": _sk,
                        f"Quiebres ({_qb_unit})": int(_nb),
                        "Períodos afectados": ", ".join(_qb_per_afect),
                    })
                _qb_sum_col, _ = st.columns([3, 1])
                with _qb_sum_col:
                    st.dataframe(pd.DataFrame(_qb_rows), hide_index=True)
            else:
                st.success("✅ No se detectaron quiebres en el período seleccionado.")

    # ── Presencia actual: SKU × Cadena ────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="chart-title">📍 Presencia por cadena — SKU × Cadena</div>',
                unsafe_allow_html=True)

    if not _qb_src.empty:
        # Construir lista de períodos disponibles según temporalidad
        _qb_src_marca = df_full[df_full["Marca_raw"] == _qb_marca].copy()
        _qb_fechas_disp = sorted(df_full["Fecha"].unique())

        if _qb_gran == "Diario":
            _qb_per_labels = [pd.Timestamp(f).strftime("%d/%m/%Y") for f in _qb_fechas_disp]
            _qb_per_map = {lbl: [f] for lbl, f in zip(_qb_per_labels, _qb_fechas_disp)}
        elif _qb_gran == "Mensual":
            _seen_mes: dict = {}
            for _f in _qb_fechas_disp:
                _k = pd.Timestamp(_f).strftime("%b %Y")
                if _k not in _seen_mes:
                    _seen_mes[_k] = []
                _seen_mes[_k].append(_f)
            _qb_per_labels = list(_seen_mes.keys())
            _qb_per_map = _seen_mes
        else:  # Semanal
            _seen_sem: dict = {}
            for _f in _qb_fechas_disp:
                _ts = pd.Timestamp(_f)
                _k = f"Sem {_ts.isocalendar().week} · {_ts.strftime('%b %Y')}"
                if _k not in _seen_sem:
                    _seen_sem[_k] = []
                _seen_sem[_k].append(_f)
            _qb_per_labels = list(_seen_sem.keys())
            _qb_per_map = _seen_sem

        _pres_sel_lbl = st.selectbox(
            "🗓️ Período a visualizar",
            _qb_per_labels,
            index=len(_qb_per_labels) - 1,
            key="qb_pres_ventana",
        )
        _pres_fechas_sel = _qb_per_map[_pres_sel_lbl]

        st.markdown(
            f'<div class="chart-note">'
            f'🟢 activo en al menos 1 scrape de <b>{_pres_sel_lbl}</b> &nbsp;·&nbsp; '
            f'🔴 estuvo antes pero no en este período &nbsp;·&nbsp; — nunca en esa cadena.'
            f'</div>',
            unsafe_allow_html=True)

        # Presencia acumulada: aparece en CUALQUIER scrape del período seleccionado
        _qb_ventana_df = _qb_src_marca[_qb_src_marca["Fecha"].isin(_pres_fechas_sel)]
        _qb_pres_set = set(zip(_qb_ventana_df["SKU_canonico"], _qb_ventana_df["Cadena"]))

        # SKUs históricos de la marca + todas las cadenas
        _qb_todos_skus = sorted(_qb_src_marca["SKU_canonico"].unique())
        _qb_todas_cad  = sorted(df_full["Cadena"].unique())
        # Combinaciones que alguna vez existieron (para distinguir rojo de gris)
        _qb_historial_set = set(zip(_qb_src_marca["SKU_canonico"], _qb_src_marca["Cadena"]))

        _qb_z, _qb_txt = [], []
        for _sk in _qb_todos_skus:
            _row_z, _row_t = [], []
            for _cd in _qb_todas_cad:
                if (_sk, _cd) in _qb_pres_set:
                    _row_z.append(1); _row_t.append("✓")
                elif (_sk, _cd) in _qb_historial_set:
                    _row_z.append(-1); _row_t.append("✗")
                else:
                    _row_z.append(0); _row_t.append("—")
            _qb_z.append(_row_z)
            _qb_txt.append(_row_t)

        _qb_pres_h = max(200, len(_qb_todos_skus)*38 + 80)
        fig_pres = go.Figure(go.Heatmap(
            z=_qb_z,
            x=_qb_todas_cad,
            y=_qb_todos_skus,
            colorscale=_qb_colorscale, zmin=-1, zmax=1,
            text=_qb_txt, texttemplate="%{text}",
            textfont=dict(size=13, color="#111827"),
            showscale=False, xgap=3, ygap=3,
        ))
        fig_pres.update_layout(
            **BASE,
            height=_qb_pres_h,
            xaxis=dict(tickfont=dict(size=12, color="#111827"), side="top"),
            yaxis=dict(tickfont=dict(size=11, color="#111827")),
        )
        _pchart(fig_pres)

# ══════════════════════════════════════════════════════════════════════════
# TAB 11 · TABLA DINÁMICA
# ══════════════════════════════════════════════════════════════════════════
if _page_sel == "🔢  Tabla dinámica":
    _TD_DIMS = {
        "Cadena":       "Cadena",
        "Marca":        "Marca",
        "Marca (raw)":  "Marca_raw",
        "SKU":          "SKU_canonico",
        "Gramaje":      "Gramaje",
        "En oferta":    "En_oferta",
        "Período":      "Periodo",
        "Fecha":        "Fecha",
    }
    _TD_METRICS = {
        "Precio ($)":          "Precio",
        "Precio/litro ($/L)":  "Precio_litro",
        "Precio real ($)":     "Precio_real",
        "Precio/litro real":   "Precio_litro_real",
        "Descuento (%)":       "Descuento_pct",
    }
    _TD_AGGS = {
        "Promedio":  "mean",
        "Mínimo":    "min",
        "Máximo":    "max",
        "Mediana":   "median",
        "Suma":      "sum",
        "Cantidad":  "count",
    }

    _td_lbl = 'font-size:0.72rem;font-weight:700;color:#6B7280;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:6px'

    # ── Fila 1: selectores ───────────────────────────────────────────────
    _td_r1, _td_r2, _td_r3, _td_r4 = st.columns([3, 3, 2, 2])
    with _td_r1:
        st.markdown(f'<div style="{_td_lbl}">📋 FILAS</div>', unsafe_allow_html=True)
        _td_rows_sel = st.multiselect(
            "Filas", list(_TD_DIMS.keys()),
            default=["Cadena"], key="td_rows", label_visibility="collapsed",
        )
    with _td_r2:
        st.markdown(f'<div style="{_td_lbl}">📊 COLUMNAS</div>', unsafe_allow_html=True)
        _td_cols_sel = st.multiselect(
            "Columnas", list(_TD_DIMS.keys()),
            default=["Marca"], key="td_cols", label_visibility="collapsed",
        )
    with _td_r3:
        st.markdown(f'<div style="{_td_lbl}">📐 MÉTRICA</div>', unsafe_allow_html=True)
        _td_metric = st.selectbox(
            "Métrica", list(_TD_METRICS.keys()),
            key="td_metric", label_visibility="collapsed",
        )
    with _td_r4:
        st.markdown(f'<div style="{_td_lbl}">⚙️ AGREGACIÓN</div>', unsafe_allow_html=True)
        _td_agg = st.selectbox(
            "Agregación", list(_TD_AGGS.keys()),
            key="td_agg", label_visibility="collapsed",
        )

    _td_rows_ord = _td_rows_sel
    _td_cols_ord = _td_cols_sel

    _td_src = dff.copy()
    # Fecha como string para evitar errores en pivot con columnas datetime
    if "Fecha" in _td_src.columns:
        _td_src["Fecha"] = _td_src["Fecha"].dt.strftime("%Y-%m-%d")

    if not _td_rows_ord:
        st.info("Elegí al menos una dimensión para las filas.")
    else:
        _td_row_cols = [_TD_DIMS[d] for d in _td_rows_ord]
        _td_col_cols = [_TD_DIMS[d] for d in _td_cols_ord] if _td_cols_ord else None
        _td_val_col  = _TD_METRICS[_td_metric]
        _td_agg_fn   = _TD_AGGS[_td_agg]
        _td_is_pct   = "pct" in _td_val_col.lower()
        _td_is_litro = "litro" in _td_val_col.lower()

        if _td_agg_fn != "count":
            _td_src = _td_src.dropna(subset=[_td_val_col])

        try:
            if _td_col_cols:
                _td_pivot = pd.pivot_table(
                    _td_src,
                    values=_td_val_col,
                    index=_td_row_cols,
                    columns=_td_col_cols,
                    aggfunc=_td_agg_fn,
                    fill_value=0,
                    margins=True,
                    margins_name="Total",
                )
                if isinstance(_td_pivot.columns, pd.MultiIndex):
                    _td_pivot.columns = [" · ".join(str(c) for c in col).strip()
                                         for col in _td_pivot.columns]
                _td_pivot = _td_pivot.reset_index()
            else:
                _td_pivot = (
                    _td_src.groupby(_td_row_cols)[_td_val_col]
                    .agg(_td_agg_fn)
                    .reset_index()
                    .rename(columns={_td_val_col: _td_metric})
                )
                if _td_agg_fn in ("sum", "mean", "count"):
                    _agg_val = _td_src[_td_val_col].agg(_td_agg_fn)
                    _total_row = {c: "Total" if i == 0 else "" for i, c in enumerate(_td_row_cols)}
                    _total_row[_td_metric] = _agg_val
                    _td_pivot = pd.concat([_td_pivot, pd.DataFrame([_total_row])], ignore_index=True)

            # Formato de columnas numéricas
            _td_num_cols = _td_pivot.select_dtypes(include="number").columns.tolist()
            _td_col_cfg  = {}
            if _td_agg_fn == "count":
                _td_fmt = "%d"
            elif _td_is_pct:
                _td_fmt = "%.1f%%"
            elif _td_is_litro:
                _td_fmt = "$%,.0f/L"
            else:
                _td_fmt = "$%,.0f"
            for _nc in _td_num_cols:
                _td_col_cfg[_nc] = st.column_config.NumberColumn(format=_td_fmt)

            st.markdown(
                f'<div class="chart-title">{_td_metric} · {_td_agg} &nbsp;'
                f'<span style="font-weight:400;color:#9CA3AF">({len(_td_pivot):,} filas)</span></div>',
                unsafe_allow_html=True,
            )
            st.dataframe(
                _td_pivot,
                height=min(700, max(200, len(_td_pivot) * 36 + 60)),
                column_config=_td_col_cfg,
                hide_index=True,
            )

            _td_csv = _td_pivot.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Descargar tabla CSV",
                data=_td_csv,
                file_name=f"tabla_dinamica_{_td_metric.replace(' ','_')}.csv",
                mime="text/csv",
                key="td_download",
            )

        except Exception as _td_err:
            st.error(f"No se puede armar la tabla con esa combinación: {_td_err}")

