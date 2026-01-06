import streamlit as st

from auth import require_access
from pages.admin import render_admin
from pages.admin_usuarios import render_admin_usuarios
from pages.import_pdf import render_import_pdf
from pages.conciliacao import render_conciliacao
#from pages.fechamento import render_fechamento
from pages.exportacao import render_exportacao

from db import fetch_df_cached


# 1) Page config
st.set_page_config(page_title="Treecomex • Conciliação", layout="wide")

st.markdown(
    """
    <style>
        /* Esconde o menu multipage (lista de arquivos) */
        [data-testid="stSidebarNav"] {
            display: none;
        }

        /* Ajuste fino: remove espaço extra no topo da sidebar */
        section[data-testid="stSidebar"] > div:first-child {
            padding-top: 1rem;
        }
    </style>
    """,
    unsafe_allow_html=True
)

# 2) Defaults de session_state (evita KeyError mesmo se auth falhar)
DEFAULTS = {
    "auth_ok": False,
    "usuario_id": None,
    "is_admin": False,
    "access_key": None,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def _logout():
    # limpa sessão relacionada ao acesso e telas
    for k in [
        "auth_ok", "usuario_id", "is_admin", "access_key",
        "import_preview", "conc_df_mov"
    ]:
        st.session_state.pop(k, None)

    # restaura defaults mínimos
    st.session_state["auth_ok"] = False
    st.session_state["usuario_id"] = None
    st.session_state["is_admin"] = False
    st.session_state["access_key"] = None

    st.rerun()


# 3) Autenticação
require_access()

# 4) Carregar permissões (somente após autenticar)
allowed = set()

if not st.session_state.get("is_admin"):
    usuario_id = st.session_state.get("usuario_id")
    if not usuario_id:
        st.error("Sessão sem usuario_id. Verifique o fluxo de autenticação.")
        st.stop()

    dfp = fetch_df_cached("SELECT tela FROM usuario_tela WHERE usuario_id=%s", (usuario_id,))
    if dfp is not None and not dfp.empty:
        allowed = set(dfp["tela"].astype(str).tolist())
    else:
        allowed = set()  # sem permissões => bloqueia tudo




# 5) UI
st.title("Treecomex • Sistema Integrado (Fase 1)")

PAGES = [
    "Admin Usuários",
    "Cadastros",
    "Importar Extrato PDF",
    "Conciliação",
    "Relatórios de Cliente",
    #"Fechamento",
]


with st.sidebar:
    st.header("Menu")

    if st.button("Sair"):
        _logout()

    st.divider()

    pages = PAGES.copy()

    if st.session_state.get("is_admin"):
        pass  # admin vê tudo
    else:
        pages = [p for p in pages if p in allowed]
        if not pages:
            st.warning("Nenhuma tela liberada para seu usuário. Peça ao admin para liberar permissões.")
            st.stop()

    page = st.radio("Ir para:", pages, index=0)




# 6) Roteamento
if page == "Cadastros":
    render_admin()
elif page == "Admin Usuários":
    render_admin_usuarios()
elif page == "Importar Extrato PDF":
    render_import_pdf()
elif page == "Conciliação":
    render_conciliacao()
else:
    render_exportacao()
#else:
    render_fechamento()
