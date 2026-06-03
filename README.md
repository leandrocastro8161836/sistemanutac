# Sistema de Controle de Ouvidoria

## Como executar

### Requisitos
- Python 3.8+ (já instalado no computador)
- Nenhuma dependência externa — tudo é padrão da biblioteca Python

### Passo a passo

1. **Abra o terminal** e navegue até a pasta do sistema:
   ```
   cd ouvidoria-sistema
   ```

2. **Inicie o servidor:**
   ```
   python3 server.py
   ```
   (no Windows: `python server.py`)

3. **Acesse no navegador:**
   ```
   http://localhost:8765
   ```

### Na primeira execução
- O banco de dados `ouvidoria.db` será criado automaticamente.
- Os **1.380 registros** da planilha original serão importados do arquivo `data_seed.json`.
- As categorias (temas, assuntos, descritivos) serão populadas automaticamente.

---

## Estrutura de arquivos

```
ouvidoria-sistema/
├── server.py         ← Servidor web + API REST + banco SQLite
├── ouvidoria.db      ← Banco de dados (criado na 1ª execução)
├── data_seed.json    ← Dados importados da planilha
└── public/
    └── index.html    ← Interface web completa
```

---

## Funcionalidades

### Dashboard
- Totais: processos, em tramitação, prorrogados, finalizados, alta sensibilidade, em atraso
- Gráficos de barras: por servidor, modalidade, tema, sensibilidade, situação final, evolução mensal
- Alertas automáticos de processos com prazo vencido

### Processos
- Listagem paginada (50 por página)
- Busca por número do processo, tema, assunto, síntese, nome dos envolvidos
- Filtros: status, servidor, sensibilidade, modalidade, tema
- Ordenação por qualquer coluna
- Indicador visual de processos em atraso (linha vermelha)
- Cadastro completo de novos processos
- Edição com log automático de alterações
- Visualização detalhada com 4 abas: Informações, Síntese, Instrução, Histórico
- Exclusão com confirmação
- Exportação CSV com os filtros ativos

### Categorizador
- Visualização de temas, assuntos e descritivos cadastrados
- Adição de novos valores em cada categoria

---

## API REST disponível

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/processos` | Lista com filtros e paginação |
| GET | `/api/processos/:id` | Detalhe de um processo |
| POST | `/api/processos` | Criar novo processo |
| PUT | `/api/processos/:id` | Atualizar processo |
| DELETE | `/api/processos/:id` | Excluir processo |
| GET | `/api/processos/:id/log` | Histórico de alterações |
| GET | `/api/stats` | Dados para dashboard |
| GET | `/api/categorias` | Lista de categorias |
| GET | `/api/export` | Download CSV |

### Parâmetros de busca (GET /api/processos)
- `q` — busca geral
- `status` — filtra por status
- `servidor` — filtra por servidor
- `sensibilidade` — ALTA, MÉDIA, BAIXA
- `modalidade` — tipo da manifestação
- `tema` — tema da manifestação
- `page` — página (default: 1)
- `per_page` — por página (default: 50)
- `sort` — campo de ordenação
- `order` — ASC ou DESC

---

## Segurança dos dados
- Banco SQLite com WAL mode (mais seguro contra corrupção)
- Todas as alterações ficam registradas no log com data/hora
- Os dados originais da planilha ficam preservados em `data_seed.json`
- **Faça backup periódico do arquivo `ouvidoria.db`** — ele contém todos os dados
