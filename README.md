# Treecomex • Sistema Integrado (Fase 1)

Sistema web desenvolvido para apoiar a **gestão financeira operacional** da Treecomex, com foco em **importação de extratos bancários, conciliação manual, relatórios por cliente e auditoria básica**.

Este projeto foi construído com **Streamlit + PostgreSQL (Supabase)**, priorizando rapidez de entrega, rastreabilidade e clareza para uso interno e apresentações.

---

## ✨ Funcionalidades Atuais (Fase 1)

### 📥 Importação de Extrato Bancário
- Upload de extratos em PDF ou CSV
- Parsers específicos por banco (Nubank via CSV, Inter PDF/CSV, demais via PDF)
- Pré-visualização antes da gravação
- Identificação de duplicidade por hash do arquivo
- Identificação de duplicidade por hash do arquivo

### 🔗 Conciliação Manual e Rateio Dinâmico
- Associação de movimentos bancários a:
  - Processo (suporte a rateio N:N para múltiplos processos)
  - Cliente
  - Categoria Financeira
  - Tipo (Entrada / Saída)
- Rateio Dinâmico: Divisão de um único movimento bancário em diversas categorias e processos.
- Marcação de conciliação manual com proteção transacional.
- Persistência com auditoria:
  - usuário
  - data/hora
- Salvamento seguro com commit explícito

### 📊 Relatório de Cliente e Licitação (Exportação)
- Geração de relatório por:
  - Cliente
  - Empresa
  - Período (Mês associado)
- Cálculo automático de:
  - Saldo anterior
  - Total de entradas
  - Total de saídas
- Emissão de Relatório Específico de Licitação filtrado por categoria.
- Exportação estruturada em **PDF** usando ReportLab
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
- Funcionalidade ativa com **leitura e gravação no banco de dados Postgres**.
- Importação rápida de dados por meio das abas `Resumo` e `Rateio` de planilhas Excel.
- Extração de valores: FOB, Frete, Seguro, CIF, II, IPI, PIS, COFINS, ICMS.
- Cálculo de conversão (BRL / USD), Despesas Gerais e Custo de Aquisição.
- Geração de Relatório PDF Consolidado detalhando os custos por DI/Processo.

---

## 🏗️ Arquitetura

- **Frontend:** Streamlit
- **Backend:** PostgreSQL (Supabase)
- **Driver:** psycopg (v3)
- **Relatórios & PDFs:** ReportLab (Exportação) e FPDF (Fechamento)
- **Processamento de Dados:** Pandas
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

- Conciliação automática por regras

- Dashboards consolidados

- Controle de permissões por perfil

- Histórico de alterações por registro

## 👩‍💻 Autoria

Projeto desenvolvido por Hianara Camilla
com foco em dados, automação e sistemas financeiros,
priorizando entregas rápidas, estabilidade e clareza para o negócio.