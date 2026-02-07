import logging
import streamlit as st
from datetime import datetime

class StreamlitSessionHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            ts = datetime.now().strftime('%H:%M:%S')
            log_entry = f"[{ts}] {msg}"
            # Direct append to session state if it exists
            if hasattr(st, 'session_state') and 'log_history' in st.session_state:
                st.session_state.log_history.append(log_entry)
                if len(st.session_state.log_history) > 100:
                    st.session_state.log_history.pop(0)
        except Exception:
            pass

def setup_logger():
    logger = logging.getLogger("TradingBot")
    logger.setLevel(logging.INFO)
    
    # Avoid adding handlers multiple times? 
    # We check specifically for our custom handler to avoid dupes on re-runs
    
    # File Handler
    # We want to keep one file handler
    has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
    if not has_file:
        try:
            file_handler = logging.FileHandler("trading_log.txt", encoding='utf-8')
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass # Maybe permission issue
    
    # Streamlit Handler
    # Check by class name to be safe even if reload happens (though less likely if in module)
    has_sl = any(h.__class__.__name__ == 'StreamlitSessionHandler' for h in logger.handlers)
    
    if not has_sl:
        sl_handler = StreamlitSessionHandler()
        formatter = logging.Formatter('%(message)s')
        sl_handler.setFormatter(formatter)
        logger.addHandler(sl_handler)

    return logger
