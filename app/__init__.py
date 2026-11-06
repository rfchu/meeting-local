"""Application-owned process defaults; deployments may explicitly override them."""
import os

# No Pydantic entry-point plugins are used by this app. Avoid scanning unrelated
# installed distributions, whose metadata may be cloud-only on macOS.
os.environ.setdefault('PYDANTIC_DISABLE_PLUGINS','__all__')
