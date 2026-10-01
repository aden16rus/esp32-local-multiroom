#!/usr/bin/env python3
"""
Root Launcher for Local Voice Control Central Server.
Usage:
    python run_server.py --host 0.0.0.0 --port 8765 --ha-url ws://192.168.1.50:8123/api/websocket
"""
import argparse
import asyncio
import logging
import sys
import signal
from server.main import CentralVoiceServer
from server.config import ServerConfig


def parse_args():
    cfg = ServerConfig.from_env()
    parser = argparse.ArgumentParser(description="Local Voice Control Central Server for Home Assistant")
    parser.add_argument(
        "--host",
        type=str,
        default=cfg.host,
        help=f"Host address to bind WebSocket server (default: {cfg.host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=cfg.port,
        help=f"Port to listen for satellite connections (default: {cfg.port})"
    )
    parser.add_argument(
        "--ha-url",
        type=str,
        default=cfg.ha_url,
        help=f"Home Assistant API endpoint URL (default: {cfg.ha_url})"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=cfg.log_level,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=f"Logging verbosity level (default: {cfg.log_level})"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=cfg.log_file,
        help=f"Path to log file (default: {cfg.log_file})"
    )
    return parser.parse_args()



async def main():
    args = parse_args()

    # Configure logging (Console + Rotating File)
    from server.config import setup_logging
    setup_logging(log_level=args.log_level, log_file=args.log_file)
    logger = logging.getLogger("Launcher")

    logger.info("==========================================================")
    logger.info(" Starting Local Voice Control System Central Server")
    logger.info(f" Server Address : ws://{args.host}:{args.port}")
    logger.info(f" Home Assistant  : {args.ha_url}")
    logger.info(f" Log File        : {args.log_file}")
    logger.info("==========================================================")

    server_app = CentralVoiceServer(host=args.host, port=args.port, ha_url=args.ha_url)
    server_instance = await server_app.start()

    # Wait until interrupted
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Shutdown signal received.")
        stop_event.set()

    # Register OS signals where supported
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

    try:
        if sys.platform == "win32":
            # Keep event loop running on Windows until Ctrl+C
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
        else:
            await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Stopping Central Server...")
    finally:
        await server_app.ha_client.close()
        logger.info("Server stopped cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
