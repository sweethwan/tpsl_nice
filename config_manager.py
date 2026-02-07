import json
import os

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "exchanges": {
        "binance": {
            "api_key": "", "secret_key": "", 
            "sl": 5.0, "tp": 10.0, 
            "sl_enabled": True, "tp_enabled": True,
            "market_type": "spot", # spot or future
            "whitelist": []
        },
        "okx": {
            "api_key": "", "secret_key": "", "password": "", 
            "sl": 5.0, "tp": 10.0, 
            "sl_enabled": True, "tp_enabled": True,
            "whitelist": []
        },
        "bithumb": {
            "api_key": "", "secret_key": "", 
            "sl": 5.0, "tp": 10.0, 
            "sl_enabled": True, "tp_enabled": True,
            "whitelist": []
        },
        "upbit": {
            "api_key": "", "secret_key": "", 
            "sl": 5.0, "tp": 10.0, 
            "sl_enabled": True, "tp_enabled": True,
            "whitelist": []
        }
    },
    "global_settings": {
        "auto_trading": False,
        "update_interval": 5
    }
}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return DEFAULT_CONFIG

def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4)
