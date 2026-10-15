"""Voz: texto → áudio (Piper) → voz de ciclope (SoX) → envelope para o olho.

Mudança face à primeira versão que escreveste: lá chamávamos o executável
`piper` a cada frase. Isso recarrega o modelo de ~60 MB de cada vez — e era a
maior fatia das pausas entre frases. Aqui o modelo é carregado UMA vez, com a
API Python do Piper, e fica em memória.

O SoX continua a ser chamado como subprocesso: corre em poucos milissegundos e
mantém a cadeia de efeitos que afinaste exatamente como estava.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np


@dataclass
class Fala:
    """Um bocado de áudio pronto a tocar e a animar."""

    texto: str
    amostras: np.ndarray  # int16, mono
    taxa: int             # amostras por segundo (sample rate)

    @property
    def duracao(self) -> float:
        return len(self.amostras) / self.taxa

    def envelope(self, janela_ms: int = 30) -> list[int]:
        """Volume ao longo do tempo, como inteiros de 0 a 255.

        Parte o áudio em janelas de `janela_ms` e calcula o RMS (raiz da média
        dos quadrados) de cada uma. O resultado sobe nas sílabas e desce nas
        pausas — é o que faz o olho pulsar com a voz.

        Inteiros de 0 a 255 em vez de decimais: a mensagem para o ESP32 fica
        três a quatro vezes mais pequena, e a resolução chega e sobra.
        """
        x = self.amostras.astype(np.float32)
        por_janela = max(1, int(self.taxa * janela_ms / 1000))
        n = int(np.ceil(len(x) / por_janela))
        if n == 0:
            return []
        # Acrescenta zeros no fim para o comprimento ser múltiplo da janela,
        # e depois reorganiza numa matriz (n janelas × amostras por janela).
        x = np.pad(x, (0, n * por_janela - len(x)))
        rms = np.sqrt(np.mean(x.reshape(n, por_janela) ** 2, axis=1))
        pico = float(rms.max())
        if pico <= 0:
            return [0] * n
        # A raiz quadrada comprime a gama dinâmica: sem ela, o olho ficaria
        # quase sempre "fechado", porque a fala tem picos curtos e muito
        # volume médio baixo.
        normal = np.sqrt(rms / pico)
        return [int(v) for v in np.clip(np.round(normal * 255), 0, 255)]


class Piper:
    """Sintetizador Piper com o modelo carregado em memória."""

    def __init__(self, modelo: str | Path, velocidade: float = 1.0):
        modelo = Path(modelo).expanduser()
        if not modelo.is_file():
            raise FileNotFoundError(
                f"Voz não encontrada: {modelo}\n"
                "Descarrega-a com: scripts/descarregar-voz.sh "
                "(ou ajusta [voz] modelo no ciclope.toml)"
            )
        if not modelo.with_name(modelo.name + ".json").is_file():
            raise FileNotFoundError(f"Falta o {modelo.name}.json ao lado do modelo — o Piper precisa dos dois.")

        # Import aqui dentro: carregar o piper demora, e só o pagamos quando
        # a voz é mesmo precisa (os testes, por exemplo, não precisam).
        from piper import PiperVoice, SynthesisConfig

        self._voz = PiperVoice.load(str(modelo))
        self._config = SynthesisConfig(length_scale=velocidade)
        self.taxa = int(self._voz.config.sample_rate)

    def sintetizar(self, texto: str) -> Fala:
        # O Piper devolve o áudio aos bocados (um por frase que ele próprio
        # deteta). Juntamos tudo num só array.
        blocos = [c.audio_int16_array for c in self._voz.synthesize(texto, syn_config=self._config)]
        amostras = np.concatenate(blocos) if blocos else np.zeros(0, dtype=np.int16)
        return Fala(texto=texto, amostras=amostras.astype(np.int16, copy=False), taxa=self.taxa)


class ErroSox(Exception):
    pass


def aplicar_efeitos(fala: Fala, efeitos: Sequence[str], timeout: float = 30) -> Fala:
    """Passa o áudio pelo SoX com a cadeia de efeitos dada.

    Usamos PCM cru (só amostras, sem cabeçalho WAV) à entrada e à saída, e
    declaramos o formato à mão com -t raw, -r, -e, -b, -c. O `-` no lugar do
    nome de ficheiro é a convenção Unix para "usa stdin/stdout".

    Forçar a mesma taxa à saída faz o SoX reamostrar depois do `speed`, por
    isso sabemos sempre a que taxa está o resultado.

    O -G (guard) baixa o volume o necessário para nenhum efeito saturar. Sem
    ele, como o Piper já entrega a voz no volume máximo, o `pitch` e o
    `reverb` passavam do limite e cortavam picos — um som áspero, e o SoX só
    avisa no stderr, sem dar erro.
    """
    if not efeitos or len(fala.amostras) == 0:
        return fala
    if shutil.which("sox") is None:
        raise ErroSox(
            "O SoX não está instalado. Mac: brew install sox · Pi: sudo apt install sox\n"
            "(ou põe efeitos = [] no ciclope.toml para desligar a voz de ciclope)"
        )

    formato = ["-t", "raw", "-r", str(fala.taxa), "-e", "signed-integer", "-b", "16", "-c", "1"]
    resultado = subprocess.run(
        ["sox", "-G", *formato, "-", *formato, "-", *efeitos],
        input=fala.amostras.astype("<i2").tobytes(),  # <i2 = int16 little-endian
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if resultado.returncode != 0:
        erro = resultado.stderr.decode("utf-8", errors="replace").strip()
        raise ErroSox(f"sox falhou (código {resultado.returncode}): {erro}")

    amostras = np.frombuffer(resultado.stdout, dtype="<i2").astype(np.int16)
    return Fala(texto=fala.texto, amostras=amostras, taxa=fala.taxa)
