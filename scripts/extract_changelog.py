import os
import sys
import re

def extract_changelog(version):
    """Extracts the changelog section for a specific version from CHANGELOG.md"""
    # Strip 'v' prefix if it exists to match the markdown format ## [1.0.0]
    clean_version = version.lstrip('v')
    
    changelog_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")
    
    if not os.path.exists(changelog_path):
        print(f"Error: CHANGELOG.md not found at {changelog_path}")
        sys.exit(1)
        
    try:
        with open(changelog_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading CHANGELOG.md: {e}")
        sys.exit(1)

    # Regex to capture content between this version's header and the next version's header
    pattern = re.compile(rf"## \[{re.escape(clean_version)}\](?: - [0-9-]+)?(.*?)(?=\n## \[|\Z)", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)
    
    if match:
        release_notes = match.group(1).strip()
        print(release_notes)
        return True
    else:
        print(f"Could not find changelog section for version: {clean_version}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_changelog.py <version_tag>")
        sys.exit(1)
    
    extract_changelog(sys.argv[1])
