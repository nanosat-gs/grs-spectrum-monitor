"""Testes do Spectrum Monitor.

O que se confere é o caminho do dado até a tela: as mensagens do repasse do
Station Manager (fft.<rádio>, afc.<rádio>) viram o estado certo por rádio, o
contexto da passagem dá a sintonia efetiva (nominal + Doppler + ajuste fino),
e o HTTP serve tudo de verdade — servidor real numa porta real.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from array import array

import pytest
import zmq

from grs_spectrum_monitor.feed import MonitorFeed, StationManagerStatus
from grs_spectrum_monitor.main import parse_args
from grs_spectrum_monitor.server import start_server

HEADER = {"radio": "vhf", "sample_rate_hz": 240000.0, "bins": 4, "window_hz": 8000.0,
          "zoom_start_hz": -117.0, "zoom_resolution_hz": 58.6, "zoom_bins": 3,
          "expected_bandwidth_hz": 1800.0}

TRACKING = {
    "satellite_name": "ISS", "until": "2026-10-02T18:00:00+00:00", "is_pointing": True,
    "azimuth_degrees": 120.0, "elevation_degrees": 35.0,
    "downlinks": [
        {"name": "beacon", "frequency_hz": 145_900_000.0, "radio": "vhf",
         "doppler_hz": -915.0, "offset_hz": 1200.0, "afc_updates": 6},
        {"name": "dados", "frequency_hz": 468_400_000.0, "radio": "uhf",
         "doppler_hz": -2933.0, "offset_hz": 0.0, "afc_updates": 0},
    ],
}


def floats(values):
    return array("f", values).tobytes()


def fft_frames(radio="vhf", full=(-100.0, -99.5, -60.25, -100.0), zoom=(-90.0, -55.0, -90.0)):
    return [f"fft.{radio}".encode(), json.dumps({**HEADER, "radio": radio}).encode(),
            floats(full), floats(zoom)]


# --- o estado ------------------------------------------------------------------------


def test_espectro_vira_estado_do_radio():
    feed = MonitorFeed(None)
    feed.handle_spectrum(fft_frames())
    feed.handle_spectrum(fft_frames())

    spectrum = feed.spectrum()["vhf"]
    assert spectrum["seq"] == 2
    assert spectrum["full"] == [-100.0, -99.5, -60.2, -100.0]
    assert spectrum["zoom"] == [-90.0, -55.0, -90.0]
    assert spectrum["header"]["window_hz"] == 8000.0


def test_medida_do_ajuste_fino_vira_ultima_rajada():
    feed = MonitorFeed(None)
    feed.handle_spectrum([b"afc.uhf", json.dumps({"offset_hz": -31.5, "snr_db": 33.0,
                                                  "bandwidth_hz": 7266.0}).encode()])

    afc = feed.state()["radios"]["uhf"]["afc"]
    assert afc["offset_hz"] == -31.5 and afc["age_s"] < 1.0


@pytest.mark.parametrize("frames", [
    [b"fft.vhf", b"nao e json", b""],
    [b"afc.vhf", json.dumps({"snr_db": 3}).encode()],
    [b"fft", b"{}", b""],
    [],
])
def test_mensagem_ilegivel_nao_derruba_nem_suja_o_estado(frames):
    feed = MonitorFeed(None)
    feed.handle_spectrum(frames)

    assert feed.spectrum() == {} and feed.state()["radios"] == {}


def test_sintonia_efetiva_e_nominal_mais_doppler_mais_ajuste():
    feed = MonitorFeed(None)
    feed.set_tracking(TRACKING)

    radios = feed.state()["radios"]
    assert radios["vhf"]["tune_hz"] == 145_900_000 - 915 + 1200
    assert radios["uhf"]["downlink"]["name"] == "dados"
    assert radios["vhf"]["spectrum_stale"] is True  # sem espectro ainda


def test_sem_passagem_o_radio_aparece_pelo_espectro():
    feed = MonitorFeed(None)
    feed.set_tracking(None)
    feed.handle_spectrum(fft_frames("uhf"))

    state = feed.state()
    assert state["tracking"] is None
    assert state["radios"]["uhf"]["downlink"] is None
    assert state["radios"]["uhf"]["tune_hz"] is None
    assert state["radios"]["uhf"]["spectrum_stale"] is False


def test_station_manager_fora_aparece_com_o_motivo():
    feed = MonitorFeed(None)
    feed.set_tracking(None, "Station Manager não respondeu em 1000 ms")

    assert feed.state()["station_manager"] == {
        "available": False, "error": "Station Manager não respondeu em 1000 ms"}


# --- ZMQ de verdade ---------------------------------------------------------------------


def test_threads_assinam_o_repasse_e_consultam_o_station_manager():
    context = zmq.Context()
    relay = context.socket(zmq.XPUB)
    relay_port = relay.bind_to_random_port("tcp://127.0.0.1")
    rep = context.socket(zmq.REP)
    rep_port = rep.bind_to_random_port("tcp://127.0.0.1")

    done = threading.Event()

    def station_manager():
        while not done.is_set():
            if rep.poll(100):
                rep.recv_json()
                rep.send_json({"ok": True, "tracking": TRACKING})

    responder = threading.Thread(target=station_manager, daemon=True)
    responder.start()
    status = StationManagerStatus(f"tcp://127.0.0.1:{rep_port}", context=context)
    feed = MonitorFeed(f"tcp://127.0.0.1:{relay_port}", tracking=status.get_tracking, poll_s=0.2)
    feed.start()
    try:
        # O XPUB recebe as assinaturas do monitor: é o que o repasse sobe aos blocos FFT.
        subscriptions = set()
        deadline = time.time() + 3
        while len(subscriptions) < 2 and time.time() < deadline:
            if relay.poll(200):
                subscriptions.add(relay.recv()[1:])
        deadline = time.time() + 3
        while not feed.spectrum() and time.time() < deadline:
            relay.send_multipart(fft_frames())
            time.sleep(0.1)
        state = feed.state()
    finally:
        feed.stop()
        done.set()
        responder.join(timeout=2)
        status.close()
        relay.close()
        rep.close()
        context.term()

    assert subscriptions == {b"fft.", b"afc."}
    assert feed.spectrum()["vhf"]["zoom"] == [-90.0, -55.0, -90.0]
    assert state["tracking"]["satellite_name"] == "ISS"
    assert state["station_manager"]["available"] is True


def test_station_manager_mudo_vira_erro_e_o_socket_se_recupera():
    context = zmq.Context()
    status = StationManagerStatus("tcp://127.0.0.1:9", timeout_ms=200, context=context)
    try:
        with pytest.raises(TimeoutError):
            status.get_tracking()
        with pytest.raises(TimeoutError):  # e não "Operation cannot be accomplished"
            status.get_tracking()
    finally:
        status.close()
        context.term()


# --- HTTP de verdade ----------------------------------------------------------------------


@pytest.fixture
def server():
    feed = MonitorFeed(None)
    feed.set_tracking(TRACKING)
    feed.handle_spectrum(fft_frames())
    http = start_server(feed, "127.0.0.1", 0)
    yield f"http://127.0.0.1:{http.server_address[1]}"
    http.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def test_pagina_e_servida(server):
    status, body = get(server + "/")

    assert status == 200
    assert b"<title>SpaceLab Spectrum Monitor</title>" in body
    assert b"/api/spectrum" in body and b"/api/state" in body


def test_api_state_e_spectrum(server):
    state = json.loads(get(server + "/api/state")[1])
    spectrum = json.loads(get(server + "/api/spectrum?radio=vhf")[1])
    other = json.loads(get(server + "/api/spectrum?radio=uhf")[1])

    assert state["radios"]["vhf"]["tune_hz"] == 145_900_285
    assert list(spectrum) == ["vhf"] and spectrum["vhf"]["seq"] == 1
    assert other == {}


def test_rota_desconhecida_e_404(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(server + "/api/nada")

    assert error.value.code == 404


def test_linha_de_comando():
    args = parse_args([])

    assert (args.station_manager, args.spectrum_source, args.port) == (
        "tcp://station-manager:5580", "tcp://station-manager:5583", 8094)
