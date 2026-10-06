import json
import os
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount(app):
    root = Path(__file__).parent
    app.mount("/assets", StaticFiles(directory=root / "web"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(root / "web/index.html")

    @app.get("/app-config")
    def config():
        return {"demo": os.getenv("APP_DEMO") == "1"}

    @app.get("/benchmarks")
    def benchmarks():
        reports = {}
        for name in ["calibration", "reliability"]:
            path = root / "resources" / (name + ".json")
            if path.exists():
                reports[name] = json.loads(path.read_text())
        return reports
