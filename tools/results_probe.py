"""Open Betwinner's results page in a real browser and record which JSON endpoints it calls, so we can find where
final scores of finished matches come from. Writes results_probe.json (URLs, status, size, first bytes). No secrets."""
import json, os, re, sys, time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE = (os.environ.get("BW_BASE_URLS") or "https://betwinner2.com").split(",")[0].rstrip("/")
PROXY = os.environ.get("BW_PROXY_URL") or ""
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1")
rec: list[dict] = []

def redact(u: str) -> str:
    return re.sub(r"([?&](?:token|key|auth|sign|hash)[^=]*=)[^&]+", r"\1<x>", u)

def main() -> None:
    kw = {}
    if PROXY:
        pu = urlparse(PROXY)
        kw["proxy"] = {"server": f"{pu.scheme}://{pu.hostname}:{pu.port}", "username": pu.username or "", "password": pu.password or ""}
    with sync_playwright() as p:
        b = p.chromium.launch(**kw)
        ctx = b.new_context(user_agent=UA, locale="es-419", viewport={"width": 390, "height": 844}, is_mobile=True)
        page = ctx.new_page()

        def on_resp(r):
            try:
                ct = r.headers.get("content-type", "")
                u = r.url
                if r.request.resource_type in ("xhr", "fetch") or "json" in ct:
                    body = ""
                    try:
                        body = r.text()[:700]
                    except Exception:  # noqa: BLE001
                        pass
                    rec.append(dict(url=redact(u)[:300], method=r.request.method, status=r.status, ct=ct[:40], size=len(body), head=body))
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_resp)
        notes = []
        for path in ("/es/results", "/es/results/table-tennis", "/es/results/10"):
            try:
                page.goto(BASE + path, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(5000)
                notes.append(dict(path=path, title=page.title(), text=page.inner_text("body")[:500]))
                for label in ("Tenis de mesa", "Table Tennis"):
                    loc = page.get_by_text(label, exact=False).first
                    if loc.count():
                        loc.click(timeout=5000)
                        page.wait_for_timeout(4000)
                        notes.append(dict(clicked=label, text=page.inner_text("body")[:500]))
                        break
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(2500)
            except Exception as e:  # noqa: BLE001
                notes.append(dict(path=path, error=str(e)[:200]))
        b.close()
    seen, uniq = set(), []
    for r in rec:
        k = (r["method"], r["url"].split("?")[0])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    json.dump(dict(base=BASE, notes=notes, n_all=len(rec), endpoints=uniq), open("results_probe.json", "w"), ensure_ascii=False)
    print("endpoints", len(uniq), "of", len(rec))

if __name__ == "__main__":
    main()
