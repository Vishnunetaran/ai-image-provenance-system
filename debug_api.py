"""Simple API test to debug issues."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

os.environ['FLASK_ENV'] = 'testing'

try:
    print("Importing Flask app...")
    from provena_flask import create_app
    
    print("Creating app...")
    app = create_app('testing')
    
    print("Creating test client...")
    client = app.test_client()
    
    print("Testing health endpoint...")
    response = client.get('/health')
    print(f"Health response: {response.status_code} - {response.get_json()}")
    
    print("\n✓ Basic setup works!")
    
except Exception as e:
    print(f"\n✗ Error: {type(e).__name__}: {str(e)}")
    import traceback
    traceback.print_exc()
