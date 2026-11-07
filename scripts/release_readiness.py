"""Print a redacted, passive delivery report. No capture, inference or service control."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from app.release_readiness import inspect_release
if __name__=='__main__':print(json.dumps(inspect_release(ROOT),ensure_ascii=False,indent=2))
