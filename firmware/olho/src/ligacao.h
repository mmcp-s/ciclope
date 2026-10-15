// Ligação: WiFi + WebSocket ao Ciclope (o Raspberry Pi).
//
// Corre numa tarefa própria no núcleo 0 do ESP32-S3 (o desenho corre no
// núcleo 1). Ligar ao WiFi, procurar o Pi por mDNS e abrir a ligação podem
// demorar segundos; se fosse no mesmo ciclo do desenho, o olho congelava.
// As mensagens passam de uma tarefa para a outra por uma fila do FreeRTOS.
#pragma once
#include <vector>

#include "olho.h"

struct Mensagem {
  enum Tipo : uint8_t { DeEstado, DeFala } tipo;
  Estado estado = Estado::Inativo;
  uint16_t ms = 30;
  std::vector<uint8_t> env;
  double quando = 0;  // segundos() no instante em que chegou
};

void ligacaoIniciar();  // arranca a tarefa de rede
bool ligacaoAtiva();

// Próxima mensagem recebida, ou nullptr se não houver. Quem a recebe é
// dono dela e tem de a apagar com delete.
Mensagem *ligacaoProxima();
