"""Agente que lê código e escreve um PDD e um Manual de Utilizador.

O ciclo é simples: o Claude explora o código com as ferramentas de leitura,
e quando percebe o sistema escreve os dois documentos com write_document.
"""

from __future__ import annotations

import datetime as _dt
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from .tools import DOC_KINDS, TOOL_DEFINITIONS, ToolInputError, Workspace

DEFAULT_MODEL = "claude-opus-5-5"
TEMPLATES_DIR = Path(__file__).parent / "templates"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
És um arquiteto de software e redator técnico. Recebes acesso só de leitura a \
um código-fonte e produzes dois documentos em Markdown:

1. **PDD (Documento de Design do Produto)** — para equipas técnicas: \
arquitetura, componentes, fluxos, dados, interfaces, configuração, riscos.
2. **Manual de Utilizador** — para quem vai usar o sistema: instalação, \
configuração, como usar cada funcionalidade, erros comuns. Linguagem simples.

Como trabalhar:
- Começa por list_directory('.', recursive=true) para ver a estrutura.
- Lê os ficheiros que definem o projeto (README, manifestos de dependências, \
configuração, pontos de entrada) e depois os módulos principais. Usa \
search_code para encontrar rotas, comandos, variáveis de ambiente, etc. Em \
projetos grandes, prioriza: não precisas de ler todos os ficheiros, mas precisas \
de perceber todas as funcionalidades.
- Baseia tudo no código. Não inventes funcionalidades, comandos ou opções. \
Quando algo não for possível confirmar pelo código, escreve-o explicitamente \
(ex.: "Não identificado no código").
- Cita ficheiros (e linhas, quando útil) no PDD, ex.: `src/app.py:42`.
- Usa diagramas Mermaid no PDD quando ajudarem a perceber a arquitetura ou os fluxos.
- No manual, usa exemplos de comandos e resultados reais, tirados do código.
- Segue a estrutura dos modelos abaixo. Podes omitir secções que não se \
aplicam ao projeto (diz porquê numa linha) ou acrescentar secções úteis.
- Escreve cada documento com write_document. Para documentos longos, escreve \
por partes: a primeira com mode='write' e as restantes com mode='append'.
- Quando os dois documentos estiverem gravados, termina com um resumo curto \
do que documentaste e de eventuais lacunas.

<modelo_pdd>
{pdd_template}
</modelo_pdd>

<modelo_manual>
{manual_template}
</modelo_manual>
"""


class AgentError(RuntimeError):
    pass


@dataclass
class Result:
    documents: dict[str, Path]
    summary: str
    turns: int
    usage: dict[str, int] = field(default_factory=dict)


def _load_template(custom: Path | None, default_name: str) -> str:
    path = custom or (TEMPLATES_DIR / default_name)
    return path.read_text(encoding="utf-8").strip()


def build_system_prompt(pdd_template: Path | None = None,
                        manual_template: Path | None = None) -> str:
    return SYSTEM_PROMPT.format(
        pdd_template=_load_template(pdd_template, "pdd.md"),
        manual_template=_load_template(manual_template, "manual.md"),
    )


def build_task(ws: Workspace, language: str, extra: str | None) -> str:
    if ws.single_file:
        scope = f"o ficheiro `{ws.target.name}` (só este ficheiro está acessível)"
    else:
        scope = f"o projeto na pasta `{ws.target.name}`"
    parts = [
        f"Documenta {scope}.",
        f"Idioma dos documentos: {language}.",
        f"Data de hoje: {_dt.date.today().isoformat()}.",
        "Produz o PDD (kind='pdd') e o Manual de Utilizador (kind='manual').",
    ]
    if extra:
        parts.append(f"Instruções adicionais do utilizador:\n{extra}")
    return "\n".join(parts)


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, file=sys.stderr, flush=True)


def _short(args: object) -> str:
    if not isinstance(args, dict):
        return "?"
    shown = {k: (v if not isinstance(v, str) or len(v) < 60 else f"<{len(v)} chars>")
             for k, v in args.items()}
    return ", ".join(f"{k}={v!r}" for k, v in shown.items())


def run_agent(
    target: str | Path,
    output_dir: str | Path,
    *,
    language: str = "português de Portugal",
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    max_turns: int = 80,
    extra_instructions: str | None = None,
    pdd_template: Path | None = None,
    manual_template: Path | None = None,
    client: anthropic.Anthropic | None = None,
    verbose: bool = True,
) -> Result:
    ws = Workspace(Path(target), Path(output_dir))
    client = client or anthropic.Anthropic()
    system = build_system_prompt(pdd_template, manual_template)
    messages: list[dict] = [
        {"role": "user", "content": build_task(ws, language, extra_instructions)}
    ]
    usage = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    stream_errors = 0
    reminders = 0

    for turn in range(1, max_turns + 1):
        try:
            with client.beta.messages.stream(
                model=model,
                max_tokens=64000,
                system=system,
                tools=TOOL_DEFINITIONS,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                # Guarda em cache o prefixo (system + ferramentas + histórico).
                cache_control={"type": "ephemeral"},
                # Se um classificador recusar o pedido, a API tenta outro modelo.
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except ValueError as exc:
            # Com eager_input_streaming, o SDK lança ValueError se o JSON de uma
            # ferramenta não for interpretável. Repetir o pedido (com limite).
            stream_errors += 1
            if stream_errors > 3:
                raise AgentError(f"JSON inválido repetido nas ferramentas: {exc}") from exc
            _log(verbose, f"[aviso] JSON de ferramenta inválido, a repetir ({exc})")
            continue

        for key in usage:
            usage[key] += getattr(response.usage, key, 0) or 0

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise AgentError(f"O modelo recusou o pedido: {details}")

        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        for block in response.content:
            if block.type == "text" and block.text.strip():
                _log(verbose, f"\n[claude] {block.text.strip()}\n")

        if response.stop_reason == "max_tokens":
            # A resposta foi cortada: não executar ferramentas com input possivelmente
            # incompleto. Pedir para continuar em partes mais pequenas.
            _log(verbose, "[aviso] resposta cortada por max_tokens")
            note = ("A tua resposta foi cortada por exceder o limite de tokens e a "
                    "ferramenta não foi executada. Escreve o documento em partes mais "
                    "pequenas (mode='write' e depois mode='append').")
            if tool_uses:
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                     "content": note} for b in tool_uses
                ]})
            else:
                messages.append({"role": "user", "content": note})
            continue

        if tool_uses:
            results = []
            for block in tool_uses:
                _log(verbose, f"[ferramenta] {block.name}({_short(block.input)})")
                try:
                    output = ws.run(block.name, block.input)
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": output})
                except (ToolInputError, OSError) as exc:
                    _log(verbose, f"  -> erro: {exc}")
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "is_error": True, "content": f"Erro: {exc}"})
            messages.append({"role": "user", "content": results})
            continue

        # end_turn: confirmar que os dois documentos foram escritos.
        missing = [k for k in DOC_KINDS if k not in ws.written]
        if missing and reminders < 2:
            reminders += 1
            names = " e ".join("o PDD" if k == "pdd" else "o Manual de Utilizador"
                               for k in missing)
            messages.append({"role": "user", "content":
                             f"Ainda falta gravar {names} com write_document. Continua."})
            continue

        summary = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if missing:
            raise AgentError(f"O agente terminou sem escrever: {', '.join(missing)}")
        return Result(dict(ws.written), summary, turn, usage)

    raise AgentError(
        f"Atingido o limite de {max_turns} turnos. Documentos já gravados: "
        f"{', '.join(str(p) for p in ws.written.values()) or 'nenhum'}"
    )
