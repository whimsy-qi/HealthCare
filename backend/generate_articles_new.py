"""Compatibility wrapper for the structured article generation pipeline.

The old version of this script contained a small placeholder topic list and an
invalid category name. Keep this entrypoint so existing commands still work,
but route all generation through generate_articles.py.
"""

from generate_articles import main


if __name__ == "__main__":
    main()
