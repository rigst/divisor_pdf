#!/usr/bin/env python3
"""Gera docs/LICENCAS-TERCEIROS.md a partir dos pacotes instalados no venv.

Usa só a biblioteca padrão (importlib.metadata) — não adiciona dependência ao
projeto. Rode com o Python do venv do próprio projeto, para inventariar o que
está de fato instalado:

    ./venv/bin/python scripts/licencas_terceiros.py

As dependências diretas são as listadas em requirements.txt; o restante entra
como transitivo. Programas externos (invocados por subprocess, não linkados)
ficam em PROGRAMAS_EXTERNOS, abaixo — mantenha essa lista à mão quando mudar a
stack.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

# --------------------------------------------------------------------------
# Ajuste por projeto
# --------------------------------------------------------------------------

PROJETO = "Divisor de PDFs"
LICENCA_PROJETO = "AGPL-3.0"

# Programas chamados por subprocess (não linkados ao código do projeto).
# Formato: (nome, versão, licença, observação)
PROGRAMAS_EXTERNOS: list[tuple[str, str, str, str]] = [
    ("Ghostscript", "10.02.1", "AGPL-3.0", "Compressão de PDF (`gs`), chamado em `splitter/services.py`"),
]

# Observações que valem manutenção contínua.
NOTAS: list[str] = [
    "**Redis**: o servidor em uso é a série 7.0 (BSD-3-Clause). As versões 7.4 a "
    "7.9 passaram a ser RSALv2/SSPL, que não são licenças livres segundo a OSI. "
    "Ao atualizar o servidor, reveja esta seção e a página de licenças do site.",
    "Os programas externos acima rodam como processos separados, invocados por "
    "linha de comando. Não há linkagem com o código deste projeto, e o serviço não "
    "distribui os binários — por isso as obrigações de reciprocidade da GPL não se "
    "estendem a este código.",
]

# --------------------------------------------------------------------------

RAIZ = Path(__file__).resolve().parent.parent

# Licenças que exigem atenção ao redistribuir ou ao combinar com código fechado.
# Sem \b no fim de propósito: os classificadores do PyPI escrevem "LGPLv3",
# "GPLv2+" e afins, que um limite de palavra à direita deixaria passar.
COPYLEFT = re.compile(
    r"\b(?:A|L)?GPL"
    r"|General Public License"
    r"|Mozilla Public License|\bMPL\b"
    r"|Eclipse Public License|\bEPL\b"
    r"|Common Development|\bCDDL\b"
    r"|\bSSPL\b|Server Side Public"
    r"|Open Software License|\bOSL\b"
    r"|CeCILL",
    re.IGNORECASE,
)


def normalizar(nome: str) -> str:
    return re.sub(r"[-_.]+", "-", nome).lower()


def licenca_de(dist: metadata.Distribution) -> str:
    """Resolve a licença na ordem mais confiável dos metadados."""
    meta = dist.metadata

    # PEP 639: campo canônico e não ambíguo.
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()

    # Classifiers costumam ser mais limpos que o campo livre License.
    classifiers = [
        c.split("License :: ")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    if classifiers:
        return " / ".join(
            c.replace("OSI Approved :: ", "") for c in classifiers
        )

    # Campo livre: só serve se for curto — muitos pacotes colam a licença inteira.
    livre = (meta.get("License") or "").strip()
    if livre and len(livre) <= 90 and "\n" not in livre:
        return livre

    return "não declarada nos metadados"


def diretas() -> set[str]:
    req = RAIZ / "requirements.txt"
    if not req.exists():
        return set()
    nomes = set()
    for linha in req.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if not linha or linha.startswith("-"):
            continue
        nome = re.split(r"[\[<>=!~;]", linha, 1)[0].strip()
        if nome:
            nomes.add(normalizar(nome))
    return nomes


def coletar() -> list[dict]:
    pacotes = {}
    for dist in metadata.distributions():
        nome = dist.metadata.get("Name")
        if not nome:
            continue
        chave = normalizar(nome)
        # Instalações duplicadas: fica a primeira encontrada.
        if chave in pacotes:
            continue
        pacotes[chave] = {
            "nome": nome,
            "versao": dist.version,
            "licenca": licenca_de(dist),
        }
    return [pacotes[k] for k in sorted(pacotes)]


def tabela(linhas: list[dict]) -> list[str]:
    if not linhas:
        return ["_Nenhum._", ""]
    out = ["| Pacote | Versão | Licença |", "|---|---|---|"]
    for p in linhas:
        out.append(f"| {p['nome']} | {p['versao']} | {p['licenca']} |")
    out.append("")
    return out


def main() -> int:
    todos = coletar()
    if not todos:
        print("Nenhum pacote encontrado — rode com o Python do venv.", file=sys.stderr)
        return 1

    nomes_diretos = diretas()
    ferramentas = {"pip", "setuptools", "wheel"}
    diretos, transitivos = [], []
    for p in todos:
        chave = normalizar(p["nome"])
        if chave in ferramentas:
            continue
        (diretos if chave in nomes_diretos else transitivos).append(p)

    atencao = [
        p for p in diretos + transitivos if COPYLEFT.search(p["licenca"])
    ]

    linhas = [
        f"# Licenças de terceiros — {PROJETO}",
        "",
        f"Gerado por `scripts/licencas_terceiros.py` em {date.today().isoformat()} "
        "a partir dos pacotes instalados no venv de produção.",
        f"Para regenerar: `./venv/bin/python scripts/licencas_terceiros.py`.",
        "",
        f"O código deste projeto é licenciado sob **{LICENCA_PROJETO}** (ver `LICENSE`). "
        "As bibliotecas abaixo permanecem sob suas licenças originais.",
        "",
        "## Dependências diretas",
        "",
        *tabela(diretos),
        "## Dependências transitivas",
        "",
        *tabela(transitivos),
    ]

    if PROGRAMAS_EXTERNOS:
        linhas += [
            "## Programas externos",
            "",
            "Invocados por `subprocess` como processos separados — não são linkados "
            "ao código deste projeto.",
            "",
            "| Programa | Versão | Licença | Observação |",
            "|---|---|---|---|",
            *[f"| {n} | {v} | {lic} | {obs} |" for n, v, lic, obs in PROGRAMAS_EXTERNOS],
            "",
        ]

    if atencao:
        linhas += [
            "## Componentes com licença recíproca (copyleft)",
            "",
            "Listados para conferência ao redistribuir o código ou ao combinar com "
            "componentes fechados. O uso como biblioteca, sem modificação e sem "
            "distribuição do binário, não propaga obrigações de abertura.",
            "",
            *tabela(atencao),
        ]

    if NOTAS:
        linhas += ["## Notas de manutenção", ""]
        linhas += [f"- {nota}" for nota in NOTAS]
        linhas.append("")

    destino = RAIZ / "docs" / "LICENCAS-TERCEIROS.md"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text("\n".join(linhas), encoding="utf-8")
    print(
        f"{destino}: {len(diretos)} diretas, {len(transitivos)} transitivas, "
        f"{len(atencao)} com copyleft."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
