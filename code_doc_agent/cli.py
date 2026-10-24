"""Linha de comandos: python -m code_doc_agent <caminho> [opções]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anthropic

from .agent import DEFAULT_MODEL, AgentError, run_agent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="code_doc_agent",
        description="Lê um ficheiro ou pasta de código e gera um PDD e um Manual de Utilizador.",
    )
    parser.add_argument("caminho", help="Ficheiro ou pasta com o código a documentar.")
    parser.add_argument("-o", "--saida", default=None,
                        help="Pasta onde gravar os documentos (padrão: ./docs_gerados/<nome>).")
    parser.add_argument("--idioma", default="português de Portugal",
                        help="Idioma dos documentos (padrão: português de Portugal).")
    parser.add_argument("--instrucoes", default=None,
                        help="Instruções extra para o agente (ex.: 'foca-te na API REST').")
    parser.add_argument("--modelo-pdd", type=Path, default=None,
                        help="Ficheiro Markdown com um modelo próprio de PDD.")
    parser.add_argument("--modelo-manual", type=Path, default=None,
                        help="Ficheiro Markdown com um modelo próprio de manual.")
    parser.add_argument("--modelo", default=DEFAULT_MODEL, help=f"Modelo Claude (padrão: {DEFAULT_MODEL}).")
    parser.add_argument("--esforco", default="high",
                        choices=["low", "medium", "high", "xhigh", "max"],
                        help="Profundidade de análise (padrão: high).")
    parser.add_argument("--max-turnos", type=int, default=80)
    parser.add_argument("-q", "--silencioso", action="store_true", help="Não mostrar o progresso.")
    args = parser.parse_args(argv)

    target = Path(args.caminho).expanduser()
    if not target.exists():
        parser.error(f"caminho não existe: {target}")
    name = target.resolve().stem or "projeto"
    output = Path(args.saida) if args.saida else Path("docs_gerados") / name

    try:
        result = run_agent(
            target, output,
            language=args.idioma,
            model=args.modelo,
            effort=args.esforco,
            max_turns=args.max_turnos,
            extra_instructions=args.instrucoes,
            pdd_template=args.modelo_pdd,
            manual_template=args.modelo_manual,
            verbose=not args.silencioso,
        )
    except AgentError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print("Erro: credenciais da API inválidas ou em falta. Defina ANTHROPIC_API_KEY.",
              file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("Erro: limite de pedidos da API atingido. Tente mais tarde.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as exc:
        print(f"Erro da API ({exc.status_code}): {exc.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Erro: não foi possível ligar à API da Anthropic.", file=sys.stderr)
        return 1

    print("\nDocumentos gerados:")
    for path in result.documents.values():
        print(f"  {path}")
    u = result.usage
    print(f"Turnos: {result.turns} | tokens entrada: {u['input_tokens']} "
          f"(cache lida: {u['cache_read_input_tokens']}) | saída: {u['output_tokens']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
