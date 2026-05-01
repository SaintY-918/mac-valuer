import importlib
import src.dashboard

# Streamlit re-runs this file on every interaction, but Python caches modules.
# importlib.reload forces re-execution of dashboard code on each rerun so that
# all Streamlit widgets are re-registered correctly.
importlib.reload(src.dashboard)
