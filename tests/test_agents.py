import pytest
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.config import BusinessConfig

# Load test config
CONFIG_PATH = Path(__file__).parent.parent / "configs" / "dental_clinic.json"

def test_config_loads_correctly():
    """Business config loads and validates"""
    config = BusinessConfig.from_json_file(str(CONFIG_PATH))
    assert config.business_name == "SmileCare Dental Clinic"
    assert len(config.services) > 0
    assert config.business_type == "dental"
    print("Config loads correctly")

def test_config_has_required_fields():
    """All required fields present in config"""
    config = BusinessConfig.from_json_file(str(CONFIG_PATH))
    assert config.business_id is not None
    assert config.owner_whatsapp is not None
    assert config.greeting_message is not None
    assert config.escalation_keywords is not None
    print("All required fields present")

def test_escalation_keywords_detection():
    """Escalation keywords trigger correctly"""
    config = BusinessConfig.from_json_file(str(CONFIG_PATH))
    message = "I have a severe pain emergency"
    triggered = any(
        kw.lower() in message.lower()
        for kw in config.escalation_keywords
    )
    assert triggered is True
    print("Escalation keywords detected correctly")

def test_working_hours_format():
    """Working hours are properly formatted"""
    config = BusinessConfig.from_json_file(str(CONFIG_PATH))
    hours = config.working_hours
    assert hours.monday is not None
    assert hours.wednesday == "CLOSED"
    print("Working hours format correct")

def test_multiple_business_configs():
    """Multiple business config templates load"""
    configs_dir = Path(__file__).parent.parent / "configs"
    for config_file in configs_dir.glob("*.json"):
        config = BusinessConfig.from_json_file(str(config_file))
        assert config.business_name is not None
        assert config.business_id is not None
        print(f"Config loaded: {config.business_name}")