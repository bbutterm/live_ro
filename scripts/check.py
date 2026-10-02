from pathlib import Path
import subprocess
root = Path(__file__).resolve().parents[1]
pins = {"rathena": "e985006171d2eb320ee512a653f4c83aea3d81b6", "openkore": "51de1ddfc4449ae5217f6886de702f87ca934030"}
for name, expected in pins.items():
    actual = subprocess.check_output(["git", "-C", str(root / "upstream" / name), "rev-parse", "HEAD"], text=True).strip()
    if actual != expected:
        raise SystemExit(f"Version mismatch: {name}")
    print(f"{name}: pinned version OK")
print("Repository checks OK; runtime not tested")
