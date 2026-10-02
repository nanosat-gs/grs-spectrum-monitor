"""O que o Spectrum Monitor sabe, e de onde vem — tudo do Station Manager.

O monitor mora no Control Desktop (Display 4 do diagrama), do outro lado da
rede. Ele conhece UM endereço, o do Station Manager, e tira dele:

- o espectro e as medidas de cada rádio, pelo repasse do Station Manager
  (`--spectrum-bind`, :5583): os blocos FFT publicam `fft.<rádio>` e
  `afc.<rádio>`, e o repasse (XSUB/XPUB) os entrega a quem assinar;
- o contexto da passagem — satélite, downlinks, Doppler, ajuste fino — pelo
  `get_tracking` (REQ :5580), o mesmo que o TC Scheduler e o GRS Manager usam.

Só lê. Nada aqui comanda a estação.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from array import array
from typing import Any, Callable

import zmq

logger = logging.getLogger(__name__)

# Espectro mais velho que isto: o bloco FFT parou, ou o rádio não está no ar.
SPECTRUM_STALE_S = 3.0


def _floats(raw: bytes) -> list[float]:
    """float32 little-endian (o que o grs-fft publica) -> lista arredondada."""
    values = array("f")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    return [round(v, 1) for v in values]


class StationManagerStatus:
    """`get_tracking` por REQ, com timeout e socket recriado ao falhar.

    REQ que estoura o timeout fica fora de ordem no ciclo envia/recebe; sem
    recriá-lo, todo pedido seguinte falharia (mesma limitação documentada no
    cliente do TC Scheduler).
    """

    def __init__(self, address: str, timeout_ms: int = 1000,
                 context: zmq.Context | None = None) -> None:
        self._address = address
        self._timeout_ms = timeout_ms
        self._context = context or zmq.Context.instance()
        self._socket = None
        self._connect()

    def _connect(self) -> None:
        if self._socket is not None:
            self._socket.close()
        self._socket = self._context.socket(zmq.REQ)
        self._socket.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        self._socket.setsockopt(zmq.SNDTIMEO, self._timeout_ms)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.connect(self._address)

    def get_tracking(self) -> dict | None:
        try:
            self._socket.send_json({"cmd": "get_tracking"})
            reply = self._socket.recv_json()
        except zmq.Again:
            self._connect()
            raise TimeoutError(f"Station Manager não respondeu em {self._timeout_ms} ms") from None
        if not isinstance(reply, dict) or not reply.get("ok"):
            raise RuntimeError(f"resposta inesperada: {reply!r}")
        return reply.get("tracking")

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()


class MonitorFeed:
    """Estado do monitor, alimentado por duas threads e lido pelo servidor HTTP."""

    def __init__(self, spectrum_source: str | None,
                 tracking: Callable[[], dict | None] | None = None,
                 poll_s: float = 1.0) -> None:
        self._spectrum_source = spectrum_source
        self._tracking = tracking
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._spectra: dict[str, dict] = {}
        self._afc: dict[str, dict] = {}
        self._tracking_state: dict | None = None
        self._tracking_error: str | None = "ainda sem resposta"
        self._threads: list[threading.Thread] = []

    # --- entrada ----------------------------------------------------------------

    def handle_spectrum(self, frames: list[bytes]) -> None:
        """Uma mensagem do repasse: `fft.<rádio>` ou `afc.<rádio>`."""
        if not frames:
            return
        topic = frames[0].decode(errors="replace")
        kind, _, radio = topic.partition(".")
        if not radio:
            return
        try:
            if kind == "fft" and len(frames) >= 3:
                header = json.loads(frames[1])
                full = _floats(frames[2])
                zoom = _floats(frames[3]) if len(frames) >= 4 else []
                with self._lock:
                    previous = self._spectra.get(radio, {}).get("seq", 0)
                    self._spectra[radio] = {"seq": previous + 1, "header": header, "full": full,
                                            "zoom": zoom, "received": time.monotonic()}
            elif kind == "afc" and len(frames) >= 2:
                data = json.loads(frames[1])
                float(data["offset_hz"])
                with self._lock:
                    self._afc[radio] = {**data, "received": time.monotonic()}
        except (ValueError, KeyError, TypeError) as error:
            logger.warning("Mensagem ilegível em %s: %s", topic, error)

    def set_tracking(self, tracking: dict | None, error: str | None = None) -> None:
        with self._lock:
            self._tracking_state = tracking
            self._tracking_error = error

    # --- saída -----------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Contexto da passagem e situação de cada rádio (sem o espectro)."""
        now = time.monotonic()
        with self._lock:
            tracking = self._tracking_state
            radios = sorted(set(self._spectra) | set(self._afc)
                            | {d.get("radio") for d in (tracking or {}).get("downlinks", [])
                               if d.get("radio")})
            out = {}
            for radio in radios:
                spectrum = self._spectra.get(radio)
                afc = self._afc.get(radio)
                downlink = next((d for d in (tracking or {}).get("downlinks", [])
                                 if d.get("radio") == radio), None)
                tune = None
                if downlink:
                    tune = (downlink["frequency_hz"] + (downlink.get("doppler_hz") or 0.0)
                            + (downlink.get("offset_hz") or 0.0))
                out[radio] = {
                    "downlink": downlink,
                    "tune_hz": tune,
                    "afc": None if afc is None else {
                        **{k: v for k, v in afc.items() if k != "received"},
                        "age_s": now - afc["received"]},
                    "spectrum_age_s": None if spectrum is None else now - spectrum["received"],
                    "spectrum_stale": spectrum is None
                                      or now - spectrum["received"] > SPECTRUM_STALE_S,
                    "header": None if spectrum is None else spectrum["header"],
                }
            return {
                "station_manager": {"available": self._tracking_error is None,
                                    "error": self._tracking_error},
                "tracking": None if tracking is None else {
                    k: tracking.get(k) for k in ("satellite_name", "until", "is_pointing",
                                                 "azimuth_degrees", "elevation_degrees")},
                "radios": out,
            }

    def spectrum(self, radio: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            return {
                name: {"seq": s["seq"], "header": s["header"], "full": s["full"],
                       "zoom": s["zoom"], "age_s": now - s["received"]}
                for name, s in self._spectra.items() if radio in (None, name)
            }

    # --- threads ------------------------------------------------------------------------

    def start(self) -> None:
        if self._spectrum_source:
            self._threads.append(threading.Thread(target=self._spectrum_loop, daemon=True,
                                                  name="spectrum"))
        if self._tracking is not None:
            self._threads.append(threading.Thread(target=self._tracking_loop, daemon=True,
                                                  name="tracking"))
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stopping.set()
        for thread in self._threads:
            thread.join(timeout=3)

    def _spectrum_loop(self) -> None:
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, 500)
        # O repasse do Station Manager é XPUB: estas assinaturas sobem até os
        # blocos FFT, e só fft.* e afc.* atravessam a rede.
        socket.setsockopt(zmq.SUBSCRIBE, b"fft.")
        socket.setsockopt(zmq.SUBSCRIBE, b"afc.")
        socket.connect(self._spectrum_source)
        logger.info("Espectro de %s", self._spectrum_source)
        try:
            while not self._stopping.is_set():
                try:
                    self.handle_spectrum(socket.recv_multipart())
                except zmq.Again:
                    continue
        finally:
            socket.close()
            context.term()

    def _tracking_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                self.set_tracking(self._tracking(), None)
            except Exception as error:
                self.set_tracking(None, str(error))
            self._stopping.wait(self._poll_s)
