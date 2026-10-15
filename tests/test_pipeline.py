"""Testa a resposta falada com peças falsas: sem rede, sem Piper, sem som."""

import threading
import time

import numpy as np

from ciclope.main import Conversa
from ciclope.pipeline import Boca, FalaPronta, Sinais, responder
from ciclope.tts import Fala


class ModeloFalso:
    fornecedor, nome = "falso", "falso"

    def __init__(self, texto: str, atraso: float = 0.0):
        self.texto, self.atraso = texto, atraso

    def stream(self, mensagens, sistema=""):
        for i in range(0, len(self.texto), 5):
            time.sleep(self.atraso)
            yield self.texto[i : i + 5]


class BocaFalsa(Boca):
    def __init__(self, tempo_sintese: float = 0.0, interromper_na: int | None = None, evento=None):
        self.tocadas: list[str] = []
        self.tempo_sintese = tempo_sintese
        self.interromper_na = interromper_na
        self.evento = evento

    def preparar(self, texto):
        time.sleep(self.tempo_sintese)
        return FalaPronta(Fala(texto, np.zeros(2205, dtype=np.int16), 22050), [10, 200, 30])

    def dizer(self, pronta, interromper, ao_comecar=None):
        if ao_comecar:
            ao_comecar()
        self.tocadas.append(pronta.fala.texto)
        if self.interromper_na is not None and len(self.tocadas) == self.interromper_na:
            self.evento.set()
            return False
        return True


class CaraFalsa:
    def __init__(self):
        self.registo: list = []

    def estado(self, e):
        self.registo.append(("estado", e))

    def fala(self, env):
        self.registo.append(("fala", env))


TEXTO = "Sou o guardião desta caverna. Ninguém entra sem licença. Quem és tu, afinal?"


def test_diz_todas_as_frases_por_ordem_e_mede():
    cara = CaraFalsa()
    boca = BocaFalsa()
    texto, m = responder(ModeloFalso(TEXTO), [], "", boca, Sinais(cara), threading.Event(), ao_dizer=lambda f: None)
    assert boca.tocadas == [
        "Sou o guardião desta caverna.",
        "Ninguém entra sem licença.",
        "Quem és tu, afinal?",
    ]
    assert texto == TEXTO
    assert m.frases == 3 and m.ttpw is not None and not m.interrompida
    assert cara.registo[0] == ("estado", "a_pensar")
    assert ("estado", "a_falar") in cara.registo and ("fala", [10, 200, 30]) in cara.registo


def test_interrupcao_para_de_falar_e_esvazia_a_fila():
    evento = threading.Event()
    boca = BocaFalsa(interromper_na=1, evento=evento)
    texto, m = responder(ModeloFalso(TEXTO), [], "", boca, Sinais(CaraFalsa()), evento, ao_dizer=lambda f: None)
    assert boca.tocadas == ["Sou o guardião desta caverna."]
    assert m.interrompida
    assert texto == "Sou o guardião desta caverna."


def test_sintese_em_paralelo_poupa_tempo():
    # Cada síntese demora 0,2 s e cada frase "toca" 0,2 s. Em série seriam
    # ~1,2 s; com produtor e consumidor em paralelo, bastante menos.
    class BocaLenta(BocaFalsa):
        def dizer(self, pronta, interromper, ao_comecar=None):
            time.sleep(0.2)
            return super().dizer(pronta, interromper, ao_comecar)

    boca = BocaLenta(tempo_sintese=0.2)
    _, m = responder(ModeloFalso(TEXTO), [], "", boca, Sinais(CaraFalsa()), threading.Event(), ao_dizer=lambda f: None)
    assert m.total < 1.05


def test_erro_do_modelo_sem_nada_dito_propaga():
    class ModeloPartido:
        def stream(self, mensagens, sistema=""):
            raise RuntimeError("sem rede")
            yield  # torna isto um gerador

    try:
        responder(ModeloPartido(), [], "", BocaFalsa(), Sinais(CaraFalsa()), threading.Event(), ao_dizer=lambda f: None)
    except RuntimeError as e:
        assert "sem rede" in str(e)
    else:
        raise AssertionError("devia ter propagado o erro")


def test_falha_ao_tocar_nao_deixa_o_produtor_pendurado():
    # O altifalante falha (por exemplo, desligado a meio). O produtor não
    # pode ficar bloqueado para sempre a tentar pôr frases na fila.
    class BocaAvariada(BocaFalsa):
        def dizer(self, pronta, interromper, ao_comecar=None):
            raise OSError("altifalante desapareceu")

    antes = {t for t in threading.enumerate() if t.name == "produtor-voz"}
    for _ in range(3):
        try:
            responder(ModeloFalso(TEXTO * 3), [], "", BocaAvariada(), Sinais(CaraFalsa()),
                      threading.Event(), ao_dizer=lambda f: None)
        except OSError:
            pass
    time.sleep(0.3)
    vivos = {t for t in threading.enumerate() if t.name == "produtor-voz"} - antes
    assert not vivos


def test_interrupcao_corta_o_modelo_a_meio_da_frase():
    # Uma "frase" enorme sem pontuação: o botão tem de a cortar na mesma.
    evento = threading.Event()
    modelo = ModeloFalso("palavra " * 400, atraso=0.002)
    threading.Timer(0.05, evento.set).start()
    t = time.perf_counter()
    responder(modelo, [], "", BocaFalsa(), Sinais(CaraFalsa()), evento, ao_dizer=lambda f: None)
    assert time.perf_counter() - t < 1.0  # o modelo inteiro demoraria ~1,6 s


def test_conversa_corta_o_historico_e_comeca_sempre_numa_pergunta():
    c = Conversa(maximo=4)
    for i in range(5):
        c.perguntar(f"p{i}")
        c.responder(f"r{i}")
    assert [m["content"] for m in c.mensagens] == ["p3", "r3", "p4", "r4"]
    assert c.mensagens[0]["role"] == "user"
    c.perguntar("p5")
    c.responder("")  # falhou: a pergunta é esquecida
    assert c.mensagens[-1]["content"] == "r4"
