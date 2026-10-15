# Arquitetura do Ciclope

Este documento explica as decisões de desenho, o protocolo entre o Pi e o
olho, e de onde vem cada facto de hardware. O [README](../README.md) diz como
montar; aqui está o porquê.

## Divisão de trabalho

| | Raspberry Pi 5 | ESP32-S3 |
|---|---|---|
| Faz | ouvir, transcrever, pensar, falar | desenhar o olho |
| Decide | o estado (a ouvir, a pensar...) e o volume de cada frase | piscar, para onde olhar, como suavizar |
| Liga-se | é o servidor WebSocket (porta 8765) | é um cliente WebSocket |

O olho podia estar ligado diretamente ao Pi por SPI, sem ESP32. Não está, por
uma razão: o Pi vai estar ocupado com o Whisper e o modelo exatamente nos
momentos em que o olho mais se devia mexer, e a animação seria a primeira coisa
a engasgar. Com um processador dedicado, o olho nunca espera pelo cérebro.

## O caminho de uma pergunta

1. **Acordar** (`ouvidos.Despertador`): o openWakeWord ouve blocos de 80 ms à
   procura da palavra de ativação. Também acorda com o botão ou com Enter.
2. **Gravar** (`ouvidos.Gravador`): grava até haver `silencio_fim_ms` de
   silêncio. O detetor de voz (webrtcvad) decide, em janelas de 20 ms, se há
   alguém a falar.
3. **Fechar o microfone.** Meio-duplex: o microfone só está aberto dentro do
   `with Microfone(...)`. Quando o ciclope fala, ninguém está a ouvir — não há
   eco possível, sem precisar de cancelamento de eco.
4. **Transcrever** (`ouvidos.Transcritor`): faster-whisper, quantizado a int8.
5. **Responder** (`pipeline.responder`), com dois fios de execução:
   - o **produtor** lê o streaming do modelo, corta em frases (`frases.dividir`),
     sintetiza com o Piper, aplica o SoX e calcula o envelope;
   - o **consumidor** toca cada frase e avisa o olho no instante em que começa.

   Ligados por uma fila de tamanho 2: enquanto uma frase toca, a seguinte já
   está a ser preparada.

## Dentro do ESP32

Duas tarefas em núcleos diferentes do ESP32-S3:

- **Núcleo 0, rede** (`ligacao.cpp`): WiFi, mDNS e WebSocket. Ligar ao WiFi,
  procurar `ciclope.local` ou tentar abrir a ligação a um Pi desligado pode
  bloquear durante segundos. Aqui, isso não incomoda ninguém.
- **Núcleo 1, desenho** (`main.cpp`): a cada ~33 ms aplica as mensagens que
  chegaram, atualiza a animação e desenha.

Passam mensagens de uma para a outra por uma fila do FreeRTOS. Assim o olho
nunca congela à espera da rede — continua a piscar, cinzento e sonolento,
enquanto procura o Pi.

O tempo da animação vem do `esp_timer` (microssegundos em 64 bits), em
`double`. O `millis()` dá a volta aos 49 dias e, convertido para segundos em
`float`, perde precisão em poucos dias — num aparelho sempre ligado, o olho
acabaria por deixar de piscar.

## Protocolo do olho

JSON por WebSocket, só do Pi para o olho:

```json
{"t": "ola", "versao": 1, "estado": "inativo"}
{"t": "estado", "v": "a_pensar"}
{"t": "fala", "ms": 30, "env": [0, 40, 180, 255, 120, ...]}
```

- `ola` chega a quem se liga, com o estado atual — um ESP32 que reinicie a meio
  de uma conversa sincroniza logo.
- `estado` é um de `inativo`, `a_ouvir`, `a_pensar`, `a_falar`, `erro`.
- `fala` traz o **envelope inteiro** de uma frase: um valor de 0 a 255 por cada
  `ms` milissegundos de áudio, calculado depois dos efeitos do SoX. O olho
  reprodu-lo com o seu próprio relógio a partir do momento em que a recebe.

Porque o envelope inteiro e não um valor de volume 33 vezes por segundo: por
WiFi, mensagens frequentes chegam aos solavancos e o olho tremia. Uma mensagem
por frase chega inteira e a animação fica suave. A desvantagem é a latência da
rede somar-se ao início da animação — numa rede local, poucos milissegundos.

## Mudanças face ao que foi conversado

Ao montar o projeto, verificou-se o estado atual de cada peça. Mudou isto:

- **Piper pela API Python, não pelo executável.** O executável `piper`
  recarrega o modelo (~60 MB) a cada chamada — era a maior parte das pausas
  entre frases. A API `PiperVoice` carrega-o uma vez.
- **Haiku 5.5 em vez de Haiku 4.5.** O `claude-haiku-5-5` é o Haiku atual e mais
  barato; segundo a página de descontinuações da Anthropic (outubro de 2026), a
  retirada do Haiku 4.5 está prevista para não antes de 15 de outubro de 2026.
- **webrtcvad em vez do Silero VAD.** O pacote `silero-vad` obriga a instalar o
  PyTorch. O webrtcvad é minúsculo e chega para detetar o fim da fala.
- **openWakeWord com ONNX.** O motor tflite, que é o predefinido, não existe para
  macOS e parte-se com o NumPy 2 no Linux.
- **Porta do llama-server.** Nas versões recentes passou de 8080 para 9931.
- **Envelope por frase** em vez de valores de volume contínuos (ver acima).
- **Um olho, não uma boca.** O ciclope tem um olho: a voz anima a íris (acende)
  e a pupila (contrai), em vez de abrir uma boca.

## Fontes do hardware

### reSpeaker 2-Mics Pi HAT V2.0

- Instalação no Pi 5 (overlay `respeaker-2mic-v2_0`, `aplay -l` esperado com
  `1f000a4000.i2s`, botão no GPIO17, aviso sobre o RPi.GPIO no Pi 5):
  https://wiki.seeedstudio.com/respeaker_2_mics_pi_hat_raspberry_v2/
- Código do overlay e o histórico da correção do ruído no Pi 5 (commit 11d38c9,
  novembro de 2024): https://github.com/Seeed-Studio/seeed-linux-dtoverlays
- Distinguir a V1 da V2:
  https://wiki.seeedstudio.com/how-to-distinguish-respeaker_2-mics_pi_hat-hardware-revisions/
- LEDs APA102 no SPI 0, chip select 1: https://github.com/respeaker/mic_hat
  (`interfaces/apa102.py`)
- Gravação só em estéreo, 8–96 kHz, nomes dos controlos do misturador: controlador
  `tlv320aic3x` no kernel do Raspberry Pi
  (https://github.com/raspberrypi/linux/blob/rpi-6.12.y/sound/soc/codecs/tlv320aic3x.c)

### Waveshare ESP32-S3-Touch-LCD-2.1

- Pinos do ecrã RGB, expansor TCA9554 (0x20, SDA 15, SCL 7), luz no GPIO6:
  https://www.waveshare.com/wiki/ESP32-S3-Touch-LCD-2.1 e o esquema elétrico
  ligado nessa página.
- Sequência de configuração do ST7701S e tempos do painel: código de exemplo da
  Waveshare (`Display_ST7701.cpp`), conferido com os ficheiros de placa do
  CircuitPython
  (https://github.com/adafruit/circuitpython/tree/main/ports/espressif/boards/waveshare_esp32_s3_touch_lcd_2_1).
- ESP32-S3R8, 8 MB de PSRAM octal: ficha técnica do ESP32-S3 da Espressif.

### Waveshare ESP32-S3-LCD-1.28

- Pinos (DC 8, CS 9, SCK 10, MOSI 11, RST 12, luz 40):
  https://www.waveshare.com/wiki/ESP32-S3-LCD-1.28 e os ficheiros de placa do
  CircuitPython
  (https://github.com/adafruit/circuitpython/tree/main/ports/espressif/boards/waveshare_esp32_s3_lcd_1_28).
- ESP32-S3R2, 2 MB de PSRAM quad: ficha técnica do ESP32-S3.

### Ferramentas do firmware

- O PlatformIO estável ainda traz o Arduino-ESP32 2.x; a Arduino_GFX precisa do
  3.x para ecrãs RGB. Por isso o `platformio.ini` usa o pioarduino:
  https://github.com/pioarduino/platform-espressif32
- Bibliotecas: Arduino_GFX 1.6.9, WebSockets (links2004) 2.7.3, ArduinoJson 7.4.3.
