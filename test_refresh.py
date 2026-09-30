import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
from model import refresh_signals
try:
    result = refresh_signals()
    print(f"Signale generiert: {len(result)}")
    for s in result[:5]:
        print(f"  {s['symbol']}: prob_up={s['prob_up']:.2f}, ts={s['timestamp']}")
except Exception as e:
    print(f"FEHLER: {e}")
    import traceback
    traceback.print_exc()
