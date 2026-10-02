# GRS Spectrum Monitor

O Spectrum Monitor da estação terrestre SpaceLab: o "Spectrum Monitor" do
Control Desktop no diagrama da estação (Display 4). Mostra o espectro de cada
rádio ao vivo, com o contexto da passagem.

```
Station Server                      Control Server                 Control Desktop
grs-fft (um por rádio) ─fft/afc─▶ Station Manager ─:5583 (repasse)─▶ [ grs-spectrum-monitor ] ─▶ navegador
                                                  ─:5580 get_tracking─▶        :8094
```

## O que mostra, por rádio

- **Em volta da sintonia, em resolução total** (~59 Hz por faixa): a janela
  onde o ajuste fino procura o satélite, com
  - a faixa verde: onde o sinal deveria ficar (a largura de um GFSK naquela
    taxa, centrada);
  - a linha laranja: onde o bloco FFT mediu a última rajada;
  - o centro da sintonia, em MHz absolutos quando há passagem.
- **A cascata** desse trecho: o tempo corre para baixo. Com o ajuste fino
  convergindo, dá para ver o sinal "entrando" na faixa verde.
- **A banda inteira** do rádio (±120 kHz), com a janela marcada.
- **O contexto**: satélite e downlink, nominal, Doppler, ajuste fino (e quantos
  ajustes), sintonia efetiva, e a última rajada medida (desvio, SNR, largura,
  idade).

## De onde vem

De um endereço só, o do Station Manager:

- o espectro e as medidas, pelo repasse dele (`--spectrum-bind`, :5583), que
  junta os `fft.<rádio>` e `afc.<rádio>` dos blocos FFT;
- o contexto, pelo `get_tracking` (:5580).

O IQ não sai do Station Server: o que atravessa a rede são quadros de
espectro (algumas dezenas de KB/s por rádio) e as medidas.

## Usando

```bash
pip install -e ".[dev]"
pytest

python -m grs_spectrum_monitor.main --station-manager tcp://station-manager:5580 \
    --spectrum-source tcp://station-manager:5583 --port 8094
```

Na estação (`nanosat-gs/grs-station`) é o serviço `grs-spectrum-monitor`:
<http://localhost:8094>.

Só lê. Nada nesta página comanda a estação.
