#!/usr/bin/env python3
"""
Entry point to run the gmail-mcp server.
"""
import sys
from pathlib import Path

# Add parent to path so 'src' package is importable
sys.path.insert(0, str(Path(__file__).parent))

from src.server import mcp

if __name__ == "__main__":
    mcp.run()
