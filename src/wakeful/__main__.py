# Absolute import on purpose: PyInstaller runs this file as a top-level script.
from wakeful.app import main

raise SystemExit(main())
