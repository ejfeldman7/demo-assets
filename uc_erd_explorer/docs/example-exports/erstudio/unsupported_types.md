# Unsupported type fallbacks

The following columns have a Unity Catalog type with no direct sqlserver equivalent and were mapped to a wide text column in physical_model.sql. Review these before import -- ER/Studio will show them as plain text, not their original shape.

_None -- every column type had a direct mapping._