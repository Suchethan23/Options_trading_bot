"""
conftest.py — ensures project root is on sys.path for all tests.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
