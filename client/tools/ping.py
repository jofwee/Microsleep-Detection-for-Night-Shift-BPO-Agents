"""Play the alert ping once, to check you can hear it:   python -m tools.ping

This is the sound used for the microsleep alert and the "quick check". It does not depend on the Windows sound
scheme. If you hear nothing, check the volume of the output device the call desk uses (your headset).
"""
from __future__ import annotations

import sys
import time

from ui.sound import play_ping


def main() -> int:
    if not play_ping():
        print("The ping could not be played on this computer.")
        return 1
    print("Playing the alert ping (two rising tones). Did you hear it?")
    time.sleep(0.8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
