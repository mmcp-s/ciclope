"""Liga um cliente WebSocket ao servidor da cara e confirma o protocolo."""

import asyncio
import json
import socket

import pytest

from ciclope.cara import Cara, mensagem_estado, mensagem_fala


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mensagens_sao_json_compacto():
    assert json.loads(mensagem_estado("a_ouvir")) == {"t": "estado", "v": "a_ouvir"}
    assert mensagem_fala([0, 128, 255], 30) == '{"t":"fala","ms":30,"env":[0,128,255]}'
    with pytest.raises(ValueError):
        mensagem_estado("a_dancar")


def test_cliente_recebe_ola_estado_e_fala():
    from websockets.asyncio.client import connect

    porta = porta_livre()
    cara = Cara(porta_ws=porta, porta_http=0)
    cara.iniciar()
    cara.estado("a_pensar")  # antes de alguém ligar

    async def cliente():
        async with connect(f"ws://127.0.0.1:{porta}") as ws:
            ola = json.loads(await ws.recv())
            cara.estado("a_falar")
            cara.fala([1, 2, 3])
            estado = json.loads(await ws.recv())
            fala = json.loads(await ws.recv())
            return ola, estado, fala

    try:
        ola, estado, fala = asyncio.run(asyncio.wait_for(cliente(), 5))
    finally:
        cara.parar()

    assert ola == {"t": "ola", "versao": 1, "estado": "a_pensar"}
    assert estado == {"t": "estado", "v": "a_falar"}
    assert fala == {"t": "fala", "ms": 30, "env": [1, 2, 3]}


def test_simulador_servido_por_http_sabe_a_porta_do_websocket():
    import urllib.request

    porta_ws, porta_http = porta_livre(), porta_livre()
    cara = Cara(porta_ws=porta_ws, porta_http=porta_http)
    cara.iniciar()
    try:
        base = f"http://127.0.0.1:{porta_http}"
        sem_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with sem_proxy.open(f"{base}/ciclope.json", timeout=5) as r:
            assert json.loads(r.read()) == {"porta_ws": porta_ws}
        with sem_proxy.open(f"{base}/", timeout=5) as r:
            assert b"O olho do Ciclope" in r.read()
    finally:
        cara.parar()


def test_desligar_a_bruta_nao_e_erro(capfd):
    """Um cliente que corta a ligação sem a fechar não deve sujar o terminal."""
    porta = porta_livre()
    cara = Cara(porta_ws=porta, porta_http=0)
    cara.iniciar()
    try:
        s = socket.create_connection(("127.0.0.1", porta))
        s.sendall(
            b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        s.recv(4096)
        # SO_LINGER a zero faz o close() mandar um RST: o "desligar à bruta".
        import struct

        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()
        import time

        time.sleep(0.5)
    finally:
        cara.parar()
    assert "Traceback" not in capfd.readouterr().err
