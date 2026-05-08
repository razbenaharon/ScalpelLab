"""Launch the ScalpelLab NiceGUI dashboard.

Imports app.app directly so PyInstaller / nicegui-pack can package this
file as the entry point. Run from a Python interpreter:

    python run_app.py
"""

from app.app import main

if __name__ in {"__main__", "__mp_main__"}:
    main()
