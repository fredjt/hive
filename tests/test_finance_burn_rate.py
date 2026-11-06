import sys
from pathlib import Path

# Ensure lib/ is on the path
_hive_lib = str(Path(__file__).resolve().parent.parent / "lib")
if _hive_lib not in sys.path:
    sys.path.insert(0, _hive_lib)

def test_generate_q3_burn_rate_report():
    """Test that Q3 burn rate report can be generated."""
    from lib.finance.burn_rate import generate_quarterly_burn_rate_report
    
    result = generate_quarterly_burn_rate_report(quarter="Q3", year=2026)
    
    assert result is not None
    assert isinstance(result, dict), "Report should be a dictionary"
    assert "quarter" in result
    assert result["quarter"] == "Q3"
