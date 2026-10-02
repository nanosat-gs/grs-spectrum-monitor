# Contexto do projeto

Spectrum Monitor da estação terrestre SpaceLab (Control Desktop, Display 4).
Página web que mostra o espectro de cada rádio ao vivo, com o contexto da
passagem. Ver o README.

## Desenho

```
feed.py     assina o repasse do Station Manager (fft.*, afc.*) e consulta o
            get_tracking; guarda o último de cada rádio
server.py   http.server: GET /, /api/state, /api/spectrum
page.html   a página (canvas: zoom, cascata, banda inteira)
main.py     linha de comando
```

## Decisões, e por quê

**Um endereço só, o do Station Manager.** No diagrama o monitor mora no
Control Desktop, do outro lado da rede, e o Station Manager é o ponto de
passagem. Os blocos FFT ficam no Station Server, ao lado do IQ; o monitor não
os conhece.

**Duas consultas em ritmos diferentes.** O espectro a 5 por segundo (o ritmo
do bloco FFT); o contexto (`/api/state`) a cada segundo. A página só desenha
um quadro novo quando o `seq` muda.

**Zoom em resolução total, e não a banda reduzida.** A banda inteira vem em
512 faixas de ~470 Hz — um beacon de 1,8 kHz ocupa quatro. O bloco FFT manda
junto a janela de busca em resolução total, e é ela que mostra o sinal.

**Só lê.** Sem escrita nem comando: a porta pode ser publicada como a do
painel do operador.

## Armadilhas

- **REQ que estoura o timeout fica fora de ordem.** `StationManagerStatus`
  recria o socket; sem isso, toda consulta seguinte falharia.
- **A assinatura sobe pelo repasse.** O repasse é XPUB/XSUB: o monitor assina
  `fft.` e `afc.`, e é essa assinatura que faz os blocos FFT mandarem. Sem
  assinante, nada atravessa a rede.

## Convenções

- Comentários e mensagens de commit em português; código em inglês.
- Testes passam pelo ZMQ e pelo HTTP de verdade.
