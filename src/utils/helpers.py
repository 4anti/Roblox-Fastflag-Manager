import os
import sys

def get_resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # If not running in a bundle, use the directory of main.pyw (root)
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    return os.path.join(base_path, relative_path)

def infer_type(value):
    lower_value = str(value).lower().strip()
    # In Roblox, 1 and 0 are almost always integers, not booleans.
    # We should only treat 'true'/'false'/'yes'/'no' as booleans.
    if lower_value in ['true', 'false', 'yes', 'no']:
        return 'bool'
    try:
        if '.' in lower_value:
            float(lower_value)
            return 'float'
        int(lower_value)
        return 'int'
    except:
        pass
    return 'string'


# Roblox FFlag prefix → required data type mapping
# The prefix is the ONLY reliable source of truth for the type.
_PREFIX_TYPE_MAP = [
    ('DFFlag', 'bool'),
    ('SFFlag', 'bool'),
    ('FFlag',  'bool'),
    ('GFFlag', 'bool'),
    ('DFInt',  'int'),
    ('SFInt',  'int'),
    ('FInt',   'int'),
    ('DFLog',  'int'),
    ('FLog',   'int'),
    ('DFFloat', 'float'),
    ('SFFloat', 'float'),
    ('FFloat',  'float'),
    ('DFString', 'string'),
    ('SFString', 'string'),
    ('FString',  'string'),
]

def infer_type_from_name(full_flag_name):
    """Deterministically detect a flag's required type from its Roblox prefix.

    Returns one of: 'bool', 'int', 'string', or None if unknown.
    """
    for prefix, ftype in _PREFIX_TYPE_MAP:
        if full_flag_name.startswith(prefix):
            return ftype
    return None


def clean_flag_name(flag_name):
    prefixes = [p[0] for p in _PREFIX_TYPE_MAP]

    # Sort by length descending to match longest prefix first (e.g. DFString before DF)
    prefixes.sort(key=len, reverse=True)

    for prefix in prefixes:
        if flag_name.startswith(prefix):
            return flag_name[len(prefix):]

    return flag_name

def get_flag_prefix(full_flag_name):
    """Return just the prefix portion of a flag name (e.g. 'FInt' from 'FIntSomeFlag')."""
    prefixes = [p[0] for p in _PREFIX_TYPE_MAP]
    prefixes.sort(key=len, reverse=True)
    
    for prefix in prefixes:
        if full_flag_name.startswith(prefix):
            return prefix
    return ''

# Fallback database for common FFlags
# Used if memory reading fails or if we need a safe reversion target
DEFAULT_VALUES = {
    'TaskSchedulerTargetFps': '60',
    'FFlagDisableAdService': 'true',
    'DFFlagDisableAdService': 'true'
}

def get_default_value(name):
    """Return a best-guess default value based on prefix or known constants."""
    if name in DEFAULT_VALUES:
        return DEFAULT_VALUES[name]
        
    if name.startswith('FFlag') or name.startswith('DFFlag') or name.startswith('SFFlag'):
        return 'false'
    if name.startswith('FInt') or name.startswith('DFInt') or name.startswith('SFInt'):
        return '0'
    if name.startswith('FLog') or name.startswith('DFLog'):
        return '0'
    return ''
