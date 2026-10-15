// Olho do Ciclope — firmware para ESP32-S3 com ecrã redondo.
//
// O ESP32 não ouve, não pensa e não fala: recebe do Raspberry Pi o estado
// ("a ouvir", "a pensar", ...) e o envelope de cada frase, e anima o olho.
// Piscar e mexer o olhar são decididos aqui, localmente, para o olho estar
// vivo mesmo quando o Pi está calado — ou desligado.
//
// Duas tarefas em núcleos diferentes:
//   núcleo 0: rede (ligacao.cpp) — WiFi, mDNS, WebSocket
//   núcleo 1: este loop() — aplica as mensagens recebidas e desenha
#include <Arduino.h>

#include "ecra.h"
#include "ligacao.h"
#include "olho.h"

#if __has_include("config.h")
#include "config.h"
#endif
#ifndef BRILHO_ECRA
#define BRILHO_ECRA 900
#endif

static Olho olho;
static uint32_t ultimoFotograma = 0;
static const uint32_t MS_POR_FOTOGRAMA = 33;  // ~30 fotogramas por segundo

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[olho] a arrancar");

  if (!ecraIniciar()) {
    Serial.println("[olho] o ecrã não respondeu — confirma a placa escolhida no platformio.ini");
  }
  Serial.printf("[olho] ecrã de %d px, PSRAM livre: %u bytes\n", ecraLado(), ESP.getFreePsram());

  // Desenha um primeiro fotograma antes de acender a luz, para não se ver lixo.
  olho.desenhar(ecraTela());
  ecraMostrar();
  ecraBrilho(BRILHO_ECRA);

  ligacaoIniciar();
}

void loop() {
  // Aplica tudo o que chegou da rede desde o último fotograma.
  while (Mensagem *m = ligacaoProxima()) {
    if (m->tipo == Mensagem::DeFala) {
      olho.iniciarFala(std::move(m->env), m->ms, m->quando);
    } else {
      olho.definirEstado(m->estado);
    }
    delete m;
  }

  // A subtração de dois uint32_t continua certa mesmo quando o millis() dá
  // a volta (aos 49 dias), porque a aritmética sem sinal é modular.
  uint32_t agoraMs = millis();
  if (agoraMs - ultimoFotograma < MS_POR_FOTOGRAMA) {
    delay(1);
    return;
  }
  float dt = (agoraMs - ultimoFotograma) / 1000.0f;
  if (dt > 0.1f) dt = 0.1f;  // depois de uma pausa longa, não dar um salto
  ultimoFotograma = agoraMs;

  olho.definirLigado(ligacaoAtiva());
  olho.atualizar(segundos(), dt);
  olho.desenhar(ecraTela());
  ecraMostrar();
}
