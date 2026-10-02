"""Ponto de entrada: `python -m grs_spectrum_monitor.main`."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from grs_spectrum_monitor.feed import MonitorFeed, StationManagerStatus
from grs_spectrum_monitor.server import start_server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Spectrum Monitor da estação terrestre SpaceLab")
    parser.add_argument("--station-manager", default="tcp://station-manager:5580",
                        help="REQ do Station Manager (get_tracking: satélite, downlinks, "
                             "Doppler, ajuste fino)")
    parser.add_argument("--spectrum-source", default="tcp://station-manager:5583",
                        help="Repasse do espectro no Station Manager (--spectrum-bind)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8094)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    status = StationManagerStatus(args.station_manager)
    feed = MonitorFeed(args.spectrum_source, tracking=status.get_tracking)
    feed.start()
    server = start_server(feed, args.host, args.port)
    logging.getLogger(__name__).info(
        "Spectrum Monitor em http://%s:%d/ — espectro de %s, contexto de %s",
        args.host, args.port, args.spectrum_source, args.station_manager)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    server.shutdown()
    feed.stop()
    status.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
