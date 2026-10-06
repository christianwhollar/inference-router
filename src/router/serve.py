import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Launch the model gateway and operations console")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument(
        "--models", help="Model configuration JSON; omit in demo mode for offline fixtures"
    )
    parser.add_argument("--port", type=int, default=8104)
    parser.add_argument("--data-dir", default="runtime")
    args = parser.parse_args()
    os.environ["DATA_DIR"] = args.data_dir
    if args.demo:
        os.environ["APP_DEMO"] = "1"
    if args.models:
        os.environ["MODELS_CONFIG"] = str(Path(args.models).resolve())
    import uvicorn

    uvicorn.run("router.api:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
