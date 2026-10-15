"""Som: tocar pelos altifalantes e ouvir pelo microfone.

Usa o sounddevice (por cima da biblioteca PortAudio), que funciona igual no
Mac e no Pi. Substitui o afplay/aplay da primeira versão: tocar a partir da
memória permite saber o instante exato em que a fala começa (para sincronizar
o olho) e interromper a meio (para o botão "cala-te").

Dois problemas práticos que este módulo resolve sozinho:

- Canais: o reSpeaker, acedido diretamente, só aceita estéreo. A voz é mono.
  Duplicamos o canal ao tocar e fazemos a média dos dois microfones ao gravar.
- Taxa de amostragem: se o dispositivo recusar a taxa da voz (22050 Hz),
  reamostramos para a taxa que ele prefere e tentamos de novo.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np

from ciclope.tts import Fala

TAXA_OUVIDOS = 16000   # o Whisper, o openWakeWord e o VAD querem 16 kHz
BLOCO_OUVIDOS = 1280   # 80 ms a 16 kHz: o tamanho de bloco do openWakeWord


def _sd():
    """Importa o sounddevice só quando é preciso.

    No Linux, o import falha se a PortAudio não estiver instalada
    (sudo apt install libportaudio2). Assim, o resto do pacote — e os testes —
    funcionam sem ela.
    """
    import sounddevice

    return sounddevice


def listar_dispositivos() -> str:
    return str(_sd().query_devices())


def _dispositivo(nome: str) -> str | None:
    """'' significa o dispositivo predefinido; o sounddevice aceita parte do nome."""
    return nome or None


def reamostrar(x: np.ndarray, de: int, para: int) -> np.ndarray:
    """Muda a taxa de amostragem por interpolação linear.

    Não é o método de melhor qualidade, mas para voz é indistinguível e não
    obriga a mais uma dependência.
    """
    if de == para or len(x) == 0:
        return x
    n_saida = int(round(len(x) * para / de))
    t_entrada = np.arange(len(x)) / de
    t_saida = np.arange(n_saida) / para
    y = np.interp(t_saida, t_entrada, x.astype(np.float32))
    return np.clip(np.round(y), -32768, 32767).astype(np.int16)


class Altifalante:
    def __init__(self, dispositivo: str = ""):
        self.dispositivo = _dispositivo(dispositivo)
        info = _sd().query_devices(self.dispositivo, "output")
        self.nome = info["name"]
        self.canais = 2 if info["max_output_channels"] >= 2 else 1
        self.taxa_preferida = int(info["default_samplerate"])

    def tocar(
        self,
        fala: Fala,
        interromper: threading.Event | None = None,
        ao_comecar: Callable[[], None] | None = None,
    ) -> bool:
        """Toca e bloqueia até acabar. Devolve False se foi interrompida."""
        sd = _sd()
        try:
            return self._tocar(sd, fala.amostras, fala.taxa, interromper, ao_comecar)
        except sd.PortAudioError:
            if fala.taxa == self.taxa_preferida:
                raise
            # O dispositivo não aceita esta taxa: reamostra e tenta outra vez.
            amostras = reamostrar(fala.amostras, fala.taxa, self.taxa_preferida)
            return self._tocar(sd, amostras, self.taxa_preferida, interromper, ao_comecar)

    def _tocar(self, sd, amostras, taxa, interromper, ao_comecar) -> bool:
        # Mono → (n, canais): o mesmo som em todos os canais.
        dados = np.repeat(amostras.reshape(-1, 1), self.canais, axis=1)
        pos = 0
        terminou = threading.Event()

        # O PortAudio chama esta função num fio de execução (thread) próprio,
        # sempre que precisa de mais som — tipicamente a cada poucos ms.
        # Tem de ser rápida: só copia o próximo bocado do array.
        def callback(outdata, frames, _tempo, _estado):
            nonlocal pos
            if interromper is not None and interromper.is_set():
                outdata.fill(0)
                raise sd.CallbackStop
            bloco = dados[pos : pos + frames]
            outdata[: len(bloco)] = bloco
            if len(bloco) < frames:
                outdata[len(bloco) :] = 0
                raise sd.CallbackStop  # o último bocado ainda é tocado
            pos += frames

        with sd.OutputStream(
            samplerate=taxa,
            channels=self.canais,
            dtype="int16",
            device=self.dispositivo,
            callback=callback,
            finished_callback=terminou.set,
        ):
            if ao_comecar:
                ao_comecar()
            terminou.wait()
        return not (interromper is not None and interromper.is_set())


class Microfone:
    """Captura contínua em blocos de 80 ms, mono, int16, a 16 kHz.

    Usa-se com `with`: o microfone só está aberto dentro do bloco. É assim que
    fazemos meio-duplex — o microfone fecha antes de o ciclope falar, e ele
    nunca se ouve a si próprio.
    """

    def __init__(self, dispositivo: str = "", bloco: int = BLOCO_OUVIDOS):
        self.dispositivo = _dispositivo(dispositivo)
        self.bloco = bloco
        info = _sd().query_devices(self.dispositivo, "input")
        self.nome = info["name"]
        # O reSpeaker só grava em estéreo (um canal por microfone).
        self.canais = 2 if info["max_input_channels"] >= 2 else 1
        self.taxa_preferida = int(info["default_samplerate"])
        self._fila: queue.Queue[np.ndarray] = queue.Queue()
        self._resto = np.zeros(0, dtype=np.int16)
        self._stream = None
        self._taxa = TAXA_OUVIDOS

    def __enter__(self) -> "Microfone":
        sd = _sd()
        try:
            self._abrir(sd, TAXA_OUVIDOS)
        except sd.PortAudioError:
            # Sem 16 kHz nativos: grava na taxa do dispositivo e reamostra.
            self._abrir(sd, self.taxa_preferida)
        return self

    def _abrir(self, sd, taxa: int) -> None:
        self._taxa = taxa
        tamanho = int(round(self.bloco * taxa / TAXA_OUVIDOS))

        def callback(indata, _frames, _tempo, _estado):
            # Média dos canais → mono. O .copy() é obrigatório: o PortAudio
            # reutiliza o mesmo buffer na chamada seguinte.
            mono = indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0]
            self._fila.put(mono.astype(np.int16, copy=True))

        self._stream = sd.InputStream(
            samplerate=taxa,
            channels=self.canais,
            dtype="int16",
            blocksize=tamanho,
            device=self.dispositivo,
            callback=callback,
        )
        self._stream.start()

    def __exit__(self, *_exc) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._fila = queue.Queue()
        self._resto = np.zeros(0, dtype=np.int16)

    def ler(self, timeout: float = 2.0) -> np.ndarray:
        """Devolve exatamente `bloco` amostras a 16 kHz (bloqueia até haver)."""
        while len(self._resto) < self.bloco:
            novo = self._fila.get(timeout=timeout)
            if self._taxa != TAXA_OUVIDOS:
                novo = reamostrar(novo, self._taxa, TAXA_OUVIDOS)
            self._resto = np.concatenate([self._resto, novo])
        bloco, self._resto = self._resto[: self.bloco], self._resto[self.bloco :]
        return bloco
