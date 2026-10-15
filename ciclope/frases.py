"""Divide o texto que chega aos pedaços em frases completas.

É a peça que torna o streaming útil: assim que a primeira frase fecha, segue
para a voz enquanto o modelo continua a gerar o resto. Sem isto, esperavas a
resposta inteira antes de ouvir a primeira palavra.

`dividir` é um gerador e uma pequena máquina de estados: o estado é o texto
acumulado (`buffer`) que ainda não formou frase. Como a função congela em cada
`yield` em vez de terminar, o buffer sobrevive entre entregas.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

# Fim de frase: pontuação forte (uma ou mais, para as reticências contarem
# como um só fim), aspas ou parênteses de fecho opcionais, e depois espaço.
# O (?=\s) é um lookahead: exige o espaço sem o consumir — é o que impede
# "3.14" de ser cortado. A segunda alternativa trata quebras de linha como fim
# de frase (listas sem pontuação).
#
# Atenção ao que NÃO está aqui: "ou fim de texto" ($). Num stream, o fim do
# buffer não é o fim do texto — o modelo manda muitas vezes o "." num pedaço
# sozinho, e o "14" de "3.14" só chega no pedaço seguinte. O fim verdadeiro do
# texto é tratado à parte, quando o stream acaba.
_FIM_FRASE = re.compile(r"(?:[.!?…]+[\"'»)\]]*(?=\s)|\n+)")

# Pausa fraca: só usada como escape quando uma frase se arrasta demasiado.
_PAUSA = re.compile(r"[,;:—](?=\s)")

# Abreviaturas comuns que acabam em ponto mas não fecham frase.
_ABREVIATURAS = {
    "sr", "sra", "dr", "dra", "eng", "prof", "profa", "av", "ex", "exa",
    "etc", "fig", "vol", "pág", "págs", "séc", "lda", "nº", "n.º", "cap",
}


def _e_abreviatura(texto: str, pos: int) -> bool:
    """Verifica se o ponto na posição `pos` pertence a uma abreviatura conhecida."""
    inicio = max(0, pos - 12)
    palavra = re.split(r"[\s(]", texto[inicio:pos].lower())[-1]
    return palavra in _ABREVIATURAS


def dividir(pedacos: Iterable[str], max_chars: int = 180, min_chars: int = 12) -> Iterator[str]:
    """Consome pedaços de texto e produz frases completas.

    max_chars: se o buffer passar disto sem pontuação forte, corta na primeira
               pausa fraca, para não ficar à espera numa enumeração longa.
    min_chars: não emite fragmentos minúsculos ("Sim.") isolados — junta-os à
               frase seguinte, para o Piper não fazer pausas artificiais.
    """
    buffer = ""

    for pedaco in pedacos:
        buffer += pedaco

        # Um só pedaço pode fechar várias frases ("bem. E tu? Diz"); o while
        # continua a cortar até não haver mais nada pronto.
        while True:
            corte = None

            for m in _FIM_FRASE.finditer(buffer):
                if m.group().startswith(".") and _e_abreviatura(buffer, m.start()):
                    continue
                if m.end() >= min_chars:
                    corte = m.end()
                    break

            if corte is None and len(buffer) > max_chars:
                # Corta na ÚLTIMA pausa que caiba em max_chars, para os
                # pedaços saírem compridos; só se não houver nenhuma é que
                # aceita a primeira depois disso.
                pausas = [m.end() for m in _PAUSA.finditer(buffer, min_chars)]
                cabem = [p for p in pausas if p <= max_chars]
                if cabem or pausas:
                    corte = (cabem or pausas)[-1 if cabem else 0]

            if corte is None:
                break

            frase = buffer[:corte].strip()
            buffer = buffer[corte:].lstrip()
            if frase:
                yield frase

    # O modelo pode acabar sem pontuação final: despeja o que sobrou.
    resto = buffer.strip()
    if resto:
        yield resto


# Marcas de markdown que não fazem sentido em voz alta.
_MARCADOR_LINHA = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
_ENFASE = re.compile(r"[*_`#>|]+")
_ESPACOS = re.compile(r"\s+")


def limpar_para_voz(frase: str) -> str:
    """Remove markdown que o modelo deixe escapar apesar das instruções.

    Defesa em profundidade: o prompt de sistema já pede texto corrido, mas se
    vier um "**importante**", o Piper não o deve ler com asteriscos.
    """
    texto = _MARCADOR_LINHA.sub("", frase)
    texto = _ENFASE.sub("", texto)
    return _ESPACOS.sub(" ", texto).strip()
