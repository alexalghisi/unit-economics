"""Capture docs/preview.gif from the running app: the SDK in a terminal, then the
dashboard and the simulator in Chromium.

Needs the API (UE_API_URL), a rollup worker and the web app (WEB_URL) running on seeded data.

    python scripts/preview.py
"""

import html
import io
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, ViewportSize, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB_URL = os.environ.get("WEB_URL", "http://localhost:3130")
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
VIEWPORT: ViewportSize = {"width": 1280, "height": 800}
OUT_WIDTH = 960

TERMINAL = """<!doctype html><meta charset=utf-8><style>
body{{margin:0;background:#0d0d0d;font:15px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;color:#e8e6df}}
.win{{margin:56px auto;width:1100px;border-radius:10px;background:#1a1a19;box-shadow:0 20px 60px #0008;overflow:hidden}}
.bar{{height:34px;background:#232322;display:flex;align-items:center;gap:8px;padding:0 14px}}
.bar i{{width:12px;height:12px;border-radius:50%;background:#383835;display:inline-block}}
.bar span{{margin-left:12px;color:#898781;font-size:13px}}
pre{{margin:0;padding:22px 26px;min-height:560px;white-space:pre-wrap}}
.p{{color:#3987e5}} .c{{color:#898781}}
</style><div class=win><div class=bar><i></i><i></i><i></i><span>unit-economics</span></div><pre>{body}</pre></div>"""


def env() -> dict[str, str]:
    pairs = (
        line.split("=", 1) for line in (ROOT / "web" / ".env.local").read_text().splitlines() if "=" in line
    )
    return {**os.environ, **dict(pairs)}


def shot(page: Page) -> Image.Image:
    return Image.open(io.BytesIO(page.screenshot())).convert("RGB")


def terminal_frames(page: Page) -> list[tuple[Image.Image, int]]:
    out = (
        subprocess.run(
            [sys.executable, "examples/quickstart.py"],
            cwd=ROOT,
            env=env(),
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()
    )
    command = "python examples/quickstart.py"
    comment = (
        "<span class=c># 3,000 LLM calls recorded with the Python SDK, then read back from the API</span>\n"
    )
    frames: list[tuple[Image.Image, int]] = []
    for n in (0, 10, 20, len(command)):
        page.set_content(
            TERMINAL.format(body=comment + f"<span class=p>$</span> {html.escape(command[:n])}▌")
        )
        frames.append((shot(page), 350 if n < len(command) else 700))
    for k in range(1, len(out) + 1):
        body = comment + f"<span class=p>$</span> {command}\n" + html.escape("\n".join(out[:k]))
        page.set_content(TERMINAL.format(body=body))
        frames.append((shot(page), 900 if k == 1 else 600))
    frames[-1] = (frames[-1][0], 2600)
    return frames


def app_frames(page: Page) -> list[tuple[Image.Image, int]]:
    frames: list[tuple[Image.Image, int]] = []
    page.goto(f"{WEB_URL}/?days=30")
    page.wait_for_selector("text=Least profitable customers")
    page.wait_for_timeout(1200)
    frames.append((shot(page), 2400))
    chart = page.locator("svg[aria-label='Revenue and LLM cost per day']")
    box = chart.bounding_box()
    assert box is not None
    for fx in (0.45, 0.62):
        page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] / 2)
        page.wait_for_timeout(300)
        frames.append((shot(page), 1300))
    page.mouse.move(5, 5)
    page.mouse.wheel(0, 560)
    page.wait_for_timeout(600)
    frames.append((shot(page), 2600))

    page.goto(f"{WEB_URL}/simulator?days=30")
    page.wait_for_selector("text=Customers on this plan")
    page.wait_for_selector("text=Included credits")
    page.wait_for_timeout(1500)
    frames.append((shot(page), 2400))
    overage = page.get_by_label("Overage amount")
    for value in ("3", "4.5"):
        overage.fill(value)
        page.wait_for_timeout(250)
        frames.append((shot(page), 400))  # the results dim while recomputing
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(600)
        frames.append((shot(page), 2600))
    page.get_by_label("Prepaid amount").fill("79")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(700)
    frames.append((shot(page), 3200))
    return frames


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page(viewport=VIEWPORT)
        frames = terminal_frames(page) + app_frames(page)
        browser.close()
    scaled = [
        (img.resize((OUT_WIDTH, round(img.height * OUT_WIDTH / img.width)), Image.Resampling.LANCZOS), ms)
        for img, ms in frames
    ]
    quantized = [
        img.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        for img, _ in scaled
    ]
    target = ROOT / "docs" / "preview.gif"
    quantized[0].save(
        target,
        save_all=True,
        append_images=quantized[1:],
        duration=[ms for _, ms in scaled],
        loop=0,
        optimize=True,
    )
    for i, (img, _) in enumerate(scaled):
        img.save(ROOT / "docs" / ".frames" / f"{i:02d}.png") if (ROOT / "docs" / ".frames").is_dir() else None
    print(f"{target.relative_to(ROOT)}: {len(frames)} frames, {target.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
