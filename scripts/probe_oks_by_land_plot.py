# scripts/probe_oks_by_land_plot.py
import signal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from nspd_request import NSPD

def _call(label, fn, *args):
    print(f"→ {label} ...", flush=True)
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        r = ex.submit(fn, *args).result(timeout=10)
        print(f"  {label}: {type(r).__name__}, {str(r)[:300]}", flush=True)
    except FTimeout:
        print(f"  {label}: TIMEOUT", flush=True)
    except Exception as e:
        print(f"  {label}: FAILED {type(e).__name__}: {e}", flush=True)
    finally:
        ex.shutdown(wait=False)

api = NSPD(timeout=10)
# Участок 61:44:0060958:9 — знаем из probe; на нём стоит ОКС :173
_call("get_oks_by_land_plot", api.get_oks_by_land_plot, "61:44:0060958:9")
_call("get_related_objects", api.get_related_objects, "61:44:0060958:9")