"""Levanta la API y la aplicación web: python -m averias [--puerto 8000]"""

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Servidor de la aplicación de averías")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--puerto", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run("averias.api:crear_app", factory=True, host=args.host, port=args.puerto)


if __name__ == "__main__":
    main()
