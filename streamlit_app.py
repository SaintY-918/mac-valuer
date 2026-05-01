import os
import runpy

# runpy.run_path executes the file in a fresh namespace on every Streamlit rerun,
# bypassing Python's module cache. This avoids StreamlitDuplicateElementKey errors
# that occur with importlib.reload, while still allowing st.cache_resource to work
# (Streamlit keys the cache by function bytecode, not object identity).
runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "dashboard.py"),
    run_name="__main__",
)
