"""Syntax-check the shipped browser code using Node.js."""

from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
for path in sorted(root.glob("src/*/web/*.js")):
    subprocess.run(["node", "--check", str(path)], check=True)
