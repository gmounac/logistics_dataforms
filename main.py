"""Run the container yard app.

    uv run main.py            # serve on 0.0.0.0:8000, reachable from a phone
    uv run main.py --port 9000

On the same Wi-Fi, open http://<this-machine-LAN-IP>:8000 on the phone.
The LAN address is printed on startup.
"""

import argparse
import socket


def lan_ip() -> str:
    """Best-effort primary LAN IP of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the container yard app.")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = parser.parse_args()

    import uvicorn

    print(f"  Container Yard on this machine : http://127.0.0.1:{args.port}")
    print(f"  From a phone on the same Wi-Fi : http://{lan_ip()}:{args.port}")
    uvicorn.run("src.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
