// Copia este ficheiro para config.h (na mesma pasta) e preenche.
// O config.h está no .gitignore: a password do WiFi nunca vai para o GitHub.
#pragma once

// A tua rede WiFi. O ESP32-S3 só funciona em 2,4 GHz.
#define WIFI_SSID "nome-da-rede"
#define WIFI_PASS "password-da-rede"

// Onde está o Ciclope (o Raspberry Pi). Duas opções:
//   - o IP do Pi, por exemplo "192.168.1.50"  (descobre-o no Pi com: hostname -I)
//   - o nome na rede local, por exemplo "ciclope.local" (se o Pi se chamar ciclope)
#define CICLOPE_HOST "192.168.1.50"
#define CICLOPE_PORTA 8765

// Brilho do ecrã, de 0 a 1023.
#define BRILHO_ECRA 900
