# Centralized constants for FactoryStock application

# Master list of materials - single source of truth for all forms
MATERIALS = [
    'PE',
    'HDPE',
    'PP',
    'PE - Recycle',
    'HDPE - Recycle',
]

# Material display labels (if different from value)
MATERIAL_LABELS = {
    'PE': 'PE',
    'HDPE': 'HDPE',
    'PP': 'PP',
    'PE - Recycle': 'PE - Recycle',
    'HDPE - Recycle': 'HDPE - Recycle',
}

# Item types
ITEM_TYPES = [
    ('roll', 'Roll'),
    ('sheet', 'Sheet'),
    ('bag', 'Bag'),
]

# Roll gusset types
GUSSET_TYPES = [
    ('1-side', '1-Side Gusset'),
    ('2-side', '2-Side Gusset'),
]

# Bag handle types
HANDLE_TYPES = [
    ('', 'Select handle...'),
    ('D-cut', 'D-cut Handle'),
    ('Loop', 'Loop Handle'),
    ('None', 'No Handle'),
]

# Dimension units
DIMENSION_UNITS = [
    ('inch', 'Inches'),
    ('cm', 'Centimeters'),
]