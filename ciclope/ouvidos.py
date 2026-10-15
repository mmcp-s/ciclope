"""Ouvidos: acordar, ouvir até ao silêncio, transcrever.

Três peças independentes, cada uma substituível:

- Despertador: openWakeWord, deteta a palavra de ativação.
- Gravador:    webrtcvad, decide quando acabaste de falar.
- Transcritor: faster-whisper, converte a gravação em texto.

Sobre o detetor de voz: na conversa falámos do Silero VAD. O pacote
`silero-vad` obriga a instalar o PyTorch (centenas de MB) no Pi, por isso aqui
usamos o webrtcvad, que é minúsculo e rápido. Para decidir "a pessoa calou-se"
chega perfeitamente. O Whisper traz o seu próprio Silero (sem PyTorch) se um
dia quiseres o vad_filter dele.

Todos os imports pesados ficam dentro dos construtores: o modo `ciclope texto`
não precisa de nada disto instalado.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ciclope.audio import BLOCO_OUVIDOS, TAXA_OUVIDOS, Microfone


class Despertador:
    """Palavra de ativação com o openWakeWord."""

    def __init__(self, palavra: str = "hey_jarvis", limiar: float = 0.5):
        import openwakeword.utils
        from openwakeword.model import Model

        self.limiar = limiar
        if Path(palavra).expanduser().is_file():
            modelos = [str(Path(palavra).expanduser())]
            # Um modelo teu precisa na mesma dos modelos de base (espectrograma
            # e "embedding") do openWakeWord. O download_models() traz sempre
            # esses; com um nome que não existe, não traz mais nada.
            openwakeword.utils.download_models(model_names=["(só os modelos de base)"])
        else:
            # Descarrega o modelo pré-treinado e os de base na primeira vez;
            # depois ficam em cache.
            openwakeword.utils.download_models(model_names=[palavra])
            modelos = [palavra]
        # "onnx" em vez do "tflite" por omissão: o tflite-runtime não existe
        # para macOS e parte-se com o NumPy 2 no Linux. O ONNX funciona nos dois.
        self._modelo = Model(wakeword_models=modelos, inference_framework="onnx")

    def ouviu(self, bloco: np.ndarray) -> bool:
        """Recebe 1280 amostras int16 a 16 kHz. True se ouviu a palavra.

        Atenção: o openWakeWord converte tudo para int16 sem avisar. Se lhe
        passares float32 entre -1 e 1, vira silêncio e nunca deteta nada.
        """
        pontuacoes = self._modelo.predict(bloco.astype(np.int16, copy=False))
        return max(pontuacoes.values(), default=0.0) >= self.limiar

    def reiniciar(self) -> None:
        """Limpa o histórico interno, para não disparar duas vezes seguidas."""
        self._modelo.reset()


class Gravador:
    """Grava a partir do momento em que acorda até haver silêncio."""

    def __init__(
        self,
        agressividade: int = 2,
        silencio_fim_ms: int = 800,
        espera_max_s: float = 6.0,
        fala_max_s: float = 15.0,
    ):
        import webrtcvad

        self._vad = webrtcvad.Vad(agressividade)
        self.silencio_fim_ms = silencio_fim_ms
        self.espera_max_s = espera_max_s
        self.fala_max_s = fala_max_s

    # O webrtcvad só aceita janelas de 10, 20 ou 30 ms. Um bloco de 80 ms
    # parte-se em quatro janelas de 20 ms (320 amostras a 16 kHz).
    _JANELA = 320
    _MS_JANELA = 20

    def _tem_voz(self, bloco: np.ndarray) -> bool:
        votos = 0
        for i in range(0, len(bloco) - self._JANELA + 1, self._JANELA):
            janela = bloco[i : i + self._JANELA].astype("<i2").tobytes()
            if self._vad.is_speech(janela, TAXA_OUVIDOS):
                votos += 1
        # Maioria simples: pelo menos metade das janelas com voz.
        return votos * 2 >= len(bloco) // self._JANELA

    def gravar(self, microfone: Microfone) -> np.ndarray | None:
        """Devolve o áudio da fala (int16, 16 kHz), ou None se ninguém falou."""
        blocos: list[np.ndarray] = []
        ms_bloco = BLOCO_OUVIDOS * 1000 // TAXA_OUVIDOS  # 80 ms
        inicio = time.monotonic()
        inicio_fala = inicio
        falou = False
        seguidos_com_voz = 0
        silencio_ms = 0

        while True:
            bloco = microfone.ler()
            blocos.append(bloco)
            voz = self._tem_voz(bloco)

            if not falou:
                # Exige dois blocos seguidos com voz (160 ms) para não
                # arrancar com um estalido ou uma tosse.
                seguidos_com_voz = seguidos_com_voz + 1 if voz else 0
                if seguidos_com_voz >= 2:
                    falou = True
                    inicio_fala = time.monotonic()
                elif time.monotonic() - inicio > self.espera_max_s:
                    return None
                else:
                    # Antes de começar a falar, guarda só um pouco de margem.
                    blocos = blocos[-4:]
                continue

            silencio_ms = 0 if voz else silencio_ms + ms_bloco
            if silencio_ms >= self.silencio_fim_ms:
                break
            if time.monotonic() - inicio_fala > self.fala_max_s:
                break

        return np.concatenate(blocos)


class Transcritor:
    """Fala → texto com o faster-whisper."""

    def __init__(self, modelo: str = "small", idioma: str = "pt"):
        from faster_whisper import WhisperModel

        # int8: pesos quantizados a 8 bits. Cerca de 4× menos memória e bem
        # mais rápido em CPU, com perda de qualidade quase impercetível.
        # Na primeira vez descarrega o modelo do Hugging Face.
        self._modelo = WhisperModel(modelo, device="cpu", compute_type="int8")
        self.idioma = idioma

    def transcrever(self, audio: np.ndarray) -> str:
        # O Whisper quer float32 entre -1 e 1; temos int16 entre -32768 e 32767.
        x = audio.astype(np.float32) / 32768.0
        segmentos, _info = self._modelo.transcribe(
            x,
            language=self.idioma,
            beam_size=1,                       # pesquisa gulosa: mais rápida
            condition_on_previous_text=False,  # cada pergunta é independente
        )
        # `segmentos` é um gerador preguiçoso: a transcrição só acontece aqui,
        # quando o percorremos.
        return " ".join(s.text.strip() for s in segmentos).strip()


class Ouvidos:
    """Junta as três peças: espera a ativação, grava, transcreve."""

    def __init__(
        self,
        microfone: Microfone,
        transcritor: Transcritor,
        gravador: Gravador,
        despertador: Despertador | None = None,
    ):
        self.microfone = microfone
        self.transcritor = transcritor
        self.gravador = gravador
        self.despertador = despertador
        # Instante (time.perf_counter) em que acabou a última gravação: é daí
        # que se mede "primeira palavra" no modo voz, com a transcrição incluída.
        self.fim_gravacao: float | None = None

    def esperar_palavra(self, parar=None) -> bool:
        """Bloqueia até ouvir a palavra de ativação.

        Devolve False se `parar` (um threading.Event) for ativado entretanto —
        por exemplo, porque carregaste no botão em vez de falar.
        """
        assert self.despertador is not None
        self.despertador.reiniciar()
        with self.microfone as mic:
            while parar is None or not parar.is_set():
                if self.despertador.ouviu(mic.ler()):
                    return True
        return False

    def ouvir_pergunta(self) -> str:
        """Abre o microfone, grava até ao silêncio, fecha, transcreve."""
        with self.microfone as mic:
            audio = self.gravador.gravar(mic)
        self.fim_gravacao = time.perf_counter()
        if audio is None:
            return ""
        return self.transcritor.transcrever(audio)
