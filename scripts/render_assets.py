"""Render docs/architecture.mmd with Mermaid and capture live Streamlit screenshots from localhost:8501."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
IMG_DIR = ROOT / "docs" / "img"


def render_mermaid_architecture(playwright) -> None:
    mmd_text = (ROOT / "docs" / "architecture.mmd").read_text(encoding="utf-8")
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ margin: 0; padding: 24px; background: #ffffff; display: inline-block; }}
</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: 'default', securityLevel: 'loose' }});
</script>
</head>
<body>
  <div id="diagram" class="mermaid">
{mmd_text}
  </div>
</body>
</html>"""
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(html)
        tmp_path = Path(f.name)

    browser = playwright.chromium.launch(
        executable_path="/usr/bin/google-chrome",
        args=["--no-sandbox", "--disable-gpu"],
    )
    page = browser.new_page(viewport={"width": 1600, "height": 1200}, device_scale_factor=2)
    page.goto(f"file://{tmp_path}", wait_until="networkidle")
    page.wait_for_selector("#diagram svg", timeout=15000)
    svg_el = page.locator("#diagram svg")
    svg_el.screenshot(path=str(IMG_DIR / "architecture.png"))
    browser.close()
    tmp_path.unlink(missing_ok=True)
    print("Rendered docs/img/architecture.png from docs/architecture.mmd")


def wait_for_streamlit(url: str = "http://localhost:8501/_stcore/health", timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("Streamlit server did not become healthy in time")


def capture_streamlit_pages(playwright) -> None:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(ROOT / "src" / "dashboard.py"),
            "--server.port",
            "8501",
            "--server.headless",
            "true",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_streamlit()
        browser = playwright.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        targets = [
            ("http://localhost:8501/?page=Signal+receipt", "signal_receipt.png"),
            ("http://localhost:8501/?page=Project+history", "project_timeline.png"),
            ("http://localhost:8501/?page=Scoreboard", "eval_scoreboard.png"),
        ]
        for url, out_name in targets:
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=15000)
            time.sleep(2.0)
            page.screenshot(path=str(IMG_DIR / out_name), full_page=False)
            print(f"Captured live Streamlit screenshot: docs/img/{out_name}")
        browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        render_mermaid_architecture(p)
        capture_streamlit_pages(p)


if __name__ == "__main__":
    main()
