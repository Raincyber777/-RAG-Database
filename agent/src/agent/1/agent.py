"""Legacy import location. Use the injected factory in agent.runtime.

Importing this module never connects to PostgreSQL or initializes schemas.
"""

if __name__ == '__main__' and not __package__:
    # The old script is named agent.py; prefer the package over this file.
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.runtime import CustomState, build_agent, open_agent
from agent.schemas import UserContext

__all__ = ["CustomState", "UserContext", "build_agent", "open_agent"]

if __name__ == "__main__":
    from agent import main
    main()
