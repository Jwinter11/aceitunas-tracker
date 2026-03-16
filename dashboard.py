#!/usr/bin/env python3
"""
Dashboard de precios de aceite de oliva — Aceite Tracker
Uso: streamlit run dashboard.py
"""

import json
import re
from pathlib import Path

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
def _check_password():
    pwd_ok = st.session_state.get("_pwd_ok", False)
    if pwd_ok:
        return True
    st.markdown("""
    <div style="display:flex;flex-direction:column;align-items:center;
                justify-content:center;min-height:60vh;gap:1.2rem">
      <div style="font-size:2.5rem">🫒</div>
      <div style="font-size:1.5rem;font-weight:800;color:#0F172A">Aceite Tracker</div>
      <div style="font-size:0.9rem;color:#6B7280">Ingresá la contraseña para continuar</div>
    </div>
    """, unsafe_allow_html=True)
    _, col, _ = st.columns([2, 1.5, 2])
    with col:
        pwd = st.text_input("Contraseña", type="password", label_visibility="collapsed",
                            placeholder="Contraseña…")
        if st.button("Entrar", use_container_width=True, type="primary"):
            correct = st.secrets.get("PASSWORD", "LaToscana2026")
            if pwd == correct:
                st.session_state["_pwd_ok"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta")
    st.stop()

_check_password()

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html,body,[class*="css"],.stApp{font-family:'Inter',sans-serif!important}
.stApp{background:#F0F2F6}
.block-container{padding:1.5rem 2rem 3rem;max-width:1500px}
#MainMenu,footer,header{visibility:hidden}
/* ── Botón para abrir sidebar (flecha) siempre visible ── */
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"]{
    visibility:visible!important;
    opacity:1!important;
    display:flex!important;
    align-items:center!important;
    justify-content:center!important;
    position:fixed!important;
    top:0.6rem!important;
    left:0.6rem!important;
    z-index:9999999!important;
    background:#1e3a5f!important;
    border-radius:8px!important;
    width:2.6rem!important;
    height:2.6rem!important;
    box-shadow:0 2px 12px rgba(0,0,0,0.5)!important;
    cursor:pointer!important;
    border:2px solid rgba(255,255,255,0.2)!important;
}
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg{
    color:#fff!important;
    fill:#fff!important;
}
/* Botón de colapsar (cuando está abierto) */
[data-testid="stSidebarCollapseButton"]{
    visibility:visible!important;
    opacity:1!important;
    display:flex!important;
}

.main-header{background:linear-gradient(135deg,#0A1628 0%,#0D2137 60%,#0F3460 100%);
    padding:1.8rem 2.5rem;border-radius:16px;margin-bottom:1.5rem;
    display:flex;align-items:center;justify-content:space-between}
.header-left h1{font-size:1.7rem;font-weight:800;color:#fff;margin:0;letter-spacing:-0.5px}
.header-left p{font-size:0.9rem;color:rgba(255,255,255,0.55);margin:0.3rem 0 0}
.header-badge{background:rgba(255,255,255,0.12);border:1px solid rgba(255,255,255,0.2);
    border-radius:50px;padding:0.4rem 1.2rem;color:#fff;font-size:0.82rem;font-weight:600}

.kpi-card{background:#fff;border-radius:14px;padding:1.1rem 1.3rem;
    box-shadow:0 1px 8px rgba(0,0,0,0.07);border-top:3px solid #0F3460}
.kpi-card.green{border-top-color:#00B050}.kpi-card.orange{border-top-color:#F7931E}
.kpi-card.purple{border-top-color:#7C3AED}.kpi-card.red{border-top-color:#EF4444}
.kpi-card.teal{border-top-color:#0D9488}.kpi-card.yellow{border-top-color:#EAB308}
.kpi-label{font-size:0.68rem;font-weight:700;text-transform:uppercase;
    letter-spacing:1px;color:#9CA3AF;margin-bottom:0.4rem}
.kpi-value{font-size:1.7rem;font-weight:800;color:#111827;line-height:1}
.kpi-sub{font-size:0.76rem;color:#6B7280;margin-top:0.3rem}

.chart-title{font-size:0.88rem;font-weight:700;color:#374151;margin-bottom:0.75rem;
    padding-bottom:0.5rem;border-bottom:1px solid #E5E7EB;
    text-transform:uppercase;letter-spacing:0.4px}
.chart-note{font-size:0.75rem;color:#9CA3AF;margin-top:-0.4rem;margin-bottom:0.75rem}

[data-testid="stSidebar"]{background:#0A1628!important}
[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p{color:#E5E7EB!important}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h3{color:#FFFFFF!important}
.sidebar-logo{font-size:1.45rem;font-weight:800;color:#fff;letter-spacing:-0.5px}
.sidebar-sub{font-size:0.75rem;color:rgba(255,255,255,0.4);margin-bottom:1.5rem}
.sidebar-sep{font-size:0.65rem;font-weight:700;text-transform:uppercase;
    letter-spacing:1.2px;color:rgba(255,255,255,0.3);
    margin:1.2rem 0 0.4rem;padding-bottom:0.4rem;
    border-bottom:1px solid rgba(255,255,255,0.08)}

.stTabs [data-baseweb="tab-list"]{background:#fff;border-radius:12px 12px 0 0;
    padding:0.4rem 0.4rem 0;gap:0.2rem;box-shadow:0 1px 6px rgba(0,0,0,0.06)}
.stTabs [data-baseweb="tab"]{font-size:0.84rem;font-weight:600;color:#6B7280;
    border-radius:8px 8px 0 0;padding:0.55rem 1.2rem}
.stTabs [aria-selected="true"]{color:#0F3460!important;background:#EEF2FF!important}
.stTabs [data-baseweb="tab-panel"]{background:#fff;border-radius:0 0 14px 14px;
    padding:1.5rem 1.6rem;box-shadow:0 2px 8px rgba(0,0,0,0.06)}

/* ══════════════════════════════════════════════
   MOBILE RESPONSIVE
   ══════════════════════════════════════════════ */
@media (max-width: 768px) {
    /* Layout general */
    .block-container{
        padding:0.6rem 0.6rem 2rem!important;
        max-width:100%!important;
    }
    /* Header compacto */
    .main-header{
        padding:1rem 1.2rem!important;
        flex-direction:column!important;
        gap:0.5rem!important;
        border-radius:10px!important;
    }
    .header-left h1{font-size:1.2rem!important}
    .header-left p{font-size:0.7rem!important}
    .header-right{display:none!important}
    /* Tabs: scroll horizontal */
    .stTabs [data-baseweb="tab-list"]{
        overflow-x:auto!important;
        flex-wrap:nowrap!important;
        -webkit-overflow-scrolling:touch!important;
        scrollbar-width:none!important;
    }
    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar{display:none}
    .stTabs [data-baseweb="tab"]{
        font-size:0.72rem!important;
        padding:0.4rem 0.7rem!important;
        white-space:nowrap!important;
    }
    .stTabs [data-baseweb="tab-panel"]{
        padding:0.8rem 0.7rem!important;
    }
    /* Columnas → apiladas */
    [data-testid="stHorizontalBlock"]{
        flex-wrap:wrap!important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="stVerticalBlock"]{
        min-width:100%!important;
        flex:1 1 100%!important;
    }
    /* Métricas KPI más chicas */
    [data-testid="stMetric"]{padding:0.5rem!important}
    [data-testid="stMetricValue"]{font-size:1.3rem!important}
    [data-testid="stMetricLabel"]{font-size:0.65rem!important}
    /* Gráficos: altura reducida */
    .js-plotly-plot{max-height:320px!important}
    /* Sidebar */
    [data-testid="stSidebar"]{width:85vw!important;max-width:85vw!important}
    /* Texto */
    .chart-title{font-size:0.85rem!important}
    .chart-note{font-size:0.7rem!important}
    /* Dataframes con scroll */
    [data-testid="stDataFrame"]{overflow-x:auto!important}
}
@media (max-width: 480px) {
    .block-container{padding:0.4rem 0.4rem 2rem!important}
    .stTabs [data-baseweb="tab"]{font-size:0.65rem!important;padding:0.35rem 0.5rem!important}
}
</style>
""", unsafe_allow_html=True)

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
@st.cache_data(ttl=120)
def cargar_datos() -> pd.DataFrame:
    path = DIRECTORIO / "historial_precios.json"
    if not path.exists():
        return pd.DataFrame()
    with open(path, encoding="utf-8") as f:
        hist = json.load(f)
    rows = []
    for sem in hist.get("semanas", []):
        fecha = sem["fecha"]
        for p in sem.get("productos", []):
            ml      = p.get("ml")
            precio  = p["precio"]
            gondola = p.get("precio_sin_dto") or precio
            pl_g    = round(gondola / ml * 1000) if (ml and ml > 0) else None
            desc    = round((gondola - precio) / gondola * 100) if gondola > precio else 0
            marca_r = _marca(p["nombre"], p.get("marca"))
            rows.append({
                "Fecha":         fecha,
                "Cadena":        p["supermercado"],
                "Marca_raw":     marca_r,
                "Marca":         categorizar(marca_r),
                "Producto":      p["nombre"],
                "SKU_canonico":  canonicalizar_sku(marca_r, p["nombre"], ml),
                "Tamaño_ml":     ml,
                "Gramaje":       bucket_gramaje(ml),
                "Precio":        int(round(gondola)),       # siempre góndola
                "Precio_litro":  int(round(pl_g)) if pl_g else None,
                "Precio_oferta": int(round(precio)),        # solo para tab Ofertas
                "Descuento_pct": desc,
                "En_oferta":     bool(p.get("en_oferta", False)),
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["Fecha"] = pd.to_datetime(df["Fecha"])
        df["Semana_num"] = df["Fecha"].dt.isocalendar().week.astype(int)
        df["Periodo"] = df["Fecha"].apply(
            lambda d: f"Sem {d.isocalendar().week} · {d.strftime('%b %Y')}"
        )
    return df

df_full = cargar_datos()
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

orden_cats = ["La Toscana","Zuelo","Oliovita","Natura","Nucete","Cocinero","Lira",
              "Marca Propia","Otras"]

# ── SIDEBAR ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="sidebar-logo">Aceite Tracker</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-sub">Monitor de precios · Argentina</div>',
                unsafe_allow_html=True)

    periodos_disp = sorted(df_full["Periodo"].unique(),
                           key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())
    st.markdown('<div class="sidebar-sep">Período semanal</div>', unsafe_allow_html=True)
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

    st.markdown('<div class="sidebar-sep">📤 Exportar</div>', unsafe_allow_html=True)
    _export_excel_btn = st.button("⬇️ Exportar Excel ejecutivo", use_container_width=True, key="btn_excel")
    _export_csv_btn   = st.button("⬇️ Exportar CSV completo",    use_container_width=True, key="btn_csv")

    # ── Categorías personalizadas ──────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="sidebar-sep">🗂️ Categorías</div>', unsafe_allow_html=True)
    if "custom_categorias" not in st.session_state:
        st.session_state["custom_categorias"] = []
    _cat_nueva = st.text_input(
        "Nueva categoría", placeholder="ej: Premium, Orgánico…",
        key="cat_nueva_input", label_visibility="collapsed",
    )
    _cat_col1, _cat_col2 = st.columns([3, 1])
    with _cat_col2:
        if st.button("＋", key="btn_add_cat", use_container_width=True):
            _v = _cat_nueva.strip()
            if _v and _v not in st.session_state["custom_categorias"]:
                st.session_state["custom_categorias"].append(_v)
    if st.session_state["custom_categorias"]:
        for _ci, _cn in enumerate(list(st.session_state["custom_categorias"])):
            _cc1, _cc2 = st.columns([4, 1])
            with _cc1:
                st.markdown(
                    f'<div style="font-size:0.78rem;color:#D1D5DB;padding:4px 0">{_cn}</div>',
                    unsafe_allow_html=True,
                )
            with _cc2:
                if st.button("✕", key=f"del_cat_{_ci}", use_container_width=True):
                    st.session_state["custom_categorias"].pop(_ci)
                    st.rerun()
    else:
        st.markdown(
            '<div style="font-size:0.75rem;color:#6B7280;padding:4px 0">Sin categorías creadas</div>',
            unsafe_allow_html=True,
        )

    # ── Productos favoritos ────────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="sidebar-sep">⭐ Favoritos</div>', unsafe_allow_html=True)
    if "favoritos" not in st.session_state:
        st.session_state["favoritos"] = []
    _fav_opts = sorted(df_full["SKU_canonico"].unique().tolist())
    _fav_sel = st.multiselect(
        "Productos favoritos",
        _fav_opts,
        default=[f for f in st.session_state["favoritos"] if f in _fav_opts],
        key="fav_multiselect",
        label_visibility="collapsed",
        placeholder="Buscar SKU…",
    )
    st.session_state["favoritos"] = _fav_sel

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

# ── Exportación ejecutiva ──────────────────────────────────────────────────
_ultima_sem_lbl = df_full["Semana_num"].max() if not df_full.empty else "XX"
_df_ult_export  = df_full[df_full["Fecha"] == df_full["Fecha"].max()].copy()

if _export_csv_btn:
    _csv_data = (dff[["Periodo","Cadena","Marca","Marca_raw","Producto","SKU_canonico",
                        "Gramaje","Precio","Precio_litro","Precio_oferta","Descuento_pct","En_oferta"]]
                 .copy())
    _csv_data.columns = ["Semana","Cadena","Marca","Marca_raw","Producto","SKU_canonico",
                          "Gramaje","Precio góndola ($)","Precio/Litro ($)",
                          "Precio oferta ($)","Descuento %","En oferta"]
    with st.sidebar:
        st.download_button("📥 Descargar CSV",
                           _csv_data.to_csv(index=False).encode("utf-8-sig"),
                           f"aceite_tracker_sem{_ultima_sem_lbl}.csv",
                           "text/csv", use_container_width=True, key="dl_csv")

if _export_excel_btn:
    try:
        import io, openpyxl  # noqa
        from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
        _buf = io.BytesIO()

        # Hoja 1 — Resumen por marca
        _tbl_exp1 = (dff.groupby("Marca_raw").agg(
            precio_medio=("Precio","mean"), precio_min=("Precio","min"),
            precio_max=("Precio","max"),
            pl_medio=("Precio_litro","mean"),
            pct_oferta=("En_oferta", lambda s: s.mean()*100),
            desc_medio=("Descuento_pct","mean"),
        ).reset_index())
        _tbl_exp1.columns = ["Marca","Precio medio ($)","Mínimo ($)","Máximo ($)",
                              "$/L promedio","% en oferta","Dto. prom. (%)"]

        # Hoja 2 — Heatmap marca × cadena
        _tbl_exp2 = (dff.groupby(["Marca_raw","Cadena"])["Precio"]
                     .mean().round(0).unstack("Cadena").reset_index())
        _tbl_exp2.columns.name = None

        # Hoja 3 — Ofertas activas
        _tbl_exp3 = (_df_ult_export[_df_ult_export["En_oferta"]]
                     [["Cadena","Marca_raw","SKU_canonico","Gramaje","Precio","Precio_oferta","Descuento_pct"]]
                     .copy())
        _tbl_exp3.columns = ["Cadena","Marca","SKU","Gramaje","Precio góndola ($)","Precio oferta ($)","Dto. %"]

        # Hoja 4 — Movimientos
        _f_primera = df_full["Fecha"].min()
        _f_ultima  = df_full["Fecha"].max()
        _skus_pri  = set(zip(df_full[df_full["Fecha"]==_f_primera]["SKU_canonico"],
                              df_full[df_full["Fecha"]==_f_primera]["Cadena"]))
        _skus_ult  = set(zip(df_full[df_full["Fecha"]==_f_ultima]["SKU_canonico"],
                              df_full[df_full["Fecha"]==_f_ultima]["Cadena"]))
        _entradas  = [{"SKU":s,"Cadena":c,"Estado":"Entrada"} for s,c in _skus_ult - _skus_pri]
        _salidas   = [{"SKU":s,"Cadena":c,"Estado":"Salida"}  for s,c in _skus_pri - _skus_ult]
        _tbl_exp4  = pd.DataFrame(_entradas + _salidas) if (_entradas or _salidas) else pd.DataFrame(columns=["SKU","Cadena","Estado"])

        with pd.ExcelWriter(_buf, engine="openpyxl") as _xw:
            _tbl_exp1.to_excel(_xw, sheet_name="Resumen",          index=False)
            _tbl_exp2.to_excel(_xw, sheet_name="Por Cadena",        index=False)
            _tbl_exp3.to_excel(_xw, sheet_name="Ofertas activas",   index=False)
            _tbl_exp4.to_excel(_xw, sheet_name="Movimientos",       index=False)

        with st.sidebar:
            st.download_button("📥 Descargar Excel",
                               _buf.getvalue(),
                               f"aceite_tracker_semana_{_ultima_sem_lbl}.xlsx",
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               use_container_width=True, key="dl_excel")
    except ImportError:
        st.sidebar.warning("Instalá openpyxl: `pip install openpyxl`")

# ── Header ────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="main-header">
  <div class="header-left">
    <h1>Monitor de Precios · Aceite de Oliva</h1>
    <p>Última actualización: {fecha_max_str} &nbsp;·&nbsp; {len(df_ult):,} productos &nbsp;·&nbsp;
       {df_ult["Cadena"].nunique()} cadenas &nbsp;·&nbsp;
       {n_sem} semana{"s" if n_sem>1 else ""} acumulada{"s" if n_sem>1 else ""}</p>
  </div>
  <div class="header-badge">🏷️ Precios de góndola</div>
</div>
""", unsafe_allow_html=True)

# ── Notificaciones de favoritos ───────────────────────────────────────────
_favs = st.session_state.get("favoritos", [])
if _favs:
    _fechas_ord = sorted(df_full["Fecha"].unique())
    if len(_fechas_ord) >= 2:
        _f_rec  = _fechas_ord[-1]
        _f_prev = _fechas_ord[-2]
        _fav_notifs = []
        for _fsku in _favs:
            _fdf = df_full[df_full["SKU_canonico"] == _fsku]
            _prec_rec  = _fdf[_fdf["Fecha"] == _f_rec]["Precio"].mean()
            _prec_prev = _fdf[_fdf["Fecha"] == _f_prev]["Precio"].mean()
            if pd.notna(_prec_rec) and pd.notna(_prec_prev) and _prec_prev > 0:
                _pct = (_prec_rec - _prec_prev) / _prec_prev * 100
                if abs(_pct) >= 1:
                    _arrow = "🔴 ▲" if _pct > 0 else "🟢 ▼"
                    _fav_notifs.append(
                        f"{_arrow} <b>{_fsku}</b> &nbsp;{_pct:+.1f}% "
                        f"&nbsp;<span style='color:#9CA3AF'>"
                        f"${_prec_prev:,.0f} → ${_prec_rec:,.0f}</span>"
                    )
            elif pd.notna(_prec_rec) and pd.isna(_prec_prev):
                _fav_notifs.append(f"🆕 <b>{_fsku}</b> &nbsp;reapareció en góndola")
            elif pd.isna(_prec_rec) and pd.notna(_prec_prev):
                _fav_notifs.append(f"⚠️ <b>{_fsku}</b> &nbsp;ya no aparece (posible quiebre)")
        if _fav_notifs:
            _notif_html = "".join(
                f'<div style="padding:4px 0;font-size:0.82rem;color:#E5E7EB;'
                f'border-bottom:1px solid rgba(255,255,255,0.07)">{n}</div>'
                for n in _fav_notifs
            )
            st.markdown(f"""
            <div style="background:rgba(15,52,96,0.55);border:1px solid rgba(99,179,237,0.25);
                        border-radius:10px;padding:0.7rem 1rem;margin-bottom:0.8rem">
              <div style="font-size:0.7rem;font-weight:700;text-transform:uppercase;
                          letter-spacing:0.8px;color:#93C5FD;margin-bottom:6px">
                ⭐ Cambios en favoritos
              </div>
              {_notif_html}
            </div>""", unsafe_allow_html=True)

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
    ("",       "Productos relevados",    f"{dff['SKU_canonico'].nunique():,}", f"{dff['Cadena'].nunique()} cadenas"),
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

# ── TABS ──────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11 = st.tabs([
    "📊  Resumen",
    "🏪  Por Cadena",
    "🏷️  Por Marca",
    "📈  Evolución",
    "🔖  Ofertas",
    "⚖️  Comparativa",
    "🎯  Mi Marca",
    "💡  Insights",
    "📦  Quiebres",
    "📋  Base",
    "🔢  Tabla dinámica",
])

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
with tab1:
    # ══ Novedades ═══════════════════════════════════════════════════════════
    _pord1 = sorted(df_full["Periodo"].unique(),
                    key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())
    _ult1  = _pord1[-1] if _pord1 else None
    _pen1  = _pord1[-2] if len(_pord1) >= 2 else None

    # Cambios de precio vs período anterior (mismo Cadena + SKU canónico, ≥3%)
    _cambios1: list[dict] = []
    if _ult1 and _pen1:
        _avg_u = (dff[dff["Periodo"]==_ult1]
                      .groupby(["Cadena","SKU_canonico"])["Precio"].mean())
        _avg_p = (dff[dff["Periodo"]==_pen1]
                      .groupby(["Cadena","SKU_canonico"])["Precio"].mean())
        for _k in _avg_u.index.intersection(_avg_p.index):
            _pn, _pv = float(_avg_u[_k]), float(_avg_p[_k])
            _cp = (_pn - _pv) / _pv * 100
            if abs(_cp) >= 3:
                _cambios1.append({"cadena":_k[0],"sku":_k[1],
                                   "viejo":_pv,"nuevo":_pn,"pct":_cp})
        _cambios1.sort(key=lambda x: abs(x["pct"]), reverse=True)

    # Ofertas de la semana (último período, todos los filtros activos salvo marca)
    _of_now_df = pd.DataFrame()
    if _ult1:
        _of_now_df = df_full[
            (df_full["Periodo"]==_ult1) &
            df_full["En_oferta"] &
            df_full["Cadena"].isin(cadenas_sel) &
            (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(gram_sel))
        ].copy()

    # Top 3 ofertas + Zuelo/Oliovita garantizados si tienen oferta
    _top_of1: list[dict] = []
    if not _of_now_df.empty:
        _of_agg1 = (_of_now_df
                    .groupby(["Cadena","SKU_canonico","Marca_raw"])
                    .agg(desc=("Descuento_pct","max"),
                         pof=("Precio_oferta","min"),
                         pg =("Precio","mean"))
                    .reset_index()
                    .sort_values("desc", ascending=False)
                    .reset_index(drop=True))
        _top3_1   = _of_agg1.head(3).copy()
        _in_top3_1 = set(_top3_1["SKU_canonico"])
        for _mb1 in ("Zuelo","Oliovita"):
            _mbr1 = _of_agg1[(_of_agg1["Marca_raw"]==_mb1) &
                               (~_of_agg1["SKU_canonico"].isin(_in_top3_1))]
            if not _mbr1.empty:
                _top3_1 = pd.concat([_top3_1, _mbr1.head(1)], ignore_index=True)
                _in_top3_1.add(_mbr1.iloc[0]["SKU_canonico"])
        _top_of1 = _top3_1.to_dict("records")

    if _cambios1 or _top_of1:
        _lbl1 = _ult1 or ""
        st.markdown(f"""
        <div style="background:#fff;border-radius:14px;padding:1rem 1.4rem 0.6rem;
                    box-shadow:0 1px 6px rgba(0,0,0,0.07);margin-bottom:1.1rem;
                    border-top:3px solid #0F3460">
          <div style="font-size:0.8rem;font-weight:700;text-transform:uppercase;
                      letter-spacing:0.6px;color:#374151;margin-bottom:0.1rem">
            🔔 Novedades &nbsp;·&nbsp; {_lbl1}
          </div>
        </div>""", unsafe_allow_html=True)
        _cn_l, _cn_r = st.columns(2, gap="large")

        # ── Columna izquierda: cambios de precio ──────────────────────────
        with _cn_l:
            st.markdown('<div class="chart-note">📊 Cambios de precio vs semana anterior</div>',
                        unsafe_allow_html=True)
            if not _cambios1:
                st.markdown(
                    '<div style="color:#9CA3AF;font-size:0.8rem;padding:0.3rem 0 0.8rem">'
                    'Sin cambios significativos de precio esta semana.</div>',
                    unsafe_allow_html=True)
            else:
                for _c1 in _cambios1[:8]:
                    _arr1 = "▲" if _c1["pct"] > 0 else "▼"
                    _clr1 = "#EF4444" if _c1["pct"] > 0 else "#16A34A"
                    st.markdown(f"""
                    <div style="display:flex;align-items:center;gap:0.8rem;
                                background:#FAFAFA;border-radius:9px;
                                padding:0.55rem 0.85rem;margin-bottom:0.4rem;
                                border-left:4px solid {_clr1}">
                      <div style="flex:1;min-width:0">
                        <div style="font-size:0.77rem;font-weight:700;color:#111827;
                                    word-break:break-word">{_c1['sku']}</div>
                        <div style="font-size:0.69rem;color:#6B7280">{_c1['cadena']}</div>
                      </div>
                      <div style="text-align:right;white-space:nowrap;flex-shrink:0">
                        <span style="font-size:0.88rem;font-weight:800;
                                     color:{_clr1}">{_arr1} {abs(_c1['pct']):.1f}%</span><br>
                        <span style="font-size:0.68rem;color:#9CA3AF">
                          ${_c1['viejo']:,.0f} → ${_c1['nuevo']:,.0f}
                        </span>
                      </div>
                    </div>""", unsafe_allow_html=True)

        # ── Columna derecha: top ofertas ───────────────────────────────────
        with _cn_r:
            st.markdown('<div class="chart-note">🏷️ Top ofertas activas esta semana</div>',
                        unsafe_allow_html=True)
            if not _top_of1:
                st.markdown(
                    '<div style="color:#9CA3AF;font-size:0.8rem;padding:0.3rem 0 0.8rem">'
                    'Sin ofertas activas esta semana.</div>',
                    unsafe_allow_html=True)
            else:
                _MEDALS = ["🥇","🥈","🥉"]
                for _i1, _o1 in enumerate(_top_of1):
                    _is_dest1 = _o1["Marca_raw"] in ("Zuelo","Oliovita")
                    _bdg1 = ("⭐" if _is_dest1 and _i1 >= 3
                             else _MEDALS[_i1] if _i1 < 3 else "⭐")
                    _clr_border1 = (COLORES_CAT.get(_o1["Marca_raw"],"#3B82F6")
                                    if _is_dest1 else "#3B82F6")
                    st.markdown(f"""
                    <div style="background:#FFFFFF;border-radius:10px;
                                padding:0.8rem 1rem;margin-bottom:0.55rem;
                                border-left:4px solid {_clr_border1};
                                box-shadow:0 1px 5px rgba(0,0,0,0.08)">
                      <div style="font-size:0.82rem;font-weight:700;color:#111827;
                                  margin-bottom:0.5rem;line-height:1.3">
                        {_bdg1} {_o1['SKU_canonico'][:55]}
                      </div>
                      <div style="display:flex;gap:1.8rem;flex-wrap:wrap;
                                  align-items:flex-end">
                        <div>
                          <div style="font-size:0.65rem;color:#374151;
                                      text-transform:uppercase;letter-spacing:0.5px">
                            Precio oferta
                          </div>
                          <div style="font-size:1.15rem;font-weight:800;color:#111827">
                            ${_o1['pof']:,.0f}
                          </div>
                        </div>
                        <div>
                          <div style="font-size:0.65rem;color:#374151;
                                      text-transform:uppercase;letter-spacing:0.5px">
                            Precio góndola
                          </div>
                          <div style="font-size:1.15rem;font-weight:800;color:#111827">
                            ${_o1['pg']:,.0f}
                          </div>
                        </div>
                        <div>
                          <div style="font-size:0.65rem;color:#374151;
                                      text-transform:uppercase;letter-spacing:0.5px">
                            Descuento
                          </div>
                          <div style="font-size:1.15rem;font-weight:800;color:#111827">
                            -{_o1['desc']:.0f}%
                          </div>
                        </div>
                      </div>
                      <div style="font-size:0.71rem;color:#374151;margin-top:0.45rem">
                        🏪 {_o1['Cadena']} &nbsp;·&nbsp; {_ult1}
                      </div>
                    </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Gráficos principales ─────────────────────────────────────────────
    _fc1, _ = st.columns([2, 5])
    with _fc1:
        dff1, _ = gram_filter("gram_tab1")

    col_l, col_r = st.columns([3, 2], gap="large")

    with col_l:
        st.markdown('<div class="chart-title">Precio de góndola promedio por cadena</div>',
                    unsafe_allow_html=True)
        df_c = (dff1.groupby("Cadena")["Precio"].mean()
                    .reset_index().sort_values("Precio"))
        fig = hbar(
            df_x=df_c["Precio"].tolist(),
            df_y=df_c["Cadena"].tolist(),
            colores=[cc(c) for c in df_c["Cadena"]],
            textos=[f"${v:,.0f}" for v in df_c["Precio"]],
            titulo_x="Precio promedio ($)",
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        st.markdown('<div class="chart-title">Productos por cadena</div>',
                    unsafe_allow_html=True)
        df_pie = dff1.groupby("Cadena").size().reset_index(name="n")
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
        st.plotly_chart(fig, use_container_width=True)

    # Box distribución por cadena — IQR estándar, escala enfocada
    st.markdown('<div class="chart-title">Distribución de precios de góndola por cadena</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="chart-note">Caja = rango intercuartil (Q1–Q3) · Línea central = mediana · Bigotes = 1.5×IQR</div>',
                unsafe_allow_html=True)
    _precios_box = dff1["Precio"].dropna()
    _p10 = float(_precios_box.quantile(0.10)) if not _precios_box.empty else 0
    _p90 = float(_precios_box.quantile(0.90)) if not _precios_box.empty else 30000
    fig = go.Figure()
    for cadena in sorted(dff1["Cadena"].unique()):
        sub = dff1[dff1["Cadena"]==cadena]["Precio"].dropna()
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
    st.plotly_chart(fig, use_container_width=True)

    # ── Movimientos de catálogo ───────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="chart-title">🆕 Movimientos de catálogo</div>', unsafe_allow_html=True)

    _mov_fechas_ord = sorted(df_full["Fecha"].unique())

    _mov_fa, _mov_fb, _mov_fc, _ = st.columns([2, 2, 2, 1])
    with _mov_fa:
        _mov_gran = st.selectbox("Granularidad", ["Semanal", "Mensual"], key="mov_gran")

    if _mov_gran == "Semanal":
        _mov_opciones = _mov_fechas_ord
        _mov_fmt = lambda f: pd.Timestamp(f).strftime("%d/%m/%Y")
    else:
        _meses_vistos: dict = {}
        for _mf in _mov_fechas_ord:
            _mk = pd.Timestamp(_mf).strftime("%b %Y")
            if _mk not in _meses_vistos:
                _meses_vistos[_mk] = _mf
        _mov_opciones = list(_meses_vistos.values())
        _mov_fmt = lambda f: pd.Timestamp(f).strftime("%b %Y")

    _mov_labels = [_mov_fmt(f) for f in _mov_opciones]

    if len(_mov_opciones) < 2:
        st.info("Se necesitan al menos 2 períodos de datos para este análisis.")
    else:
        with _mov_fb:
            _mov_ini_lbl = st.selectbox("Desde", _mov_labels, index=0, key="mov_ini")
        with _mov_fc:
            _mov_fin_lbl = st.selectbox("Hasta", _mov_labels,
                                         index=len(_mov_labels)-1, key="mov_fin")

        _mov_ini_f = _mov_opciones[_mov_labels.index(_mov_ini_lbl)]
        _mov_fin_f = _mov_opciones[_mov_labels.index(_mov_fin_lbl)]

        if _mov_gran == "Mensual":
            _ini_ts = pd.Timestamp(_mov_ini_f)
            _fin_ts = pd.Timestamp(_mov_fin_f)
            _ff_ini = [f for f in _mov_fechas_ord
                       if pd.Timestamp(f).year == _ini_ts.year and pd.Timestamp(f).month == _ini_ts.month]
            _ff_fin = [f for f in _mov_fechas_ord
                       if pd.Timestamp(f).year == _fin_ts.year and pd.Timestamp(f).month == _fin_ts.month]
            _f_pri_mov = min(_ff_ini) if _ff_ini else _mov_ini_f
            _f_ult_mov = max(_ff_fin) if _ff_fin else _mov_fin_f
        else:
            _f_pri_mov, _f_ult_mov = _mov_ini_f, _mov_fin_f

        st.markdown(
            f"<div class='chart-note'>Comparando snapshot de <b>{_mov_fmt(_f_pri_mov)}</b> "
            f"vs <b>{_mov_fmt(_f_ult_mov)}</b></div>",
            unsafe_allow_html=True)

        if _f_pri_mov == _f_ult_mov:
            st.info("El período de inicio y fin son iguales. Elegí períodos distintos.")
        else:
            _skus_primera = set(zip(df_full[df_full["Fecha"]==_f_pri_mov]["SKU_canonico"],
                                     df_full[df_full["Fecha"]==_f_pri_mov]["Cadena"]))
            _skus_ultima  = set(zip(df_full[df_full["Fecha"]==_f_ult_mov]["SKU_canonico"],
                                     df_full[df_full["Fecha"]==_f_ult_mov]["Cadena"]))

            _entradas_mov = sorted([{"SKU": s, "Cadena": c,
                                       "Primera vez visto": pd.Timestamp(_f_ult_mov).strftime("%d/%m/%Y")}
                                      for s,c in _skus_ultima - _skus_primera],
                                     key=lambda x: x["SKU"])[:20]
            _salidas_mov  = sorted([{"SKU": s, "Cadena": c,
                                       "Última vez visto": pd.Timestamp(_f_pri_mov).strftime("%d/%m/%Y")}
                                      for s,c in _skus_primera - _skus_ultima],
                                     key=lambda x: x["SKU"])[:20]

            _cmov_l, _cmov_r = st.columns(2, gap="large")
            with _cmov_l:
                st.markdown(f"<span style='color:#16A34A;font-weight:700;font-size:0.85rem'>"
                            f"✅ Entradas ({len(_entradas_mov)})</span>",
                            unsafe_allow_html=True)
                if _entradas_mov:
                    st.dataframe(pd.DataFrame(_entradas_mov), use_container_width=True,
                                 height=min(400, len(_entradas_mov)*38+60), hide_index=True)
                else:
                    st.info("Sin entradas en este período.")
            with _cmov_r:
                st.markdown(f"<span style='color:#DC2626;font-weight:700;font-size:0.85rem'>"
                            f"❌ Salidas ({len(_salidas_mov)})</span>",
                            unsafe_allow_html=True)
                if _salidas_mov:
                    st.dataframe(pd.DataFrame(_salidas_mov), use_container_width=True,
                                 height=min(400, len(_salidas_mov)*38+60), hide_index=True)
                else:
                    st.info("Sin salidas en este período.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 2 · POR CADENA
# ══════════════════════════════════════════════════════════════════════════
with tab2:
    _fc2, _ = st.columns([2, 5])
    with _fc2:
        dff2, _ = gram_filter("gram_tab2")

    st.markdown('<div class="chart-title">Precio de góndola promedio — Cadena × Marca</div>',
                unsafe_allow_html=True)
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
        st.plotly_chart(fig, use_container_width=True)

    st.markdown('<div class="chart-title">Precio de góndola mínimo por cadena y marca</div>',
                unsafe_allow_html=True)
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
    st.plotly_chart(fig, use_container_width=True)

    # ── Share of shelf implícito ──────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="chart-title">Share of shelf implícito por cadena</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="chart-note">% de SKUs de cada marca sobre el total de SKUs de la categoría en cada cadena</div>',
                unsafe_allow_html=True)

    _shelf_src = df_full[df_full["Cadena"].isin(cadenas_sel)].copy()
    _shelf_src["Marca_shelf"] = _shelf_src["Marca"].apply(
        lambda m: m if m in (MARCAS_DESTACADAS | {"Regionales/Import.", "Marca Propia"}) else "Otras"
    )
    _total_skus_cad = (_shelf_src.groupby("Cadena")["SKU_canonico"]
                       .nunique().reset_index(name="total"))
    _skus_marca_cad = (_shelf_src.groupby(["Cadena","Marca_shelf"])["SKU_canonico"]
                       .nunique().reset_index(name="n_skus"))
    _shelf = _skus_marca_cad.merge(_total_skus_cad, on="Cadena")
    _shelf["share_pct"] = _shelf["n_skus"] / _shelf["total"] * 100

    _shelf_orden_cad = (_total_skus_cad.sort_values("total", ascending=False)["Cadena"].tolist())
    _shelf_orden_marc = list(dict.fromkeys(
        m for m in (orden_cats + ["Otras"]) if m in _shelf["Marca_shelf"].unique()
    ))
    _shelf["Marca_shelf"] = pd.Categorical(_shelf["Marca_shelf"],
                                            categories=_shelf_orden_marc, ordered=True)
    _shelf["Cadena"] = pd.Categorical(_shelf["Cadena"],
                                       categories=_shelf_orden_cad, ordered=True)
    _shelf = _shelf.sort_values(["Cadena","Marca_shelf"])

    if not _shelf.empty:
        _fig_shelf = px.bar(
            _shelf, x="share_pct", y="Cadena", color="Marca_shelf",
            orientation="h", barmode="stack",
            color_discrete_map={**COLORES_CAT, "Otras":"#D1D5DB"},
            labels={"share_pct":"Share de SKUs (%)","Cadena":"","Marca_shelf":"Marca"},
            height=max(280, len(_shelf_orden_cad)*45+80),
            category_orders={"Cadena": _shelf_orden_cad, "Marca_shelf": _shelf_orden_marc},
        )
        _fig_shelf.update_layout(
            **BASE,
            xaxis=dict(ticksuffix="%", range=[0,105], tickfont=dict(size=12,color="#111827")),
            yaxis=dict(tickfont=dict(size=13,color="#111827")),
        )
        st.plotly_chart(_fig_shelf, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════
# TAB 3 · POR MARCA
# ══════════════════════════════════════════════════════════════════════════
with tab3:
    # Filtros en la misma fila para que ambos gráficos arranquen al mismo nivel
    _fc3a, _fc3b, _ = st.columns([2, 2, 3])
    with _fc3a:
        dff3, _ = gram_filter("gram_tab3")
    with _fc3b:
        cadenas_pie3 = ["Todas las cadenas"] + sorted(dff3["Cadena"].unique().tolist())
        cadena_pie3  = st.selectbox("🏪 Cadena (torta)", cadenas_pie3, key="cadena_pie3")

    col_l, col_r = st.columns([3, 2], gap="large")

    with col_l:
        st.markdown('<div class="chart-title">Precio de góndola promedio por marca</div>',
                    unsafe_allow_html=True)
        df_m = (dff3.groupby("Marca")
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
        st.plotly_chart(fig, use_container_width=True)

    with col_r:
        st.markdown('<div class="chart-title">Cantidad de productos por marca</div>',
                    unsafe_allow_html=True)
        src_pie3 = dff3 if cadena_pie3 == "Todas las cadenas" else dff3[dff3["Cadena"]==cadena_pie3]
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
        st.plotly_chart(fig, use_container_width=True)

    # Heatmap presencia: marca × cadena (SKUs canónicos únicos)
    # Usamos df_full filtrado solo por cadena y marca (sin periodo ni gramaje)
    # para contar el catálogo real, independientemente de los filtros de semana y tamaño
    st.markdown('<div class="chart-title">Presencia por marca y cadena — SKUs distintos</div>',
                unsafe_allow_html=True)
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
        st.plotly_chart(fig, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════
# TAB 4 · EVOLUCIÓN
# ══════════════════════════════════════════════════════════════════════════
with tab4:
    if n_sem < 2:
        st.info("📅 **Solo hay una semana cargada.** "
                "Ejecutá `python scraper.py` la semana que viene para ver la evolución.")

    _fc4a, _fc4b, _ = st.columns([2, 2, 3])
    with _fc4a:
        dff4, _ = gram_filter("gram_tab4")
    with _fc4b:
        _usar_real4 = st.checkbox("Mostrar en pesos reales (ajustado por inflación)",
                                   key="toggle_real_ev4", value=False)

    orden_per = sorted(df_full["Periodo"].unique(),
                       key=lambda p: df_full[df_full["Periodo"]==p]["Fecha"].min())

    _col_precio4 = "Precio_real" if _usar_real4 else "Precio"
    _lbl_precio4 = "Precio real promedio ($, base semana actual)" if _usar_real4 else "Precio promedio góndola ($)"

    st.markdown(
        f'<div class="chart-title">Evolución precio de góndola promedio por marca'
        f'{"  ·  <span style=\'color:#7C3AED\'>ajustado por inflación</span>" if _usar_real4 else ""}</div>',
        unsafe_allow_html=True)
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
                      xaxis=dict(tickfont=dict(size=12,color="#111827")))
    st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════
# TAB 5 · OFERTAS
# ══════════════════════════════════════════════════════════════════════════
with tab5:
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

    if df_of5.empty:
        st.info("🏷️ No hay productos en oferta con los filtros actuales.")
    else:
        # KPIs
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#7C2D12,#C2410C);border-radius:14px;
                    padding:1.2rem 2rem;margin-bottom:1.2rem;display:flex;gap:3rem;align-items:center">
          <div>
            <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                        letter-spacing:1px;color:rgba(255,255,255,0.6)">Productos en oferta</div>
            <div style="font-size:2rem;font-weight:800;color:#fff">{len(df_of5):,}</div>
          </div>
          <div>
            <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                        letter-spacing:1px;color:rgba(255,255,255,0.6)">Descuento promedio</div>
            <div style="font-size:2rem;font-weight:800;color:#fff">{df_of5["Descuento_pct"].mean():.0f}%</div>
          </div>
          <div>
            <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                        letter-spacing:1px;color:rgba(255,255,255,0.6)">Precio oferta prom.</div>
            <div style="font-size:2rem;font-weight:800;color:#fff">${df_of5["Precio_oferta"].mean():,.0f}</div>
          </div>
          <div>
            <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                        letter-spacing:1px;color:rgba(255,255,255,0.6)">Precio góndola prom.</div>
            <div style="font-size:2rem;font-weight:800;color:rgba(255,255,255,0.7)">${df_of5["Precio"].mean():,.0f}</div>
          </div>
        </div>
        """, unsafe_allow_html=True)

        col_l, col_r = st.columns([1,1], gap="large")

        with col_l:
            st.markdown('<div class="chart-title">Descuento promedio por cadena</div>',
                        unsafe_allow_html=True)
            df_desc_c = (df_of5.groupby("Cadena")["Descuento_pct"].mean()
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
            st.plotly_chart(fig, use_container_width=True)

        with col_r:
            st.markdown('<div class="chart-title">Cantidad de ofertas por cadena</div>',
                        unsafe_allow_html=True)
            df_of_cnt = df_of5.groupby("Cadena").size().reset_index(name="n")
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
            st.plotly_chart(fig, use_container_width=True)

        # Góndola vs oferta por marca
        st.markdown('<div class="chart-title">Precio góndola vs precio oferta por marca</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="chart-note">La diferencia entre las barras = ahorro de la oferta</div>',
                    unsafe_allow_html=True)
        df_gvof = (df_of5.groupby("Marca")
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
        st.plotly_chart(fig, use_container_width=True)

        # Ofertas en el tiempo por marca y por cadena
        _n_per_of5 = df_of5["Periodo"].nunique()
        if _n_per_of5 >= 2:
            col_ol, col_or = st.columns(2, gap="large")

            with col_ol:
                st.markdown('<div class="chart-title">Cantidad de ofertas por período · Marca</div>',
                            unsafe_allow_html=True)
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
                st.plotly_chart(fig, use_container_width=True)

            with col_or:
                st.markdown('<div class="chart-title">Cantidad de ofertas por período · Cadena</div>',
                            unsafe_allow_html=True)
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
                st.plotly_chart(fig, use_container_width=True)

        # Top 20 mejores descuentos
        st.markdown('<div class="chart-title">Top 20 · Mejores descuentos del período</div>',
                    unsafe_allow_html=True)
        df_top = (df_of5.sort_values("Descuento_pct", ascending=False)
                        .head(20)[["Cadena","Marca","Producto","Gramaje",
                                   "Precio","Precio_oferta","Descuento_pct"]]
                        .copy())
        df_top.columns = ["Cadena","Marca","Producto","Gramaje",
                          "Precio góndola ($)","Precio oferta ($)","Descuento %"]
        st.dataframe(df_top, use_container_width=True, height=400,
            column_config={
                "Precio góndola ($)":st.column_config.NumberColumn(format="$%d"),
                "Precio oferta ($)": st.column_config.NumberColumn(format="$%d"),
                "Descuento %":       st.column_config.NumberColumn(format="%.0f%%"),
            },
            hide_index=True,
        )

        # ── Presencia de ofertas Oliovita & Zuelo × período ──────────────────
        _MARCAS_OF2 = {"Oliovita", "Zuelo"}
        _dest_periodos = _periodos_of_sel if _periodos_of_sel else _todos_periodos_of

        # Filtros locales: cadena y granularidad
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
                "Granularidad", ["Semanal", "Mensual"],
                key="of2_gran", label_visibility="collapsed",
            )
        _cadenas_of2_act = _cadenas_of2_sel if _cadenas_of2_sel else _cadenas_of2_disp

        st.markdown('<div class="chart-title">Presencia de ofertas · Oliovita & Zuelo</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="chart-note">✓ = hubo oferta ese período · — = sin oferta</div>',
                    unsafe_allow_html=True)

        _df_dest = df_full[
            df_full["Marca_raw"].isin(_MARCAS_OF2) &
            df_full["Periodo"].isin(_dest_periodos) &
            df_full["Cadena"].isin(_cadenas_of2_act) &
            (df_full["Gramaje"].isna() | df_full["Gramaje"].isin(gram_sel))
        ].copy()

        if not _df_dest.empty:
            # Aplicar granularidad
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
            _hmap_num = _hmap_df.replace({"✓": 1, "—": 0}).astype(float)

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
            st.plotly_chart(fig_oh, use_container_width=True)
        else:
            st.info("No hay SKUs de Oliovita o Zuelo con los filtros seleccionados.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 10 · BASE (tabla completa)
# ══════════════════════════════════════════════════════════════════════════
with tab10:
    col_b, col_s = st.columns([3, 1])
    with col_b:
        busqueda = st.text_input("🔍 Buscar producto, marca o cadena",
                                  placeholder="ej: Nucete, Lira, Jumbo, 500 ml…",
                                  label_visibility="collapsed")
    with col_s:
        orden_col = st.selectbox("Ordenar por",
                                  ["Precio ($)","Precio/Litro ($)","Cadena","Marca"],
                                  label_visibility="collapsed")

    df_tabla = dff.copy()
    if busqueda:
        m = (df_tabla["Producto"].str.contains(busqueda, case=False, na=False) |
             df_tabla["Marca"].str.contains(busqueda, case=False, na=False)    |
             df_tabla["Cadena"].str.contains(busqueda, case=False, na=False))
        df_tabla = df_tabla[m]

    col_ord = {"Precio ($)":"Precio","Precio/Litro ($)":"Precio_litro",
               "Cadena":"Cadena","Marca":"Marca"}
    df_tabla = df_tabla.sort_values(col_ord[orden_col], na_position="last")

    df_show = df_tabla[["Periodo","Cadena","Marca","Producto",
                          "Gramaje","Precio","Precio_litro","En_oferta"]].copy()
    df_show.columns = ["Semana","Cadena","Marca","Producto",
                        "Gramaje","Precio góndola ($)","Precio/Litro ($)","En oferta"]

    st.markdown(f"**{len(df_show):,} productos** · precios de góndola")
    st.dataframe(df_show, use_container_width=True, height=530,
        column_config={
            "Precio góndola ($)":st.column_config.NumberColumn(format="$%d"),
            "Precio/Litro ($)":  st.column_config.NumberColumn(format="$%d"),
            "En oferta":         st.column_config.CheckboxColumn(),
        },
        hide_index=True,
    )
    csv = df_show.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️  Exportar CSV", csv, "aceite_gondola.csv",
                        "text/csv", use_container_width=False)

# ══════════════════════════════════════════════════════════════════════════
# TAB 3 (continuación) · DETALLE POR MARCA
# ══════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown("<hr style='border:none;border-top:2px solid #E5E7EB;margin:1.5rem 0 1rem'>",
                unsafe_allow_html=True)
    st.markdown('<div class="chart-title">🔍 Detalle de marca</div>', unsafe_allow_html=True)
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
            st.plotly_chart(fig, use_container_width=True)

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
            st.plotly_chart(fig, use_container_width=True)

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
                st.plotly_chart(fig, use_container_width=True)

        # Tabla detallada
        st.markdown('<div class="chart-title">Detalle completo de registros</div>',
                    unsafe_allow_html=True)
        df7_show = (df7_sku_filter[["Periodo","Cadena","SKU_canonico","Gramaje",
                                    "Precio","Precio_oferta","Descuento_pct","En_oferta"]]
                    .sort_values(["Periodo","Cadena","Precio"]).copy())
        df7_show.columns = ["Semana","Cadena","SKU","Gramaje",
                             "Precio góndola ($)","Precio oferta ($)","Descuento %","En oferta"]
        st.dataframe(df7_show, use_container_width=True, height=400,
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
            st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════
# TAB 6 · COMPARATIVA DE SKUs
# ══════════════════════════════════════════════════════════════════════════
with tab6:
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
        st.plotly_chart(fig, use_container_width=True)

        # Mini tabla: ¿hubo oferta ese período?
        if orden_per8:
            st.markdown('<div class="chart-title">Semanas en oferta</div>',
                        unsafe_allow_html=True)
            _of_rows = []
            for _pe in orden_per8:
                _of_rows.append({
                    "Período": _pe,
                    lbl1[:35]: "✓" if _pe in _of_pers1 else "—",
                    lbl2[:35]: "✓" if _pe in _of_pers2 else "—",
                })
            _of_tbl = pd.DataFrame(_of_rows)
            st.dataframe(_of_tbl, use_container_width=True,
                         height=min(400, len(orden_per8)*38+60),
                         hide_index=True)

        # Precio por cadena × período — heatmap para cada SKU
        st.markdown('<div class="chart-title">Precio por cadena y período</div>',
                    unsafe_allow_html=True)
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
            st.plotly_chart(_fig_cp, use_container_width=True)

        _col_cp1, _col_cp2 = st.columns(2, gap="large")
        with _col_cp1:
            _cad_per_heatmap(sku_c1, lbl1, "#065F46")
        with _col_cp2:
            _cad_per_heatmap(sku_c2, lbl2, "#7C1D2D")

        # Tabla comparativa por cadena en el último período disponible
        st.markdown('<div class="chart-title">Precio por cadena · último período disponible</div>',
                    unsafe_allow_html=True)
        ult_per8 = orden_per8[-1] if orden_per8 else None
        if ult_per8:
            df_cmp_tbl = dff[(dff["Periodo"]==ult_per8) &
                              (dff["SKU_canonico"].isin([sku_c1, sku_c2]))]
            df_cmp_tbl = df_cmp_tbl[["Cadena","SKU_canonico","Gramaje","Precio","En_oferta"]].copy()
            df_cmp_tbl.columns = ["Cadena","SKU","Gramaje","Precio góndola ($)","En oferta"]
            st.dataframe(df_cmp_tbl.sort_values(["SKU","Cadena"]),
                use_container_width=True, height=300,
                column_config={
                    "Precio góndola ($)":st.column_config.NumberColumn(format="$%d"),
                    "En oferta":         st.column_config.CheckboxColumn(),
                },
                hide_index=True,
            )

        # Diferencia de precio entre los dos SKUs por período
        if df_comp["Periodo"].nunique() > 1:
            st.markdown('<div class="chart-title">Diferencia de precio entre SKUs por período</div>',
                        unsafe_allow_html=True)
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
                st.plotly_chart(fig, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════
# TAB 7 · MI MARCA
# ══════════════════════════════════════════════════════════════════════════
with tab7:
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
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">📍 Posicionamiento de precio relativo</div>',
                    unsafe_allow_html=True)

        _mm_pl_marca = _mm_dff.dropna(subset=["Precio_litro"])
        _mm_pl_resto = _mm_resto.dropna(subset=["Precio_litro"])

        _mm_avg_marca = _mm_pl_marca["Precio_litro"].mean() if not _mm_pl_marca.empty else 0
        _mm_avg_merc  = _mm_pl_resto["Precio_litro"].mean() if not _mm_pl_resto.empty else 0
        _mm_prima     = ((_mm_avg_marca / _mm_avg_merc) - 1) * 100 if _mm_avg_merc > 0 else 0
        _mm_cadenas   = _mm_dff["Cadena"].nunique()

        _mm_k1, _mm_k2, _mm_k3, _mm_k4 = st.columns(4)
        _mm_kpis = [
            ("orange", "$/L marca",       f"${_mm_avg_marca:,.0f}", f"promedio {_mm_sel}"),
            ("",       "$/L mercado",     f"${_mm_avg_merc:,.0f}",  "promedio resto marcas"),
            ("red" if _mm_prima > 0 else "green",
                       "Prima vs mercado",
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
                st.plotly_chart(fig_mm_bar, use_container_width=True)

        # ── B) Presencia por cadena — heatmap SKU × cadena ───────────────
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="chart-title">🏪 Presencia por cadena</div>',
                    unsafe_allow_html=True)

        _mm_heat_src = df_full[df_full["Marca_raw"] == _mm_sel]
        if _mm_sku_sel != "Todos los SKUs":
            _mm_heat_src = _mm_heat_src[_mm_heat_src["SKU_canonico"] == _mm_sku_sel]
        _mm_heat_src = _mm_heat_src.copy()
        _mm_pres_piv = (_mm_heat_src.groupby(["SKU_canonico","Cadena"])["Precio"]
                        .mean().round(0).unstack("Cadena"))
        if not _mm_pres_piv.empty:
            _mm_text_h = [[f"${v:,.0f}" if not pd.isna(v) else "—" for v in row]
                          for row in _mm_pres_piv.values]
            fig_mm_h = go.Figure(go.Heatmap(
                z=_mm_pres_piv.values,
                x=_mm_pres_piv.columns.tolist(),
                y=_mm_pres_piv.index.tolist(),
                colorscale="Blues",
                text=_mm_text_h, texttemplate="%{text}",
                textfont=dict(size=11, color="#111827"),
                colorbar=dict(title="$", tickprefix="$", tickformat=","),
            ))
            fig_mm_h.update_layout(
                **BASE, height=max(280, len(_mm_pres_piv)*42+80),
                xaxis=dict(tickfont=dict(size=12,color="#111827"), side="top"),
                yaxis=dict(tickfont=dict(size=11,color="#111827")),
            )
            st.plotly_chart(fig_mm_h, use_container_width=True)

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
        st.plotly_chart(fig_mm_ev, use_container_width=True)

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
            _mm_pct_of_marca = (_mm_of_src["En_oferta"].mean() * 100)
            st.markdown(
                f"<div style='background:#FFF7ED;border-radius:10px;padding:0.8rem 1rem;"
                f"border-left:3px solid #F97316'>"
                f"<span style='font-size:0.65rem;text-transform:uppercase;color:#9CA3AF'>"
                f"% registros en oferta · {_mm_sel}</span><br>"
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
            st.dataframe(_mm_of_tbl, use_container_width=True,
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
            st.dataframe(_mm_dist_df, use_container_width=True,
                         height=min(500, len(_mm_dist_df)*38+60), hide_index=True)
        else:
            st.info("Sin datos de distribución para esta marca.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 8 · INSIGHTS
# ══════════════════════════════════════════════════════════════════════════
with tab8:

    def _insight_card(icon, titulo, valor, detalle, color="#0F3460"):
        st.markdown(f"""
        <div style="background:#FFFFFF;border-radius:12px;padding:0.9rem 1.1rem;
                    border-left:4px solid {color};
                    box-shadow:0 1px 6px rgba(0,0,0,0.07)">
          <div style="font-size:1.2rem;margin-bottom:0.2rem">{icon}</div>
          <div style="font-size:0.65rem;color:#6B7280;text-transform:uppercase;
                      letter-spacing:0.5px;margin-bottom:0.12rem">{titulo}</div>
          <div style="font-size:1.1rem;font-weight:800;color:#111827;
                      line-height:1.2;margin-bottom:0.18rem">{valor}</div>
          <div style="font-size:0.71rem;color:#374151;line-height:1.4">{detalle}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("""
    <div style="font-size:0.75rem;color:#6B7280;margin-bottom:1.2rem">
      Análisis estadístico calculado sobre los datos con los filtros activos del panel lateral.
    </div>""", unsafe_allow_html=True)

    # ── cómputos ──────────────────────────────────────────────────────────
    _ins = dff.copy()
    _ins_pl = _ins.dropna(subset=["Precio_litro"])

    _by_marca = (_ins.groupby("Marca_raw").agg(
        precio_medio  =("Precio","mean"),
        precio_mediana=("Precio","median"),
        precio_min    =("Precio","min"),
        precio_max    =("Precio","max"),
        precio_std    =("Precio","std"),
        n             =("Precio","count"),
    ).assign(cv=lambda d: d["precio_std"]/d["precio_medio"]*100).reset_index())

    # Precio/litro correcto:
    # 1) media por (Marca, SKU, Cadena) → cada combo cuenta como 1
    # 2) media por (Marca, SKU) promediando cadenas
    # 3) media por Marca promediando SKUs
    _pl_s1 = (_ins_pl
              .groupby(["Marca_raw","SKU_canonico","Cadena"])["Precio_litro"]
              .mean().reset_index())
    _pl_s2 = (_pl_s1
              .groupby(["Marca_raw","SKU_canonico"])["Precio_litro"]
              .mean().reset_index())
    _by_marca_pl = (_pl_s2
                    .groupby("Marca_raw")["Precio_litro"]
                    .mean().reset_index()
                    .rename(columns={"Precio_litro":"pl_medio"}))
    _by_marca = _by_marca.merge(_by_marca_pl, on="Marca_raw", how="left")

    _of_ins   = df_full[df_full["Cadena"].isin(cadenas_sel)].copy()
    _of_rate  = (_of_ins.groupby("Marca_raw")
                 .apply(lambda d: d["En_oferta"].mean()*100)
                 .reset_index(name="pct_oferta"))
    _desc_m   = (_of_ins[_of_ins["En_oferta"]]
                 .groupby("Marca_raw")["Descuento_pct"]
                 .mean().reset_index(name="desc_medio"))
    _by_marca = _by_marca.merge(_of_rate, on="Marca_raw", how="left").merge(_desc_m, on="Marca_raw", how="left")

    _by_cadena = (_ins.groupby("Cadena").agg(precio_medio=("Precio","mean"), n=("Precio","count")).reset_index())
    # Precio/litro por cadena: media de SKUs (cada SKU cuenta como 1 por cadena)
    _cadena_pl_s1 = (_ins_pl.groupby(["Cadena","SKU_canonico"])["Precio_litro"]
                     .mean().reset_index())
    _by_cadena_pl = (_cadena_pl_s1.groupby("Cadena")["Precio_litro"]
                     .mean().reset_index(name="pl_medio"))
    _by_cadena = _by_cadena.merge(_by_cadena_pl, on="Cadena", how="left")

    _dest_ins   = _by_marca[_by_marca["Marca_raw"].isin(MARCAS_DESTACADAS) & (_by_marca["n"]>=3)]
    _marca_cara   = _dest_ins.loc[_dest_ins["pl_medio"].idxmax()]   if not _dest_ins.empty else None
    _marca_barata = _dest_ins.loc[_dest_ins["pl_medio"].idxmin()]   if not _dest_ins.empty else None
    _marca_estable= _dest_ins.loc[_dest_ins["cv"].idxmin()]         if not _dest_ins.empty else None
    _marca_volat  = _dest_ins.loc[_dest_ins["cv"].idxmax()]         if not _dest_ins.empty else None
    _marca_of_tbl = (_by_marca[_by_marca["Marca_raw"].isin(MARCAS_DESTACADAS)]
                     .dropna(subset=["pct_oferta"]).sort_values("pct_oferta", ascending=False))
    _cadena_barata = _by_cadena.sort_values("pl_medio").iloc[0]  if not _by_cadena.empty else None
    _cadena_cara   = _by_cadena.sort_values("pl_medio").iloc[-1] if not _by_cadena.empty else None
    # SKU más barato: media de (SKU × cadena), cada cadena cuenta como 1
    _sku_pl_s1 = (_ins_pl.groupby(["SKU_canonico","Cadena"])["Precio_litro"]
                  .mean().reset_index())
    _sku_pl = (_sku_pl_s1.groupby("SKU_canonico")
               .agg(pl=("Precio_litro","mean"), n=("Cadena","count"))
               .reset_index().query("n>=2").sort_values("pl"))
    _sku_barato = _sku_pl.iloc[0]  if not _sku_pl.empty else None
    _sku_caro   = _sku_pl.iloc[-1] if not _sku_pl.empty else None
    _brecha_pct = ((_marca_cara["pl_medio"]/_marca_barata["pl_medio"]-1)*100
                   if _marca_cara is not None and _marca_barata is not None else None)
    _brecha_prod_pct = ((_sku_caro["pl"]/_sku_barato["pl"]-1)*100
                        if _sku_caro is not None and _sku_barato is not None else None)

    # ── FILA 1: precio/litro por gramaje (250 ml y 500 ml) ────────────────
    st.markdown('<div class="chart-title">💰 Precio por litro · 250 ml y 500 ml</div>', unsafe_allow_html=True)

    def _sku_extremos_gramaje(df_pl, df_all, gramaje_label):
        _g = (df_pl[df_pl["Gramaje"] == gramaje_label]
              .groupby(["SKU_canonico", "Cadena"])["Precio_litro"].mean().reset_index())
        _g2 = (_g.groupby("SKU_canonico")
                 .agg(pl=("Precio_litro", "mean"), n=("Cadena", "count"))
                 .reset_index().sort_values("pl"))
        if _g2.empty:
            return None, None
        _gond = (df_all[df_all["Gramaje"] == gramaje_label]
                 .groupby("SKU_canonico")["Precio"].mean().reset_index()
                 .rename(columns={"Precio": "precio_gond"}))
        _g2 = _g2.merge(_gond, on="SKU_canonico", how="left")
        return _g2.iloc[0], _g2.iloc[-1]

    _b250, _c250 = _sku_extremos_gramaje(_ins_pl, _ins, "250 ml")
    _b500, _c500 = _sku_extremos_gramaje(_ins_pl, _ins, "500 ml")

    _c1a, _c1b, _c1c, _c1d = st.columns(4, gap="medium")
    with _c1a:
        if _b250 is not None:
            _insight_card("🏆", "Más barato · 250 ml",
                _b250["SKU_canonico"],
                f"Góndola ${_b250['precio_gond']:,.0f} · ${_b250['pl']:,.0f}/L · {int(_b250['n'])} cadena(s)",
                "#16A34A")
    with _c1b:
        if _c250 is not None:
            _insight_card("💎", "Más caro · 250 ml",
                _c250["SKU_canonico"],
                f"Góndola ${_c250['precio_gond']:,.0f} · ${_c250['pl']:,.0f}/L · {int(_c250['n'])} cadena(s)",
                "#7C3AED")
    with _c1c:
        if _b500 is not None:
            _insight_card("🏆", "Más barato · 500 ml",
                _b500["SKU_canonico"],
                f"Góndola ${_b500['precio_gond']:,.0f} · ${_b500['pl']:,.0f}/L · {int(_b500['n'])} cadena(s)",
                "#16A34A")
    with _c1d:
        if _c500 is not None:
            _insight_card("💎", "Más caro · 500 ml",
                _c500["SKU_canonico"],
                f"Góndola ${_c500['precio_gond']:,.0f} · ${_c500['pl']:,.0f}/L · {int(_c500['n'])} cadena(s)",
                "#7C3AED")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── FILA 2: estabilidad ───────────────────────────────────────────────
    st.markdown('<div class="chart-title">📉 Variabilidad de precios</div>', unsafe_allow_html=True)
    _c2a, _c2b, _c2c = st.columns(3, gap="medium")
    with _c2a:
        if _marca_estable is not None:
            _insight_card("🎯","Precio más estable",
                _marca_estable["Marca_raw"],
                f"CV {_marca_estable['cv']:.1f}% · rango ${_marca_estable['precio_min']:,.0f}–${_marca_estable['precio_max']:,.0f}",
                "#0369A1")
    with _c2b:
        if _marca_volat is not None:
            _insight_card("📊","Precio más variable",
                _marca_volat["Marca_raw"],
                f"CV {_marca_volat['cv']:.1f}% · rango ${_marca_volat['precio_min']:,.0f}–${_marca_volat['precio_max']:,.0f}",
                "#EA580C")
    with _c2c:
        _p25 = float(_ins["Precio"].quantile(0.25))
        _p75 = float(_ins["Precio"].quantile(0.75))
        _insight_card("📦","Rango IQR del mercado",
            f"${_p25:,.0f} – ${_p75:,.0f}",
            f"El 50% central de productos · mediana ${_ins['Precio'].median():,.0f}",
            "#374151")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── FILA 3: ofertas ───────────────────────────────────────────────────
    st.markdown('<div class="chart-title">🏷️ Comportamiento de ofertas</div>', unsafe_allow_html=True)
    _c3a, _c3b, _c3c = st.columns(3, gap="medium")
    with _c3a:
        if not _marca_of_tbl.empty:
            _mo = _marca_of_tbl.iloc[0]
            _insight_card("🔥","Marca con más ofertas",
                _mo["Marca_raw"],
                f"{_mo['pct_oferta']:.0f}% de registros en oferta · "
                f"dto. prom. {_mo.get('desc_medio',0):.0f}%",
                "#DC2626")
    with _c3b:
        _cadena_of = (_of_ins.groupby("Cadena")
                      .apply(lambda d: d["En_oferta"].mean()*100)
                      .reset_index(name="pct").sort_values("pct", ascending=False))
        if not _cadena_of.empty:
            _co = _cadena_of.iloc[0]
            _insight_card("🏪","Cadena con más ofertas",
                _co["Cadena"],
                f"{_co['pct']:.0f}% de sus productos están en oferta en el período",
                "#B45309")
    with _c3c:
        _desc_cad = (_of_ins[_of_ins["En_oferta"]]
                     .groupby("Cadena")["Descuento_pct"].mean()
                     .reset_index().sort_values("Descuento_pct", ascending=False))
        if not _desc_cad.empty:
            _dc = _desc_cad.iloc[0]
            _insight_card("💸","Mayor descuento promedio por cadena",
                _dc["Cadena"],
                f"Descuento medio {_dc['Descuento_pct']:.0f}% sobre precio góndola",
                "#065F46")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── FILA 4: cadenas + cobertura ───────────────────────────────────────
    st.markdown('<div class="chart-title">🏬 Cadenas y cobertura</div>', unsafe_allow_html=True)
    _c4a, _c4b, _c4c = st.columns(3, gap="medium")
    with _c4a:
        if _cadena_barata is not None:
            _insight_card("✅","Cadena más barata ($/L)",
                _cadena_barata["Cadena"],
                f"${_cadena_barata['pl_medio']:,.0f}/L promedio · {int(_cadena_barata['n'])} productos",
                "#16A34A")
    with _c4b:
        if _cadena_cara is not None:
            _insight_card("🏅","Cadena más cara ($/L)",
                _cadena_cara["Cadena"],
                f"${_cadena_cara['pl_medio']:,.0f}/L promedio · {int(_cadena_cara['n'])} productos",
                "#7C3AED")
    with _c4c:
        _cob = (_ins.groupby("Cadena")["SKU_canonico"].nunique()
                .reset_index(name="n").sort_values("n", ascending=False))
        if not _cob.empty:
            _mc = _cob.iloc[0]
            _insight_card("🗺️","Mayor catálogo de productos",
                _mc["Cadena"],
                f"{_mc['n']} SKUs distintos listados · mayor cobertura de mercado",
                "#0F3460")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── FILA 5: SKU destacado + variedad ─────────────────────────────────
    st.markdown('<div class="chart-title">🔍 Producto y variedad</div>', unsafe_allow_html=True)
    _c5a, _c5b, _c5c = st.columns(3, gap="medium")
    with _c5a:
        if _sku_barato is not None:
            _insight_card("💡","SKU con mejor precio/litro",
                _sku_barato["SKU_canonico"][:38],
                f"${_sku_barato['pl']:,.0f}/L · {int(_sku_barato['n'])} registros",
                "#0369A1")
    with _c5b:
        _smarca = (_ins.groupby("Marca_raw")["SKU_canonico"].nunique()
                   .reset_index(name="n_skus").sort_values("n_skus", ascending=False))
        if not _smarca.empty:
            _msm = _smarca.iloc[0]
            _insight_card("📦","Marca con más variedad",
                _msm["Marca_raw"],
                f"{_msm['n_skus']} SKUs distintos en las cadenas seleccionadas",
                "#0F3460")
    with _c5c:
        _total_skus = _ins["SKU_canonico"].nunique()
        _total_cad  = _ins["Cadena"].nunique()
        _insight_card("📡","Cobertura total del dataset",
            f"{_total_skus} SKUs únicos",
            f"En {_total_cad} cadenas · {len(_ins):,} registros totales en el período",
            "#374151")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Tabla resumen estadístico por marca ───────────────────────────────
    st.markdown('<div class="chart-title">📋 Tabla resumen estadístico por marca</div>', unsafe_allow_html=True)
    _tbl_ins = (_by_marca[_by_marca["n"]>=3]
                .sort_values("pl_medio", na_position="last")
                [["Marca_raw","n","precio_medio","precio_mediana",
                  "precio_min","precio_max","cv","pl_medio","pct_oferta","desc_medio"]]
                .copy())
    _tbl_ins.columns = ["Marca","Registros","Precio medio ($)","Mediana ($)",
                        "Mínimo ($)","Máximo ($)","CV (%)","$/L promedio",
                        "% en oferta","Dto. prom. (%)"]
    st.dataframe(_tbl_ins.reset_index(drop=True), use_container_width=True,
        height=min(480, len(_tbl_ins)*38+60), hide_index=True,
        column_config={
            "Precio medio ($)":st.column_config.NumberColumn(format="$%d"),
            "Mediana ($)":     st.column_config.NumberColumn(format="$%d"),
            "Mínimo ($)":      st.column_config.NumberColumn(format="$%d"),
            "Máximo ($)":      st.column_config.NumberColumn(format="$%d"),
            "$/L promedio":    st.column_config.NumberColumn(format="$%d"),
            "CV (%)":          st.column_config.NumberColumn(format="%.1f%%"),
            "% en oferta":     st.column_config.NumberColumn(format="%.0f%%"),
            "Dto. prom. (%)":  st.column_config.NumberColumn(format="%.0f%%"),
        })
    st.markdown(
        "<div style='font-size:0.72rem;color:#9CA3AF;margin-top:0.3rem'>"
        "💡 Precios nominales (pesos corrientes). "
        f"Inflación mensual configurada: {inflacion_mensual:.1f}% "
        "— activar ajuste en el tab Evolución para ver pesos reales.</div>",
        unsafe_allow_html=True)

    # ── Gráfico: precio/litro ranking por marca destacada ─────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="chart-title">Precio promedio por litro · marcas destacadas</div>', unsafe_allow_html=True)
    _pl_chart = (_by_marca[_by_marca["Marca_raw"].isin(MARCAS_DESTACADAS)]
                 .dropna(subset=["pl_medio"]).sort_values("pl_medio"))
    if not _pl_chart.empty:
        _fig_ins = go.Figure(go.Bar(
            x=_pl_chart["pl_medio"], y=_pl_chart["Marca_raw"],
            orientation="h",
            marker_color=[COLORES_CAT.get(m,"#9CA3AF") for m in _pl_chart["Marca_raw"]],
            text=[f"${v:,.0f}/L" for v in _pl_chart["pl_medio"]],
            textposition="outside", textfont=dict(size=12, color="#111827"), cliponaxis=False,
        ))
        _fig_ins.update_layout(**_BASE_CORE, height=300,
            margin=dict(l=10,r=160,t=20,b=20),
            xaxis=dict(title="$/L promedio", tickprefix="$", tickformat=",",
                       tickfont=dict(size=11,color="#111827"),
                       range=[0, float(_pl_chart["pl_medio"].max())*1.35]),
            yaxis=dict(tickfont=dict(size=13,color="#111827")),
            showlegend=False)
        st.plotly_chart(_fig_ins, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════
# TAB 9 · QUIEBRES DE STOCK
# ══════════════════════════════════════════════════════════════════════════
with tab9:
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

    # Filtros: Marca · Cadena · Granularidad
    _qb_fa, _qb_fb, _qb_fc, _ = st.columns([2, 2, 2, 1])
    with _qb_fa:
        _qb_marcas_opts = sorted(df_full["Marca_raw"].unique())
        _qb_marca = st.selectbox("🏷️ Marca", _qb_marcas_opts, key="qb_marca")
    with _qb_fb:
        _qb_cadenas_opts = sorted(df_full[df_full["Marca_raw"] == _qb_marca]["Cadena"].unique())
        _qb_cadena = st.selectbox("🏪 Cadena", _qb_cadenas_opts, key="qb_cadena")
    with _qb_fc:
        _qb_gran = st.selectbox("📅 Granularidad", ["Semanal", "Mensual", "Diario"], key="qb_gran")

    # Fuente: marca + cadena seleccionadas
    _qb_src = df_full[
        (df_full["Marca_raw"] == _qb_marca) &
        (df_full["Cadena"] == _qb_cadena)
    ].copy()

    if _qb_src.empty:
        st.info("Sin datos para la selección.")
    else:
        # Columna de período según granularidad
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
            st.plotly_chart(fig, use_container_width=True)

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
                    st.dataframe(pd.DataFrame(_qb_rows), use_container_width=True, hide_index=True)
            else:
                st.success("✅ No se detectaron quiebres en el período seleccionado.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 11 · TABLA DINÁMICA
# ══════════════════════════════════════════════════════════════════════════
with tab11:
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
                use_container_width=True,
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

# ── NOTIFICACIONES: cambios de precio semana a semana ─────────────────────
st.markdown("<br>", unsafe_allow_html=True)
_notif_fechas = sorted(df_full["Fecha"].unique())
if len(_notif_fechas) >= 2:
    _fn_ant = _notif_fechas[-2]
    _fn_act = _notif_fechas[-1]
    _fn_ant_str = pd.Timestamp(_fn_ant).strftime("%d/%m/%Y")
    _fn_act_str = pd.Timestamp(_fn_act).strftime("%d/%m/%Y")

    # Cambios de precio de góndola real: por producto único (Producto + Cadena), sin promediar
    _KEY = ["Producto", "Cadena"]
    _gond_ant = (df_full[(df_full["Fecha"] == _fn_ant) & (~df_full["En_oferta"])]
                 [_KEY + ["Precio", "Marca_raw"]].drop_duplicates(subset=_KEY)
                 .rename(columns={"Precio": "p_ant"}))
    _gond_act = (df_full[(df_full["Fecha"] == _fn_act) & (~df_full["En_oferta"])]
                 [_KEY + ["Precio", "Marca_raw"]].drop_duplicates(subset=_KEY)
                 .rename(columns={"Precio": "p_act"}))

    _cambios = (_gond_ant.merge(_gond_act, on=_KEY + ["Marca_raw"])
                .assign(delta=lambda d: d["p_act"] - d["p_ant"],
                        delta_pct=lambda d: ((d["p_act"] / d["p_ant"]) - 1) * 100)
                .query("delta != 0")
                .sort_values("delta_pct"))

    _subas = _cambios[_cambios["delta"] > 0].sort_values("delta_pct", ascending=False)
    _bajas = _cambios[_cambios["delta"] < 0].sort_values("delta_pct")

    # Ofertas nuevas: productos únicos (Producto + Cadena) sin oferta antes, con oferta ahora
    _sin_of_ant = (df_full[(df_full["Fecha"] == _fn_ant) & (~df_full["En_oferta"])]
                   [_KEY].drop_duplicates())
    _con_of_act = (df_full[(df_full["Fecha"] == _fn_act) & df_full["En_oferta"]]
                   .drop_duplicates(subset=_KEY)
                   [_KEY + ["Precio", "Precio_oferta", "Descuento_pct"]]
                   .rename(columns={"Precio": "p_gond", "Precio_oferta": "p_of",
                                    "Descuento_pct": "desc"}))
    _nuevas_of = _con_of_act.merge(_sin_of_ant, on=_KEY, how="inner")

    _total    = len(_cambios)
    _total_of = len(_nuevas_of)
    _hay_algo = _total > 0 or _total_of > 0

    _partes = []
    if _total > 0:
        _partes.append(f"{_total} cambio{'s' if _total != 1 else ''} de precio")
    if _total_of > 0:
        _partes.append(f"{_total_of} oferta{'s' if _total_of != 1 else ''} nueva{'s' if _total_of != 1 else ''}")
    _label = ("🔔 " + " · ".join(_partes) + f" · {_fn_ant_str} → {_fn_act_str}"
              if _hay_algo else
              f"✅ Sin cambios de precio ni ofertas nuevas · {_fn_ant_str} → {_fn_act_str}")

    def _fila(sku, cadena, flecha, pct_str, p_de, p_a, color_flecha):
        return (
            f"<div style='padding:5px 0;border-bottom:1px solid #E5E7EB'>"
            f"<span style='font-size:0.82rem;color:#111827'>"
            f"<b style='word-break:break-word'>{sku}</b><br>"
            f"<span style='color:#6B7280'>{cadena}</span>&nbsp;&nbsp;"
            f"<span style='color:{color_flecha}'>{flecha} {pct_str}</span>"
            f"&nbsp;&nbsp;${p_de:,.0f} → <b>${p_a:,.0f}</b>"
            f"</span></div>"
        )

    def _titulo_col(emoji, texto, n):
        st.markdown(
            f"<p style='font-size:0.95rem;font-weight:700;color:#111827;margin:0 0 4px 0'>"
            f"{emoji} {texto} ({n})</p>"
            f"<p style='font-size:0.75rem;color:#6B7280;margin:0 0 8px 0'>"
            f"{_fn_ant_str} → {_fn_act_str}</p>",
            unsafe_allow_html=True)

    with st.expander(_label, expanded=_hay_algo):
        if not _hay_algo:
            st.info("Ningún cambio detectado entre las últimas dos semanas.")
        else:
            _col_s, _col_b, _col_o = st.columns(3, gap="large")
            with _col_s:
                _titulo_col("🔴", "Subas", len(_subas))
                if _subas.empty:
                    st.markdown("<span style='color:#111827;font-size:0.82rem'>Ninguna suba.</span>", unsafe_allow_html=True)
                for _, r in _subas.iterrows():
                    st.markdown(_fila(r["Producto"], r["Cadena"], "▲",
                                      f"{r['delta_pct']:+.1f}%",
                                      r["p_ant"], r["p_act"], "#DC2626"),
                                unsafe_allow_html=True)
            with _col_b:
                _titulo_col("🟢", "Bajas", len(_bajas))
                if _bajas.empty:
                    st.markdown("<span style='color:#111827;font-size:0.82rem'>Ninguna baja.</span>", unsafe_allow_html=True)
                for _, r in _bajas.iterrows():
                    st.markdown(_fila(r["Producto"], r["Cadena"], "▼",
                                      f"{r['delta_pct']:+.1f}%",
                                      r["p_ant"], r["p_act"], "#16A34A"),
                                unsafe_allow_html=True)
            with _col_o:
                _titulo_col("🏷️", "Ofertas nuevas", len(_nuevas_of))
                if _nuevas_of.empty:
                    st.markdown("<span style='color:#111827;font-size:0.82rem'>Ninguna oferta nueva.</span>", unsafe_allow_html=True)
                for _, r in _nuevas_of.sort_values("desc", ascending=False).iterrows():
                    st.markdown(_fila(r["Producto"], r["Cadena"], "▼",
                                      f"{r['desc']:.0f}% dto.",
                                      r["p_gond"], r["p_of"], "#B45309"),
                                unsafe_allow_html=True)
