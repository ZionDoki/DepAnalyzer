import re

# Read the file
with open('depanalyzer/runtime/transaction.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern to match "except Exception:" or "except Exception as e:"
# We need to be careful to only match Exception, not other specific exceptions
pattern = r'(\s+)except Exception( as \w+)?:'

# Replacement with specific exceptions
replacement = r'\1except (\n\1    RuntimeError,\n\1    ValueError,\n\1    TypeError,\n\1    AttributeError,\n\1    KeyError,\n\1    IndexError,\n\1    OSError,\n\1    ImportError,\n\1    LookupError,\n\1)\2:'

# Replace all occurrences
new_content = re.sub(pattern, replacement, content)

# Write back
with open('depanalyzer/runtime/transaction.py', 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Replaced all broad exception catches")
