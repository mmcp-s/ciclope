"""Linha de comandos do Ciclope.

    ciclope texto           escreves, ele responde em voz alta        (Fase 0)
    ciclope voz             falas, ele ouve e responde                (Fase 1)
    ciclope dizer "Olá"     diz uma frase (testa voz + olho)
    ciclope cara-demo       anima o olho sem modelo nem voz           (Fase 2)
    ciclope testar-modelo   confirma que a chave da API funciona
    ciclope dispositivos    lista os dispositivos de som

Opções comuns: -c ficheiro.toml · --mudo (sem som) · --sem-cara (sem olho)
Podem vir antes ou depois do comando: `ciclope --mudo texto` = `ciclope texto --mudo`.
Para ver o erro completo (traceback) em vez de uma linha: CICLOPE_DEBUG=1 ciclope ...
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import random
import sys
import threading
import time

from ciclope.cara import Cara, CaraNula
from ciclope.config import Config, ErroConfig, carregar, resolver_caminho
from ciclope.frases import dividir
from ciclope.llm import Modelo, criar_modelo
from ciclope.pipeline import Boca, Sinais, responder

COMANDOS = ("texto", "voz", "dizer", "cara-demo", "testar-modelo", "dispositivos")


# --- montagem ---------------------------------------------------------------


class Ciclope:
    """Monta as peças a partir da configuração e arruma-as no fim."""

    def __init__(self, cfg: Config, mudo: bool = False, sem_cara: bool = False, com_voz: bool = True):
        self.cfg = cfg
        self.respeaker = None

        if cfg.cara.ativa and not sem_cara:
            self.cara = Cara(cfg.cara.porta_ws, cfg.cara.porta_http, cfg.cara.janela_ms)
            self.cara.iniciar()
            for linha in self.cara.enderecos():
                print(f"  {linha}")
        else:
            self.cara = CaraNula()

        if cfg.hardware.respeaker:
            from ciclope.hardware import ReSpeaker

            self.respeaker = ReSpeaker(cfg.hardware.botao_gpio, cfg.hardware.leds, cfg.hardware.brilho_leds)

        self.sinais = Sinais(self.cara, self.respeaker)
        self.boca: Boca | None = None

        if com_voz:
            from ciclope.tts import Piper

            caminho = resolver_caminho(cfg, cfg.voz.modelo)
            print(f"  voz: {caminho.name} · efeitos: {' '.join(cfg.voz.efeitos) or 'nenhum'}")
            piper = Piper(caminho, cfg.voz.velocidade)

            altifalante = None
            if not mudo:
                from ciclope.audio import Altifalante

                altifalante = Altifalante(cfg.audio.saida)
                print(f"  saída de som: {altifalante.nome}")

            self.boca = Boca(piper, cfg.voz.efeitos, altifalante, cfg.cara.janela_ms)
            # Aquecimento: a primeira síntese é sempre mais lenta (o motor ONNX
            # prepara-se). Fazemo-la já, em silêncio, para não contaminar a
            # medição da primeira resposta — e para descobrir logo se falta o SoX.
            self.boca.preparar("Olá.")

    def fechar(self) -> None:
        self.sinais.estado("inativo")
        self.cara.parar()
        if self.respeaker is not None:
            self.respeaker.fechar()


class Conversa:
    """Histórico de mensagens. A API não tem memória: reenviamos isto sempre."""

    def __init__(self, maximo: int):
        self.maximo = maximo
        self.mensagens: list[dict] = []

    def perguntar(self, texto: str) -> None:
        self.mensagens.append({"role": "user", "content": texto})

    def responder(self, texto: str) -> None:
        if texto:
            self.mensagens.append({"role": "assistant", "content": texto})
        else:
            self.mensagens.pop()  # sem resposta: esquece a pergunta
        # Corta as mais antigas, mantendo sempre um número par, para a
        # conversa começar numa pergunta (a API da Anthropic exige isso).
        excesso = len(self.mensagens) - self.maximo
        if excesso > 0:
            self.mensagens[:] = self.mensagens[excesso + (excesso % 2) :]


def _uma_resposta(
    c: Ciclope,
    modelo: Modelo,
    conversa: Conversa,
    pergunta: str,
    interromper: threading.Event,
    t0: float | None = None,
) -> bool:
    """Uma pergunta, uma resposta falada. Devolve False se correu mal."""
    conversa.perguntar(pergunta)
    interromper.clear()
    try:
        assert c.boca is not None
        texto, metricas = responder(
            modelo,
            conversa.mensagens,
            c.cfg.personagem.sistema,
            c.boca,
            c.sinais,
            interromper,
            ao_dizer=lambda frase: print(f"ciclope > {frase}", flush=True),
            t0=t0,
        )
        conversa.responder(texto)
        print(f"  {metricas.resumo()}\n")
        c.sinais.estado("inativo")
        return True
    except Exception as e:
        conversa.responder("")
        c.sinais.estado("erro")
        print(f"  [erro] {e}\n", file=sys.stderr)
        return False


# --- comandos ----------------------------------------------------------------


def cmd_texto(cfg: Config, args) -> int:
    print("Ciclope — modo texto (Fase 0)")
    modelo = criar_modelo(cfg.modelo)
    print(f"  modelo: {modelo.fornecedor} · {modelo.nome}")
    c = Ciclope(cfg, mudo=args.mudo, sem_cara=args.sem_cara)
    conversa = Conversa(cfg.modelo.historico)
    interromper = threading.Event()
    print("\nEscreve uma pergunta. Ctrl-D ou Ctrl-C para sair.\n")
    try:
        while True:
            try:
                pergunta = input("tu > ").strip()
            except EOFError:
                break
            if pergunta:
                _uma_resposta(c, modelo, conversa, pergunta, interromper)
    except KeyboardInterrupt:
        pass
    finally:
        print()
        c.fechar()
    return 0


def cmd_voz(cfg: Config, args) -> int:
    from ciclope.audio import Microfone
    from ciclope.ouvidos import Despertador, Gravador, Ouvidos, Transcritor

    o = cfg.ouvidos
    if o.ativacao == "enter" and not sys.stdin.isatty():
        # Num serviço systemd não há teclado: o input() falharia logo.
        print("[erro] ativacao = 'enter' precisa de um terminal. Num serviço, usa 'palavra' ou 'botao'.",
              file=sys.stderr)
        return 1
    print("Ciclope — modo voz (Fase 1)")
    modelo = criar_modelo(cfg.modelo)
    print(f"  modelo: {modelo.fornecedor} · {modelo.nome}")
    c = Ciclope(cfg, mudo=args.mudo, sem_cara=args.sem_cara)

    microfone = Microfone(cfg.audio.entrada)
    print(f"  microfone: {microfone.nome}")
    print(f"  a carregar o Whisper '{o.whisper}' (a primeira vez descarrega-o)...")
    ouvidos = Ouvidos(
        microfone,
        Transcritor(o.whisper, o.idioma),
        Gravador(o.agressividade_vad, o.silencio_fim_ms, o.espera_max_s, o.fala_max_s),
        Despertador(o.palavra, o.limiar_palavra) if o.ativacao == "palavra" else None,
    )

    conversa = Conversa(cfg.modelo.historico)
    interromper = threading.Event()  # "cala-te"
    acordar = threading.Event()      # "ouve-me"
    a_responder = threading.Event()

    def botao() -> None:
        # Corre num fio do gpiozero. Se estiver a falar, cala-o; senão, acorda-o.
        if a_responder.is_set():
            interromper.set()
        else:
            acordar.set()

    if c.respeaker is not None:
        c.respeaker.ao_carregar(botao)
    if o.ativacao == "botao" and not (c.respeaker and c.respeaker.tem_botao):
        print("  [erro] ativacao = 'botao' precisa de [hardware] respeaker = true e do botão a funcionar.")
        c.fechar()
        return 1

    instrucao = {
        "palavra": f"Diz '{o.palavra.replace('_', ' ')}' (ou carrega no botão) para falar.",
        "botao": "Carrega no botão do reSpeaker para falar.",
        "enter": "Carrega Enter para falar.",
    }.get(o.ativacao)
    if instrucao is None:
        print(f"  [erro] ativacao desconhecida: {o.ativacao!r} (usa palavra, botao ou enter)")
        c.fechar()
        return 1
    print(f"\n{instrucao} Ctrl-C para sair.\n")

    try:
        while True:
            c.sinais.estado("inativo")
            acordar.clear()
            try:
                if o.ativacao == "palavra":
                    ouvidos.esperar_palavra(parar=acordar)
                elif o.ativacao == "botao":
                    acordar.wait()
                else:
                    try:
                        input("[Enter] ")
                    except EOFError:
                        break

                c.sinais.estado("a_ouvir")
                print("  a ouvir...", flush=True)
                pergunta = ouvidos.ouvir_pergunta()
            except queue.Empty:
                # O microfone deixou de mandar som durante 2 s (cabo, placa...).
                print("  [aviso] o microfone deixou de responder; a tentar de novo.", file=sys.stderr)
                c.sinais.estado("erro")
                time.sleep(2)
                continue

            if not pergunta:
                print("  (não ouvi nada)\n")
                continue
            print(f"tu > {pergunta}")

            # Meio-duplex: o microfone já está fechado (ouvir_pergunta fechou-o),
            # por isso o ciclope não se ouve a si próprio enquanto fala.
            a_responder.set()
            try:
                correu_bem = _uma_resposta(c, modelo, conversa, pergunta, interromper, t0=ouvidos.fim_gravacao)
            finally:
                a_responder.clear()
            if not correu_bem:
                time.sleep(2)  # deixa ver o olho vermelho antes de voltar ao repouso
    except KeyboardInterrupt:
        pass
    finally:
        print()
        c.fechar()
    return 0


def cmd_dizer(cfg: Config, args) -> int:
    c = Ciclope(cfg, mudo=args.mudo, sem_cara=args.sem_cara)
    assert c.boca is not None
    texto = " ".join(args.frase)
    interromper = threading.Event()
    try:
        for frase in dividir([texto]):
            pronta = c.boca.preparar(frase)
            print(f"ciclope > {frase}")

            def ao_comecar(p=pronta) -> None:
                c.sinais.estado("a_falar")
                c.sinais.fala(p.envelope)

            c.boca.dizer(pronta, interromper, ao_comecar)
        time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        c.fechar()
    return 0


def envelope_sintetico(segundos: float, janela_ms: int = 30) -> list[int]:
    """Um envelope que imita sílabas: rajadas de 120-250 ms com pausas curtas."""
    env: list[int] = []
    total = int(segundos * 1000 / janela_ms)
    while len(env) < total:
        silaba = random.randint(4, 8)
        pico = random.randint(150, 255)
        for i in range(silaba):
            # Sobe e desce em forma de seno, como o volume de uma sílaba.
            env.append(int(pico * math.sin(math.pi * (i + 0.5) / silaba)))
        env.extend([random.randint(0, 25)] * random.randint(1, 4))
    return env[:total]


def cmd_cara_demo(cfg: Config, args) -> int:
    print("Ciclope — demonstração do olho (sem modelo, sem voz)")
    c = Ciclope(cfg, mudo=True, sem_cara=False, com_voz=False)
    if isinstance(c.cara, CaraNula):
        print("  [erro] o olho está desligado em [cara] ativa = false")
        return 1
    print("\nA percorrer os estados em ciclo. Ctrl-C para sair.\n")
    try:
        while True:
            for estado, duracao in (("inativo", 5), ("a_ouvir", 3), ("a_pensar", 3)):
                print(f"  estado: {estado}")
                c.sinais.estado(estado)
                time.sleep(duracao)
            print("  estado: a_falar (fala simulada)")
            c.sinais.estado("a_falar")
            for _ in range(2):
                segundos = random.uniform(1.5, 3.0)
                c.sinais.fala(envelope_sintetico(segundos, cfg.cara.janela_ms))
                time.sleep(segundos + 0.2)
    except KeyboardInterrupt:
        pass
    finally:
        c.fechar()
    return 0


def cmd_testar_modelo(cfg: Config, _args) -> int:
    modelo = criar_modelo(cfg.modelo)
    print(f"A perguntar a {modelo.fornecedor} · {modelo.nome}...\n")
    t0 = time.perf_counter()
    primeiro = None
    for pedaco in modelo.stream([{"role": "user", "content": "Diz olá em três palavras."}]):
        if primeiro is None:
            primeiro = time.perf_counter() - t0
        # flush=True: escreve já, em vez de acumular num buffer até ao fim.
        print(pedaco, end="", flush=True)
    print(f"\n\n[primeiro pedaço em {primeiro or 0:.2f}s · total {time.perf_counter() - t0:.2f}s]")
    return 0


def cmd_dispositivos(_cfg: Config, _args) -> int:
    from ciclope.audio import listar_dispositivos

    print(listar_dispositivos())
    print("\n> marca a entrada predefinida, < a saída. Usa parte do nome em [audio] no ciclope.toml.")
    return 0


# --- entrada -----------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("-c", "--config", help="ficheiro de configuração (por omissão: ciclope.toml)")
    comum.add_argument("--mudo", action="store_true", help="não tocar som (o olho mexe-se na mesma)")
    comum.add_argument("--sem-cara", action="store_true", help="não arrancar o servidor do olho")

    p = argparse.ArgumentParser(prog="ciclope", description="Assistente de voz com um olho animado.")
    sub = p.add_subparsers(dest="comando", metavar="comando")
    sub.add_parser("texto", parents=[comum], help="escreves, ele responde em voz alta (Fase 0)")
    sub.add_parser("voz", parents=[comum], help="falas, ele ouve e responde (Fase 1)")
    d = sub.add_parser("dizer", parents=[comum], help="diz uma frase (testa voz e olho)")
    d.add_argument("frase", nargs="+")
    sub.add_parser("cara-demo", parents=[comum], help="anima o olho sem modelo nem voz")
    sub.add_parser("testar-modelo", parents=[comum], help="confirma que a chave da API funciona")
    sub.add_parser("dispositivos", parents=[comum], help="lista os dispositivos de som")
    return p


def _comando_primeiro(argv: list[str]) -> list[str]:
    """Põe o comando à frente das opções: `-c x.toml voz` → `voz -c x.toml`.

    O argparse só aceita as opções comuns depois do comando; assim podes
    escrevê-las onde te apetecer. Sem comando nenhum, assume `texto`.
    """
    for i, a in enumerate(argv):
        if a in COMANDOS:
            return [a] + argv[:i] + argv[i + 1 :]
    if any(a in ("-h", "--help") for a in argv):
        return argv
    return ["texto"] + argv


def main(argv: list[str] | None = None) -> int:
    argv = _comando_primeiro(list(sys.argv[1:] if argv is None else argv))
    args = _parser().parse_args(argv)

    try:
        cfg = carregar(args.config)
    except ErroConfig as e:
        print(f"[configuração] {e}", file=sys.stderr)
        return 2
    if cfg.origem is None:
        print("  (sem ciclope.toml — a usar valores por omissão; copia ciclope.example.toml)")

    comandos = {
        "texto": cmd_texto,
        "voz": cmd_voz,
        "dizer": cmd_dizer,
        "cara-demo": cmd_cara_demo,
        "testar-modelo": cmd_testar_modelo,
        "dispositivos": cmd_dispositivos,
    }
    try:
        return comandos[args.comando](cfg, args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        # Erros esperados (chave em falta, SoX por instalar, porta ocupada,
        # dispositivo de som com o nome errado...) mostram-se numa linha.
        if os.environ.get("CICLOPE_DEBUG"):
            raise
        print(f"[erro] {_explicar(e)}", file=sys.stderr)
        print("       (CICLOPE_DEBUG=1 mostra o erro completo)", file=sys.stderr)
        return 1


def _explicar(e: Exception) -> str:
    """Acrescenta uma pista aos erros mais comuns."""
    texto = str(e) or type(e).__name__
    if isinstance(e, OSError) and getattr(e, "errno", None) in (48, 98):  # endereço em uso (macOS, Linux)
        return f"{texto}\n       Outra cópia do Ciclope já está a usar esta porta (o serviço systemd?)."
    nome = type(e).__name__
    if nome == "PortAudioError" or "PortAudio" in texto:
        return f"{texto}\n       Problema no dispositivo de som — `ciclope dispositivos` lista os nomes."
    if nome == "ValueError" and ("device" in texto.lower() or "No input" in texto or "No output" in texto):
        return f"{texto}\n       Confirma [audio] saida/entrada com `ciclope dispositivos`."
    return texto


if __name__ == "__main__":
    raise SystemExit(main())
