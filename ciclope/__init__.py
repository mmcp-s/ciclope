"""Ciclope — assistente de voz com um olho animado.

Mapa do pacote, pela ordem em que os dados passam:

    ouvidos.py   microfone → palavra de ativação → gravação até ao silêncio → texto (Whisper)
    llm.py       texto → modelo de linguagem → pedaços de resposta (streaming)
    frases.py    pedaços → frases completas
    tts.py       frase → áudio (Piper + SoX) → envelope de amplitude
    audio.py     áudio → altifalante
    cara.py      estado + envelope → WebSocket → olho (ESP32 ou simulador)
    pipeline.py  junta tudo numa resposta falada, com síntese em paralelo
    hardware.py  botão e LEDs do reSpeaker
    main.py      a linha de comandos (`ciclope texto`, `ciclope voz`, ...)
"""

__version__ = "0.1.0"
