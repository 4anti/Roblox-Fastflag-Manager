"""Empty first paint of the flag list must not pin a zero-length search cache."""
from src.gui.api import Api


class _FM:
    preset_flags_list = []
    user_flags = []
    official_types = {}


def test_empty_search_cache_rebuilds_when_flag_list_grows():
    api = Api.__new__(Api)
    api.flag_manager = _FM()

    assert api.get_fflag_count("") == 0

    api.flag_manager.preset_flags_list = [
        "FFlagExampleOne",
        "FFlagExampleTwo",
        "DFIntExampleThree",
    ]
    assert api.get_fflag_count("") == 3
    flags = api.get_available_flags("", 0, 10)
    assert [f["name"] for f in flags] == api.flag_manager.preset_flags_list
