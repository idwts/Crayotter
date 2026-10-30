"""Start from any working directory: python /path/to/Crayotter/script/run_mcp.py."""
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from script.mcp_server import main
    main()
