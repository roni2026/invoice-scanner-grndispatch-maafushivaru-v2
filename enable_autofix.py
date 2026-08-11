"""
enable_autofix.py
-------------------
Run this ONCE, next to your real config.json:

    python enable_autofix.py

Adds "auto_fix_supplier_by_invoice_pattern": true to app_settings, so the
feature runs by default -- no Settings checkbox required. Back up is made
automatically before writing.
"""
import json, shutil
from pathlib import Path
from datetime import datetime

CONFIG = Path("config.json")
cfg = json.loads(CONFIG.read_text(encoding="utf-8"))

backup = CONFIG.with_name(f"config_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
shutil.copy2(CONFIG, backup)
print(f"Backed up -> {backup.name}")

cfg.setdefault("app_settings", {})["auto_fix_supplier_by_invoice_pattern"] = True
CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
print("Set app_settings.auto_fix_supplier_by_invoice_pattern = true")
