// Ecrã: liga o ecrã redondo da placa escolhida e dá-nos uma tela para desenhar.
#pragma once
#include <Arduino_GFX_Library.h>

// Prepara o ecrã. Devolve false se a inicialização falhar.
bool ecraIniciar();

// Tela fora do ecrã (framebuffer na PSRAM): desenha-se aqui e depois
// ecraMostrar() copia tudo de uma vez — assim nunca se vê um desenho a meio.
Arduino_GFX *ecraTela();
void ecraMostrar();

int ecraLado();                 // 240 ou 480
void ecraBrilho(uint16_t nivel); // 0 a 1023
