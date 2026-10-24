# Agente de documentação de código

Agente que lê o código de um **ficheiro ou pasta local** e gera dois documentos
em Markdown:

- **`PDD.md`** — Documento de Design do Produto (arquitetura, componentes,
  fluxos, modelo de dados, interfaces, configuração, riscos), com diagramas Mermaid
  e referências a ficheiros/linhas.
- **`MANUAL_UTILIZADOR.md`** — Manual de Utilizador (instalação, configuração,
  como usar cada funcionalidade, erros comuns, FAQ).

Usa a API do Claude (`claude-opus-5-5`). O agente explora o código com
ferramentas **só de leitura** presas ao caminho indicado (não lê nada fora dele)
e só escreve os dois documentos na pasta de saída.

## Instalação

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## Utilização

```bash
# Uma pasta
python -m code_doc_agent /caminho/para/projeto

# Um único ficheiro
python -m code_doc_agent /caminho/para/script.py

# Escolher pasta de saída, idioma e instruções extra
python -m code_doc_agent ./meu-projeto -o ./docs \
    --idioma "português do Brasil" \
    --instrucoes "O público do manual são operadores sem conhecimentos técnicos."
```

Por omissão, os documentos ficam em `./docs_gerados/<nome-do-projeto>/`.

| Opção | Descrição |
|---|---|
| `-o`, `--saida` | Pasta de saída |
| `--idioma` | Idioma dos documentos (padrão: português de Portugal) |
| `--instrucoes` | Instruções extra para o agente |
| `--modelo-pdd`, `--modelo-manual` | Ficheiro Markdown com um modelo próprio de documento |
| `--esforco` | `low` / `medium` / `high` / `xhigh` / `max` — profundidade da análise (padrão: `high`) |
| `--modelo` | Modelo Claude (padrão: `claude-opus-5-5`) |
| `--max-turnos` | Limite de iterações do agente (padrão: 80) |
| `-q` | Não mostrar o progresso |

### Em Python

```python
from code_doc_agent import run_agent

result = run_agent("./meu-projeto", "./docs")
print(result.documents)   # {'pdd': Path(...), 'manual': Path(...)}
```

## Personalizar os documentos

A estrutura de cada documento vem de `code_doc_agent/templates/pdd.md` e
`code_doc_agent/templates/manual.md`. Edite-os, ou passe os seus com
`--modelo-pdd` / `--modelo-manual` (ex.: o modelo de PDD da sua empresa).

## Como funciona

1. O agente recebe o caminho e os modelos dos documentos.
2. Explora o código: `list_directory`, `read_file` (por partes, em ficheiros
   grandes) e `search_code` (expressões regulares). Pastas como `.git`,
   `node_modules`, `venv`, `dist` são ignoradas, e ficheiros binários não são lidos.
3. Escreve cada documento com `write_document` (por secções, se for longo).
4. Se terminar sem gravar os dois documentos, é lembrado para os concluir.

Outros detalhes: prompt caching (reduz custo nas leituras repetidas do
histórico), fallback automático do lado do servidor se um pedido for recusado,
e validação dos inputs das ferramentas.

## Subagente do Claude Code

Também existe uma versão como subagente do Claude Code em
`.claude/agents/gerador-documentacao.md`. Dentro deste repositório, no Claude
Code, peça por exemplo: *"usa o gerador-documentacao para documentar ../meu-projeto"*.

## Testes

```bash
pip install pytest
python -m pytest
```

Os testes usam uma API simulada; não fazem chamadas reais nem precisam de chave.
