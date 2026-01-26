"""Simple test to debug registry issues."""
import sys
from pathlib import Path
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

try:
    from provena_flask.models.provenance import ProvenanceDatabase
    print("✓ Successfully imported ProvenanceDatabase")
    
    # Try to create database
    temp_dir = tempfile.mkdtemp()
    db_path = f"{temp_dir}/test.db"
    print(f"Creating database at: {db_path}")
    
    db = ProvenanceDatabase(db_path)
    print("✓ Database created successfully")
    
    # Try to count records
    count = db.count_records()
    print(f"✓ Record count: {count}")
    
except Exception as e:
    print(f"✗ Error: {type(e).__name__}: {str(e)}")
    import traceback
    traceback.print_exc()
