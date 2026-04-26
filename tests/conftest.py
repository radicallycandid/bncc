import sys
from pathlib import Path

# batch_utils é instalado via `pip install -e .` e importa globalmente.
# scripts/ ainda é necessário para importar build_pairs nos testes.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
