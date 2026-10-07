"""Auto-discovers every module in this package so that dropping a new
`wfXXX_*.py` file here is all that is needed to register a workflow."""
import importlib
import pkgutil

for _m in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{_m.name}")
