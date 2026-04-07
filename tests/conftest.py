"""Shared test fixtures."""

import os
import sys
import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load .env for tests
from dotenv import load_dotenv
load_dotenv()

from pipeline.logger import setup_logging
setup_logging(mode="cli")
