# Treecomex • Sistema Integrado (Fase 1)

Sistema web desenvolvido para apoiar a **gestão financeira operacional** da Treecomex, com foco em **importação de extratos bancários, conciliação manual, relatórios por cliente e auditoria básica**.

Este projeto foi construído com **Streamlit + PostgreSQL (Supabase)**, priorizando rapidez de entrega, rastreabilidade e clareza para uso interno e apresentações.

---

## ✨ Funcionalidades Atuais (Fase 1)

### 📥 Importação de Extrato Bancário
- Upload de extratos em PDF
- Parsers específicos por banco
- Pré-visualização antes da gravação
- Identificação de duplicidade por hash do arquivo

### 🔗 Conciliação Manual
- Associação de movimentos bancários a:
  - Processo
  - Cliente
  - Categoria
  - Tipo (Entrada / Saída)
- Marcação de conciliação manual
- Persistência com auditoria:
  - usuário
  - data/hora
- Salvamento seguro com commit explícito

### 📊 Relatório de Cliente (Exportação)
- Geração de relatório por:
  - Cliente
  - Empresa
  - Mês
- Cálculo automático de:
  - Saldo anterior
  - Total de entradas
  - Total de saídas
- Exportação em **PDF**
- Tabela com:
  - Banco
  - Data
  - Histórico
  - Tipo de lançamento
  - Categoria
  - Entrada
  - Saída
  - Saldo

### 👥 Administração de Usuários
- Cadastro de usuários
- Ativação/desativação
- Geração de chave de acesso
- Base para controle de auditoria

### 🧭 Navegação e UX
- Menu lateral com identificação do usuário:
  - “Olá, Nome do Usuário”
- Filtros rápidos de período
- Estados preservados com `session_state`
- Performance otimizada com cache controlado

---

## 🚧 Funcionalidades em Desenvolvimento

### 📦 Fechamento Financeiro
- Tela disponível em **modo demonstração**
- Interface funcional para apresentação
- **Sem leitura/gravação em banco**
- Feature preparada para ativação futura

---

## 🏗️ Arquitetura

- **Frontend:** Streamlit
- **Backend:** PostgreSQL (Supabase)
- **Driver:** psycopg (v3)
- **Relatórios:** ReportLab (PDF)
- **Cache:** `st.cache_data` + controle manual
- **Conexão:** 1 conexão por sessão (otimizada para Streamlit Cloud)

---

## 📁 Estrutura do Projeto

```text
Financeiro/
├── app.py
├── db.py
├── pages/
│   ├── admin_usuarios.py
│   ├── import_pdf.py
│   ├── conciliacao.py
│   ├── exportacao.py
│   └── fechamento.py
├── parsers/
│   ├── inter.py
│   ├── itau.py
│   └── ...
├── requirements.txt
└── README.md
```

## ⚙️ Configuração do Ambiente
Variáveis obrigatórias

O sistema requer a variável:

```text
DATABASE_URL=postgresql://...
```

Pode ser definida via:

st.secrets (Streamlit Cloud)

variável de ambiente local

## ▶️ Executar Localmente
```text
pip install -r requirements.txt
streamlit run app.py
```

## 🧪 Modo Demonstração

Algumas funcionalidades (como Fechamento) podem operar em modo demonstração, exibindo resultados simulados sem persistência, permitindo:

apresentações

validação de layout

testes de navegação

## 🔒 Segurança e Auditoria

Não há exclusão física de dados críticos

Conciliações registram:

usuário

data/hora

Estrutura preparada para evolução de permissões e perfis

## 🗺️ Próximos Passos (Fase 2)

- Fechamento financeiro real

- Conciliação automática por regras

- Dashboards consolidados

- Controle de permissões por perfil

- Histórico de alterações por registro

## 👩‍💻 Autoria

Projeto desenvolvido por Hianara Camilla
com foco em dados, automação e sistemas financeiros,
priorizando entregas rápidas, estabilidade e clareza para o negócio.