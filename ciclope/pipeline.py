"""Resposta falada: modelo → frases → voz → altifalante, com o olho a acompanhar.

A grande diferença face à Fase 0 que escreveste: lá, cada frase era
sintetizada SÓ DEPOIS de a anterior acabar de tocar. Daí as pausas.

Aqui há dois trabalhadores ao mesmo tempo, ligados por uma fila:

    produtor (outro fio):  modelo → dividir → Piper + SoX → fila
    consumidor (este fio): fila → altifalante

Enquanto a frase 1 toca, a frase 2 já está a ser sintetizada. Quando a 1
acaba, a 2 está pronta e começa logo. É o padrão produtor-consumidor.

A fila tem tamanho máximo 2: se o produtor se adiantar demasiado, o put()
bloqueia até o consumidor tirar uma. Isso chama-se contrapressão
(backpressure) — impede de sintetizar uma resposta inteira que pode vir a
ser interrompida pelo botão.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from ciclope.audio import Altifalante
from ciclope.frases import dividir, limpar_para_voz
from ciclope.llm import Modelo
from ciclope.tts import Fala, Piper, aplicar_efeitos


@dataclass
class FalaPronta:
    fala: Fala
    envelope: list[int]


class Sinais:
    """Tudo o que mostra o estado do ciclope: o olho e os LEDs."""

    def __init__(self, cara, leds=None):
        self.cara = cara
        self.leds = leds

    def estado(self, estado: str) -> None:
        self.cara.estado(estado)
        if self.leds is not None:
            self.leds.estado(estado)

    def fala(self, envelope: list[int]) -> None:
        self.cara.fala(envelope)


class Boca:
    """Sintetiza, aplica a voz de ciclope e toca."""

    def __init__(
        self,
        piper: Piper,
        efeitos: Sequence[str],
        altifalante: Altifalante | None,
        janela_ms: int = 30,
    ):
        self.piper = piper
        self.efeitos = list(efeitos)
        self.altifalante = altifalante  # None = modo mudo
        self.janela_ms = janela_ms

    def preparar(self, texto: str) -> FalaPronta:
        fala = aplicar_efeitos(self.piper.sintetizar(texto), self.efeitos)
        # O envelope é calculado DEPOIS dos efeitos: o `speed` estica a
        # duração, e um envelope do áudio original ficaria cada vez mais
        # adiantado em relação à voz ao longo da frase.
        return FalaPronta(fala, fala.envelope(self.janela_ms))

    def dizer(
        self,
        pronta: FalaPronta,
        interromper: threading.Event,
        ao_comecar: Callable[[], None] | None = None,
    ) -> bool:
        """Toca a frase. Devolve False se foi interrompida."""
        if self.altifalante is None:
            # Mudo: o olho mexe-se como se falasse, mas não sai som.
            if ao_comecar:
                ao_comecar()
            return not interromper.wait(pronta.fala.duracao)
        return self.altifalante.tocar(pronta.fala, interromper, ao_comecar)


@dataclass
class Metricas:
    ttpw: float | None = None   # time to first word: até à primeira sílaba audível
    total: float = 0.0
    audio: float = 0.0
    frases: int = 0
    interrompida: bool = False

    def resumo(self) -> str:
        primeira = f"{self.ttpw:.2f}s" if self.ttpw is not None else "—"
        # rácio > 1: o sistema demorou mais do que a fala que produziu.
        racio = self.total / self.audio if self.audio else 0.0
        extra = " · interrompida" if self.interrompida else ""
        return (
            f"[primeira palavra {primeira} · total {self.total:.2f}s · "
            f"áudio {self.audio:.2f}s · rácio {racio:.2f} · {self.frases} frases{extra}]"
        )


_FIM = object()  # marcador único: "não há mais frases"


def _ate_parar(pedacos, *eventos: threading.Event):
    """Deixa passar os pedaços do modelo até um dos eventos ser ativado.

    Verificar a cada pedaço (e não só a cada frase) faz o botão "cala-te"
    responder logo, mesmo que o modelo esteja a meio de uma frase longa.
    """
    for pedaco in pedacos:
        if any(e.is_set() for e in eventos):
            return
        yield pedaco


def responder(
    modelo: Modelo,
    historico: list[dict],
    sistema: str,
    boca: Boca,
    sinais: Sinais,
    interromper: threading.Event,
    ao_dizer: Callable[[str], None] = print,
    t0: float | None = None,
) -> tuple[str, Metricas]:
    """Pede a resposta ao modelo e di-la em voz alta, frase a frase.

    Devolve o texto efetivamente dito (para o histórico) e as métricas.
    `t0` (de time.perf_counter) é o instante a partir do qual se mede a
    primeira palavra; por omissão, agora.
    """
    t0 = time.perf_counter() if t0 is None else t0
    metricas = Metricas()
    fila: queue.Queue = queue.Queue(maxsize=2)
    erros: list[Exception] = []
    # "Desiste": ativado se o consumidor sair por um erro, para o produtor não
    # ficar pendurado para sempre num put() que ninguém vai ler.
    desistir = threading.Event()

    def por(item) -> bool:
        while not desistir.is_set():
            try:
                fila.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def produzir() -> None:
        gerador = dividir(_ate_parar(modelo.stream(historico, sistema=sistema), interromper, desistir))
        try:
            for frase in gerador:
                if interromper.is_set() or desistir.is_set():
                    break
                texto = limpar_para_voz(frase)
                if texto and not por(boca.preparar(texto)):
                    break
        except Exception as e:  # rede, chave errada, SoX em falta...
            erros.append(e)
        finally:
            # Fechar o gerador fecha também o pedido HTTP a meio, se for o caso.
            gerador.close()
            por(_FIM)  # garante que o consumidor acorda e sai

    sinais.estado("a_pensar")
    produtor = threading.Thread(target=produzir, name="produtor-voz", daemon=True)
    produtor.start()

    ditas: list[str] = []
    try:
        while True:
            item = fila.get()
            if item is _FIM:
                break
            if interromper.is_set():
                continue  # interrompido: esvazia a fila sem tocar

            pronta: FalaPronta = item

            def ao_comecar(pronta=pronta) -> None:
                if metricas.ttpw is None:
                    metricas.ttpw = time.perf_counter() - t0
                sinais.estado("a_falar")
                sinais.fala(pronta.envelope)

            ao_dizer(pronta.fala.texto)
            completa = boca.dizer(pronta, interromper, ao_comecar)
            ditas.append(pronta.fala.texto)
            metricas.audio += pronta.fala.duracao
            metricas.frases += 1
            if not completa:
                metricas.interrompida = True
    finally:
        # Se saímos por um erro (o altifalante desapareceu, por exemplo),
        # avisamos o produtor e esperamos que acabe. Assim nunca ficam dois
        # produtores ao mesmo tempo a usar a mesma ligação ao modelo.
        desistir.set()
        produtor.join(timeout=10)

    metricas.total = time.perf_counter() - t0
    metricas.interrompida = metricas.interrompida or interromper.is_set()
    if erros:
        if not ditas:
            raise erros[0]
        print(f"  [aviso] a resposta foi cortada por um erro: {erros[0]}")
    return " ".join(ditas), metricas
