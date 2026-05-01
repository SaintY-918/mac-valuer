# Entry point for Streamlit Community Cloud.
# Streamlit Cloud requires the main file at the repo root;
# this shim ensures src/ package imports resolve correctly.
import src.dashboard  # noqa: F401 — side-effectful import runs the dashboard
