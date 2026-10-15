"""Cara: o servidor que diz ao olho o que fazer.

O Pi é o servidor; o ESP32 e o simulador no browser ligam-se como clientes por
WebSocket — uma ligação que fica aberta e onde ambos os lados podem mandar
mensagens a qualquer momento, ao contrário do HTTP normal (pedido → resposta).

Protocolo (JSON, só do Pi para o olho):

    {"t": "ola",    "versao": 1, "estado": "inativo"}   ao ligar
    {"t": "estado", "v": "inativo|a_ouvir|a_pensar|a_falar|erro"}
    {"t": "fala",   "ms": 30, "env": [0..255, ...]}       uma por frase

Repara na mensagem "fala": em vez de mandar o volume 33 vezes por segundo, o
Pi manda o envelope INTEIRO da frase de uma vez, no instante em que começa a
tocar, e o olho reprodu-lo com o seu próprio relógio. Por WiFi, 33 mensagens
por segundo chegam aos solavancos; uma mensagem por frase chega inteira.

O servidor corre num fio de execução (thread) à parte, com o seu próprio ciclo
asyncio. O resto do programa é síncrono e fala com ele através de
call_soon_threadsafe — a única forma segura de pedir algo a um ciclo asyncio
a partir de outro fio.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ESTADOS = ("inativo", "a_ouvir", "a_pensar", "a_falar", "erro")
PASTA_SIMULADOR = Path(__file__).resolve().parent.parent / "simulador"


def mensagem_estado(estado: str) -> str:
    if estado not in ESTADOS:
        raise ValueError(f"Estado desconhecido: {estado!r}")
    return json.dumps({"t": "estado", "v": estado}, separators=(",", ":"))


def mensagem_fala(envelope: list[int], janela_ms: int) -> str:
    return json.dumps({"t": "fala", "ms": janela_ms, "env": envelope}, separators=(",", ":"))


def ip_local() -> str:
    """O IP desta máquina na rede local (o que o ESP32 deve usar)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Não envia nada: um socket UDP "ligado" só escolhe a interface de saída.
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _tratador_http(pasta: Path, porta_ws: int):
    """Serve o simulador e um /ciclope.json com a porta do WebSocket.

    O simulador pede o /ciclope.json para saber a que porta ligar — assim, se
    mudares [cara] porta_ws, não tens de mexer no HTML.
    """

    class Tratador(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(pasta), **kwargs)

        def do_GET(self) -> None:
            if self.path.split("?")[0] == "/ciclope.json":
                corpo = json.dumps({"porta_ws": porta_ws}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            super().do_GET()

        def log_message(self, *_args) -> None:
            pass  # sem uma linha no terminal por cada pedido do browser

    return Tratador


class Cara:
    def __init__(self, porta_ws: int = 8765, porta_http: int = 8080, janela_ms: int = 30):
        self.porta_ws = porta_ws
        self.porta_http = porta_http
        self.janela_ms = janela_ms
        self._estado = "inativo"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._servidor = None
        self._parar_async: asyncio.Event | None = None
        self._pronto = threading.Event()
        self._fio: threading.Thread | None = None
        self._http: ThreadingHTTPServer | None = None
        self.erro: Exception | None = None

    # --- arranque e paragem -------------------------------------------------

    def iniciar(self) -> None:
        self._fio = threading.Thread(target=self._correr, name="cara-ws", daemon=True)
        self._fio.start()
        self._pronto.wait(timeout=5)
        if self.erro:
            raise self.erro

        if self.porta_http and PASTA_SIMULADOR.is_dir():
            try:
                tratador = _tratador_http(PASTA_SIMULADOR, self.porta_ws)
                self._http = ThreadingHTTPServer(("0.0.0.0", self.porta_http), tratador)
                threading.Thread(target=self._http.serve_forever, name="cara-http", daemon=True).start()
            except OSError as e:
                print(f"  [cara] não consegui abrir a porta HTTP {self.porta_http}: {e}")
                self._http = None

    def parar(self) -> None:
        if self._loop and self._parar_async:
            self._loop.call_soon_threadsafe(self._parar_async.set)
        if self._fio:
            self._fio.join(timeout=3)
        if self._http:
            self._http.shutdown()

    def enderecos(self) -> list[str]:
        ip = ip_local()
        linhas = [f"olho (WebSocket): ws://{ip}:{self.porta_ws}"]
        if self._http:
            linhas.append(f"simulador:        http://{ip}:{self.porta_http}")
        return linhas

    # --- API usada pelo resto do programa (síncrona) -------------------------

    def estado(self, estado: str) -> None:
        msg = mensagem_estado(estado)  # valida antes de mudar
        self._estado = estado
        self._enviar(msg)

    def fala(self, envelope: list[int]) -> None:
        self._enviar(mensagem_fala(envelope, self.janela_ms))

    # --- interior (corre no fio do asyncio) ----------------------------------

    def _correr(self) -> None:
        try:
            asyncio.run(self._principal())
        except Exception as e:  # porta ocupada, por exemplo
            self.erro = e
            self._pronto.set()

    async def _principal(self) -> None:
        from websockets.asyncio.server import serve

        self._loop = asyncio.get_running_loop()
        self._parar_async = asyncio.Event()
        async with serve(self._ao_ligar, "0.0.0.0", self.porta_ws) as servidor:
            self._servidor = servidor
            self._pronto.set()
            await self._parar_async.wait()

    async def _ao_ligar(self, ligacao) -> None:
        from websockets.exceptions import ConnectionClosed

        # Quem liga recebe logo o estado atual, para o olho não ficar
        # dessincronizado se o ESP32 reiniciar a meio de uma conversa.
        ola = {"t": "ola", "versao": 1, "estado": self._estado}
        try:
            await ligacao.send(json.dumps(ola, separators=(",", ":")))
            async for _ in ligacao:  # o olho não manda nada; só esperamos que desligue
                pass
        except ConnectionClosed:
            # Desligar à bruta (ESP32 reiniciado, WiFi em baixo, separador do
            # telemóvel suspenso) é normal aqui, não é um erro.
            pass

    def _enviar(self, texto: str) -> None:
        if self._loop is None or self._servidor is None:
            return
        self._loop.call_soon_threadsafe(self._difundir, texto)

    def _difundir(self, texto: str) -> None:
        from websockets.asyncio.server import broadcast

        # broadcast() manda para todas as ligações sem esperar por nenhuma:
        # um cliente lento ou pendurado não atrasa os outros.
        broadcast(self._servidor.connections, texto)


class CaraNula:
    """A mesma interface, sem fazer nada. Para quando o olho está desligado."""

    janela_ms = 30

    def iniciar(self) -> None: ...
    def parar(self) -> None: ...
    def estado(self, estado: str) -> None: ...
    def fala(self, envelope: list[int]) -> None: ...
    def enderecos(self) -> list[str]:
        return []
